"""Bridges architect's own WebSocket transport to pixelagents' generic
office bootstrap protocol.

Read-only: architect never accepts `saveLayout`/`saveAgentSeats` messages
(there is no in-browser editor) -- the WebSocket server this bridges to
(`infrastructure/websocket.py`) only ever calls in here for one thing,
`webviewReady`, matching the single message this webview needs to answer
until the future layout-editing tools exist (see docs/architect-design.md).

`pixelagents.application.office.OfficeService.bootstrap_messages` is
reused directly, not duplicated: unlike `WebviewAssetProvider`/
`WebSocketServer`/`ClientHub` (floorplan package-internal transport code,
duplicated per docs/architect-design.md section 5), `OfficeService` is
pixelagents' own generic, framework-neutral application layer -- the
intended shared surface any consuming cog (floorplan already does this)
builds its bootstrap sequence from.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from .cog_base import CogBase


class OfficeGatewayMixin(CogBase):
    """Requires `self._repository`, `self._websocket_server`,
    `self._client_hub`, `self._office_service`, `self._webview_assets`
    (all provided by CogBase)."""

    async def _start_ws_server(self) -> bool:
        settings = await self._repository.global_settings()
        return await self._websocket_server.start(settings.ws_host, settings.ws_port)

    def _health_snapshot(self) -> dict[str, object]:
        return {
            "status": "ok",
            "clients": self._client_hub.client_count,
            "assets": sorted(self._webview_assets.assets),
        }

    async def _current_layout(self) -> dict[str, Any] | None:
        return await self._repository.layout()

    async def _on_webview_ready(self, socket: web.WebSocketResponse) -> None:
        """Send this one connecting client the full bootstrap sequence --
        never broadcast, matching floorplan's own `_send_bootstrap`."""

        messages = self._office_service.bootstrap_messages(
            assets=self._webview_assets.assets,
            seats={},
            layout=await self._current_layout(),
        )
        for message in messages:
            await self._client_hub.send_to(socket, message)


__all__ = ["OfficeGatewayMixin"]
