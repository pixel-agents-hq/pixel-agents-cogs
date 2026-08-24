"""Red Dashboard routes for the Pixel Agents webview."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import discord
from redbot.core import commands

from ..infrastructure.menu import render_menu
from .cog_base import PixelAgentsBase

log = logging.getLogger("red.d_cogs.floorplan")

DashboardCallable = TypeVar("DashboardCallable", bound=Callable[..., Any])

# Red Web Dashboard (AAA3A-cogs' `dashboard`) registers its Cog under this
# name -- the same string Red's `[p]load dashboard` and `bot.get_cog(...)`
# use for it elsewhere in the ecosystem. There is no cross-repo contract
# test for this (dashboard lives outside this repo), so the shape check in
# `dashboard_cog_loaded` below (mirroring `on_dashboard_cog_add`'s own
# `.rpc.third_parties_handler` check) is the actual source of truth --
# this name is just how we find a candidate to check.
DASHBOARD_COG_NAME = "Dashboard"

DASHBOARD_DOCS_URL = "https://red-web-dashboard.readthedocs.io/en/latest/"


def dashboard_cog_loaded(bot: Any) -> bool:
    """Whether Red Web Dashboard is loaded and ready to register third parties.

    Checked the same way `on_dashboard_cog_add` already does when dashboard
    broadcasts its own load event (`.rpc.third_parties_handler`) -- reusing
    that shape check here means this stays correct even if it turns out
    dashboard doesn't register under `DASHBOARD_COG_NAME` on some install:
    a `bot.get_cog("Dashboard")` hit without that shape is treated the same
    as not finding it at all, rather than assumed to be a working dashboard.
    """

    dashboard_cog = bot.get_cog(DASHBOARD_COG_NAME)
    if dashboard_cog is None:
        return False
    third_parties = getattr(getattr(dashboard_cog, "rpc", None), "third_parties_handler", None)
    return third_parties is not None


def dashboard_not_loaded_notification() -> str:
    """DM text for `Red.send_to_owners` when dashboard isn't loaded yet."""

    return (
        "⚠️ Pixel Agents floorplan could not find the Red Web Dashboard cog "
        "loaded, so the office webview has nowhere to be served from. Set it "
        f"up, then load/reload floorplan: {DASHBOARD_DOCS_URL}"
    )


def dashboard_page(
    *args: object, **kwargs: object
) -> Callable[[DashboardCallable], DashboardCallable]:
    """Attach the metadata consumed by Red Dashboard's third-party router."""

    def decorator(func: DashboardCallable) -> DashboardCallable:
        object.__setattr__(func, "__dashboard_decorator_params__", (args, kwargs))
        return func

    return decorator


class DashboardMixin(PixelAgentsBase):
    """Expose the public office page, editor session, and static assets."""

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if not hasattr(dashboard_cog, "rpc"):
            return
        third_parties = getattr(dashboard_cog.rpc, "third_parties_handler", None)
        if third_parties is not None:
            third_parties.add_third_party(self, overwrite=True)

    async def _notify_owners_dashboard_missing_if_unloaded(self) -> None:
        """DM the owner once, at `cog_load` time, if dashboard isn't loaded yet.

        Deliberately a one-shot check here, not a recurring one: dashboard
        loading *after* floorplan is already handled by
        `on_dashboard_cog_add` above (Red Dashboard's own registration
        broadcast), so this only needs to catch the case that listener can
        never see -- dashboard still missing at the moment floorplan itself
        loads. Must never raise: a missing/unreachable owner DM is not a
        reason to fail floorplan's own load.
        """

        if dashboard_cog_loaded(self.bot):
            return
        try:
            await self.bot.send_to_owners(dashboard_not_loaded_notification())
        except Exception:
            log.exception("floorplan: could not notify owners about the missing dashboard cog")

    def _webview_dist_root(self) -> Path:
        return self._webview_assets.root

    def _resolve_webview_asset(self, asset_path: str) -> Path | None:
        return self._webview_assets.resolve(asset_path)

    def _content_type_for_asset(self, asset_path: str) -> str:
        return self._webview_assets.content_type(asset_path)

    def _mint_ticket(self, user_id: int) -> str:
        return self._ticket_store.mint(user_id)

    def _resolve_ticket(self, ticket: str) -> int | None:
        return self._ticket_store.resolve(ticket)

    @staticmethod
    def _parse_int(raw: str) -> int | None:
        try:
            return int(raw)
        except ValueError:
            return None

    async def _visible_public_guilds(self) -> list[discord.Guild]:
        visible = []
        for guild in self.bot.guilds:
            if not await self._settings_repository.guild_enabled(guild):
                continue
            if not await self._settings_repository.guild_private(guild):
                visible.append(guild)
        return visible

    async def _visible_private_guilds(self, user_id: int | None) -> list[discord.Guild]:
        visible = []
        for guild in self.bot.guilds:
            if not await self._settings_repository.guild_enabled(guild):
                continue
            if not await self._settings_repository.guild_private(guild):
                continue
            if await self._can_view_office(user_id, guild.id):
                visible.append(guild)
        return visible

    async def _guild_office_is_public(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None or not await self._settings_repository.guild_enabled(guild):
            return False
        return not await self._settings_repository.guild_private(guild)

    @staticmethod
    def _office_not_found(message: str) -> dict[str, object]:
        return {"status": 1, "error_code": 404, "error_message": message}

    @staticmethod
    def _office_forbidden(message: str) -> dict[str, object]:
        return {"status": 1, "error_code": 403, "error_message": message}

    # This signature must stay context-free so the server menu remains public.
    @dashboard_page(name=None, description="Pixel Agents server menu.", methods=("GET",))
    async def dashboard_webview(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        public_guilds = await self._visible_public_guilds()
        source = render_menu([(guild.id, guild.name) for guild in public_guilds])
        return {"status": 0, "web_content": {"standalone": True, "source": source}}

    @dashboard_page(
        name="session",
        description="Pixel Agents editor session ticket.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_session(self, user_id: int, **kwargs: object) -> dict[str, object]:
        del kwargs
        body = json.dumps({"ticket": self._mint_ticket(user_id)}).encode()
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
        name="servers",
        description="Pixel Agents private server list for a session ticket.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_servers(self, ticket: str = "", **kwargs: object) -> dict[str, object]:
        del kwargs
        user_id = self._resolve_ticket(ticket) if ticket else None
        private_guilds = await self._visible_private_guilds(user_id)
        body = json.dumps(
            {"private": [{"id": guild.id, "name": guild.name} for guild in private_guilds]}
        ).encode()
        return {
            "status": 0,
            "raw_response": {
                "status": 200,
                "content_type": "application/json",
                "body_base64": base64.b64encode(body).decode("ascii"),
                "headers": {"Cache-Control": "no-store"},
            },
        }

    # `guild` (not `guild_id`) deliberately -- a `guild_id`-named parameter
    # would make Red Dashboard force a login on every visit (see
    # Architecture.md), which would break public offices. This route stays
    # public and only ever serves a guild that is both enabled and public.
    @dashboard_page(name="office", description="Pixel Agents office.", methods=("GET",))
    async def dashboard_office(self, guild: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        guild_id = self._parse_int(guild)
        if guild_id is None or not await self._guild_office_is_public(guild_id):
            return self._office_not_found(
                "This server's office is unavailable or private. If you're a member, "
                f"try logging in: office-login?guild={guild}"
            )
        await self._sync_webview_assets()
        return self._webview_assets.dashboard_office_response(guild_id)

    # `user_id` forces Red Dashboard's existing Discord OAuth login (the
    # same context-id name `dashboard_session` already relies on) -- this
    # handler still re-checks guild membership itself via `_can_view_office`
    # rather than trusting Dashboard to enforce that on our behalf.
    @dashboard_page(
        name="office-login",
        description="Pixel Agents office (private servers).",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_office_login(
        self, user_id: int, guild: str, **kwargs: object
    ) -> dict[str, object]:
        del kwargs
        guild_id = self._parse_int(guild)
        if guild_id is None or not await self._can_view_office(user_id, guild_id):
            return self._office_forbidden("You are not authorized to view this server's office.")
        await self._sync_webview_assets()
        return self._webview_assets.dashboard_office_response(guild_id)

    @dashboard_page(
        name="static", description="Pixel Agents static asset.", methods=("GET", "HEAD")
    )
    async def dashboard_static(self, asset_path: str, **kwargs: object) -> dict[str, object]:
        return self._webview_assets.dashboard_static_response(
            asset_path, head_only=kwargs.get("method") == "HEAD"
        )
