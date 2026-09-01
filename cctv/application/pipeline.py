"""One isolated live projection for a CCTV page."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from aiohttp import web

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind, SeatRecords
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
            previous = self._state
            self._state = state
            self.error = None
            # A revision bump doesn't mean the layout changed -- most bumps
            # are seat-only (agent spawned/despawned, palette assigned) from
            # ambient presence activity, completely unrelated to what's on
            # screen in the editor. Broadcasting layoutLoaded on every one of
            # those raced against the editor's own in-progress edits: the
            # browser only guards against an incoming layoutLoaded while it
            # has unsaved local changes, and that guard drops the instant the
            # user hits Save (before this save's own write even lands) --
            # a same-instant seat-only broadcast would slip through and
            # revert the layout the user just placed. Only send layoutLoaded
            # when the layout itself actually changed.
            if previous is None or previous.layout != state.layout:
                await self.clients.broadcast({"type": "layoutLoaded", "layout": state.layout})
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
            await self._pixelagents.set_office_layout(self.kind, message.layout.to_raw())
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


__all__ = ["Authorize", "CctvPipeline", "PixelAgentsStateGateway", "SeatRepository"]
