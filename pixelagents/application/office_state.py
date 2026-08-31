"""The one validated office-state facade every consumer (cctv, floorplan,
architect, painter) goes through. Corridor persists `OfficeState` as
opaque JSON-compatible data (`corridor/domain/office_state.py`) and knows
nothing about its schema; this module is where the Pixel Agents wire
schema and the Semantic IR codec actually get enforced, and where a
still-blank aggregate gets seeded with the real bundled default the
first time anything touches it. See docs/cctv-design.md §2.6.

No consumer bypasses this facade for office-state reads/writes -- not
even pixelagents' own commands, once any exist.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, TypeVar

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind

from ..domain.office_ir import Office
from ..infrastructure.furniture_styles import FurnitureStyleManifest
from ..infrastructure.pixel_agents_adapter import decode, encode
from .office import DEFAULT_PALETTE_COUNT, SeatRecords, merge_seat_patch

log = logging.getLogger("red.d_cogs.pixelagents")

MutationResult = TypeVar("MutationResult")


class OfficeStateBackend(Protocol):
    """The narrow slice of corridor's public `CogBase` surface this facade
    needs -- satisfied by a real corridor Cog (`bot.get_cog("Corridor")`)
    or a test fake. Deliberately a `Protocol`, not an import of corridor's
    own `CogBase`: this module has no reason to depend on corridor's full
    type, only the five methods it actually calls."""

    async def read_office_state(self, kind: OfficeStateKind) -> OfficeState: ...

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: dict[str, object]
    ) -> OfficeState: ...

    async def mutate_office_seats(
        self, kind: OfficeStateKind, mutation: Callable[[SeatRecords], MutationResult]
    ) -> tuple[OfficeState, MutationResult]: ...

    async def watch_office_state(
        self,
        kind: OfficeStateKind,
        handler: Callable[[OfficeStateChanged], Awaitable[None]],
        *,
        owner: str,
    ) -> OfficeState: ...

    def unwatch_office_state_owner(self, owner: str) -> None: ...


class InvalidDiscordLayoutError(ValueError):
    """Raised by `set_discord_layout` when `raw` fails the Pixel Agents
    wire-schema check (`validate_discord_layout`)."""


class OfficeLayoutNotSeededError(RuntimeError):
    """Raised by `load_editor_office` when the editor aggregate is still
    blank and no bundled default was available to seed it with yet --
    mirrors the pre-facade contract
    (`architect/infrastructure/office_layout_repository.py`'s identically
    named error, now retired along with that module)."""


def validate_discord_layout(layout: object) -> bool:
    """The Pixel Agents wire-schema structural check for the Discord
    aggregate's layout -- ported verbatim from floorplan's former
    `adapters/office_gateway.py::_validate_layout` (now retired, see
    docs/cctv-design.md). The editor aggregate's layout is validated
    differently, through the Semantic IR codec itself
    (`load_editor_office`/`set_editor_layout`), not this function."""

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


class OfficeStateFacade:
    """The single choke point every consumer uses for office-state reads
    and writes. `backend` is corridor's own Cog (or a fake satisfying
    `OfficeStateBackend`); `default_layout` is a zero-argument callable
    returning the bundle's current default layout, or `None` if no
    webview has been built yet (`pixelagents.infrastructure.webview_build.bundled_default_layout`,
    bound to this cog's own dist path -- kept as an injected callable
    rather than a direct import so this module stays framework-neutral
    and independently testable)."""

    def __init__(
        self,
        backend: OfficeStateBackend,
        *,
        default_layout: Callable[[], dict[str, object] | None],
        logger: logging.Logger | None = None,
    ) -> None:
        self._backend = backend
        self._default_layout = default_layout
        self._log = logger or log

    # --- current-state reads, seeding a still-blank aggregate lazily ----

    async def read(self, kind: OfficeStateKind) -> OfficeState:
        state = await self._backend.read_office_state(kind)
        return await self._ensure_seeded(kind, state)

    async def watch(
        self,
        kind: OfficeStateKind,
        handler: Callable[[OfficeStateChanged], Awaitable[None]],
        *,
        owner: str,
    ) -> OfficeState:
        """Atomically watch `kind` and return its current snapshot --
        thin pass-through to corridor's own atomic primitive, plus the
        same lazy-seeding `read` applies."""

        state = await self._backend.watch_office_state(kind, handler, owner=owner)
        return await self._ensure_seeded(kind, state)

    def unwatch_owner(self, owner: str) -> None:
        self._backend.unwatch_office_state_owner(owner)

    async def _ensure_seeded(self, kind: OfficeStateKind, state: OfficeState) -> OfficeState:
        """A blank aggregate (revision 0, empty layout) hasn't been given
        the real bundled default yet -- corridor's own repository stays
        schema-neutral by design (docs/cctv-design.md's lazy-init split),
        so this is the one place that recognizes "still blank" and seeds
        it. Two callers racing this concurrently both write the same
        bundled content -- harmless, the same last-write-wins tone every
        other office-state write already accepts."""

        if state.revision != 0 or state.layout:
            return state
        default = self._default_layout()
        if default is None:
            return state
        return await self._backend.set_office_layout(kind, default)

    # --- Discord aggregate: wire-schema-validated raw layout ------------

    async def set_discord_layout(self, raw: dict[str, object]) -> OfficeState:
        if not validate_discord_layout(raw):
            raise InvalidDiscordLayoutError("layout failed the Pixel Agents wire-schema check")
        return await self._backend.set_office_layout("discord", raw)

    # --- editor aggregate: Semantic-IR-validated Office ------------------

    async def load_editor_office(self, styles: FurnitureStyleManifest) -> Office:
        state = await self.read("editor")
        if not state.layout:
            raise OfficeLayoutNotSeededError("the editor office layout has not been seeded yet")
        return decode(state.layout, styles)

    async def set_editor_layout(
        self, office: Office, styles: FurnitureStyleManifest
    ) -> OfficeState:
        raw = encode(office, styles)
        return await self._backend.set_office_layout("editor", raw)

    # --- avatar seats: shared, byte-identical shape for both kinds -------

    async def mutate_seats(
        self,
        kind: OfficeStateKind,
        agent_id: str,
        patch: Mapping[str, object],
        *,
        palette_count: int = DEFAULT_PALETTE_COUNT,
    ) -> OfficeState:
        """Validate and merge one agent's seat/palette patch -- the exact
        same `merge_seat_patch` validation/shape floorplan's own seat
        writes already used, now shared by both aggregates so an editor-
        page avatar assignment round-trips identically to a Discord-page
        one (docs/cctv-design.md's "same wire-shaped record" requirement)."""

        def apply(seats: SeatRecords) -> None:
            merge_seat_patch(seats, agent_id, palette_count, patch)

        updated, _ = await self._backend.mutate_office_seats(kind, apply)
        return updated


__all__ = [
    "InvalidDiscordLayoutError",
    "OfficeLayoutNotSeededError",
    "OfficeStateBackend",
    "OfficeStateFacade",
    "validate_discord_layout",
]
