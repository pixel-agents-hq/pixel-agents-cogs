"""Two Dashboard pages sharing one static Pixel Agents bundle."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any, TypeVar

from redbot.core import commands

from corridor.domain import OfficeStateKind

from .cog_base import CctvBase

DashboardCallable = TypeVar("DashboardCallable", bound=Callable[..., Any])
DASHBOARD_COG_NAME = "Dashboard"


def dashboard_page(
    *args: object, **kwargs: object
) -> Callable[[DashboardCallable], DashboardCallable]:
    def decorator(func: DashboardCallable) -> DashboardCallable:
        object.__setattr__(func, "__dashboard_decorator_params__", (args, kwargs))
        return func

    return decorator


def dashboard_cog_loaded(bot: Any) -> bool:
    dashboard = bot.get_cog(DASHBOARD_COG_NAME)
    return getattr(getattr(dashboard, "rpc", None), "third_parties_handler", None) is not None


class DashboardMixin(CctvBase):
    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        third_parties = getattr(getattr(dashboard_cog, "rpc", None), "third_parties_handler", None)
        if third_parties is not None:
            third_parties.add_third_party(self, overwrite=True)

    @dashboard_page(name="discord", description="Discord office CCTV.", methods=("GET",))
    async def dashboard_discord(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        error = await self._ensure_page(OfficeStateKind.DISCORD)
        return (
            self._assets.unavailable_response(error)
            if error is not None
            else self._assets.page_response("discord")
        )

    @dashboard_page(name="editor", description="Agent editor CCTV.", methods=("GET",))
    async def dashboard_editor(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        error = await self._ensure_page(OfficeStateKind.EDITOR)
        return (
            self._assets.unavailable_response(error)
            if error is not None
            else self._assets.page_response("editor")
        )

    @dashboard_page(
        name="session",
        description="Discord CCTV editor session.",
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

    @dashboard_page(name="static", description="Shared CCTV static asset.", methods=("GET", "HEAD"))
    async def dashboard_static(self, asset_path: str, **kwargs: object) -> dict[str, object]:
        return self._assets.static_response(asset_path, head_only=kwargs.get("method") == "HEAD")


__all__ = ["DASHBOARD_COG_NAME", "DashboardMixin", "dashboard_cog_loaded", "dashboard_page"]
