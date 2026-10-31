"""Validated office-state facade over Corridor's opaque persistence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol, TypeVar

from pydantic import ValidationError

from corridor.domain import OfficeRef, OfficeState, OfficeStateChanged, OfficeStateKind, SeatRecords

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
    async def office_state(self, ref: OfficeRef) -> OfficeState | None: ...

    async def initialize_office_state(
        self, ref: OfficeRef, layout: dict[str, Any]
    ) -> OfficeState: ...

    async def set_office_layout(self, ref: OfficeRef, layout: dict[str, Any]) -> OfficeState: ...

    async def set_office_seats(self, ref: OfficeRef, seats: SeatRecords) -> OfficeState: ...

    async def mutate_office_seats(
        self,
        ref: OfficeRef,
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
        repeat = record.get("repeat")
        hue_shift = record.get("hueShift")
        seat_id = record.get("seatId")
        if palette is not None and (
            type(palette) is not int or not 0 <= palette < DEFAULT_PALETTE_COUNT
        ):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid palette")
        if repeat is not None and (type(repeat) is not int or repeat < 0):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid repeat")
        # Upstream's older clothes hue, still readable (see `seat_repeat`).
        if hue_shift is not None and (type(hue_shift) is not int or not 0 <= hue_shift <= 360):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid hueShift")
        if seat_id is not None and not isinstance(seat_id, str):
            raise OfficeStateValidationError(f"seat {agent_id!r} has an invalid seatId")
        validated[agent_id] = record
    return validated


class OfficeStateFacade:
    """The only schema-aware read/write surface for every office aggregate."""

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
            guild_id=state.guild_id,
        )

    async def state(self, ref: OfficeRef) -> OfficeState:
        state = await self._corridor.office_state(ref)
        if state is None:
            default = self._default_layout()
            if default is None:
                raise OfficeStateUnavailableError(
                    f"the {ref.describe()} office is absent and the bundled default is unavailable"
                )
            layout = self.validate_layout(ref.kind, default)
            state = await self._corridor.initialize_office_state(ref, layout)
        return self.validate_state(state)

    async def set_layout(self, ref: OfficeRef, raw: Mapping[str, object]) -> OfficeState:
        await self.state(ref)
        layout = self.validate_layout(ref.kind, raw)
        return self.validate_state(await self._corridor.set_office_layout(ref, layout))

    async def set_seats(self, ref: OfficeRef, seats: object) -> OfficeState:
        await self.state(ref)
        validated = validate_seat_records(seats)
        return self.validate_state(await self._corridor.set_office_seats(ref, validated))

    async def mutate_seats(
        self,
        ref: OfficeRef,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        await self.state(ref)

        def mutate_and_validate(seats: SeatRecords) -> MutationResult:
            result = mutation(seats)
            validated = validate_seat_records(seats)
            seats.clear()
            seats.update(validated)
            return result

        state, result = await self._corridor.mutate_office_seats(ref, mutate_and_validate)
        return self.validate_state(state), result

    async def merge_seat_patch(
        self,
        ref: OfficeRef,
        agent_id: str,
        patch: Mapping[str, object],
        *,
        palette_count: int = DEFAULT_PALETTE_COUNT,
    ) -> OfficeState:
        def merge(seats: SeatRecords) -> None:
            merge_seat_patch(seats, agent_id, palette_count, patch)

        state, _ = await self.mutate_seats(ref, merge)
        return state

    async def watch(
        self,
        kind: OfficeStateKind,
        handler: OfficeStateHandler,
        *,
        owner: str,
    ) -> OfficeState | None:
        """Subscribe to every office of `kind`. Returns the editor office's
        current state (initialized if absent); a Discord office is one per
        guild, so none is returned -- read each with `state` after
        subscribing."""

        async def validate_event(event: OfficeStateChanged) -> None:
            await handler(OfficeStateChanged(state=self.validate_state(event.state)))

        snapshot = await self._corridor.watch_office_state(kind, validate_event, owner=owner)
        if kind != OfficeStateKind.EDITOR:
            return None
        if snapshot is None:
            return await self.state(OfficeRef.editor())
        return self.validate_state(snapshot)


class OfficeStateSeatRepository:
    """Adapt one aggregate to ``OfficeService``'s seat repository protocol."""

    def __init__(self, facade: OfficeStateFacade, ref: OfficeRef) -> None:
        self._facade = facade
        self._ref = ref

    async def seats(self) -> SeatRecords:
        return (await self._facade.state(self._ref)).seats

    async def mutate_seats(
        self, mutation: Callable[[SeatRecords], MutationResult]
    ) -> MutationResult:
        _, result = await self._facade.mutate_seats(self._ref, mutation)
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
