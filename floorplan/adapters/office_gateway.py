"""Office protocol, layout, and editor-authorization Cog adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import discord
from aiohttp import web

from pixelagents.application.office import DEFAULT_PALETTE_COUNT, OfficeService, merge_seat_patch
from pixelagents.contracts.outbound import ExistingAgentsMessage

from ..contracts.websocket import (
    ClientMessage,
    ImportLayoutMessage,
    RequestDiagnosticsMessage,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    WebviewReadyMessage,
)
from .cog_base import PixelAgentsBase, log


def _merge_existing_agents(
    base: ExistingAgentsMessage, extra: ExistingAgentsMessage
) -> ExistingAgentsMessage:
    """Fold a second existingAgents roster (genuine agents) into a guild's
    own -- the wire protocol carries exactly one such message per
    bootstrap, so sending two separately would have the second silently
    replace the first client-side rather than add to it."""

    merged: dict[str, Any] = dict(base)
    merged["agents"] = [*base["agents"], *extra["agents"]]
    for key in ("agentMeta", "folderNames", "externalAgents", "headlessAgents"):
        merged[key] = {**base[key], **extra[key]}
    return cast(ExistingAgentsMessage, merged)


class OfficeGatewayMixin(PixelAgentsBase):
    """Bridge the WebSocket protocol and auth checks to application services."""

    def _agent_id(self, user_id: int) -> int:
        return OfficeService.agent_id(user_id)

    def _detect_collision(self, guild_id: int, user_id: int) -> None:
        self._universes.get_or_create(guild_id).office.detect_collision(user_id)

    def _tracked_user_ids(self, guild_id: int) -> list[int]:
        return self._universes.get_or_create(guild_id).office.tracked_user_ids()

    def _existing_agents_message(
        self, guild_id: int, seats: Mapping[str, Mapping[str, object]]
    ) -> ExistingAgentsMessage:
        return self._universes.get_or_create(guild_id).office.existing_agents_message(seats)

    async def _send_existing_agents(self, guild_id: int) -> None:
        await self._universes.get_or_create(guild_id).office.send_existing_agents()

    def _seat_meta(
        self, agent_id: int, seats: Mapping[str, Mapping[str, object]] | None
    ) -> dict[str, object]:
        return OfficeService.seat_meta(agent_id, seats)

    async def _assign_palette(self, guild_id: int, agent_id: int) -> tuple[int, int]:
        return await self._universes.get_or_create(guild_id).office.assign_palette(agent_id)

    def _default_layout(self) -> dict[str, Any] | None:
        return self._webview_assets.default_layout()

    async def _current_layout(self, guild_id: int) -> dict[str, Any] | None:
        return await self._settings_repository.guild_layout(guild_id) or self._default_layout()

    @staticmethod
    def _validate_layout(layout: object) -> bool:
        if not isinstance(layout, dict) or layout.get("version") != 1:
            return False
        cols = layout.get("cols")
        rows = layout.get("rows")
        tiles = layout.get("tiles")
        furniture = layout.get("furniture")
        if not isinstance(cols, int) or cols <= 0:
            return False
        if not isinstance(rows, int) or rows <= 0:
            return False
        if not isinstance(tiles, list) or len(tiles) != cols * rows:
            return False
        if not isinstance(furniture, list):
            return False
        tile_colors = layout.get("tileColors")
        return tile_colors is None or (
            isinstance(tile_colors, list) and len(tile_colors) == cols * rows
        )

    async def _start_server(self) -> None:
        host = await self._settings_repository.ws_host()
        port = await self._settings_repository.ws_port()
        await self._websocket_server.start(host, port)

    def _health_snapshot(self) -> dict[str, object]:
        return {
            "status": "ok",
            "clients": self._client_hub.client_count,
            "agents": sum(len(universe.office.active_agents) for universe in self._universes.all()),
            "assets": sorted(self._assets),
        }

    async def _handle_health(self, request: web.Request) -> web.Response:
        return await self._websocket_server.handle_health(request)

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        return await self._websocket_server.handle_ws(request)

    async def _authorize_office_client(self, user_id: int, guild_id: int) -> bool:
        return await self._check_auth(user_id, guild_id)

    async def _handle_client_message(self, socket: web.WebSocketResponse, data: object) -> None:
        await self._websocket_server.handle_payload(socket, data)

    async def _handle_application_message(
        self, socket: web.WebSocketResponse, message: ClientMessage, guild_id: int
    ) -> None:
        if isinstance(message, WebviewReadyMessage):
            await self._send_bootstrap(socket, guild_id)
        elif isinstance(message, SaveLayoutMessage):
            layout = message.layout.to_raw()
            await self._settings_repository.set_guild_layout(guild_id, layout)
            await self._client_hub.broadcast_to_guild(
                guild_id, {"type": "layoutLoaded", "layout": layout}, exclude=socket
            )
        elif isinstance(message, SaveAgentSeatsMessage):
            incoming = {
                agent_id: patch.model_dump(by_alias=True, exclude_none=True)
                for agent_id, patch in message.seats.items()
            }
            await self._save_seats(guild_id, incoming)
        elif isinstance(message, RequestDiagnosticsMessage):
            await self._send_to(socket, {"type": "agentDiagnostics", "agents": []})
        elif isinstance(message, ImportLayoutMessage):
            return

    async def _save_seats(self, guild_id: int, incoming: Mapping[str, object]) -> None:
        characters = self._assets.get("characters")
        palette_count = max(
            len(characters) if isinstance(characters, (list, tuple)) else 0,
            DEFAULT_PALETTE_COUNT,
        )

        def merge(seats: dict[str, dict[str, Any]]) -> None:
            for agent_id, value in incoming.items():
                if not isinstance(value, dict):
                    continue
                merge_seat_patch(seats, str(agent_id), palette_count, value)

        await self._settings_repository.mutate_guild_seats(guild_id, merge)

    async def _send_bootstrap(self, socket: web.WebSocketResponse, guild_id: int) -> None:
        seats = await self._settings_repository.guild_seats(guild_id)
        messages = list(
            self._universes.get_or_create(guild_id).office.bootstrap_messages(
                assets=self._assets,
                seats=seats,
                layout=await self._current_layout(guild_id),
            )
        )
        await self._merge_genuine_agents_into_bootstrap(messages)
        for message in messages:
            await self._send_to(socket, message)

    async def _merge_genuine_agents_into_bootstrap(
        self, messages: list[Mapping[str, object]]
    ) -> None:
        """Genuine agents (e.g. architect) have no guild scope, so they
        never appear in a guild's own `bootstrap_messages()` call above --
        fold them in here so a newly-connecting browser sees one already
        visible to every other tab, not just live push updates."""

        genuine_seats = await self._settings_repository.genuine_agent_seats()
        genuine_messages = self._office_service.bootstrap_messages(
            assets={}, seats=genuine_seats, layout=None
        )
        genuine_existing = next(m for m in genuine_messages if m["type"] == "existingAgents")
        if not genuine_existing["agents"]:
            return
        for index, message in enumerate(messages):
            if message["type"] == "existingAgents":
                messages[index] = _merge_existing_agents(
                    cast(ExistingAgentsMessage, message),
                    cast(ExistingAgentsMessage, genuine_existing),
                )
                break
        layout_index = next(i for i, m in enumerate(messages) if m["type"] == "layoutLoaded")
        genuine_team_info = [m for m in genuine_messages if m["type"] == "agentTeamInfo"]
        messages[layout_index + 1 : layout_index + 1] = genuine_team_info

    async def _check_auth(self, user_id: int, guild_id: int) -> bool:
        return await self._can_edit_layout_user(user_id, guild_id)

    async def _get_auth_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        fetch_member = getattr(guild, "fetch_member", None)
        if fetch_member is None:
            return None
        try:
            fetched: discord.Member = await fetch_member(user_id)
        except Exception as exc:
            log.debug(
                "floorplan: failed to fetch member %d in guild %s for auth check: %s",
                user_id,
                getattr(guild, "id", "unknown"),
                exc,
            )
            return None
        return fetched

    async def _can_edit_layout_user(self, user_id: int, guild_id: int) -> bool:
        """Whether `user_id` may edit `guild_id`'s own office layout.

        Narrowed to the one guild being edited (unlike the pre-issue-#4
        "any enabled guild" check): each guild now owns its own layout, so
        editing guild A no longer requires -- or grants -- rights over
        guild B's.
        """

        if user_id == 0:
            return False
        owner_candidate = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner_candidate):
            return True
        guild = self.bot.get_guild(guild_id)
        if guild is None or not await self._settings_repository.guild_enabled(guild):
            return False
        member = await self._get_auth_member(guild, user_id)
        if member is None:
            return False
        return bool(await self._corridor.capabilities_satisfy(member, "keyholder"))

    async def _can_view_office(self, user_id: int | None, guild_id: int) -> bool:
        """Whether an (optionally anonymous) visitor may view `guild_id`'s
        office at all: the guild must be enabled, and a private guild
        additionally requires the visitor to actually be a member (or the
        bot owner)."""

        guild = self.bot.get_guild(guild_id)
        if guild is None or not await self._settings_repository.guild_enabled(guild):
            return False
        if not await self._settings_repository.guild_private(guild):
            return True
        if user_id is None or user_id == 0:
            return False
        owner_candidate = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner_candidate):
            return True
        return await self._get_auth_member(guild, user_id) is not None
