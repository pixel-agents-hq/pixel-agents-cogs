"""Opaque Config persistence for the two revisioned office aggregates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from redbot.core import Config

from ..domain import OfficeState, OfficeStateKind

# Fresh store: deliberately unrelated to corridor's settings Config and every
# former floorplan/pixelagents layout identifier.
CONFIG_IDENTIFIER = 0x636374765F7374617465  # "cctv_state"

GLOBAL_DEFAULTS: dict[str, object] = {
    "discord_state": None,
    "editor_state": None,
}


class RedOfficeStateRepository:
    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedOfficeStateRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="corridor",
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    def _value(self, kind: OfficeStateKind) -> Any:
        return getattr(self._config, f"{kind.value}_state")

    async def state(self, kind: OfficeStateKind) -> OfficeState | None:
        raw = cast("dict[str, Any] | None", await self._value(kind)())
        if raw is None:
            return None
        return OfficeState(
            kind=kind,
            layout=deepcopy(cast("dict[str, Any]", raw["layout"])),
            seats=deepcopy(cast("dict[str, dict[str, Any]]", raw["seats"])),
            revision=cast(int, raw["revision"]),
        )

    async def save(self, state: OfficeState) -> None:
        await self._value(state.kind).set(
            {
                "layout": deepcopy(state.layout),
                "seats": deepcopy(state.seats),
                "revision": state.revision,
            }
        )


__all__ = ["CONFIG_IDENTIFIER", "GLOBAL_DEFAULTS", "RedOfficeStateRepository"]
