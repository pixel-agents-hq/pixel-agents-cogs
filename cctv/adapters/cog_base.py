"""CCTV dependency composition, startup ordering, and live event projection."""

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
    OfficeStateKind,
    RegisteredAgent,
)
from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    GenuineAgentKey,
    MessageSnapshot,
    OfficeIdentity,
    PresenceStatus,
)

from ..application import CctvPipeline, TaskSupervisor
from ..infrastructure import (
    CctvServer,
    RedSettingsRepository,
    TicketStore,
    WebviewAssets,
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
        self._pipelines: dict[OfficeStateKind, CctvPipeline] = {}
        self._server: CctvServer | None = None
        self._closing = False
        self._initial_sync_task: asyncio.Task[object] | None = None

    @property
    def discord_pipeline(self) -> CctvPipeline:
        return self._pipelines[OfficeStateKind.DISCORD]

    @property
    def editor_pipeline(self) -> CctvPipeline:
        return self._pipelines[OfficeStateKind.EDITOR]

    def _pipeline(self, kind: OfficeStateKind) -> CctvPipeline:
        return self._pipelines[kind]

    async def cog_load(self) -> None:
        from corridor.dependency_loader import ensure_loaded

        self._closing = False
        self._tasks.open()
        try:
            self._corridor = await ensure_loaded(self.bot, "corridor", "Corridor")
            self._corridor.register_dependent("cctv")
            self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
            self._sync_assets()
            self._create_pipelines()

            # Settings are loaded before subscriptions and the cache scan.
            global_settings = await self._settings.global_settings()
            guild_settings = {
                guild.id: await self._settings.guild_settings(guild) for guild in self.bot.guilds
            }

            await self._watch_state(OfficeStateKind.DISCORD)
            await self._watch_state(OfficeStateKind.EDITOR)

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
                    member_snapshot(member, bot_user_id=bot_user_id),
                    guild_settings[guild.id],
                )
                for guild in self.bot.guilds
                if guild_settings[guild.id].enabled
                for member in guild.members
            )
            editor_bot_seed = tuple(
                member_snapshot(member, bot_user_id=bot_user_id)
                for guild in self.bot.guilds
                for member in guild.members
                if bot_user_id is not None and member.id == bot_user_id
            )

            await self._seed_registered_agents(registered)
            for snapshot, settings in discord_seed:
                await self.discord_pipeline.reconcile_discord(
                    snapshot,
                    include_bots=settings.include_bots,
                    rich_presence_enabled=global_settings.broadcast_rich_presence,
                )
            for snapshot in editor_bot_seed:
                await self.editor_pipeline.reconcile_discord(
                    snapshot, include_bots=True, rich_presence_enabled=False
                )

            self._server = CctvServer(
                self.discord_pipeline,
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

    def _create_pipelines(self) -> None:
        self._pipelines = {
            OfficeStateKind.DISCORD: CctvPipeline(
                "discord",
                OfficeStateKind.DISCORD,
                self._pixelagents,
                self._pixelagents.office_seat_repository(OfficeStateKind.DISCORD),
                self._assets.assets,
                self._can_edit_discord,
                open_editor=False,
                logger=log,
            ),
            OfficeStateKind.EDITOR: CctvPipeline(
                "editor",
                OfficeStateKind.EDITOR,
                self._pixelagents,
                self._pixelagents.office_seat_repository(OfficeStateKind.EDITOR),
                self._assets.assets,
                self._can_edit_editor,
                open_editor=True,
                logger=log,
            ),
        }

    async def _watch_state(self, kind: OfficeStateKind) -> None:
        pipeline = self._pipeline(kind)
        try:
            snapshot = await self._pixelagents.watch_office_state(
                kind, pipeline.state_changed, owner="Cctv"
            )
            await pipeline.seed_state(snapshot)
        except Exception as exc:
            pipeline.error = str(exc)
            log.error("cctv/%s: office state unavailable: %s", kind.value, exc)

    def _sync_assets(self) -> None:
        try:
            self._assets.sync(self._pixelagents.webview_bundle_status())
        except Exception as exc:
            self._assets.ready = False
            self._assets.error = f"could not read Pixel Agents bundle: {exc}"
            log.error("cctv: %s", self._assets.error)

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
        for pipeline in self._pipelines.values():
            await pipeline.close()
        await self._tasks.shutdown()
        self._initial_sync_task = None
        if self._server is not None:
            await self._server.stop()
        self._server = None
        self._pipelines.clear()

    async def _seed_registered_agents(self, registered: tuple[RegisteredAgent, ...]) -> None:
        for agent in registered:
            display_name = cast(str, getattr(agent.card, "name", agent.agent_key))
            identity = GenuineAgentKey(agent.agent_key)
            await self.discord_pipeline.reconcile_genuine(identity, display_name, "online")
            await self.editor_pipeline.reconcile_genuine(identity, display_name, "online")

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self._settings.guild_enabled(guild):
                try:
                    await self._full_sync(guild)
                except Exception:
                    log.exception("cctv: full sync failed for guild %s", guild.id)

    async def _full_sync(self, guild: discord.Guild) -> str:
        settings = await self._settings.guild_settings(guild)
        global_settings = await self._settings.global_settings()
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        snapshots = tuple(
            member_snapshot(member, bot_user_id=bot_user_id) for member in guild.members
        )
        return await self.discord_pipeline.office.sync_guild(
            guild.id,
            snapshots,
            include_bots=settings.include_bots,
            rich_presence_enabled=global_settings.broadcast_rich_presence,
        )

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
        await self.discord_pipeline.office.despawn_guild(guild.id)

    async def _on_agent_presence_changed(self, event: AgentPresenceChanged) -> None:
        identity = _office_identity(event.agent)
        if identity is None:
            return
        if isinstance(identity, GenuineAgentKey):
            await self.discord_pipeline.reconcile_genuine(
                identity, event.display_name, event.status
            )
            await self.editor_pipeline.reconcile_genuine(identity, event.display_name, event.status)
            return
        snapshot = _event_snapshot(event, identity)
        settings = await self._settings.guild_settings(identity.guild_id)
        if settings.enabled:
            global_settings = await self._settings.global_settings()
            await self.discord_pipeline.reconcile_discord(
                snapshot,
                include_bots=settings.include_bots,
                rich_presence_enabled=global_settings.broadcast_rich_presence,
            )
        if self.bot.user is not None and identity.user_id == self.bot.user.id:
            await self.editor_pipeline.reconcile_discord(
                snapshot, include_bots=True, rich_presence_enabled=False
            )

    def _event_targets(self, identity: OfficeIdentity) -> tuple[CctvPipeline, ...]:
        if isinstance(identity, GenuineAgentKey):
            return (self.discord_pipeline, self.editor_pipeline)
        targets = []
        if self.discord_pipeline.is_tracked(identity):
            targets.append(self.discord_pipeline)
        if (
            self.bot.user is not None
            and identity.user_id == self.bot.user.id
            and self.editor_pipeline.is_tracked(identity)
        ):
            targets.append(self.editor_pipeline)
        return tuple(targets)

    async def _on_agent_replied(self, event: AgentReplied) -> None:
        identity = _office_identity(event.agent)
        if identity is None:
            return
        global_settings = await self._settings.global_settings()
        for pipeline in self._event_targets(identity):
            if pipeline.page == "discord" and not global_settings.broadcast_messages:
                continue
            if isinstance(identity, GenuineAgentKey):
                await pipeline.office.send_genuine_agent_activity(identity, event.summary)
                delay = (
                    global_settings.discord_clear_delay
                    if pipeline.page == "discord"
                    else global_settings.editor_clear_delay
                )
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
                delay = (
                    global_settings.discord_clear_delay
                    if pipeline.page == "discord"
                    else global_settings.editor_clear_delay
                )
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
            for pipeline in self._event_targets(identity):
                await pipeline.office.highlight_agent(identity)

    async def _on_agent_unhighlighted(self, event: AgentUnhighlighted) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity):
                await pipeline.office.unhighlight_agent(identity)

    async def _on_agent_tool_started(self, event: AgentToolStarted) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity):
                await pipeline.office.start_tool_activity(
                    identity, event.tool_id, event.status, event.tool_name
                )

    async def _on_agent_status_changed(self, event: AgentStatusChanged) -> None:
        identity = _office_identity(event.agent)
        if identity is not None:
            for pipeline in self._event_targets(identity):
                await pipeline.office.set_status(identity, event.status, event.awaiting_input)

    async def _can_edit_editor(self, user_id: int) -> bool:
        return user_id >= 0

    async def _can_edit_discord(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        owner = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner):
            return True
        for guild in self.bot.guilds:
            if not await self._settings.guild_enabled(guild):
                continue
            member = guild.get_member(user_id)
            if member is None:
                fetch_member = getattr(guild, "fetch_member", None)
                if fetch_member is not None:
                    try:
                        member = await fetch_member(user_id)
                    except Exception:
                        member = None
            if member is not None and await self._corridor.capabilities_satisfy(
                member, "keyholder"
            ):
                return True
        return False

    async def _ensure_page(self, kind: OfficeStateKind) -> str | None:
        self._sync_assets()
        if not self._assets.ready:
            return self._assets.error or "Pixel Agents bundle is unavailable."
        pipeline = self._pipeline(kind)
        try:
            await pipeline.seed_state(await self._pixelagents.office_state(kind))
        except Exception as exc:
            pipeline.error = str(exc)
            return f"{kind.value} office state is unavailable: {exc}"
        return None

    def _health_snapshot(self) -> dict[str, object]:
        return {
            "status": "degraded" if self._degraded_reasons() else "ok",
            "listener": self._server.running if self._server is not None else False,
            "assets": self._assets.ready,
            "discord": self.discord_pipeline.health(),
            "editor": self.editor_pipeline.health(),
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
        for pipeline in self._pipelines.values():
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


__all__ = ["CctvBase", "_event_snapshot", "_office_identity"]
