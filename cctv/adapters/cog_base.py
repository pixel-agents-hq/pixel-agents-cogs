"""CCTV dependency composition, startup ordering, and live event projection:
one Discord office per enabled guild, plus the editor office."""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import discord
from redbot.core import commands
from redbot.core.bot import Red

from corridor.domain import (
    AgentHighlighted,
    AgentPresenceChanged,
    AgentRef,
    AgentReplied,
    AgentStatusChanged,
    AgentToolStarted,
    AgentUnhighlighted,
    OfficeRef,
    OfficeStateChanged,
    OfficeStateKind,
    RegisteredAgent,
)
from pixelagents.domain import (
    DEFAULT_VISIBILITY,
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    GenuineAgentKey,
    MessageSnapshot,
    OfficeIdentity,
    OfficeVisibility,
    PresenceStatus,
)

from ..application import CctvPipeline, GuildOffices, TaskSupervisor
from ..domain import GuildSettings
from ..infrastructure import (
    CctvServer,
    RedSettingsRepository,
    TicketStore,
    WebviewAssets,
    degraded_asset_notification,
    member_snapshot,
)

log = logging.getLogger("red.d_cogs.cctv")
_synthetic_message_ids = itertools.count(1)


def _office_identity(agent: AgentRef) -> OfficeIdentity | None:
    if agent.guild_id is not None and agent.discord_user_id is not None:
        return AgentKey(agent.guild_id, agent.discord_user_id)
    if agent.agent_key is not None:
        return GenuineAgentKey(agent.agent_key)
    return None


def _event_snapshot(event: AgentPresenceChanged, key: AgentKey) -> AgentSnapshot:
    status = None if event.status == "offline" else PresenceStatus(event.status)
    return AgentSnapshot(
        key=key,
        display_name=event.display_name,
        status=status,
        is_bot=event.agent.is_bot,
        activities=tuple(
            ActivitySnapshot(
                kind=ActivityKind(activity.kind),
                name=activity.name,
                title=activity.title,
                artist=activity.artist,
                details=activity.details,
                state=activity.state,
            )
            for activity in event.activities
        ),
    )


def _visibility(settings: GuildSettings) -> OfficeVisibility:
    return OfficeVisibility(
        show_offline=settings.show_offline,
        ghost_offline=settings.ghost_offline,
        ghost_bots=settings.ghost_bots,
    )


def roster_warnings(guild: discord.Guild, intents: discord.Intents) -> tuple[str, ...]:
    """Why this guild's roster can't be what its settings ask for: without
    the presences intent every member reads as offline, and without the
    members intent the member cache holds only members seen in an event,
    so `showoffline` can show only those."""

    warnings = []
    if not intents.presences:
        warnings.append(
            "The presences intent is disabled: Discord reports every member as offline."
        )
    if not intents.members:
        warnings.append(
            "The members intent is disabled: only members seen since startup are "
            "cached, so offline members who haven't been seen can't be shown."
        )
    elif not guild.chunked:
        warnings.append("The member list isn't fully loaded yet (guild not chunked).")
    return tuple(warnings)


class CctvBase:
    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._settings = RedSettingsRepository.create(self)
        self.config = self._settings.config
        self._corridor: Any = None
        self._pixelagents: Any = None
        self._assets = WebviewAssets(logger=log)
        self._tickets = TicketStore()
        self._tasks = TaskSupervisor(logger=log)
        self._offices = GuildOffices()
        self._editor: CctvPipeline | None = None
        # Every registered genuine agent's last presence (display name,
        # status), so an office opened later -- a guild enabled after
        # startup -- seats the agents already registered.
        self._genuine_agents: dict[str, tuple[str, str]] = {}
        self._server: CctvServer | None = None
        self._closing = False
        self._initial_sync_task: asyncio.Task[object] | None = None
        # Which built commit's degraded-assets state has already been
        # DM'd to owners, so a re-sync against the same unchanged build
        # (e.g. every `_ensure_page` call) doesn't re-notify on each hit.
        self._notified_degraded_commit: str | None = None

    @property
    def editor_pipeline(self) -> CctvPipeline:
        if self._editor is None:
            raise RuntimeError("cctv: the editor office is not open")
        return self._editor

    def office(self, guild_id: int) -> CctvPipeline | None:
        """The guild's open office, or None while CCTV is disabled there."""

        return self._offices.get(guild_id)

    async def cog_load(self) -> None:
        from corridor.dependency_loader import ensure_loaded

        self._closing = False
        self._tasks.open()
        try:
            self._corridor = await ensure_loaded(self.bot, "corridor", "Corridor")
            self._corridor.register_dependent("cctv")
            self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
            self._sync_assets()
            await self._notify_owners_if_assets_degraded()
            self._editor = self._create_pipeline(OfficeRef.editor())

            # Settings are loaded before subscriptions and the cache scan.
            global_settings = await self._settings.global_settings()
            guild_settings = {
                guild.id: await self._settings.guild_settings(guild) for guild in self.bot.guilds
            }

            # Subscribed before any guild office reads its state, so a write
            # landing in between arrives as an event (see OfficeStateService.
            # watch); offices open (and read) right below.
            await self._pixelagents.watch_office_state(
                OfficeStateKind.DISCORD, self._on_office_state_changed, owner="Cctv"
            )
            await self._watch_editor_state()

            registered = self._corridor.watch_agent_events(
                (
                    (AgentPresenceChanged, self._on_agent_presence_changed),
                    (AgentReplied, self._on_agent_replied),
                    (AgentHighlighted, self._on_agent_highlighted),
                    (AgentUnhighlighted, self._on_agent_unhighlighted),
                    (AgentToolStarted, self._on_agent_tool_started),
                    (AgentStatusChanged, self._on_agent_status_changed),
                ),
                owner="Cctv",
            )

            # No await between the atomic agent watch and this complete cache scan.
            bot_user_id = self.bot.user.id if self.bot.user is not None else None
            discord_seed = tuple(
                (
                    guild.id,
                    tuple(
                        member_snapshot(member, bot_user_id=bot_user_id) for member in guild.members
                    ),
                )
                for guild in self.bot.guilds
                if guild_settings[guild.id].enabled
            )
            editor_bot_seed = tuple(
                member_snapshot(member, bot_user_id=bot_user_id)
                for guild in self.bot.guilds
                for member in guild.members
                if bot_user_id is not None and member.id == bot_user_id
            )

            self._remember_registered_agents(registered)
            await self._seed_genuine_agents(self.editor_pipeline)
            for guild_id, snapshots in discord_seed:
                office = await self._open_office(guild_id)
                settings = guild_settings[guild_id]
                for snapshot in snapshots:
                    await office.reconcile_discord(
                        snapshot,
                        include_bots=settings.include_bots,
                        rich_presence_enabled=settings.broadcast_rich_presence,
                        visibility=_visibility(settings),
                    )
            for snapshot in editor_bot_seed:
                await self.editor_pipeline.reconcile_discord(
                    snapshot, include_bots=True, rich_presence_enabled=False
                )

            self._server = CctvServer(
                self.office,
                lambda: tuple(self._offices),
                self.editor_pipeline,
                self._tickets,
                self._health_snapshot,
                logger=log,
            )
            await self._server.start(global_settings.listener_host, global_settings.listener_port)
            self._initial_sync_task = self._tasks.create(
                self._sync_after_ready(), name="cctv-initial-sync"
            )
            await self._notify_owners_if_degraded()
        except Exception:
            await self.cog_unload()
            raise

    def _create_pipeline(self, ref: OfficeRef) -> CctvPipeline:
        if ref.guild_id is None:
            return CctvPipeline(
                "editor",
                ref,
                self._pixelagents,
                self._pixelagents.office_seat_repository(ref),
                self._assets.assets,
                self._can_edit_editor,
                open_editor=True,
                logger=log,
            )
        guild_id = ref.guild_id

        async def authorize(user_id: int) -> bool:
            return await self._can_edit_guild(guild_id, user_id)

        return CctvPipeline(
            f"office/{guild_id}",
            ref,
            self._pixelagents,
            self._pixelagents.office_seat_repository(ref),
            self._assets.assets,
            authorize,
            open_editor=False,
            logger=log,
        )

    async def _open_office(self, guild_id: int) -> CctvPipeline:
        """Open the guild's office (idempotent): its own pipeline, seeded
        from its own aggregate and seated with the registered agents."""

        existing = self._offices.get(guild_id)
        if existing is not None:
            return existing
        office = self._create_pipeline(OfficeRef.discord(guild_id))
        self._offices.add(guild_id, office)
        try:
            await office.seed_state(await self._pixelagents.office_state(office.ref))
        except Exception as exc:
            office.error = str(exc)
            log.error("cctv/%s: office state unavailable: %s", office.page, exc)
        await self._seed_genuine_agents(office)
        return office

    async def _close_office(self, guild_id: int) -> None:
        """Close the guild's office: its sockets are disconnected and its
        roster is dropped (the persisted layout and seats stay)."""

        office = self._offices.pop(guild_id)
        if office is not None:
            await office.clients.close_all()

    async def _on_office_state_changed(self, event: OfficeStateChanged) -> None:
        guild_id = event.state.guild_id
        office = self._offices.get(guild_id) if guild_id is not None else None
        if office is not None:
            await office.state_changed(event)

    async def _watch_editor_state(self) -> None:
        editor = self.editor_pipeline
        try:
            snapshot = await self._pixelagents.watch_office_state(
                OfficeStateKind.EDITOR, editor.state_changed, owner="Cctv"
            )
            await editor.seed_state(snapshot)
        except Exception as exc:
            editor.error = str(exc)
            log.error("cctv/editor: office state unavailable: %s", exc)

    def _sync_assets(self) -> None:
        try:
            self._assets.sync(self._pixelagents.webview_bundle_status())
        except Exception as exc:
            self._assets.ready = False
            self._assets.error = f"could not read Pixel Agents bundle: {exc}"
            log.error("cctv: %s", self._assets.error)

    async def _notify_owners_if_assets_degraded(self) -> None:
        """Best-effort owner DM the first time a given built commit leaves
        `WebviewAssets.degraded` non-empty -- a sprite family or the
        furniture catalog failed to decode, degrading the served webview
        without necessarily flipping `ready` (only missing `characterProfiles`
        do that). Without this, that degradation was previously
        visible only in a log line nobody watches."""

        degraded = self._assets.degraded
        if not degraded or self._assets.built_commit == self._notified_degraded_commit:
            return
        self._notified_degraded_commit = self._assets.built_commit
        if self._corridor is None:
            return
        try:
            message = await self._corridor.substitute_default_prefix(
                degraded_asset_notification(degraded)
            )
            await self.bot.send_to_owners(message)
        except Exception:  # best-effort notification only, must never raise
            log.exception("cctv: could not notify owners about degraded Pixel Agents assets")

    async def _sync_after_ready(self) -> None:
        wait: Callable[[], Awaitable[None]] = self.bot.wait_until_red_ready
        await wait()
        await self._sync_all_guilds()
        await self._sync_editor_bot()

    async def cog_unload(self) -> None:
        self._closing = True
        if self._corridor is not None:
            self._corridor.unsubscribe_owner("Cctv")
            self._corridor.unregister_dependent("cctv")
        await self._tasks.shutdown()
        self._initial_sync_task = None
        if self._server is not None:
            await self._server.stop()
        self._server = None
        self._offices.clear()
        self._editor = None

    def _pipelines(self) -> tuple[CctvPipeline, ...]:
        editor = (self._editor,) if self._editor is not None else ()
        return (*self._offices, *editor)

    async def refresh_pixelagents(self, pixelagents: Any) -> None:
        """Called by pixelagents itself after it (re)loads, so an
        independent pixelagents reload doesn't leave cctv holding a stale
        Cog reference forever. `self._pixelagents` alone isn't enough here:
        each `CctvPipeline` also captured its own copy of it at
        creation time, so every live pipeline needs its own update too.
        See pixelagents' `_refresh_dependents` docstring."""

        self._pixelagents = pixelagents
        for pipeline in self._pipelines():
            pipeline.set_pixelagents(pixelagents)

    def _remember_registered_agents(self, registered: tuple[RegisteredAgent, ...]) -> None:
        for agent in registered:
            display_name = cast(str, getattr(agent.card, "name", agent.agent_key))
            self._genuine_agents[agent.agent_key] = (display_name, "online")

    async def _seed_genuine_agents(self, pipeline: CctvPipeline) -> None:
        for agent_key, (display_name, status) in tuple(self._genuine_agents.items()):
            await pipeline.reconcile_genuine(GenuineAgentKey(agent_key), display_name, status)

    async def _refresh_character(self, identity: OfficeIdentity) -> None:
        for pipeline in self._pipelines():
            await pipeline.office.refresh_character(identity)

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self._settings.guild_enabled(guild):
                try:
                    await self._full_sync(guild)
                except Exception:
                    log.exception("cctv: full sync failed for guild %s", guild.id)

    async def _full_sync(self, guild: discord.Guild) -> str:
        settings = await self._settings.guild_settings(guild)
        office = await self._open_office(guild.id)
        intents = self.bot.intents
        if settings.show_offline and intents.members and not guild.chunked:
            # Offline members are only in the cache once the guild is
            # chunked; Red chunks at startup, but a guild can still be
            # partial (a large guild mid-ready, or chunking turned off).
            try:
                await guild.chunk()
            except Exception:
                log.exception("cctv: could not load the member list of guild %s", guild.id)
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        snapshots = tuple(
            member_snapshot(member, bot_user_id=bot_user_id) for member in guild.members
        )
        result = await office.office.sync_guild(
            guild.id,
            snapshots,
            include_bots=settings.include_bots,
            rich_presence_enabled=settings.broadcast_rich_presence,
            visibility=_visibility(settings),
        )
        warnings = roster_warnings(guild, intents) if settings.show_offline else ()
        return "\n".join((result, *(f"⚠️ {warning}" for warning in warnings)))

    async def _sync_editor_bot(self) -> None:
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        if bot_user_id is None:
            return
        for guild in self.bot.guilds:
            member = guild.get_member(bot_user_id)
            if member is not None:
                await self.editor_pipeline.reconcile_discord(
                    member_snapshot(member, bot_user_id=bot_user_id),
                    include_bots=True,
                    rich_presence_enabled=False,
                )

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        office = self._offices.get(guild.id)
        if office is not None:
            await office.office.despawn_guild(guild.id)

    async def _on_agent_presence_changed(self, event: AgentPresenceChanged) -> None:
        identity = _office_identity(event.agent)
        if identity is None:
            return
        if isinstance(identity, GenuineAgentKey):
            if event.status == "offline":
                self._genuine_agents.pop(identity.agent_key, None)
            else:
                self._genuine_agents[identity.agent_key] = (event.display_name, event.status)
            for pipeline in self._pipelines():
                await pipeline.reconcile_genuine(identity, event.display_name, event.status)
            return
        snapshot = _event_snapshot(event, identity)
        office = self._offices.get(identity.guild_id)
        if office is not None:
            settings = await self._settings.guild_settings(identity.guild_id)
            # A member who left is removed even where offline members show:
            # the default policy hides an offline snapshot.
            visibility = DEFAULT_VISIBILITY if event.left_guild else _visibility(settings)
            await office.reconcile_discord(
                snapshot,
                include_bots=settings.include_bots,
                rich_presence_enabled=settings.broadcast_rich_presence,
                visibility=visibility,
            )
        if self.bot.user is not None and identity.user_id == self.bot.user.id:
            await self.editor_pipeline.reconcile_discord(
                snapshot, include_bots=True, rich_presence_enabled=False
            )

    def _event_targets(
        self, identity: OfficeIdentity, context_guild_id: int | None
    ) -> tuple[CctvPipeline, ...]:
        """Where an activity event is shown. A Discord member's activity
        reaches their own guild's office only (and the editor, for the bot's
        own account); a genuine agent's reaches the office of the guild whose
        conversation produced it, and always the editor -- activity of
        unknown origin never reaches a guild's office."""

        targets: list[CctvPipeline] = []
        if isinstance(identity, GenuineAgentKey):
            office = self._offices.get(context_guild_id) if context_guild_id is not None else None
            if office is not None:
                targets.append(office)
            targets.append(self.editor_pipeline)
            return tuple(targets)
        office = self._offices.get(identity.guild_id)
        if office is not None and office.is_tracked(identity):
            targets.append(office)
        if (
            self.bot.user is not None
            and identity.user_id == self.bot.user.id
            and self.editor_pipeline.is_tracked(identity)
        ):
            targets.append(self.editor_pipeline)
        return tuple(targets)

    async def _shows_messages(self, pipeline: CctvPipeline) -> bool:
        guild_id = pipeline.ref.guild_id
        if guild_id is None:
            return True
        return (await self._settings.guild_settings(guild_id)).broadcast_messages

    async def _on_agent_replied(self, event: AgentReplied) -> None:
        identity = _office_identity(event.agent)
        if identity is None:
            return
        global_settings = await self._settings.global_settings()
        for pipeline in self._event_targets(identity, event.context_guild_id):
            if not await self._shows_messages(pipeline):
                continue
            delay = (
                global_settings.editor_clear_delay
                if pipeline.ref.guild_id is None
                else global_settings.discord_clear_delay
            )
            if isinstance(identity, GenuineAgentKey):
                await pipeline.office.send_genuine_agent_activity(identity, event.summary)
                self._tasks.create(
                    self._clear_genuine_after(pipeline, identity, delay),
                    name=f"cctv-{pipeline.page}-clear-{identity.agent_key}",
                )
            else:
                snapshot = MessageSnapshot(
                    identity,
                    next(_synthetic_message_ids),
                    event.summary,
                )
                await pipeline.office.send_message_activity(snapshot)
                self._tasks.create(
                    self._clear_discord_after(pipeline, identity, delay),
                    name=f"cctv-{pipeline.page}-clear-{identity.user_id}",
                )

    async def _clear_genuine_after(
        self, pipeline: CctvPipeline, identity: GenuineAgentKey, delay: float
    ) -> None:
        await asyncio.sleep(delay)
        if not self._closing:
            await pipeline.office.clear_genuine_agent_activity(identity)

    async def _clear_discord_after(
        self, pipeline: CctvPipeline, identity: AgentKey, delay: float
    ) -> None:
        await asyncio.sleep(delay)
        if not self._closing:
            await pipeline.office.clear_message_activity(identity)

    async def _on_agent_highlighted(self, event: AgentHighlighted) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity, event.context_guild_id):
                await pipeline.office.highlight_agent(identity)

    async def _on_agent_unhighlighted(self, event: AgentUnhighlighted) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity, event.context_guild_id):
                await pipeline.office.unhighlight_agent(identity)

    async def _on_agent_tool_started(self, event: AgentToolStarted) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity, event.context_guild_id):
                await pipeline.office.start_tool_activity(
                    identity, event.tool_id, event.status, event.tool_name
                )

    async def _on_agent_status_changed(self, event: AgentStatusChanged) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity, event.context_guild_id):
                await pipeline.office.set_status(identity, event.status, event.awaiting_input)

    async def _can_edit_editor(self, user_id: int) -> bool:
        return user_id >= 0

    async def _can_edit_guild(self, guild_id: int, user_id: int) -> bool:
        """Whether `user_id` may edit `guild_id`'s office: the bot owner, or
        a keyholder in that guild while CCTV is enabled there. Fails closed."""

        if user_id <= 0:
            return False
        owner = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner):
            return True
        guild = self.bot.get_guild(guild_id)
        if guild is None or not await self._settings.guild_enabled(guild_id):
            return False
        member = guild.get_member(user_id)
        if member is None:
            fetch_member = getattr(guild, "fetch_member", None)
            if fetch_member is not None:
                try:
                    member = await fetch_member(user_id)
                except Exception:
                    member = None
        return member is not None and bool(
            await self._corridor.capabilities_satisfy(member, "keyholder")
        )

    async def _ensure_editor_page(self) -> str | None:
        error = await self._ensure_assets()
        if error is not None:
            return error
        editor = self.editor_pipeline
        try:
            await editor.seed_state(await self._pixelagents.office_state(editor.ref))
        except Exception as exc:
            editor.error = str(exc)
            return f"editor office state is unavailable: {exc}"
        return None

    async def _ensure_office_page(self, office: CctvPipeline) -> str | None:
        error = await self._ensure_assets()
        if error is not None:
            return error
        try:
            await office.seed_state(await self._pixelagents.office_state(office.ref))
        except Exception as exc:
            office.error = str(exc)
            return f"this office's state is unavailable: {exc}"
        return None

    async def _ensure_assets(self) -> str | None:
        self._sync_assets()
        await self._notify_owners_if_assets_degraded()
        if not self._assets.ready:
            return self._assets.error or "Pixel Agents bundle is unavailable."
        return None

    def _health_snapshot(self) -> dict[str, object]:
        """Aggregate only: `/cctv/health` is reachable by anyone who can
        reach the listener, so it names no guild."""

        offices = tuple(self._offices)
        return {
            "status": "degraded" if self._degraded_reasons() else "ok",
            "listener": self._server.running if self._server is not None else False,
            "assets": self._assets.ready,
            "offices": {
                "open": len(offices),
                "clients": sum(office.clients.client_count for office in offices),
                "agents": sum(len(office.office.tracked_user_ids()) for office in offices),
                "errors": sum(1 for office in offices if office.error),
            },
            "editor": self._editor.health() if self._editor is not None else None,
        }

    def _degraded_reasons(self) -> tuple[str, ...]:
        reasons = []
        if not self._assets.ready:
            reasons.append(self._assets.error or "assets unavailable")
        if self._server is None or not self._server.running:
            reasons.append(
                self._server.last_error
                if self._server is not None and self._server.last_error
                else "listener is not running"
            )
        for pipeline in self._pipelines():
            if pipeline.error:
                reasons.append(f"{pipeline.page}: {pipeline.error}")
        return tuple(reasons)

    async def _notify_owners_if_degraded(self) -> None:
        reasons = self._degraded_reasons()
        if not reasons:
            return
        try:
            await self.bot.send_to_owners(
                "⚠️ CCTV loaded in degraded mode:\n- " + "\n- ".join(reasons)
            )
        except Exception:
            log.exception("cctv: could not notify owners about degraded startup")

    async def _reply(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError


__all__ = ["CctvBase", "_event_snapshot", "_office_identity", "roster_warnings"]
