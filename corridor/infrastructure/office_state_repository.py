"""Red Config-backed storage for corridor's two opaque `OfficeState`
aggregates. Pure CRUD -- no locking, no revision-increment race
protection across concurrent callers, no pub/sub. `OfficeStateService`
(`corridor/application/office_state_service.py`) owns the per-kind
locking and event publication built on top of this, the same layering
`RedCorridorRepository`/higher-level services already use elsewhere in
this package.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from redbot.core import Config

from ..domain.office_state import OfficeState, OfficeStateKind

# Freshly rolled, distinct from every other CONFIG_IDENTIFIER in this repo
# (including corridor's own RedCorridorRepository, 0x636F72726964) -- do
# not change casually once real data exists under it.
CONFIG_IDENTIFIER = 0x6F6666696365  # "office" in hex

MutationResult = TypeVar("MutationResult")

GLOBAL_DEFAULTS: dict[str, object] = {
    "discord_state": None,
    "editor_state": None,
}


def _key_for(kind: OfficeStateKind) -> str:
    return f"{kind}_state"


def _blank(kind: OfficeStateKind) -> OfficeState:
    return OfficeState(kind=kind, layout={}, seats={}, revision=0)


def _to_state(kind: OfficeStateKind, raw: dict[str, object]) -> OfficeState:
    return OfficeState(
        kind=kind,
        layout=cast("dict[str, object]", raw.get("layout") or {}),
        seats=cast("dict[str, dict[str, object]]", raw.get("seats") or {}),
        revision=cast(int, raw.get("revision") or 0),
    )


def _to_raw(state: OfficeState) -> dict[str, object]:
    return {"layout": state.layout, "seats": state.seats, "revision": state.revision}


class RedOfficeStateRepository:
    """The typed boundary around corridor's office-state Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedOfficeStateRepository:
        config = Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, force_registration=True)
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    async def get_or_create(self, kind: OfficeStateKind) -> OfficeState:
        """Read the current aggregate, creating (and persisting) a blank
        one on first touch. Idempotent under a race: two callers touching
        an unseeded kind concurrently both write the same blank content,
        the second write is a harmless no-op overwrite -- the same
        last-write-wins tone this store's mutations already accept.
        Pixelagents' facade is what recognizes a blank aggregate and
        seeds it with the real bundled default; this layer stays
        schema-neutral (docs/cctv-design.md's lazy-init note)."""

        attr = getattr(self._config, _key_for(kind))
        raw = cast("dict[str, object] | None", await attr())
        if raw is None:
            blank = _blank(kind)
            await attr.set(_to_raw(blank))
            return blank
        return _to_state(kind, raw)

    async def set_layout(self, kind: OfficeStateKind, layout: dict[str, object]) -> OfficeState:
        """Overwrite `layout`, preserving the current `seats`, incrementing
        `revision`."""

        current = await self.get_or_create(kind)
        updated = OfficeState(
            kind=kind, layout=layout, seats=current.seats, revision=current.revision + 1
        )
        await getattr(self._config, _key_for(kind)).set(_to_raw(updated))
        return updated

    async def mutate_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[dict[str, dict[str, object]]], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        """Apply a synchronous read-modify-write `mutation` to `seats`,
        preserving the current `layout`, incrementing `revision`. Mirrors
        `floorplan/infrastructure/settings.py::RedSettingsRepository.mutate_seats`'s
        shape, generalized to both office-state kinds and a revision
        counter."""

        current = await self.get_or_create(kind)
        seats = dict(current.seats)
        result = mutation(seats)
        updated = OfficeState(
            kind=kind, layout=current.layout, seats=seats, revision=current.revision + 1
        )
        await getattr(self._config, _key_for(kind)).set(_to_raw(updated))
        return updated, result


__all__ = ["CONFIG_IDENTIFIER", "RedOfficeStateRepository"]
