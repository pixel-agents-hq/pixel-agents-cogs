"""Office protocol, layout, and editor-authorization Cog adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import discord
from aiohttp import web

from ..application.office import DEFAULT_PALETTE_COUNT
from ..contracts.websocket import (
    ClientMessage,
    ImportLayoutMessage,
    RequestDiagnosticsMessage,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    WebviewReadyMessage,
)
from .cog_base import PixelAgentsBase, log


class OfficeGatewayMixin(PixelAgentsBase):
    """Bridge the WebSocket protocol and auth checks to application services."""

    def _agent_id(self, user_id: int) -> int:
        return self._office_service.agent_id(user_id)

    def _detect_collision(self, user_id: int) -> None:
        self._office_service.detect_collision(user_id)

    def _tracked_user_ids(self) -> list[int]:
        return self._office_service.tracked_user_ids()

    def _existing_agents_message(
        self, seats: Mapping[str, Mapping[str, object]]
    ) -> dict[str, object]:
        return self._office_service.existing_agents_message(seats)

    async def _send_existing_agents(self) -> None:
        await self._office_service.send_existing_agents()

    def _seat_meta(
        self, agent_id: int, seats: Mapping[str, Mapping[str, object]] | None
    ) -> dict[str, object]:
        return self._office_service.seat_meta(agent_id, seats)

    async def _assign_palette(self, agent_id: int) -> tuple[int, int]:
        return await self._office_service.assign_palette(agent_id)

    def _default_layout(self) -> dict[str, Any] | None:
        return self._webview_assets.default_layout()

    async def _current_layout(self) -> dict[str, Any] | None:
        return await self._settings_repository.layout() or self._default_layout()

    @staticmethod
    def _validate_layout(layout: object) -> bool:
        if not isinstance(layout, dict) or layout.get("version") != 1:
            return False
        cols = layout.get("cols")
        rows = layout.get("rows")
        tiles = layout.get("tiles")
        furniture = layout.get("furniture")
        if not isinstance(cols, int) or cols <= 0:
            return False
        if not isinstance(rows, int) or rows <= 0:
            return False
        if not isinstance(tiles, list) or len(tiles) != cols * rows:
            return False
        if not isinstance(furniture, list):
            return False
        tile_colors = layout.get("tileColors")
        return tile_colors is None or (
            isinstance(tile_colors, list) and len(tile_colors) == cols * rows
        )

    async def _start_server(self) -> None:
        host = await self._settings_repository.ws_host()
        port = await self._settings_repository.ws_port()
        await self._websocket_server.start(host, port)

    def _health_snapshot(self) -> dict[str, object]:
        return {
            "status": "ok",
            "clients": self._client_hub.client_count,
            "agents": len(self._tracked_user_ids()),
            "assets": sorted(self._assets),
        }

    async def _handle_health(self, request: web.Request) -> web.Response:
        return await self._websocket_server.handle_health(request)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        return await self._websocket_server.handle_ws(request)

    async def _authorize_office_client(self, user_id: int) -> bool:
        return await self._check_auth(user_id)

    async def _handle_client_message(self, socket: web.WebSocketResponse, data: object) -> None:
        await self._websocket_server.handle_payload(socket, data)

    async def _handle_application_message(
        self, socket: web.WebSocketResponse, message: ClientMessage
    ) -> None:
        if isinstance(message, WebviewReadyMessage):
            await self._send_bootstrap(socket)
        elif isinstance(message, SaveLayoutMessage):
            layout = message.layout.to_raw()
            await self._settings_repository.set_layout(layout)
            await self._client_hub.broadcast(
                {"type": "layoutLoaded", "layout": layout}, exclude=socket
            )
        elif isinstance(message, SaveAgentSeatsMessage):
            incoming = {
                agent_id: patch.model_dump(by_alias=True, exclude_none=True)
                for agent_id, patch in message.seats.items()
            }
            await self._save_seats(incoming)
        elif isinstance(message, RequestDiagnosticsMessage):
            await self._send_to(socket, {"type": "agentDiagnostics", "agents": []})
        elif isinstance(message, ImportLayoutMessage):
            return

    async def _save_seats(self, incoming: Mapping[str, object]) -> None:
        characters = self._assets.get("characters")
        palette_count = max(
            len(characters) if isinstance(characters, (list, tuple)) else 0,
            DEFAULT_PALETTE_COUNT,
        )

        def merge(seats: dict[str, dict[str, Any]]) -> None:
            for agent_id, value in incoming.items():
                if not isinstance(value, dict):
                    continue
                record = dict(seats.get(str(agent_id)) or {})
                palette = value.get("palette")
                hue_shift = value.get("hueShift")
                seat_id = value.get("seatId")
                if isinstance(palette, int) and 0 <= palette < palette_count:
                    record["palette"] = palette
                if isinstance(hue_shift, int) and 0 <= hue_shift <= 360:
                    record["hueShift"] = hue_shift
                if isinstance(seat_id, str):
                    record["seatId"] = seat_id
                seats[str(agent_id)] = record

        await self._settings_repository.mutate_seats(merge)

    async def _send_bootstrap(self, socket: web.WebSocketResponse) -> None:
        seats = await self._settings_repository.seats()
        messages = self._office_service.bootstrap_messages(
            assets=self._assets,
            seats=seats,
            layout=await self._current_layout(),
        )
        for message in messages:
            await self._send_to(socket, message)

    async def _check_auth(self, user_id: int) -> bool:
        return await self._can_edit_layout_user(user_id)

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
                "floorplan: failed to fetch member %d in guild %s for auth check: %s",
                user_id,
                getattr(guild, "id", "unknown"),
                exc,
            )
            return None
        return fetched

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        owner_candidate = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner_candidate):
            return True
        for guild in self.bot.guilds:
            if not await self._settings_repository.guild_enabled(guild):
                continue
            member = await self._get_auth_member(guild, user_id)
            if member is None:
                continue
            if await self._corridor.capabilities_satisfy(member, "keyholder"):
                return True
        return False
