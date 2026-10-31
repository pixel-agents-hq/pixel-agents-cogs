"""CCTV's Dashboard pages: the server picker, each guild's office, and the
editor office, all sharing one static Pixel Agents bundle."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any, TypeVar

from redbot.core import commands

from ..infrastructure import (
    EDITOR_WS_PATH,
    PICKER_PATH,
    PickerEntry,
    office_ws_path,
    picker_response,
)
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


def parse_guild_id(raw: object) -> int | None:
    """A guild id from the `?guild=` query value, or None if it isn't one."""

    text = str(raw).strip() if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
    if not text.isdecimal() or len(text) > 20:
        return None
    value = int(text)
    return value if value > 0 else None


def dashboard_cog_loaded(bot: Any) -> bool:
    dashboard = bot.get_cog(DASHBOARD_COG_NAME)
    return getattr(getattr(dashboard, "rpc", None), "third_parties_handler", None) is not None


class DashboardMixin(CctvBase):
    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        third_parties = getattr(getattr(dashboard_cog, "rpc", None), "third_parties_handler", None)
        if third_parties is not None:
            third_parties.add_third_party(self, overwrite=True)

    @dashboard_page(name=None, description="Pixel Agents offices.", methods=("GET",))
    async def dashboard_picker(self, **kwargs: object) -> dict[str, object]:
        """The server picker: every guild with an open office."""

        del kwargs
        return picker_response(self._picker_entries())

    @dashboard_page(name="office", description="A server's Pixel Agents office.", methods=("GET",))
    async def dashboard_office(self, guild: str = "", **kwargs: object) -> dict[str, object]:
        del kwargs
        guild_id = parse_guild_id(guild)
        office = self.office(guild_id) if guild_id is not None else None
        if guild_id is None or office is None:
            return self._assets.not_found_response()
        error = await self._ensure_office_page(office)
        return (
            self._assets.unavailable_response(error)
            if error is not None
            else self._assets.page_response(office_ws_path(guild_id), identify=True)
        )

    @dashboard_page(
        name="discord", description="Moved to the server picker.", methods=("GET",), hidden=True
    )
    async def dashboard_discord(self, **kwargs: object) -> dict[str, object]:
        """The single all-guild office this page once served is now one
        office per guild; send old links to the picker."""

        del kwargs
        return self._assets.redirect_response(PICKER_PATH)

    @dashboard_page(name="editor", description="Agent editor CCTV.", methods=("GET",))
    async def dashboard_editor(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        error = await self._ensure_editor_page()
        return (
            self._assets.unavailable_response(error)
            if error is not None
            else self._assets.page_response(EDITOR_WS_PATH, identify=False)
        )

    def _picker_entries(self) -> list[PickerEntry]:
        entries = []
        for guild_id in self._offices.guild_ids():
            guild = self.bot.get_guild(guild_id)
            office = self.office(guild_id)
            if guild is None or office is None:
                continue
            icon = getattr(guild, "icon", None)
            entries.append(
                PickerEntry(
                    guild_id=guild_id,
                    name=str(guild.name),
                    icon_url=str(icon.url) if icon is not None else None,
                    headcount=office.office.headcount(),
                )
            )
        return sorted(entries, key=lambda entry: (-entry.headcount, entry.name.casefold()))

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


__all__ = [
    "DASHBOARD_COG_NAME",
    "DashboardMixin",
    "dashboard_cog_loaded",
    "dashboard_page",
    "parse_guild_id",
]
