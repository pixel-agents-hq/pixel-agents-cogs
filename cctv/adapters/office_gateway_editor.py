"""The editor pipeline's WebSocket application behavior -- bootstrap and
whole-office layout/seat saves, with no editor-authorization concept at
all. Ported from architect's former `adapters/office_gateway.py` (now
retired there, see docs/cctv-design.md); unlike that former version,
seats are real here (backed by pixelagents' facade, not a
`NullSeatRepository`) -- an editor-page avatar assignment now survives a
restart, same as the Discord page's always has.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from aiohttp import web

from corridor.domain import OfficeStateChanged
from pixelagents.application.office import DEFAULT_PALETTE_COUNT
from pixelagents.infrastructure.pixel_agents_adapter import decode

from .cog_base import CogBase

log = logging.getLogger("red.cctv")


class OfficeGatewayEditorMixin(CogBase):
    """Requires `self._pixelagents`, `self._editor_office_service`,
    `self._editor_client_hub`, `self._webview_assets`, `self._style_loader`
    (all provided by `CogBase`)."""

    async def cog_load(self) -> None:
        await super().cog_load()
        # The editor page's one live-update path -- see
        # OfficeGatewayDiscordMixin's identically-shaped hook for the
        # Discord page. This is also what retires painter's former
        # bot.get_cog("Architect")/notify_shared_layout_changed() hack
        # (docs/cctv-design.md §1.5/§3.3): any writer's set_editor_layout
        # call now reaches every connected editor-page socket for free.
        await self._pixelagents.office_state().watch(
            "editor", self._on_editor_state_changed, owner="Cctv"
        )

    async def _on_editor_state_changed(self, event: OfficeStateChanged) -> None:
        await self._editor_client_hub.broadcast(
            {"type": "layoutLoaded", "layout": event.state.layout}
        )

    async def _on_webview_ready_editor(self, socket: web.WebSocketResponse) -> None:
        state = await self._pixelagents.office_state().read("editor")
        messages = self._editor_office_service.bootstrap_messages(
            assets=self._webview_assets.assets, seats=state.seats, layout=state.layout
        )
        for message in messages:
            await self._editor_client_hub.send_to(socket, message)

    async def _on_save_layout_editor(self, raw: dict[str, object]) -> None:
        """A whole-office replace from the browser's own drag-and-drop
        editor -- decode validates structural shape (missing/malformed
        fields raise and are dropped here, matching the "invalid payload
        never persists, never crashes the connection" contract
        architect's own former WebSocket server already guaranteed);
        re-encoding immediately after decoding also normalizes the
        payload through the same codec every other writer uses."""

        styles = self._style_loader.styles()
        try:
            office = decode(raw, styles)
        except Exception:
            log.warning(
                "cctv: dropped a structurally invalid editor saveLayout payload", exc_info=True
            )
            return
        await self._pixelagents.office_state().set_editor_layout(office, styles)

    async def _on_save_seats_editor(self, incoming: Mapping[str, object]) -> None:
        characters = self._webview_assets.assets.get("characters")
        palette_count = max(
            len(characters) if isinstance(characters, (list, tuple)) else 0, DEFAULT_PALETTE_COUNT
        )
        facade = self._pixelagents.office_state()
        for agent_id, patch in incoming.items():
            if not isinstance(patch, Mapping):
                continue
            await facade.apply_seat_patch(
                "editor", str(agent_id), patch, palette_count=palette_count
            )


__all__ = ["OfficeGatewayEditorMixin"]
