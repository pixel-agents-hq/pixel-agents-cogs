"""Thin CCTV Cog composition."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.commands import CommandsMixin
from .adapters.dashboard import DashboardMixin, dashboard_page
from .adapters.replies import ReplyMixin

__all__ = ["CCTV", "dashboard_page"]


class CCTV(DashboardMixin, CommandsMixin, ReplyMixin, commands.Cog):
    """Serve and project the Discord and editor office pages."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """For every requester type: revoke the user's unspent Dashboard
        and Activity tickets, and forget which Activity sign-in tokens are
        theirs. CCTV's own Config is per-guild display settings only; the
        user's character and seats live in corridor's office-state store and
        are deleted once, by pixelagents (the schema-aware layer CCTV writes
        them through). Who is online and what they last said is live
        presence, held in memory only and never persisted."""

        del requester
        self._tickets.forget_user(user_id)
        self._oauth.forget_user(user_id)
