"""Red Dashboard routes for architect's webview.

A deliberate parallel copy of `floorplan/adapters/dashboard.py`'s
`DashboardMixin` shape -- see docs/architect-design.md section 5: two
independent consumers of pixelagents' built `webview_dist/`, not a shared
library.

Only the public page and its static assets are served here -- still no
`/session` ticket endpoint, since architect's layout has no
editor-authorization concept at all (a deliberate decision, not a gap --
see `infrastructure/websocket.py`'s module docstring and
docs/architect-design.md section 5.1). architect's own WebSocket server
(`infrastructure/websocket.py`, `infrastructure/client_hub.py`) is bound
directly by `cog_base.py`/`office_gateway.py`, independent of this
Dashboard-route module -- this file only ever serves the static page and
its assets. The bundle's own ticket-shim script
(`WebviewAssetProvider.dashboard_webview_response`, injected
unconditionally) degrades gracefully without a `/session` endpoint here:
its fetch resolves to a null ticket on a 404, and its own `WebSocket`
patch never intercepts anything, since the real live connection is
opened against `/architect/ws` by a separate client-side rewrite shim
(`infrastructure/webview.py`'s `WS_REWRITE_SHIM`), not by this file.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from redbot.core import commands

from .cog_base import CogBase

log = logging.getLogger("red.architect")

DashboardCallable = TypeVar("DashboardCallable", bound=Callable[..., Any])

# Same convention as floorplan/adapters/dashboard.py -- see that module's
# docstring for why this is a name-based lookup rather than a direct import
# (Red Dashboard lives outside this repo, so there's no shared type to
# check against; `dashboard_cog_loaded`'s shape check below is the actual
# source of truth).
DASHBOARD_COG_NAME = "Dashboard"

DASHBOARD_DOCS_URL = "https://red-web-dashboard.readthedocs.io/en/latest/"


def dashboard_cog_loaded(bot: Any) -> bool:
    """Whether Red Web Dashboard is loaded and ready to register third parties."""

    dashboard_cog = bot.get_cog(DASHBOARD_COG_NAME)
    if dashboard_cog is None:
        return False
    third_parties = getattr(getattr(dashboard_cog, "rpc", None), "third_parties_handler", None)
    return third_parties is not None


def dashboard_not_loaded_notification() -> str:
    """DM text for `Red.send_to_owners` when dashboard isn't loaded yet."""

    return (
        "⚠️ architect could not find the Red Web Dashboard cog loaded, so its webview has "
        f"nowhere to be served from. Set it up, then load/reload architect: {DASHBOARD_DOCS_URL}"
    )


def dashboard_page(
    *args: object, **kwargs: object
) -> Callable[[DashboardCallable], DashboardCallable]:
    """Attach the metadata consumed by Red Dashboard's third-party router."""

    def decorator(func: DashboardCallable) -> DashboardCallable:
        object.__setattr__(func, "__dashboard_decorator_params__", (args, kwargs))
        return func

    return decorator


class DashboardMixin(CogBase):
    """Expose architect's webview page and static assets."""

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        if not hasattr(dashboard_cog, "rpc"):
            return
        third_parties = getattr(dashboard_cog.rpc, "third_parties_handler", None)
        if third_parties is not None:
            third_parties.add_third_party(self, overwrite=True)

    async def _notify_owners_dashboard_missing_if_unloaded(self) -> None:
        """Deliberately a one-shot check here, not a recurring one:
        dashboard loading *after* architect is already handled by
        `on_dashboard_cog_add` above (Red Dashboard's own registration
        broadcast), so this only needs to catch the case that listener can
        never see -- dashboard still missing at the moment architect
        itself loads. Must never raise: a missing/unreachable owner DM is
        not a reason to fail architect's own load."""

        if dashboard_cog_loaded(self.bot):
            return
        try:
            await self.bot.send_to_owners(dashboard_not_loaded_notification())
        except Exception:
            log.exception("architect: could not notify owners about the missing dashboard cog")

    # This signature must stay context-free so the webview page remains public.
    @dashboard_page(name=None, description="Architect webview.", methods=("GET",))
    async def dashboard_webview(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        await self._sync_webview_assets()
        return self._webview_assets.dashboard_webview_response()

    @dashboard_page(name="static", description="Architect static asset.", methods=("GET", "HEAD"))
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
