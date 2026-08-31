"""The Discord pipeline's WebSocket application behavior -- bootstrap,
layout/seat saves, and the ticket-gated editor-authorization policy.
Ported from floorplan's former `adapters/office_gateway.py` (now retired
there, see docs/cctv-design.md); `_can_edit_layout_user` is verbatim,
unchanged auth policy (docs/cctv-design.md §2.7's table: "bot owner or
keyholder in an enabled guild")."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

import discord
from aiohttp import web

from corridor.domain import OfficeStateChanged
from pixelagents.application.office import DEFAULT_PALETTE_COUNT
from pixelagents.application.office_state import InvalidDiscordLayoutError

from .cog_base import CogBase

log = logging.getLogger("red.cctv")


class OfficeGatewayDiscordMixin(CogBase):
    """Requires `self._pixelagents`, `self._discord_office_service`,
    `self._discord_client_hub`, `self._webview_assets`, `self._corridor`,
    `self._repository`, `self.bot` (all provided by `CogBase`)."""

    async def cog_load(self) -> None:
        await super().cog_load()
        # The one live-update path for the Discord page: corridor auto-
        # publishes OfficeStateChanged on every successful write (from
        # this pipeline's own saves, or from floorplan's Pixel Index
        # catalogue loads) -- cctv just re-broadcasts the resulting
        # layout to every connected socket, decoupled entirely from
        # whoever made the write (docs/cctv-design.md §2.2/§3.3).
        await self._pixelagents.office_state().watch(
            "discord", self._on_discord_state_changed, owner="Cctv"
        )

    async def _on_discord_state_changed(self, event: OfficeStateChanged) -> None:
        await self._discord_client_hub.broadcast(
            {"type": "layoutLoaded", "layout": event.state.layout}
        )

    async def _on_webview_ready_discord(self, socket: web.WebSocketResponse) -> None:
        """Never broadcast -- send the bootstrap sequence to only the one
        connecting socket, re-reading current state fresh so a client
        that connects long after `cog_load` never sees a stale snapshot
        from whenever cctv itself started (docs/cctv-design.md §2.4)."""

        state = await self._pixelagents.office_state().read("discord")
        messages = self._discord_office_service.bootstrap_messages(
            assets=self._webview_assets.assets, seats=state.seats, layout=state.layout
        )
        for message in messages:
            await self._discord_client_hub.send_to(socket, message)

    async def _on_save_layout_discord(self, raw: dict[str, object]) -> None:
        try:
            await self._pixelagents.office_state().set_discord_layout(raw)
        except InvalidDiscordLayoutError:
            log.warning("cctv: dropped an invalid Discord saveLayout payload")

    async def _on_save_seats_discord(self, incoming: Mapping[str, object]) -> None:
        characters = self._webview_assets.assets.get("characters")
        palette_count = max(
            len(characters) if isinstance(characters, (list, tuple)) else 0, DEFAULT_PALETTE_COUNT
        )
        facade = self._pixelagents.office_state()
        for agent_id, patch in incoming.items():
            if not isinstance(patch, Mapping):
                continue
            await facade.apply_seat_patch(
                "discord", str(agent_id), patch, palette_count=palette_count
            )

    async def _authorize_discord_client(self, user_id: int) -> bool:
        return await self._can_edit_layout_user(user_id)

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        owner_candidate = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner_candidate):
            return True
        for guild in self.bot.guilds:
            if not await self._repository.guild_enabled(guild.id):
                continue
            member = await self._get_auth_member(guild, user_id)
            if member is None:
                continue
            if await self._corridor.capabilities_satisfy(member, "keyholder"):
                return True
        return False

    async def _get_auth_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        fetch_member = getattr(guild, "fetch_member", None)
        if fetch_member is None:
            return None
        try:
            fetched: discord.Member = await fetch_member(user_id)
        except Exception as exc:
            log.debug(
                "cctv: failed to fetch member %d in guild %s for auth check: %s",
                user_id,
                getattr(guild, "id", "unknown"),
                exc,
            )
            return None
        return fetched


__all__ = ["OfficeGatewayDiscordMixin"]
