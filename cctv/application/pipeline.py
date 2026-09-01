"""One isolated live projection for a CCTV page."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from aiohttp import web

from corridor.domain import (
    OfficeState,
    OfficeStateChanged,
    OfficeStateKind,
    RawLayout,
    SeatRecords,
)
from pixelagents.application import DEFAULT_PALETTE_COUNT, OfficeService, PresenceService
from pixelagents.application.office import merge_seat_patch
from pixelagents.domain import AgentSnapshot, GenuineAgentKey, OfficeIdentity

from ..contracts import (
    ClientMessage,
    ImportLayoutMessage,
    RequestDiagnosticsMessage,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    WebviewReadyMessage,
)
from ..infrastructure.client_hub import ClientHub

Authorize = Callable[[int], Awaitable[bool]]

# A layout save fires once per placed tile while a user drags/paints in the
# editor. Writing+publishing corridor's shared OfficeState on every single
# one made the editor feel laggy (full aggregate read-modify-write plus a
# synchronous cross-subscriber publish per tile) -- coalesce rapid saves
# into one write, LAYOUT_SAVE_DEBOUNCE_SECONDS after the last one, instead.
# Safe because nothing subscribes to live per-tile layout changes: architect
# and painter only *pull* office_state() when a tool call runs, so a short
# staleness window mid-drag costs them nothing.
LAYOUT_SAVE_DEBOUNCE_SECONDS = 0.2


class PixelAgentsStateGateway(Protocol):
    async def office_state(self, kind: OfficeStateKind) -> OfficeState: ...

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: Mapping[str, object]
    ) -> OfficeState: ...

    async def mutate_office_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], None],
    ) -> tuple[OfficeState, None]: ...


class SeatRepository(Protocol):
    async def seats(self) -> SeatRecords: ...
    async def mutate_seats(self, mutation: Callable[[SeatRecords], Any]) -> Any: ...


class CctvPipeline:
    def __init__(
        self,
        page: str,
        kind: OfficeStateKind,
        pixelagents: PixelAgentsStateGateway,
        seat_repository: SeatRepository,
        assets: dict[str, object],
        authorize: Authorize,
        *,
        open_editor: bool,
        logger: logging.Logger | None = None,
    ) -> None:
        self.page = page
        self.kind = kind
        self.open_editor = open_editor
        self.clients = ClientHub(page, logger=logger)
        self._pixelagents = pixelagents
        self._assets = assets
        self._authorize = authorize
        self._log = logger or logging.getLogger(__name__)
        self._lock = asyncio.Lock()
        self._state: OfficeState | None = None
        self.error: str | None = None
        self._pending_layout: RawLayout | None = None
        self._pending_layout_socket: web.WebSocketResponse | None = None
        self._layout_flush_task: asyncio.Task[None] | None = None
        self._last_layout_writer: web.WebSocketResponse | None = None
        self.presence = PresenceService(self._send)
        self.office = OfficeService(
            seat_repository,
            self._send,
            presence=self.presence,
            logger=self._log,
        )

    @property
    def revision(self) -> int | None:
        return self._state.revision if self._state is not None else None

    async def authorize(self, user_id: int) -> bool:
        return self.open_editor or await self._authorize(user_id)

    async def _send(self, message: Mapping[str, object]) -> None:
        await self.clients.broadcast(message)

    async def seed_state(self, state: OfficeState) -> None:
        if state.kind != self.kind:
            raise ValueError(f"{self.page} pipeline received {state.kind.value} state")
        async with self._lock:
            if self._state is None or state.revision >= self._state.revision:
                self._state = state
                self.error = None

    async def state_changed(self, event: OfficeStateChanged) -> None:
        state = event.state
        if state.kind != self.kind:
            return
        async with self._lock:
            if self._state is not None and state.revision <= self._state.revision:
                return
            self._state = state
            self.error = None
            # Exclude whichever socket's own save just produced this
            # revision -- it already has this layout locally and doesn't
            # need it echoed back. One-shot: a later revision from anywhere
            # else (another client, or architect/painter mutating layout)
            # goes to every connected client, this one included.
            writer, self._last_layout_writer = self._last_layout_writer, None
            await self.clients.broadcast(
                {"type": "layoutLoaded", "layout": state.layout}, exclude=writer
            )
            await self.clients.broadcast(self.office.existing_agents_message(state.seats))

    async def bootstrap(self, socket: web.WebSocketResponse) -> None:
        fresh = await self._pixelagents.office_state(self.kind)
        async with self._lock:
            if self._state is None or fresh.revision > self._state.revision:
                self._state = fresh
            state = self._state
            assert state is not None
            messages = self.office.bootstrap_messages(
                assets=self._assets,
                seats=state.seats,
                layout=state.layout,
            )
            for message in messages:
                await self.clients.send_to(socket, message)

    async def handle_message(self, socket: web.WebSocketResponse, message: ClientMessage) -> None:
        if isinstance(message, WebviewReadyMessage):
            await self.bootstrap(socket)
        elif isinstance(message, SaveLayoutMessage):
            self._queue_layout_save(socket, message.layout.to_raw())
        elif isinstance(message, SaveAgentSeatsMessage):
            incoming = {
                agent_id: patch.model_dump(by_alias=True, exclude_none=True)
                for agent_id, patch in message.seats.items()
            }
            characters = self._assets.get("characters")
            asset_palette_count = len(characters) if isinstance(characters, (list, tuple)) else 0
            palette_count = max(
                asset_palette_count,
                DEFAULT_PALETTE_COUNT,
            )

            def merge(seats: SeatRecords) -> None:
                for agent_id, patch in incoming.items():
                    merge_seat_patch(seats, agent_id, palette_count, patch)

            await self._pixelagents.mutate_office_seats(self.kind, merge)
        elif isinstance(message, RequestDiagnosticsMessage):
            await self.clients.send_to(
                socket,
                {
                    "type": "agentDiagnostics",
                    "agents": [],
                    "revision": self.revision,
                },
            )
        elif isinstance(message, ImportLayoutMessage):
            return

    def _queue_layout_save(self, socket: web.WebSocketResponse, layout: RawLayout) -> None:
        """Remember `layout` as the latest unsaved edit and (re)start the
        debounce timer -- a burst of saves from one drag collapses to a
        single write of the final layout, LAYOUT_SAVE_DEBOUNCE_SECONDS
        after the last message in the burst."""

        self._pending_layout = layout
        self._pending_layout_socket = socket
        if self._layout_flush_task is not None:
            self._layout_flush_task.cancel()
        self._layout_flush_task = asyncio.create_task(
            self._debounced_flush(), name=f"cctv-{self.page}-layout-flush"
        )

    async def _debounced_flush(self) -> None:
        try:
            await asyncio.sleep(LAYOUT_SAVE_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        await self.flush_pending_layout()

    async def flush_pending_layout(self) -> None:
        """Persist the most recently queued layout edit, if any. Idempotent
        -- a no-op once nothing is pending. Called by the debounce timer and
        also explicitly on cog_unload/close, so an edit still inside the
        debounce window isn't lost to a reload or shutdown."""

        if self._pending_layout is None:
            return
        layout = self._pending_layout
        socket = self._pending_layout_socket
        self._pending_layout = None
        self._pending_layout_socket = None
        self._last_layout_writer = socket
        await self._pixelagents.set_office_layout(self.kind, layout)

    async def close(self) -> None:
        """Cancel any pending debounce timer and flush the last unsaved
        layout edit synchronously. Call before tearing down the pipeline
        (cog_unload) so a save made just before shutdown isn't dropped."""

        if self._layout_flush_task is not None:
            self._layout_flush_task.cancel()
            self._layout_flush_task = None
        await self.flush_pending_layout()

    async def reconcile_discord(
        self,
        snapshot: AgentSnapshot,
        *,
        include_bots: bool,
        rich_presence_enabled: bool,
    ) -> None:
        await self.office.reconcile(
            snapshot,
            include_bots=include_bots,
            rich_presence_enabled=rich_presence_enabled,
        )

    async def reconcile_genuine(
        self, identity: GenuineAgentKey, display_name: str, status: str
    ) -> None:
        await self.office.reconcile_genuine_agent(identity, display_name, status)

    def is_tracked(self, identity: OfficeIdentity) -> bool:
        return self.office.is_tracked(identity)

    def health(self) -> dict[str, object]:
        return {
            "clients": self.clients.client_count,
            "editors": self.clients.editor_count,
            "agents": len(self.office.tracked_user_ids()),
            "revision": self.revision,
            "error": self.error,
        }


__all__ = [
    "LAYOUT_SAVE_DEBOUNCE_SECONDS",
    "Authorize",
    "CctvPipeline",
    "PixelAgentsStateGateway",
    "SeatRepository",
]
