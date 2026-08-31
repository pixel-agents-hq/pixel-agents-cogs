"""Red Dashboard routes for cctv's two pages -- the only dashboard-hosting
surface left in this repo (docs/cctv-design.md). One shared static asset
route; two named entry pages with different auth policies:

- `/third-party/cctv/discord` -- floorplan's former public page, ticket-
  gated editing (a `/session` endpoint mints the ticket).
- `/third-party/cctv/editor` -- architect's former public page, no
  `/session` endpoint at all -- no editor-authorization concept, by
  design (docs/cctv-design.md §2.7's table).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from redbot.core import commands

from .cog_base import WEBVIEW_BASE_PATH, CogBase

log = logging.getLogger("red.cctv")

DashboardCallable = TypeVar("DashboardCallable", bound=Callable[..., Any])

# Same name-based lookup convention floorplan's/architect's own former
# dashboard modules used -- Red Dashboard lives outside this repo, so
# there's no shared type to check against; the shape check below is the
# actual source of truth.
DASHBOARD_COG_NAME = "Dashboard"
DASHBOARD_DOCS_URL = "https://red-web-dashboard.readthedocs.io/en/latest/"


def dashboard_cog_loaded(bot: Any) -> bool:
    dashboard_cog = bot.get_cog(DASHBOARD_COG_NAME)
    if dashboard_cog is None:
        return False
    third_parties = getattr(getattr(dashboard_cog, "rpc", None), "third_parties_handler", None)
    return third_parties is not None


def dashboard_not_loaded_notification() -> str:
    return (
        "⚠️ cctv could not find the Red Web Dashboard cog loaded, so neither of its office "
        f"pages have anywhere to be served from. Set it up, then load/reload cctv: {DASHBOARD_DOCS_URL}"
    )


def dashboard_page(
    *args: object, **kwargs: object
) -> Callable[[DashboardCallable], DashboardCallable]:
    def decorator(func: DashboardCallable) -> DashboardCallable:
        object.__setattr__(func, "__dashboard_decorator_params__", (args, kwargs))
        return func

    return decorator


class DashboardMixin(CogBase):
    """Expose both office pages, the Discord page's editor session, and
    their one shared static asset route."""

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if not hasattr(dashboard_cog, "rpc"):
            return
        third_parties = getattr(dashboard_cog.rpc, "third_parties_handler", None)
        if third_parties is not None:
            third_parties.add_third_party(self, overwrite=True)

    # This signature must stay context-free so both office pages remain public.
    @dashboard_page(
        name="discord", description="Pixel Agents office (Discord presence).", methods=("GET",)
    )
    async def dashboard_webview_discord(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        await self._sync_webview_assets()
        return self._webview_assets.dashboard_webview_response(
            base_href=WEBVIEW_BASE_PATH,
            ws_target_path="/cctv/discord/ws",
            include_ticket_shim=True,
        )

    @dashboard_page(
        name="editor", description="Pixel Agents office (structure/color editor).", methods=("GET",)
    )
    async def dashboard_webview_editor(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        await self._sync_webview_assets()
        return self._webview_assets.dashboard_webview_response(
            base_href=WEBVIEW_BASE_PATH,
            ws_target_path="/cctv/editor/ws",
            include_ticket_shim=False,
        )

    @dashboard_page(
        name="session",
        description="cctv Discord-page editor session ticket.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_session(self, user_id: int, **kwargs: object) -> dict[str, object]:
        del kwargs
        body = json.dumps({"ticket": self._tickets.mint(user_id)}).encode()
        return {
            "status": 0,
            "raw_response": {
                "status": 200,
                "content_type": "application/json",
                "body_base64": base64.b64encode(body).decode("ascii"),
                "headers": {"Cache-Control": "no-store"},
            },
        }

    @dashboard_page(name="static", description="cctv static asset.", methods=("GET", "HEAD"))
    async def dashboard_static(self, asset_path: str, **kwargs: object) -> dict[str, object]:
        return self._webview_assets.dashboard_static_response(
            asset_path, head_only=kwargs.get("method") == "HEAD"
        )


__all__ = [
    "DashboardMixin",
    "dashboard_cog_loaded",
    "dashboard_not_loaded_notification",
    "dashboard_page",
]
