"""CCTV's Dashboard pages: the server picker, each guild's office, and the
editor office, all sharing one static Pixel Agents bundle."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any, TypeVar

from redbot.core import commands

from ..domain import OfficeAccess
from ..infrastructure import (
    EDITOR_WS_PATH,
    PICKER_PATH,
    PickerEntry,
    office_page_path,
    office_ws_path,
    picker_response,
    private_page_path,
    session_path,
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
        """The server picker: every guild whose office is public."""

        del kwargs
        return picker_response(await self._picker_entries(user_id=None))

    @dashboard_page(
        name="mine", description="Your servers' Pixel Agents offices.", methods=("GET",)
    )
    async def dashboard_mine(self, user_id: int, **kwargs: object) -> dict[str, object]:
        """A signed-in user's picker: public offices, plus every unlisted or
        private office of a server they're a member of (all, for the bot
        owner)."""

        del kwargs
        return picker_response(await self._picker_entries(user_id=user_id), mine=True)

    @dashboard_page(name="office", description="A server's Pixel Agents office.", methods=("GET",))
    async def dashboard_office(self, guild: str = "", **kwargs: object) -> dict[str, object]:
        del kwargs
        guild_id = parse_guild_id(guild)
        if guild_id is None:
            return self._assets.not_found_response()
        office = self.office(guild_id)
        if office is None or office.private:
            # Every id without an office anyone may watch -- private, CCTV
            # disabled, unknown, not a guild -- answers the same: on to the
            # members-only page, where the Dashboard asks for a login.
            return self._assets.redirect_response(private_page_path(guild_id))
        error = await self._ensure_office_page(office)
        return (
            self._assets.unavailable_response(error)
            if error is not None
            else self._assets.page_response(
                office_ws_path(guild_id), ticket_path=session_path(guild_id)
            )
        )

    @dashboard_page(
        name="private",
        description="A server's members-only Pixel Agents office.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_private(
        self, user_id: int, guild_id: int, **kwargs: object
    ) -> dict[str, object]:
        """A private office's page. The Dashboard only calls this for a
        signed-in user who is a member of `guild_id` (or the bot owner); the
        office's socket checks membership again before sending anything."""

        del kwargs, user_id
        office = self.office(guild_id)
        if office is None:
            return self._assets.not_found_response()
        if not office.private:
            return self._assets.redirect_response(office_page_path(guild_id))
        error = await self._ensure_office_page(office)
        return (
            self._assets.unavailable_response(error)
            if error is not None
            else self._assets.page_response(
                office_ws_path(guild_id), ticket_path=session_path(guild_id), private=True
            )
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
            else self._assets.page_response(EDITOR_WS_PATH)
        )

    async def _picker_entries(self, *, user_id: int | None) -> list[PickerEntry]:
        """Public offices for anyone; with `user_id`, also the unlisted and
        private offices of servers that user is a member of."""

        entries = []
        for guild_id in self._offices.guild_ids():
            guild = self.bot.get_guild(guild_id)
            office = self.office(guild_id)
            if guild is None or office is None:
                continue
            access = (await self._settings.guild_settings(guild_id)).access
            if access is not OfficeAccess.PUBLIC and (
                user_id is None or not await self._is_member(guild_id, user_id)
            ):
                continue
            icon = getattr(guild, "icon", None)
            entries.append(
                PickerEntry(
                    guild_id=guild_id,
                    name=str(guild.name),
                    icon_url=str(icon.url) if icon is not None else None,
                    headcount=office.office.headcount(),
                    private=office.private,
                )
            )
        return sorted(entries, key=lambda entry: (-entry.headcount, entry.name.casefold()))

    @dashboard_page(
        name="session",
        description="CCTV office session.",
        methods=("GET",),
        hidden=True,
    )
    async def dashboard_session(
        self, user_id: int, guild: str = "", **kwargs: object
    ) -> dict[str, object]:
        """A single-use, one-minute ticket proving the signed-in user to one
        guild office's socket. Only for an open office the user may watch:
        anyone for a public or unlisted office, members (and the bot owner)
        for a private one."""

        del kwargs
        guild_id = parse_guild_id(guild)
        office = self.office(guild_id) if guild_id is not None else None
        allowed = (
            guild_id is not None
            and office is not None
            and (not office.private or await self._is_member(guild_id, user_id))
        )
        ticket = self._tickets.mint(user_id, guild_id) if allowed and guild_id else None
        body = json.dumps({"ticket": ticket}).encode()
        return {
            "status": 0,
            "raw_response": {
                "status": 200 if ticket is not None else 403,
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
