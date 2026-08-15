from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from pydantic import ValidationError
from redbot.core import commands
from redbot.core.bot import Red

from .application import OfficeService, PresenceService, SettingsService, TaskSupervisor
from .application.office import DEFAULT_PALETTE_COUNT, JS_MAX_SAFE, discord_id_to_agent_id
from .contracts.websocket import (
    ClientMessage,
    ImportLayoutMessage,
    RequestDiagnosticsMessage,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    WebviewReadyMessage,
)
from .domain import AgentKey, AgentSnapshot, PresenceStatus
from .infrastructure.client_hub import ClientHub, ClientState
from .infrastructure.discord import member_snapshot, message_snapshot
from .infrastructure.settings import RedSettingsRepository, normalize_http_url
from .infrastructure.tickets import TicketStore
from .infrastructure.websocket import WebSocketServer
from .infrastructure.webview import WebviewAssetProvider
from .models import LayoutDetail, LayoutListResponse

log = logging.getLogger("red.d_cogs.pixelagents")

_VISIBLE_STATUSES = {"online", "idle", "dnd"}
_PIXEL_INDEX_HEALTH_TIMEOUT = 5.0
_PIXEL_INDEX_REQUEST_TIMEOUT = 10.0
_LAYOUT_SEARCH_PAGE_SIZE = 5
_LAYOUT_SORT_CHOICES = ("newest", "furniture", "largest", "title")

# JavaScript Number.MAX_SAFE_INTEGER = 2^53 - 1 = 9007199254740991
_JS_MAX_SAFE = JS_MAX_SAFE

# Bundled character palettes (char_0.png .. char_5.png).
_PALETTE_COUNT = DEFAULT_PALETTE_COUNT

def dashboard_page(*args, **kwargs):
    def decorator(func: Callable):
        setattr(func, "__dashboard_decorator_params__", (args, kwargs))
        return func

    return decorator


def _discord_id_to_agent_id(user_id: int) -> int:
    """Map a Discord user ID to a stable negative JavaScript-safe integer.

    Discord snowflakes are up to 64 bits. We take user_id modulo JS_MAX_SAFE
    and negate. If the result is 0 (user_id is a multiple of JS_MAX_SAFE),
    we use -JS_MAX_SAFE to guarantee negativity.
    """
    return discord_id_to_agent_id(user_id)


class pixelagents(commands.Cog):
    """Serve the Pixel Agents office and mirror Discord guild presence into it."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._closing = False
        self._task_supervisor = TaskSupervisor(logger=log)
        self._settings_repository = RedSettingsRepository.create(self)
        # Keep the raw Config attribute public until all legacy adapters have
        # moved behind typed services; third-party integrations may inspect it.
        self.config = self._settings_repository.config
        self._settings_service = SettingsService(
            self._settings_repository,
            clear_rich_presence=self._clear_rich_presence_bubbles,
            reauthorize_editors=self._reauthorize_editors_after_settings_change,
            sync_guild=self._sync_guild_from_settings,
            despawn_guild=self._despawn_guild_from_settings,
        )
        self._presence_service = PresenceService(self._send)
        self._office_service = OfficeService(
            self._settings_repository,
            self._send,
            presence=self._presence_service,
            logger=log,
        )
        self._sync_task: Optional[asyncio.Task] = None
        self._webview_assets = WebviewAssetProvider(
            Path(__file__).with_name("webview_dist"), logger=log
        )
        # This alias remains during the service extraction so bootstrap and
        # presence code can consume the provider-owned immutable snapshot.
        self._assets = self._webview_assets.assets
        self._ticket_store = TicketStore()
        self._client_hub = ClientHub(logger=log)
        # `_clients` now contains identity-aware ClientState values. Keep the
        # alias temporarily for adapters/tests that inspect connected sockets.
        self._clients: Dict[web.WebSocketResponse, ClientState] = self._client_hub.clients
        self._websocket_server = WebSocketServer(
            clients=self._client_hub,
            tickets=self._ticket_store,
            authorize=self._authorize_office_client,
            handle_application_message=self._handle_application_message,
            health_snapshot=self._health_snapshot,
            logger=log,
        )

    @property
    def _agents(self) -> Dict[Tuple[int, int], Tuple[str, str]]:
        """Compatibility view of service-owned active agents."""

        return self._office_service.active_agents

    @_agents.setter
    def _agents(self, value: Dict[Tuple[int, int], Tuple[str, str]]) -> None:
        self._office_service.replace_active_agents(value)

    @property
    def _presence_cache(self) -> Dict[Tuple[int, int], str]:
        """Compatibility view of the presence service's transition cache."""

        return self._presence_service.cache

    @_presence_cache.setter
    def _presence_cache(self, value: Dict[Tuple[int, int], str]) -> None:
        self._presence_service.replace_cache(value)

    @property
    def _logged_collisions(self) -> set[int]:
        return self._office_service.logged_collisions

    async def _clear_rich_presence_bubbles(self) -> None:
        """Clear cached activity and every visible tool stack immediately."""

        await self._office_service.clear_presence()

    async def _reauthorize_editors_after_settings_change(self) -> None:
        """Immediately refresh every identified socket after auth settings change."""

        await self._client_hub.reauthorize(self._check_auth)

    async def _sync_guild_from_settings(self, guild_id: int) -> str:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return "Sync skipped: guild is no longer available."
        return await self._full_sync(guild)

    async def _despawn_guild_from_settings(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            await self._despawn_guild(guild)

    # ------------------------------------------------------------------
    # Dashboard webview hosting
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if not hasattr(dashboard_cog, "rpc"):
            return
        third_parties = getattr(dashboard_cog.rpc, "third_parties_handler", None)
        if third_parties is None:
            return
        third_parties.add_third_party(self, overwrite=True)

    def _webview_dist_root(self) -> Path:
        """Compatibility wrapper for the provider-owned asset root."""

        return self._webview_assets.root

    def _resolve_webview_asset(self, asset_path: str) -> Optional[Path]:
        """Compatibility wrapper for traversal-safe asset resolution."""

        return self._webview_assets.resolve(asset_path)

    def _content_type_for_asset(self, asset_path: str) -> str:
        """Compatibility wrapper for provider MIME detection."""

        return self._webview_assets.content_type(asset_path)

    def _mint_ticket(self, user_id: int) -> str:
        """Compatibility wrapper for editor-ticket minting."""

        return self._ticket_store.mint(user_id)

    def _resolve_ticket(self, ticket: str) -> Optional[int]:
        """Compatibility wrapper for reusable ticket resolution."""

        return self._ticket_store.resolve(ticket)

    # No `user_id` (or any other context-id-shaped name) in this signature —
    # that's what keeps this page public. `dashboard_page` infers context_ids
    # from parameters with no default, so a bare `user_id: int` here would
    # make the dashboard force a login before serving the page at all. Do NOT
    # add one back and do NOT pass `context_ids` explicitly either (that skips
    # the inference branch and files a same-named param under required_kwargs
    # instead, 404ing unless the caller appends `?user_id=`). Editor
    # authorization is handled out-of-band by `dashboard_session` below.
    @dashboard_page(name=None, description="Pixel Agents webview.", methods=("GET",))
    async def dashboard_webview(self, **kwargs) -> dict:
        del kwargs
        return self._webview_assets.dashboard_webview_response()

    # Unlike `dashboard_webview`, `user_id` here has no default, so the
    # dashboard *does* require login for this one page and hands us the
    # visitor's Discord ID. That's intentional: it's the only way a visitor
    # can mint an editor ticket, and it's fetched in the background by the
    # shim above rather than being a page a person navigates to.
    @dashboard_page(
        name="session",
        description="Pixel Agents editor session ticket.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_session(self, user_id: int, **kwargs) -> dict:
        body = json.dumps({"ticket": self._mint_ticket(user_id)}).encode("utf-8")
        return {
            "status": 0,
            "raw_response": {
                "status": 200,
                "content_type": "application/json",
                "body_base64": base64.b64encode(body).decode("ascii"),
                "headers": {"Cache-Control": "no-store"},
            },
        }

    @dashboard_page(
        name="static", description="Pixel Agents static asset.", methods=("GET", "HEAD")
    )
    async def dashboard_static(self, asset_path: str, **kwargs) -> dict:
        return self._webview_assets.dashboard_static_response(
            asset_path, head_only=kwargs.get("method") == "HEAD"
        )

    # ------------------------------------------------------------------
    # ID helpers
    # ------------------------------------------------------------------

    def _agent_id(self, user_id: int) -> int:
        return self._office_service.agent_id(user_id)

    def _detect_collision(self, user_id: int) -> None:
        self._office_service.detect_collision(user_id)

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _send_to(self, socket: web.WebSocketResponse, message: Mapping[str, object]) -> None:
        """Compatibility wrapper for targeted hub delivery."""

        if self._closing:
            return
        await self._client_hub.send_to(socket, message)

    async def _send(self, message: Mapping[str, object]) -> None:
        """Broadcast a ServerMessage to every connected office client."""

        if self._closing:
            return
        await self._client_hub.broadcast(message)

    def _tracked_user_ids(self) -> List[int]:
        """Distinct tracked users, ordered stably so agent lists don't churn."""

        return self._office_service.tracked_user_ids()

    def _existing_agents_message(self, seats: dict) -> dict:
        return self._office_service.existing_agents_message(seats)

    async def _send_existing_agents(self) -> None:
        await self._office_service.send_existing_agents()

    # ------------------------------------------------------------------
    # Seats and palettes
    # ------------------------------------------------------------------

    def _seat_meta(self, agent_id: int, seats: Optional[dict]) -> dict:
        return self._office_service.seat_meta(agent_id, seats)

    async def _assign_palette(self, agent_id: int) -> Tuple[int, int]:
        return await self._office_service.assign_palette(agent_id)

    # ------------------------------------------------------------------
    # Layout ownership
    # ------------------------------------------------------------------

    def _default_layout(self) -> Optional[dict]:
        """The layout bundled with the webview build, used until one is saved."""
        return self._webview_assets.default_layout()

    async def _current_layout(self) -> Optional[dict]:
        return await self._settings_repository.layout() or self._default_layout()

    def _validate_layout(self, layout: Any) -> bool:
        if not isinstance(layout, dict):
            return False
        if layout.get("version") != 1:
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
        if tile_colors is not None and (
            not isinstance(tile_colors, list) or len(tile_colors) != cols * rows
        ):
            return False
        return True

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _load_assets(self) -> None:
        """Compatibility wrapper for one-time decoded asset loading."""

        self._webview_assets.load_assets()

    async def cog_load(self) -> None:
        self._closing = False
        self._task_supervisor.open()
        await asyncio.to_thread(self._load_assets)
        await self._start_server()
        # The producer client used to sync on connect. Nothing dials out now, so
        # seed the agent set once the gateway cache is populated instead.
        self._sync_task = self._task_supervisor.create(
            self._initial_sync(), name="pixelagents-initial-sync"
        )

    async def _initial_sync(self) -> None:
        await self.bot.wait_until_red_ready()
        await self._sync_all_guilds()

    async def cog_unload(self) -> None:
        self._closing = True
        await self._task_supervisor.shutdown()
        self._sync_task = None
        await self._websocket_server.stop()

    async def _start_server(self) -> None:
        """Compatibility wrapper around the lifecycle-managed aiohttp server."""

        host = await self._settings_repository.ws_host()
        port = await self._settings_repository.ws_port()
        await self._websocket_server.start(host, port)

    def _health_snapshot(self) -> dict[str, object]:
        return {
            "status": "ok",
            "clients": self._client_hub.client_count,
            "agents": len(self._tracked_user_ids()),
            "assets": sorted(self._assets),
        }

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Compatibility wrapper for the server-owned health route."""

        return await self._websocket_server.handle_health(request)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Compatibility wrapper for the server-owned WebSocket route."""

        return await self._websocket_server.handle_ws(request)

    async def _authorize_office_client(self, user_id: int) -> bool:
        """Indirection keeps authorization patchable and out of the transport."""

        return await self._check_auth(user_id)

    async def _handle_client_message(self, socket: web.WebSocketResponse, data: object) -> None:
        """Temporary adapter from legacy decoded dictionaries to wire models."""

        await self._websocket_server.handle_payload(socket, data)

    async def _handle_application_message(
        self, socket: web.WebSocketResponse, message: ClientMessage
    ) -> None:
        """Handle validated non-authorization messages using existing services."""

        if isinstance(message, WebviewReadyMessage):
            await self._send_bootstrap(socket)
        elif isinstance(message, SaveLayoutMessage):
            layout = message.layout.to_raw()
            await self._settings_repository.set_layout(layout)
            # The saving client already applied it locally; mirror to other tabs.
            await self._client_hub.broadcast(
                {"type": "layoutLoaded", "layout": layout}, exclude=socket
            )
        elif isinstance(message, SaveAgentSeatsMessage):
            incoming = {
                agent_id: patch.model_dump(by_alias=True, exclude_none=True)
                for agent_id, patch in message.seats.items()
            }
            await self._save_seats(incoming)
        elif isinstance(message, RequestDiagnosticsMessage):
            await self._send_to(socket, {"type": "agentDiagnostics", "agents": []})
        elif isinstance(message, ImportLayoutMessage):
            # Protected for forward compatibility. The bundled UI imports a
            # local file, then sends saveLayout; there is no server-side action.
            return

    async def _save_seats(self, incoming: dict) -> None:
        characters = self._assets.get("characters")
        palette_count = max(
            len(characters) if isinstance(characters, (list, tuple)) else 0,
            _PALETTE_COUNT,
        )

        def merge(seats: dict) -> None:
            for agent_id, value in incoming.items():
                if not isinstance(value, dict):
                    continue
                record = dict(seats.get(str(agent_id)) or {})
                palette = value.get("palette")
                hue_shift = value.get("hueShift")
                seat_id = value.get("seatId")
                # Validate before storing: a hand-edited payload should not be able
                # to persist a palette index that renders as a missing sprite.
                if isinstance(palette, int) and 0 <= palette < palette_count:
                    record["palette"] = palette
                if isinstance(hue_shift, int) and 0 <= hue_shift <= 360:
                    record["hueShift"] = hue_shift
                if isinstance(seat_id, str):
                    record["seatId"] = seat_id
                seats[str(agent_id)] = record

        await self._settings_repository.mutate_seats(merge)

    async def _send_bootstrap(self, socket: web.WebSocketResponse) -> None:
        """Push the whole world to a freshly connected office client.

        Order matters and mirrors upstream's handleWebviewReady: capabilities
        first, then assets, then settings, and `layoutLoaded` LAST — the webview
        buffers `existingAgents` and only materializes characters when the
        layout arrives, so a layout-first bootstrap leaves an empty office.
        """
        seats = await self._settings_repository.seats()
        messages = self._office_service.bootstrap_messages(
            assets=self._assets,
            seats=seats,
            layout=await self._current_layout(),
        )
        for message in messages:
            await self._send_to(socket, message)

    # ------------------------------------------------------------------
    # Editor authorization
    # ------------------------------------------------------------------

    async def _check_auth(self, user_id: int) -> bool:
        return await self._can_edit_layout_user(user_id)

    async def _get_auth_member(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member is not None:
            return member

        fetch_member = getattr(guild, "fetch_member", None)
        if fetch_member is None:
            return None

        try:
            return await fetch_member(user_id)
        except Exception as exc:
            log.debug(
                "pixelagents: failed to fetch member %d in guild %s for auth check: %s",
                user_id,
                getattr(guild, "id", "unknown"),
                exc,
            )
            return None

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        if await self.bot.is_owner(discord.Object(id=user_id)):
            return True
        role_id = await self._settings_repository.editor_role_id()
        for guild in self.bot.guilds:
            if not await self._settings_repository.guild_enabled(guild):
                continue
            member = await self._get_auth_member(guild, user_id)
            if member is None:
                continue
            permissions = getattr(member, "guild_permissions", None)
            if getattr(permissions, "administrator", False) is True:
                return True
            if role_id is not None and any(r.id == role_id for r in getattr(member, "roles", [])):
                return True
        return False

    # ------------------------------------------------------------------
    # Presence sync
    # ------------------------------------------------------------------

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self._settings_repository.guild_enabled(guild):
                try:
                    await self._full_sync(guild)
                except Exception as exc:
                    log.error("pixelagents: sync error for guild %s: %s", guild.id, exc)

    async def _full_sync(self, guild: discord.Guild) -> str:
        guild_settings = await self._settings_repository.guild_settings(guild)
        rich_presence_enabled = await self._settings_repository.broadcast_rich_presence()
        snapshots = tuple(self._member_snapshot(member) for member in guild.members)
        return await self._office_service.sync_guild(
            guild.id,
            snapshots,
            include_bots=guild_settings.include_bots,
            rich_presence_enabled=rich_presence_enabled,
        )

    def _member_snapshot(self, member: discord.Member) -> AgentSnapshot:
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        return member_snapshot(member, bot_user_id=bot_user_id)

    def _pick_presence_activity(self, member: discord.Member) -> Optional[discord.Activity]:
        activities = [a for a in member.activities if a.type != discord.ActivityType.custom]
        for a in activities:
            if a.type == discord.ActivityType.listening:
                return a
        return activities[0] if activities else None

    def _build_presence_label(self, member: discord.Member) -> Optional[str]:
        return self._presence_service.label(self._member_snapshot(member))

    def _status_str(self, member: discord.Member) -> Optional[str]:
        status = self._member_snapshot(member).status
        return status.value if status is not None else None

    def _is_included(self, member: discord.Member, include_bots: bool) -> bool:
        return not (self._member_snapshot(member).is_bot and not include_bots)

    def _has_rich_presence(self, member: discord.Member) -> bool:
        return self._presence_service.agent_status(self._member_snapshot(member)) == "active"

    def _agent_status(self, member: discord.Member) -> str:
        return self._presence_service.agent_status(self._member_snapshot(member))

    async def _reconcile_member(self, member: discord.Member, include_bots: bool) -> None:
        await self._office_service.reconcile(
            self._member_snapshot(member),
            include_bots=include_bots,
            rich_presence_enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    def _is_user_active_in_other_guild(self, guild_id: int, user_id: int) -> bool:
        return self._office_service.is_user_active_in_other_guild(guild_id, user_id)

    async def _spawn_agent(
        self, guild_id: int, user_id: int, name: str, folder: str, member: discord.Member
    ) -> None:
        snapshot = self._member_snapshot(member)
        snapshot = AgentSnapshot(
            key=AgentKey(guild_id, user_id),
            display_name=name,
            status=PresenceStatus(folder),
            is_bot=snapshot.is_bot,
            activities=snapshot.activities,
        )
        await self._office_service.spawn(
            snapshot,
            rich_presence_enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    async def _close_agent(self, guild_id: int, user_id: int) -> None:
        await self._office_service.close(AgentKey(guild_id, user_id))

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        await self._office_service.despawn_guild(guild.id)

    async def _send_presence_tool(self, agent_id: int, label: str) -> None:
        await self._presence_service.send_tool(agent_id, label)

    async def _update_presence_tool(
        self, guild_id: int, user_id: int, member: discord.Member
    ) -> None:
        snapshot = self._member_snapshot(member)
        if snapshot.key != AgentKey(guild_id, user_id):
            snapshot = AgentSnapshot(
                key=AgentKey(guild_id, user_id),
                display_name=snapshot.display_name,
                status=snapshot.status,
                is_bot=snapshot.is_bot,
                activities=snapshot.activities,
            )
        await self._presence_service.update(
            snapshot,
            self._agent_id(user_id),
            enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    async def _clear_tool_after_delay(
        self, agent_id: int, delay: float, guild_id: int = 0, user_id: int = 0
    ) -> None:
        await asyncio.sleep(delay)
        if self._closing:
            return
        if guild_id and user_id:
            await self._office_service.clear_message_activity(AgentKey(guild_id, user_id))
            return
        await self._send({"type": "agentToolsClear", "id": agent_id})

    # ------------------------------------------------------------------
    # Reply helper
    # ------------------------------------------------------------------

    async def _reply(self, ctx: commands.Context, content=None, **kwargs) -> None:
        if ctx.interaction:
            kwargs["ephemeral"] = True
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, **kwargs)
            else:
                await ctx.interaction.followup.send(content, **kwargs)
        else:
            await ctx.send(content, **kwargs)

    async def _send_public(self, ctx: commands.Context, content=None, **kwargs) -> None:
        if ctx.interaction:
            kwargs["ephemeral"] = False
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, **kwargs)
            else:
                await ctx.interaction.followup.send(content, **kwargs)
        else:
            await ctx.send(content, **kwargs)

    # ------------------------------------------------------------------
    # Discord event listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(after.guild)
        if not guild_settings.enabled:
            return
        if before.display_name == after.display_name:
            return
        include_bots = guild_settings.include_bots
        try:
            await self._reconcile_member(after, include_bots)
        except Exception as exc:
            log.error("on_member_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(after.guild)
        if not guild_settings.enabled:
            return
        if before.status == after.status and before.activities == after.activities:
            return
        include_bots = guild_settings.include_bots
        try:
            await self._reconcile_member(after, include_bots)
        except Exception as exc:
            log.error("on_presence_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(member.guild)
        if not guild_settings.enabled:
            return
        if self._status_str(member) is None:
            return
        include_bots = guild_settings.include_bots
        try:
            await self._reconcile_member(member, include_bots)
        except Exception as exc:
            log.error("on_member_join error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not await self._settings_repository.guild_enabled(member.guild):
            return
        try:
            await self._close_agent(member.guild.id, member.id)
        except Exception as exc:
            log.error("on_member_remove error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self._closing:
            return
        snapshot = message_snapshot(message)
        if snapshot is None:
            return
        if not await self._settings_repository.guild_enabled(message.guild):
            return
        if not self._office_service.is_tracked(snapshot.key):
            return
        if not await self._settings_repository.broadcast_messages():
            return
        await self._office_service.send_message_activity(snapshot)
        delay = await self._settings_repository.message_tool_clear_delay()
        self._task_supervisor.create(
            self._clear_tool_after_delay(
                self._agent_id(snapshot.key.user_id),
                delay,
                snapshot.key.guild_id,
                snapshot.key.user_id,
            ),
            name=f"pixelagents-message-clear-{snapshot.message_id}",
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="pixelagents", invoke_without_command=True)
    @commands.guild_only()
    async def pixelagents_group(self, ctx: commands.Context) -> None:
        """Manage Pixelagents presence mirroring."""
        await ctx.send_help()

    @pixelagents_group.command(name="status")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show current Pixelagents configuration and connection status."""
        global_settings = await self._settings_service.global_settings()
        guild_settings = await self._settings_service.guild_settings(ctx.guild.id)
        tracked = sum(1 for (gid, _) in self._agents if gid == ctx.guild.id)
        serving = self._websocket_server.running
        editors = self._client_hub.editor_count

        def yn(value: bool) -> str:
            return "✅" if value else "🛑"

        embed = discord.Embed(title="Pixelagents Status", color=discord.Color.blurple())
        embed.add_field(
            name="Office Server",
            value=f"{global_settings.ws_host}:{global_settings.ws_port}/ws",
            inline=False,
        )
        embed.add_field(name="Serving", value=yn(serving), inline=True)
        embed.add_field(
            name="Office Clients",
            value=f"{self._client_hub.client_count} ({editors} editor)",
            inline=True,
        )
        embed.add_field(
            name="Assets",
            value="✅ loaded" if self._assets.get("characters") else "⚠️ missing",
            inline=True,
        )
        embed.add_field(
            name="Msg Tool Clear Delay",
            value=f"{global_settings.message_tool_clear_delay}s",
            inline=True,
        )
        embed.add_field(
            name="Editor Role ID",
            value=(
                str(global_settings.editor_role_id)
                if global_settings.editor_role_id
                else "⚠️ Not set"
            ),
            inline=True,
        )
        embed.add_field(name="Guild Enabled", value=yn(guild_settings.enabled), inline=True)
        embed.add_field(name="Include Bots", value=yn(guild_settings.include_bots), inline=True)
        embed.add_field(name="Tracked Agents", value=str(tracked), inline=True)
        embed.add_field(
            name="Broadcast Rich Presence",
            value=yn(global_settings.broadcast_rich_presence),
            inline=True,
        )
        embed.add_field(
            name="Broadcast Messages",
            value=yn(global_settings.broadcast_messages),
            inline=True,
        )
        embed.add_field(
            name="Pixel Index API",
            value=global_settings.pixel_index_api_url,
            inline=False,
        )
        embed.add_field(
            name="Pixel Index Web",
            value=global_settings.pixel_index_web_url,
            inline=False,
        )

        await self._reply(ctx, embed=embed)

    @pixelagents_group.command(name="wsport")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(port="Port the office WebSocket server binds (default: 3210)")
    async def cmd_wsport(self, ctx: commands.Context, port: int) -> None:
        """Set the port the office WebSocket server listens on.

        Traefik routes `/ws` on the dashboard host to this port, so changing it
        means updating the Traefik label in redstack too.
        """
        try:
            await self._settings_service.set_ws_port(port)
        except ValueError:
            await self._reply(ctx, "Port must be between 1 and 65535.")
            return
        await self._reply(
            ctx,
            f"Office server port set to `{port}`. Reload the cog to rebind, and update the "
            "Traefik `/ws` route to match.",
        )

    @pixelagents_group.command(name="toolcleardelay")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(seconds="Seconds to keep the message activity indicator visible")
    async def cmd_toolcleardelay(self, ctx: commands.Context, seconds: float) -> None:
        """Set how long (in seconds) a message tool indicator stays visible (default: 2.0)."""
        try:
            await self._settings_service.set_message_tool_clear_delay(seconds)
        except ValueError:
            await self._reply(ctx, "Delay must be 0 or greater.")
            return
        await self._reply(ctx, f"Message tool clear delay set to `{seconds}s`.")

    @pixelagents_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        value="Whether rich presence (Spotify, games, etc.) is shown in the webview"
    )
    async def cmd_richpresence(self, ctx: commands.Context, value: bool) -> None:
        """Set whether rich presence activity is broadcast to the webview (true/false)."""
        await self._settings_service.set_broadcast_rich_presence(value)
        await self._reply(ctx, f"Rich presence broadcasting set to `{value}`.")

    @pixelagents_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        value="Whether Discord messages are shown as tool bubbles in the webview"
    )
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        """Set whether Discord messages are broadcast as tool bubbles to the webview (true/false)."""
        await self._settings_service.set_broadcast_messages(value)
        await self._reply(ctx, f"Message broadcasting set to `{value}`.")

    @pixelagents_group.command(name="editorrole")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(role="Discord role that grants webview editor access (omit to clear)")
    async def cmd_editorrole(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ) -> None:
        """Set the Discord role that grants webview editor access. Omit to clear."""
        if role is None:
            await self._settings_service.set_editor_role_id(None)
            await self._reply(ctx, "Editor role cleared.")
        else:
            await self._settings_service.set_editor_role_id(role.id)
            await self._reply(ctx, f"Editor role set to `{role.name}` (ID: {role.id}).")

    @pixelagents_group.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable Pixel Agents office presence mirroring for this guild and run a full sync."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        result = await self._settings_service.enable_guild(ctx.guild.id)
        await self._reply(ctx, "Enabled. Running full sync…")
        await self._reply(ctx, result)

    @pixelagents_group.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable Pixel Agents office presence mirroring for this guild and despawn all agents."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._settings_service.disable_guild(ctx.guild.id)
        await self._reply(ctx, "Disabled. Despawning all tracked agents…")
        await self._reply(ctx, "Done.")

    @pixelagents_group.command(name="includebots")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether bot users should be mirrored")
    async def cmd_includebots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored (true/false)."""
        result = await self._settings_service.set_include_bots(ctx.guild.id, value)
        await self._reply(ctx, f"include_bots set to `{value}`. Running sync…")
        if result is not None:
            await self._reply(ctx, result)

    @pixelagents_group.command(name="sync")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Manually reconcile all guild members against their current Discord presence."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._settings_repository.guild_enabled(ctx.guild):
            await self._reply(ctx, "Guild is not enabled. Use `[p]pixelagents enable` first.")
            return
        await self._reply(ctx, "Syncing…")
        result = await self._full_sync(ctx.guild)
        await self._reply(ctx, result)

    @pixelagents_group.command(name="despawnall")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawnall(self, ctx: commands.Context) -> None:
        """Despawn all tracked agents for this guild without disabling the cog."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Despawning all tracked agents for this guild…")
        await self._despawn_guild(ctx.guild)
        await self._reply(ctx, "Done.")

    @pixelagents_group.group(name="index", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def pixelagents_pixelindex_group(self, ctx: commands.Context) -> None:
        """Show the configured Pixel Index API endpoint and check its health."""
        if ctx.invoked_subcommand is not None:
            return
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        settings = await self._settings_service.global_settings()
        api_url = settings.pixel_index_api_url
        web_url = settings.pixel_index_web_url
        ok, detail = await self._check_pixel_index_health(api_url)
        await self._reply(
            ctx,
            f"Pixel Index API: `{api_url}`\n"
            f"Pixel Index Web: `{web_url}`\n"
            f"Health check: {'✅ ' + detail if ok else '🛑 ' + detail}",
        )

    async def _check_pixel_index_health(self, url: str) -> Tuple[bool, str]:
        health_url = url.rstrip("/") + "/health"
        timeout = aiohttp.ClientTimeout(total=_PIXEL_INDEX_HEALTH_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(health_url) as resp:
                    if resp.status == 200:
                        return True, f"ok ({health_url})"
                    return False, f"HTTP {resp.status} ({health_url})"
        except Exception as exc:
            return False, f"unreachable ({exc})"

    @staticmethod
    def _clean_url(url: str) -> Optional[str]:
        try:
            return normalize_http_url(url)
        except ValueError:
            return None

    @pixelagents_pixelindex_group.command(name="set")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(url="Pixel Index API base URL, e.g. https://pixel-index-api.nntin.xyz")
    async def cmd_pixelindex_set(self, ctx: commands.Context, url: str) -> None:
        """Set the Pixel Index API endpoint (e.g. to switch between prod and staging)."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        try:
            clean = await self._settings_service.set_pixel_index_api_url(url)
        except ValueError:
            await self._reply(
                ctx, "Please provide a valid URL, e.g. `https://pixel-index-api.nntin.xyz`."
            )
            return
        ok, detail = await self._check_pixel_index_health(clean)
        await self._reply(
            ctx,
            f"Pixel Index API endpoint set to `{clean}`.\nHealth check: {'✅ ' + detail if ok else '🛑 ' + detail}",
        )

    @pixelagents_pixelindex_group.command(name="setweb")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        url="Pixel Index web frontend base URL, e.g. https://pixel-index.vercel.app"
    )
    async def cmd_pixelindex_setweb(self, ctx: commands.Context, url: str) -> None:
        """Set the Pixel Index web frontend base URL, used for "View on site" links."""
        try:
            clean = await self._settings_service.set_pixel_index_web_url(url)
        except ValueError:
            await self._reply(
                ctx, "Please provide a valid URL, e.g. `https://pixel-index.vercel.app`."
            )
            return
        await self._reply(ctx, f"Pixel Index web frontend set to `{clean}`.")

    # ------------------------------------------------------------------
    # Pixel Index HTTP client
    # ------------------------------------------------------------------

    async def _pixel_index_get(self, path: str, params: Optional[dict] = None) -> Tuple[bool, Any]:
        base = await self._settings_repository.pixel_index_api_url()
        url = f"{base.rstrip('/')}{path}"
        timeout = aiohttp.ClientTimeout(total=_PIXEL_INDEX_REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return False, f"Pixel Index API returned HTTP {resp.status}."
                    return True, await resp.json()
        except Exception as exc:
            return False, f"Could not reach the Pixel Index API: {exc}"

    async def _pixel_index_search(
        self,
        *,
        query: Optional[str],
        tag: Optional[str],
        sort: str,
        cursor: Optional[str] = None,
    ) -> Tuple[bool, Any]:
        params: Dict[str, Any] = {"sort": sort, "limit": _LAYOUT_SEARCH_PAGE_SIZE}
        if query:
            params["q"] = query
        if tag:
            params["tags"] = tag
        if cursor:
            params["cursor"] = cursor
        ok, data = await self._pixel_index_get("/api/v1/layouts", params)
        if not ok:
            return False, data
        try:
            page = LayoutListResponse.model_validate(data)
        except ValidationError as exc:
            log.warning("pixelagents: Pixel Index layout list response failed validation: %s", exc)
            return False, "Pixel Index returned an unexpected response. Try again later."
        return True, page

    async def _pixel_index_layout(self, slug: str) -> Tuple[bool, Any]:
        ok, data = await self._pixel_index_get(f"/api/v1/layouts/{slug}")
        if not ok:
            return False, data
        try:
            detail = LayoutDetail.model_validate(data)
        except ValidationError as exc:
            log.warning(
                "pixelagents: Pixel Index layout detail response failed validation: %s", exc
            )
            return False, "Pixel Index returned an unexpected response. Try again later."
        return True, detail

    async def _load_pixel_index_layout(self, user_id: int, slug: str) -> Tuple[bool, str]:
        """Fetch a layout from Pixel Index and push it into the shared office."""
        if not await self._can_edit_layout_user(user_id):
            return False, "You are not authorized to manage Pixel Agents layouts."
        ok, detail = await self._pixel_index_layout(slug)
        if not ok:
            return False, str(detail)
        layout = detail.layout
        if not self._validate_layout(layout):
            return False, "That layout is invalid and cannot be loaded."
        await self._settings_repository.set_layout(layout)
        await self._send({"type": "layoutLoaded", "layout": layout})
        return True, f"Loaded `{detail.title or slug}` into the office."

    # ------------------------------------------------------------------
    # Pixel Index layout browsing
    # ------------------------------------------------------------------

    @pixelagents_group.group(name="layout", invoke_without_command=True)
    async def pixelagents_layout_group(self, ctx: commands.Context) -> None:
        """Browse shared office layouts from Pixel Index."""
        await ctx.send_help()

    @pixelagents_layout_group.command(name="search")
    @app_commands.describe(
        query="Text to search for in the title/description",
        tag="Only show layouts with this tag",
        sort="Sort order",
    )
    @app_commands.choices(
        sort=[app_commands.Choice(name=choice, value=choice) for choice in _LAYOUT_SORT_CHOICES]
    )
    async def cmd_layout_search(
        self,
        ctx: commands.Context,
        query: Optional[str] = None,
        tag: Optional[str] = None,
        sort: str = "newest",
    ) -> None:
        """Search Pixel Index for shared office layouts."""
        if ctx.interaction:
            await ctx.interaction.response.defer()
        if sort not in _LAYOUT_SORT_CHOICES:
            sort = "newest"
        ok, page = await self._pixel_index_search(query=query, tag=tag, sort=sort)
        if not ok:
            await self._send_public(ctx, str(page))
            return
        if not page.layouts:
            await self._send_public(ctx, "No layouts found on Pixel Index.")
            return
        api_base = await self._settings_repository.pixel_index_api_url()
        web_base = await self._settings_repository.pixel_index_web_url()
        view = _LayoutBrowseView(
            self,
            ctx.author.id,
            query=query,
            tag=tag,
            sort=sort,
            pages=[page],
            page_index=0,
            api_base=api_base,
            web_base=web_base,
        )
        await self._send_public(ctx, view=view)

    @pixelagents_layout_group.command(name="view")
    @app_commands.describe(slug="Pixel Index layout slug")
    async def cmd_layout_view(self, ctx: commands.Context, slug: str) -> None:
        """Show a single Pixel Index layout by its slug."""
        if ctx.interaction:
            await ctx.interaction.response.defer()
        ok, detail = await self._pixel_index_layout(slug.strip().lower())
        if not ok:
            await self._send_public(ctx, str(detail))
            return
        api_base = await self._settings_repository.pixel_index_api_url()
        web_base = await self._settings_repository.pixel_index_web_url()
        view = _LayoutDetailView(self, ctx.author.id, detail, api_base=api_base, web_base=web_base)
        await self._send_public(ctx, view=view)


def _abs_url(base: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


class _LayoutBrowseView(discord.ui.LayoutView):
    """Paginated Pixel Index search results, rendered with Components V2."""

    def __init__(
        self,
        cog: "pixelagents",
        owner_id: int,
        *,
        query: Optional[str],
        tag: Optional[str],
        sort: str,
        pages: List[LayoutListResponse],
        page_index: int,
        api_base: str,
        web_base: str,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = owner_id
        self.query = query
        self.tag = tag
        self.sort = sort
        self.pages = pages
        self.page_index = page_index
        self.api_base = api_base
        self.web_base = web_base
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this search can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        page = self.pages[self.page_index]
        layouts = page.layouts
        total = page.total if page.total is not None else len(layouts)

        header_bits = [f"**Pixel Index layouts** — {total} match(es)"]
        if self.query:
            header_bits.append(f"matching `{self.query}`")
        if self.tag:
            header_bits.append(f"tagged `{self.tag}`")
        container = discord.ui.Container(discord.ui.TextDisplay(" ".join(header_bits)))

        for entry in layouts:
            display_name = entry.author.displayName if entry.author else None
            visible_cols = entry.visibleCols if entry.visibleCols is not None else "?"
            visible_rows = entry.visibleRows if entry.visibleRows is not None else "?"
            stats = (
                f"{visible_cols}×{visible_rows} · "
                f"{entry.furniture or 0} furniture · by {display_name or 'unknown'}"
            )
            tags = entry.tags
            lines = [f"**{entry.title or entry.slug}**", stats]
            if tags:
                lines.append("_" + ", ".join(tags) + "_")
            thumbnail_path = entry.files.thumbnail if entry.files else None
            accessory = (
                discord.ui.Thumbnail(_abs_url(self.api_base, thumbnail_path))
                if thumbnail_path
                else discord.ui.Button(label="?", disabled=True)
            )
            container.add_item(
                discord.ui.Section(discord.ui.TextDisplay("\n".join(lines)), accessory=accessory)
            )

        select_row = discord.ui.ActionRow()
        select = discord.ui.Select(
            placeholder="View a layout…",
            options=[
                discord.SelectOption(
                    label=(entry.title or entry.slug)[:100],
                    value=entry.slug,
                    description=(entry.description or "")[:100] or None,
                )
                for entry in layouts
            ],
        )
        select.callback = self._make_select_callback(select)
        select_row.add_item(select)
        container.add_item(select_row)

        nav_row = discord.ui.ActionRow()
        prev_button = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page_index == 0
        )
        prev_button.callback = self._on_prev
        at_last_known_page = self.page_index >= len(self.pages) - 1
        next_button = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=at_last_known_page and page.nextCursor is None,
        )
        next_button.callback = self._on_next
        nav_row.add_item(prev_button)
        nav_row.add_item(next_button)
        container.add_item(nav_row)

        self.add_item(container)

    def _make_select_callback(self, select: discord.ui.Select) -> Callable:
        async def on_select(interaction: discord.Interaction) -> None:
            slug = select.values[0]
            ok, detail = await self.cog._pixel_index_layout(slug)
            if not ok:
                await interaction.response.send_message(str(detail), ephemeral=True)
                return
            detail_view = _LayoutDetailView(
                self.cog,
                self.owner_id,
                detail,
                api_base=self.api_base,
                web_base=self.web_base,
                back=self,
            )
            await interaction.response.edit_message(view=detail_view)

        return on_select

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index == 0:
            await interaction.response.defer()
            return
        new_view = _LayoutBrowseView(
            self.cog,
            self.owner_id,
            query=self.query,
            tag=self.tag,
            sort=self.sort,
            pages=self.pages,
            page_index=self.page_index - 1,
            api_base=self.api_base,
            web_base=self.web_base,
        )
        await interaction.response.edit_message(view=new_view)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < len(self.pages):
            new_pages = self.pages
            new_index = self.page_index + 1
        else:
            cursor = self.pages[self.page_index].nextCursor
            if not cursor:
                await interaction.response.defer()
                return
            ok, page = await self.cog._pixel_index_search(
                query=self.query, tag=self.tag, sort=self.sort, cursor=cursor
            )
            if not ok:
                await interaction.response.send_message(str(page), ephemeral=True)
                return
            new_pages = self.pages + [page]
            new_index = len(new_pages) - 1
        new_view = _LayoutBrowseView(
            self.cog,
            self.owner_id,
            query=self.query,
            tag=self.tag,
            sort=self.sort,
            pages=new_pages,
            page_index=new_index,
            api_base=self.api_base,
            web_base=self.web_base,
        )
        await interaction.response.edit_message(view=new_view)


class _LayoutDetailView(discord.ui.LayoutView):
    """A single Pixel Index layout, rendered with Components V2."""

    def __init__(
        self,
        cog: "pixelagents",
        owner_id: int,
        detail: LayoutDetail,
        *,
        api_base: str,
        web_base: str,
        back: Optional["_LayoutBrowseView"] = None,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = owner_id
        self.detail = detail
        self.api_base = api_base
        self.web_base = web_base
        self.back = back
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        d = self.detail
        display_name = d.author.displayName if d.author else None
        lines = [f"**{d.title or d.slug}**"]
        if d.description:
            lines.append(d.description)
        visible_cols = d.visibleCols if d.visibleCols is not None else "?"
        visible_rows = d.visibleRows if d.visibleRows is not None else "?"
        lines.append(
            f"{visible_cols}×{visible_rows} · "
            f"{d.furniture or 0} furniture · {d.areas or 0} areas · "
            f"{d.pets or 0} pets · {d.seats or 0} seats"
        )
        lines.append(f"By {display_name or 'unknown'}")
        tags = d.tags
        if tags:
            lines.append("Tags: " + ", ".join(tags))

        container = discord.ui.Container(discord.ui.TextDisplay("\n".join(lines)))

        preview_path = d.files.preview if d.files else None
        if preview_path:
            container.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(_abs_url(self.api_base, preview_path))
                )
            )

        actions = discord.ui.ActionRow()
        download_path = d.files.layout if d.files else None
        if download_path:
            actions.add_item(
                discord.ui.Button(label="Download JSON", url=_abs_url(self.api_base, download_path))
            )
        slug = d.slug
        if slug:
            actions.add_item(
                discord.ui.Button(
                    label="View on site", url=_abs_url(self.web_base, f"/layouts/{slug}")
                )
            )
        load_button = discord.ui.Button(label="Load into office", style=discord.ButtonStyle.primary)
        load_button.callback = self._on_load
        actions.add_item(load_button)
        if self.back is not None:
            back_button = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)
            back_button.callback = self._on_back
            actions.add_item(back_button)
        container.add_item(actions)

        self.add_item(container)

    async def _on_load(self, interaction: discord.Interaction) -> None:
        slug = self.detail.slug
        ok, message = await self.cog._load_pixel_index_layout(interaction.user.id, slug)
        await interaction.response.send_message(message, ephemeral=True)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self.back)
