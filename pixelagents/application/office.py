"""Framework-neutral office agent state and projection orchestration."""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, TypeAlias, TypeVar

from ..contracts.outbound import (
    ExistingAgentsMessage,
    agent_closed,
    agent_created,
    agent_deselected,
    agent_selected,
    agent_status,
    agent_team_info,
    agent_tool_start,
    agent_tools_clear,
)
from ..domain import AgentKey, AgentSnapshot, GenuineAgentKey, MessageSnapshot, OfficeIdentity
from .presence import PresenceService, SendMessage, ServerMessage

JS_MAX_SAFE = (1 << 53) - 1
DEFAULT_PALETTE_COUNT = 6
READING_TOOLS = ("Read", "Grep", "Glob", "WebFetch", "WebSearch")
SUBAGENT_TOOL_NAMES = ("Task", "Agent")

SeatRecords: TypeAlias = dict[str, dict[str, Any]]
MutationResult = TypeVar("MutationResult")


class SeatRepository(Protocol):
    async def seats(self) -> SeatRecords: ...

    async def mutate_seats(
        self, mutation: Callable[[SeatRecords], MutationResult]
    ) -> MutationResult: ...


def to_agent_id(external_id: int) -> int:
    """Map an external member ID (e.g. a Discord snowflake) to a stable
    negative JavaScript-safe integer the webview uses as its agent ID."""

    mapped = external_id % JS_MAX_SAFE
    return -(mapped if mapped != 0 else JS_MAX_SAFE)


def to_genuine_agent_id(agent_key: str) -> int:
    """Map a genuine agent's stable slug (e.g. "architect") to a positive
    JavaScript-safe integer -- disjoint from to_agent_id's always-negative
    range by construction, so a genuine agent's webview ID can never
    collide with a real Discord user's, with no collision registry
    needed. A stable hash (not Python's randomized hash()) so the same
    agent_key maps to the same ID across restarts. See
    docs/office-agent-identity-design.md."""

    digest = hashlib.sha256(agent_key.encode()).digest()
    mapped = int.from_bytes(digest[:8], "big") % JS_MAX_SAFE
    return mapped if mapped != 0 else JS_MAX_SAFE


def merge_seat_patch(
    seats: SeatRecords,
    agent_id: str,
    palette_count: int,
    patch: Mapping[str, object],
) -> None:
    """Validate and merge one agent's seat/palette patch into `seats` in place.

    Shared by the editor-driven `SaveAgentSeats` path (an out-of-range or
    wrong-typed field is dropped rather than corrupting the persisted record)
    and available to any other seat-writing path a consuming cog adds later.
    """

    record = dict(seats.get(agent_id) or {})
    palette = patch.get("palette")
    hue_shift = patch.get("hueShift")
    seat_id = patch.get("seatId")
    if type(palette) is int and 0 <= palette < palette_count:
        record["palette"] = palette
    if type(hue_shift) is int and 0 <= hue_shift <= 360:
        record["hueShift"] = hue_shift
    if isinstance(seat_id, str):
        record["seatId"] = seat_id
    seats[agent_id] = record


class OfficeService:
    """Own the active agent registry and all office state transitions."""

    def __init__(
        self,
        repository: SeatRepository,
        send: SendMessage,
        *,
        presence: PresenceService | None = None,
        logger: logging.Logger | None = None,
        palette_count: int = DEFAULT_PALETTE_COUNT,
        choose: Callable[[Sequence[int]], int] = random.choice,
        hue_shift: Callable[[int, int], int] = random.randint,
    ) -> None:
        self._repository = repository
        self._send = send
        self.presence = presence or PresenceService(send)
        self._log = logger or logging.getLogger(__name__)
        self._palette_count = palette_count
        self._choose = choose
        self._hue_shift = hue_shift
        self._agents: dict[tuple[int, int], tuple[str, str]] = {}
        # Keyed by bare user_id, not (guild_id, user_id) like self._agents --
        # is_bot is guild-invariant (a Discord account's bot flag doesn't
        # change per guild), and _representative_agents() already collapses
        # multi-guild presence down to one representative user_id, so a
        # (guild_id, user_id)-keyed dict here would need that same
        # internally-chosen representative guild_id, which isn't exposed.
        self._agent_is_bot: dict[int, bool] = {}
        self._logged_collisions: set[int] = set()
        # Genuine agents (no Discord account, e.g. architect) -- a second,
        # simpler roster alongside self._agents: no cross-guild merge logic
        # applies (a genuine agent isn't guild-scoped, so there's only ever
        # one entry per agent_key), folded into the same outward-facing
        # roster (existing_agents_message/bootstrap_messages) every
        # connecting browser already receives. See
        # docs/office-agent-identity-design.md.
        self._genuine_agents: dict[str, tuple[str, str]] = {}

    @property
    def active_agents(self) -> dict[tuple[int, int], tuple[str, str]]:
        """Mutable legacy view retained while the Cog adapters are slimmed."""

        return self._agents

    def replace_active_agents(self, agents: dict[tuple[int, int], tuple[str, str]]) -> None:
        self._agents = agents

    @property
    def logged_collisions(self) -> set[int]:
        return self._logged_collisions

    @staticmethod
    def agent_id(user_id: int) -> int:
        return to_agent_id(user_id)

    @staticmethod
    def genuine_agent_id(agent_key: str) -> int:
        return to_genuine_agent_id(agent_key)

    def _agent_id_for(self, identity: OfficeIdentity) -> int:
        if isinstance(identity, GenuineAgentKey):
            return self.genuine_agent_id(identity.agent_key)
        return self.agent_id(identity.user_id)

    def tracked_user_ids(self) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for _, user_id in sorted(self._agents):
            if user_id in seen:
                continue
            seen.add(user_id)
            ordered.append(user_id)
        return ordered

    def _representative_agents(self) -> dict[int, tuple[str, str]]:
        representatives: dict[int, tuple[str, str]] = {}
        for (_, user_id), agent_state in sorted(self._agents.items()):
            representatives.setdefault(user_id, agent_state)
        return representatives

    def is_tracked(self, identity: OfficeIdentity) -> bool:
        if isinstance(identity, GenuineAgentKey):
            return identity.agent_key in self._genuine_agents
        return (identity.guild_id, identity.user_id) in self._agents

    def is_user_active_in_other_guild(self, guild_id: int, user_id: int) -> bool:
        return any(gid != guild_id and uid == user_id for gid, uid in self._agents)

    def detect_collision(self, user_id: int) -> None:
        agent_id = self.agent_id(user_id)
        for _, active_user_id in self._agents:
            if active_user_id == user_id or self.agent_id(active_user_id) != agent_id:
                continue
            if agent_id not in self._logged_collisions:
                self._logged_collisions.add(agent_id)
                self._log.warning(
                    "pixelagents: agent ID collision — user %d and user %d both map to %d",
                    user_id,
                    active_user_id,
                    agent_id,
                )
            break

    @staticmethod
    def seat_meta(
        agent_id: int, seats: Mapping[str, Mapping[str, object]] | None
    ) -> dict[str, object]:
        record = (seats or {}).get(str(agent_id)) or {}
        return {
            key: record[key]
            for key in ("palette", "hueShift", "seatId")
            if record.get(key) is not None
        }

    def existing_agents_message(
        self, seats: Mapping[str, Mapping[str, object]]
    ) -> ExistingAgentsMessage:
        agent_ids: list[int] = []
        folder_names: dict[str, str] = {}
        agent_meta: dict[str, dict[str, object]] = {}
        external_agents: dict[str, bool] = {}
        headless_agents: dict[str, bool] = {}
        for user_id, (folder, _) in self._representative_agents().items():
            agent_id = self.agent_id(user_id)
            agent_ids.append(agent_id)
            folder_names[str(agent_id)] = folder
            agent_meta[str(agent_id)] = self.seat_meta(agent_id, seats)
            # Every agent this service tracks is Discord-derived, hence
            # external, unconditionally.
            external_agents[str(agent_id)] = True
            headless_agents[str(agent_id)] = self._agent_is_bot.get(user_id, False)
        for agent_key, (folder, _) in self._genuine_agents.items():
            agent_id = self.genuine_agent_id(agent_key)
            agent_ids.append(agent_id)
            folder_names[str(agent_id)] = folder
            agent_meta[str(agent_id)] = self.seat_meta(agent_id, seats)
            # Ships with the same treatment a Discord bot gets for now --
            # see docs/office-agent-identity-design.md's open question on
            # whether a genuine agent deserves its own distinct isExternal/
            # isHeadless value.
            external_agents[str(agent_id)] = True
            headless_agents[str(agent_id)] = True
        return {
            "type": "existingAgents",
            "agents": agent_ids,
            "agentMeta": agent_meta,
            "folderNames": folder_names,
            "externalAgents": external_agents,
            "headlessAgents": headless_agents,
        }

    async def send_existing_agents(self) -> None:
        await self._send(self.existing_agents_message(await self._repository.seats()))

    async def assign_palette(self, agent_id: int) -> tuple[int, int]:
        live = {str(self.agent_id(user_id)) for user_id in self.tracked_user_ids()}

        def assign(seats: SeatRecords) -> tuple[int, int]:
            record = seats.get(str(agent_id))
            if record and record.get("palette") is not None:
                return int(record["palette"]), int(record.get("hueShift") or 0)

            counts = [0] * self._palette_count
            for key, value in seats.items():
                if key not in live or value.get("palette") is None:
                    continue
                index = int(value["palette"])
                if 0 <= index < self._palette_count:
                    counts[index] += 1

            fewest = min(counts)
            palette = self._choose([index for index, count in enumerate(counts) if count == fewest])
            hue_shift = 0 if fewest == 0 else self._hue_shift(45, 315)
            seats[str(agent_id)] = {
                **(record or {}),
                "palette": palette,
                "hueShift": hue_shift,
            }
            return palette, hue_shift

        return await self._repository.mutate_seats(assign)

    async def reconcile(
        self,
        snapshot: AgentSnapshot,
        *,
        include_bots: bool,
        rich_presence_enabled: bool,
    ) -> None:
        key = (snapshot.key.guild_id, snapshot.key.user_id)
        folder = snapshot.status.value if snapshot.status is not None else None
        if folder is None or (snapshot.is_bot and not include_bots):
            if key in self._agents:
                await self.close(snapshot.key)
            return

        cached = self._agents.get(key)
        if cached is None:
            await self.spawn(snapshot, rich_presence_enabled=rich_presence_enabled)
            return

        cached_folder, cached_name = cached
        agent_id = self.agent_id(snapshot.key.user_id)
        if folder != cached_folder:
            self._agents[key] = (folder, snapshot.display_name)
            self._agent_is_bot[snapshot.key.user_id] = snapshot.is_bot
            palette, hue_shift = await self.assign_palette(agent_id)
            await self._send(agent_closed(agent_id))
            await self._send(
                agent_created(
                    agent_id,
                    folder,
                    palette,
                    hue_shift,
                    is_external=True,
                    is_headless=snapshot.is_bot,
                )
            )
            if snapshot.display_name != cached_name:
                await self._send(agent_team_info(agent_id, snapshot.display_name))
            await self.send_existing_agents()
        elif snapshot.display_name != cached_name:
            self._agents[key] = (folder, snapshot.display_name)
            await self._send(agent_team_info(agent_id, snapshot.display_name))
        await self.presence.update(snapshot, agent_id, enabled=rich_presence_enabled)

    async def spawn(self, snapshot: AgentSnapshot, *, rich_presence_enabled: bool) -> None:
        guild_id = snapshot.key.guild_id
        user_id = snapshot.key.user_id
        if snapshot.status is None:
            return
        self.detect_collision(user_id)
        agent_id = self.agent_id(user_id)
        already_active = self.is_user_active_in_other_guild(guild_id, user_id)
        self._agents[(guild_id, user_id)] = (snapshot.status.value, snapshot.display_name)
        self._agent_is_bot[user_id] = snapshot.is_bot
        if not already_active:
            palette, hue_shift = await self.assign_palette(agent_id)
            await self._send(
                agent_created(
                    agent_id,
                    snapshot.status.value,
                    palette,
                    hue_shift,
                    is_external=True,
                    is_headless=snapshot.is_bot,
                )
            )
            await self._send(agent_team_info(agent_id, snapshot.display_name))
            await self._send(agent_status(agent_id, self.presence.agent_status(snapshot)))
        await self.send_existing_agents()
        await self.presence.start(snapshot, agent_id, enabled=rich_presence_enabled)

    async def close(self, key: AgentKey) -> None:
        raw_key = (key.guild_id, key.user_id)
        if raw_key not in self._agents:
            return
        agent_id = self.agent_id(key.user_id)
        del self._agents[raw_key]
        self.presence.forget(key)
        if not self.is_user_active_in_other_guild(key.guild_id, key.user_id):
            self._agent_is_bot.pop(key.user_id, None)
            await self._send(agent_closed(agent_id))
        await self.send_existing_agents()

    async def reconcile_genuine_agent(
        self, identity: GenuineAgentKey, display_name: str, status: str
    ) -> None:
        """Mirrors reconcile() for a genuine agent -- no cross-guild merge
        logic applies (a genuine agent has exactly one entry), and no
        rich-presence activities (not modeled for genuine agents yet, see
        docs/office-agent-identity-design.md). status="offline" closes it,
        the same as a Discord member going offline or leaving."""

        cached = self._genuine_agents.get(identity.agent_key)
        if status == "offline":
            if cached is not None:
                await self.close_genuine_agent(identity)
            return

        agent_id = self.genuine_agent_id(identity.agent_key)
        if cached is None:
            self._genuine_agents[identity.agent_key] = (status, display_name)
            palette, hue_shift = await self.assign_palette(agent_id)
            await self._send(
                agent_created(
                    agent_id, status, palette, hue_shift, is_external=True, is_headless=True
                )
            )
            await self._send(agent_team_info(agent_id, display_name))
            await self.send_existing_agents()
            return

        cached_status, cached_name = cached
        if status != cached_status:
            self._genuine_agents[identity.agent_key] = (status, display_name)
            palette, hue_shift = await self.assign_palette(agent_id)
            await self._send(agent_closed(agent_id))
            await self._send(
                agent_created(
                    agent_id, status, palette, hue_shift, is_external=True, is_headless=True
                )
            )
            if display_name != cached_name:
                await self._send(agent_team_info(agent_id, display_name))
            await self.send_existing_agents()
        elif display_name != cached_name:
            self._genuine_agents[identity.agent_key] = (status, display_name)
            await self._send(agent_team_info(agent_id, display_name))

    async def close_genuine_agent(self, identity: GenuineAgentKey) -> None:
        if identity.agent_key not in self._genuine_agents:
            return
        agent_id = self.genuine_agent_id(identity.agent_key)
        del self._genuine_agents[identity.agent_key]
        await self._send(agent_closed(agent_id))
        await self.send_existing_agents()

    async def despawn_guild(self, guild_id: int) -> None:
        keys = [AgentKey(gid, uid) for gid, uid in list(self._agents) if gid == guild_id]
        for key in keys:
            await self.close(key)

    async def sync_guild(
        self,
        guild_id: int,
        snapshots: Sequence[AgentSnapshot],
        *,
        include_bots: bool,
        rich_presence_enabled: bool,
    ) -> str:
        current_user_ids = {snapshot.key.user_id for snapshot in snapshots}
        stale = [
            AgentKey(gid, uid)
            for gid, uid in list(self._agents)
            if gid == guild_id and uid not in current_user_ids
        ]
        for key in stale:
            await self.close(key)

        errors = 0
        for snapshot in snapshots:
            try:
                await self.reconcile(
                    snapshot,
                    include_bots=include_bots,
                    rich_presence_enabled=rich_presence_enabled,
                )
            except Exception as exc:
                self._log.error(
                    "pixelagents: reconcile error for %s: %s", snapshot.key.user_id, exc
                )
                errors += 1
        return f"Sync complete. Errors: {errors}." if errors else "Sync complete."

    async def clear_presence(self) -> None:
        agent_ids = tuple(self.agent_id(user_id) for user_id in self.tracked_user_ids())
        await self.presence.clear_all(agent_ids)

    async def send_message_activity(self, snapshot: MessageSnapshot) -> None:
        agent_id = self.agent_id(snapshot.key.user_id)
        await self._send(self.presence.message_start(snapshot, agent_id))
        # Forces the full label panel open for this agent (bypassing the
        # hover/alwaysShowLabels gate pixel-agents' ToolOverlay otherwise
        # applies), so the message text above is visible immediately without
        # requiring the viewer to hover or enable "Always Show Labels".
        await self._send(agent_selected(agent_id))

    async def clear_message_activity(self, key: AgentKey) -> None:
        agent_id = self.agent_id(key.user_id)
        await self._send(agent_tools_clear(agent_id))
        label = self.presence.cached_label(key)
        if label:
            await self.presence.send_tool(agent_id, label)

    async def send_genuine_agent_activity(self, identity: GenuineAgentKey, content: str) -> None:
        """A genuine agent's AgentReplied (tool use/thinking) -- the same
        wire treatment send_message_activity gives a real Discord message,
        minus MessageSnapshot's Discord-shaped message_id (a genuine agent
        has no real message to key off; floorplan's synthetic-id comment
        for the Discord path already notes nothing downstream depends on
        its real value either)."""

        agent_id = self.genuine_agent_id(identity.agent_key)
        truncated = content if len(content) <= 40 else content[:40] + "…"
        await self._send(
            agent_tool_start(agent_id, f"activity-{identity.agent_key}", "Message", truncated)
        )
        await self._send(agent_selected(agent_id))

    async def clear_genuine_agent_activity(self, identity: GenuineAgentKey) -> None:
        await self._send(agent_tools_clear(self.genuine_agent_id(identity.agent_key)))

    async def highlight_agent(self, identity: OfficeIdentity) -> None:
        await self._send(agent_selected(self._agent_id_for(identity)))

    async def unhighlight_agent(self, identity: OfficeIdentity) -> None:
        await self._send(agent_deselected(self._agent_id_for(identity)))

    async def start_tool_activity(
        self, identity: OfficeIdentity, tool_id: str, status: str, tool_name: str | None = None
    ) -> None:
        await self._send(
            agent_tool_start(self._agent_id_for(identity), tool_id, tool_name or "", status)
        )

    async def set_status(
        self,
        identity: OfficeIdentity,
        status: Literal["active", "waiting"],
        awaiting_input: bool | None = None,
    ) -> None:
        await self._send(
            agent_status(self._agent_id_for(identity), status, awaiting_input=awaiting_input)
        )

    def bootstrap_messages(
        self,
        *,
        assets: Mapping[str, object],
        seats: Mapping[str, Mapping[str, object]],
        layout: object,
    ) -> tuple[ServerMessage, ...]:
        """Compose the ordered bootstrap contract consumed by the bundled UI."""

        messages: list[ServerMessage] = [
            {
                "type": "providerCapabilities",
                "readingTools": list(READING_TOOLS),
                "subagentToolNames": list(SUBAGENT_TOOL_NAMES),
            }
        ]
        asset_messages = (
            ("characters", "characterSpritesLoaded", "characters"),
            ("floors", "floorTilesLoaded", "sprites"),
            ("walls", "wallTilesLoaded", "sets"),
            ("carpets", "carpetTilesLoaded", "sets"),
        )
        for asset_key, message_type, payload_key in asset_messages:
            if asset_key in assets:
                messages.append({"type": message_type, payload_key: assets[asset_key]})
        if "catalog" in assets and "furniture" in assets:
            messages.append(
                {
                    "type": "furnitureAssetsLoaded",
                    "catalog": assets["catalog"],
                    "sprites": assets["furniture"],
                }
            )
        messages.extend(
            (
                {
                    "type": "settingsLoaded",
                    "soundEnabled": False,
                    "lastSeenVersion": "",
                    "extensionVersion": "",
                    "watchAllSessions": False,
                    "alwaysShowLabels": False,
                    # The webview only ever renders the isHeadless ghost cue
                    # (translucent sprite) when this is true -- isHeadless
                    # alone (agent_created/existingAgents, set from
                    # snapshot.is_bot) is inert without it. Unlike VS Code,
                    # where it's a genuine user preference toggle, every
                    # headless-worthy agent this office produces is a real
                    # Discord bot, so there's no case where it should be off.
                    "ghostHeadlessAgents": True,
                    "hooksEnabled": False,
                    "hooksInfoShown": True,
                    "externalAssetDirectories": [],
                    "showAreas": False,
                },
                {"type": "areaMappingsLoaded", "mappings": {}},
                self.existing_agents_message(seats),
            )
        )
        messages.append({"type": "layoutLoaded", "layout": layout})
        # agentTeamInfo (and the presence replay below) must be sent AFTER
        # layoutLoaded: the webview only creates character objects for
        # existingAgents once it processes layoutLoaded (buffering restored
        # agents until then, see pixel-agents' existingAgents.ts). Sent any
        # earlier, setTeamInfo silently no-ops on the not-yet-created
        # character and the display name is dropped for every agent that was
        # already online before this client connected.
        for user_id, (_, name) in self._representative_agents().items():
            messages.append(agent_team_info(self.agent_id(user_id), name))
        for agent_key, (_, name) in self._genuine_agents.items():
            messages.append(agent_team_info(self.genuine_agent_id(agent_key), name))
        for key, label in self.presence.replay(set(self._agents)):
            agent_id = self.agent_id(key.user_id)
            messages.append(
                {
                    "type": "agentToolStart",
                    "id": agent_id,
                    "toolId": f"rp-{agent_id}",
                    "toolName": "Activity",
                    "status": label,
                }
            )
        return tuple(messages)
