"""Validated office-state facade over Corridor's opaque persistence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol, TypeVar

from pydantic import ValidationError

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind, SeatRecords

from ..contracts.layout import OfficeLayout, RawOfficeLayout
from ..infrastructure.furniture_styles import FurnitureStyleManifest
from ..infrastructure.pixel_agents_adapter import decode, encode
from .office import DEFAULT_PALETTE_COUNT, merge_seat_patch

MutationResult = TypeVar("MutationResult")
OfficeStateHandler = Callable[[OfficeStateChanged], Awaitable[None]]


class OfficeStateValidationError(ValueError):
    """Persisted or proposed office state does not satisfy its page schema."""


class OfficeStateUnavailableError(RuntimeError):
    """A missing aggregate cannot be initialized from the built webview."""


class CorridorOfficeStateGateway(Protocol):
    async def office_state(self, kind: OfficeStateKind) -> OfficeState | None: ...

    async def initialize_office_state(
        self, kind: OfficeStateKind, layout: dict[str, Any]
    ) -> OfficeState: ...

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: dict[str, Any]
    ) -> OfficeState: ...

    async def set_office_seats(self, kind: OfficeStateKind, seats: SeatRecords) -> OfficeState: ...

    async def mutate_office_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]: ...

    async def watch_office_state(
        self,
        kind: OfficeStateKind,
        handler: OfficeStateHandler,
        *,
        owner: str,
    ) -> OfficeState | None: ...


DefaultLayoutProvider = Callable[[], dict[str, Any] | None]
StyleProvider = Callable[[], FurnitureStyleManifest]


def validate_seat_records(seats: object) -> SeatRecords:
    if not isinstance(seats, dict):
        raise OfficeStateValidationError("seats must be an object")
    validated: SeatRecords = {}
    for agent_id, value in seats.items():
        if not isinstance(agent_id, str) or not isinstance(value, dict):
            raise OfficeStateValidationError("seat entries must map string IDs to objects")
        record = deepcopy(value)
        palette = record.get("palette")
        hue_shift = record.get("hueShift")
        seat_id = record.get("seatId")
        if palette is not None and (
            type(palette) is not int or not 0 <= palette < DEFAULT_PALETTE_COUNT
        ):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid palette")
        if hue_shift is not None and (type(hue_shift) is not int or not 0 <= hue_shift <= 360):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid hueShift")
        if seat_id is not None and not isinstance(seat_id, str):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid seatId")
        validated[agent_id] = record
    return validated


class OfficeStateFacade:
    """The only schema-aware read/write surface for both office aggregates."""

    def __init__(
        self,
        corridor: CorridorOfficeStateGateway,
        default_layout: DefaultLayoutProvider,
        styles: StyleProvider,
    ) -> None:
        self._corridor = corridor
        self._default_layout = default_layout
        self._styles = styles

    def validate_layout(self, kind: OfficeStateKind, raw: Mapping[str, object]) -> RawOfficeLayout:
        try:
            layout = OfficeLayout.model_validate(raw).to_raw()
            if kind == OfficeStateKind.EDITOR:
                styles = self._styles()
                encode(decode(layout, styles), styles)
            return layout
        except (IndexError, KeyError, TypeError, ValidationError, ValueError) as exc:
            raise OfficeStateValidationError(f"invalid {kind.value} office layout: {exc}") from exc

    def validate_state(self, state: OfficeState) -> OfficeState:
        layout = self.validate_layout(state.kind, state.layout)
        seats = validate_seat_records(state.seats)
        if type(state.revision) is not int or state.revision < 1:
            raise OfficeStateValidationError(f"invalid {state.kind.value} office revision")
        return OfficeState(
            kind=state.kind,
            layout=deepcopy(layout),
            seats=seats,
            revision=state.revision,
        )

    async def state(self, kind: OfficeStateKind) -> OfficeState:
        state = await self._corridor.office_state(kind)
        if state is None:
            default = self._default_layout()
            if default is None:
                raise OfficeStateUnavailableError(
                    f"the {kind.value} office is absent and the bundled default is unavailable"
                )
            layout = self.validate_layout(kind, default)
            state = await self._corridor.initialize_office_state(kind, layout)
        return self.validate_state(state)

    async def set_layout(self, kind: OfficeStateKind, raw: Mapping[str, object]) -> OfficeState:
        await self.state(kind)
        layout = self.validate_layout(kind, raw)
        return self.validate_state(await self._corridor.set_office_layout(kind, layout))

    async def set_seats(self, kind: OfficeStateKind, seats: object) -> OfficeState:
        await self.state(kind)
        validated = validate_seat_records(seats)
        return self.validate_state(await self._corridor.set_office_seats(kind, validated))

    async def mutate_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        await self.state(kind)

        def mutate_and_validate(seats: SeatRecords) -> MutationResult:
            result = mutation(seats)
            validated = validate_seat_records(seats)
            seats.clear()
            seats.update(validated)
            return result

        state, result = await self._corridor.mutate_office_seats(kind, mutate_and_validate)
        return self.validate_state(state), result

    async def merge_seat_patch(
        self,
        kind: OfficeStateKind,
        agent_id: str,
        patch: Mapping[str, object],
        *,
        palette_count: int = DEFAULT_PALETTE_COUNT,
    ) -> OfficeState:
        def merge(seats: SeatRecords) -> None:
            merge_seat_patch(seats, agent_id, palette_count, patch)

        state, _ = await self.mutate_seats(kind, merge)
        return state

    async def watch(
        self,
        kind: OfficeStateKind,
        handler: OfficeStateHandler,
        *,
        owner: str,
    ) -> OfficeState:
        async def validate_event(event: OfficeStateChanged) -> None:
            await handler(OfficeStateChanged(state=self.validate_state(event.state)))

        snapshot = await self._corridor.watch_office_state(kind, validate_event, owner=owner)
        if snapshot is None:
            return await self.state(kind)
        return self.validate_state(snapshot)


class OfficeStateSeatRepository:
    """Adapt one aggregate to ``OfficeService``'s seat repository protocol."""

    def __init__(self, facade: OfficeStateFacade, kind: OfficeStateKind) -> None:
        self._facade = facade
        self._kind = kind

    async def seats(self) -> SeatRecords:
        return (await self._facade.state(self._kind)).seats

    async def mutate_seats(
        self, mutation: Callable[[SeatRecords], MutationResult]
    ) -> MutationResult:
        _, result = await self._facade.mutate_seats(self._kind, mutation)
        return result


__all__ = [
    "CorridorOfficeStateGateway",
    "DefaultLayoutProvider",
    "OfficeStateFacade",
    "OfficeStateHandler",
    "OfficeStateSeatRepository",
    "OfficeStateUnavailableError",
    "OfficeStateValidationError",
    "StyleProvider",
    "validate_seat_records",
]
