"""Red Dashboard routes for the Pixel Agents webview."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from redbot.core import commands

from .cog_base import PixelAgentsBase

DashboardCallable = TypeVar("DashboardCallable", bound=Callable[..., Any])


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
