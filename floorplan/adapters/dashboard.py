"""Red Dashboard routes for the Pixel Agents webview."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from redbot.core import commands

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

    # This signature must stay context-free so the office page remains public.
    @dashboard_page(name=None, description="Pixel Agents webview.", methods=("GET",))
    async def dashboard_webview(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        await self._sync_webview_assets()
        return self._webview_assets.dashboard_webview_response()

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
        name="static", description="Pixel Agents static asset.", methods=("GET", "HEAD")
    )
    async def dashboard_static(self, asset_path: str, **kwargs: object) -> dict[str, object]:
        return self._webview_assets.dashboard_static_response(
            asset_path, head_only=kwargs.get("method") == "HEAD"
        )
