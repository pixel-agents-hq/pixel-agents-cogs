"""Bridges architect's own WebSocket transport to pixelagents' generic
office bootstrap protocol, and to `OfficeLayoutService` for saves.

The WebSocket server this bridges to (`infrastructure/websocket.py`) only
ever calls in here for two things: `webviewReady` (send the bootstrap
sequence) and `saveLayout` (persist a whole new layout the in-browser
editor produced). There is deliberately no editor-authorization concept --
see `infrastructure/websocket.py`'s module docstring for why architect's
layout, unlike floorplan's, is meant to be freely editable by anyone who
can reach the page.

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
    `self._client_hub`, `self._office_service`, `self._webview_assets`,
    `self._office_layout_service` (all provided by CogBase)."""

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

    async def _on_save_layout(self, raw_layout: dict[str, Any]) -> None:
        """Persist a whole-office payload from the in-browser editor.
        `OfficeLayoutService.replace_layout` already broadcasts the
        persisted `layoutLoaded` message to every connected client (the
        same `broadcast` callback every other mutation uses) on success.
        Deliberately does not catch here -- `infrastructure/websocket.py`'s
        `_handle_save_layout` is the one place that logs and drops a
        rejected/malformed save, matching floorplan's own single-layer
        `handle_message` convention rather than catching twice."""

        await self._office_layout_service.replace_layout(raw=raw_layout)


__all__ = ["OfficeGatewayMixin"]
