"""Red Config-backed storage for the one Pixel Agents office layout
`architect`'s (and `painter`'s) own `OfficeLayoutRepository` builds a
Semantic IR on top of.

Owned by `pixelagents`, not by whichever cog happens to construct this
class -- `create()` always passes `cog_name="pixelagents"` explicitly
(never the caller's own class name) so `architect`'s and `painter`'s
independently-constructed `Config` objects resolve to the exact same
on-disk store, the same way `Config.get_conf`'s own docs describe passing
`cog_instance=None` plus an explicit `cog_name` for a store not owned by
the calling cog. See docs/painter-design.md part A for why this storage
moved out of `architect/infrastructure/settings_repository.py` (which
still owns everything else architect-specific: `max_tool_calls`,
`system_prompt`, `ws_host`/`ws_port`, `debug_logging`) and into this cog
instead -- including the direct precedent this repeats (issue #21 already
moved a `layout` Config key *out* of this same cog once, into
`floorplan`; this is a deliberate, discussed exception, not an oversight.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

# Freshly rolled -- distinct from this cog's other identifier in
# `settings.py` (0x7069786C6167656E7473) so the two stores are
# independent documents even though both resolve under the same
# `cog_name="pixelagents"`. Do not change casually once real data exists
# under it.
CONFIG_IDENTIFIER = 6850347610142909695

GLOBAL_DEFAULTS: dict[str, object] = {
    "layout": None,
}


class RedOfficeLayoutSettings:
    """The typed boundary around the shared office layout's Config
    storage -- `SupportsLayoutStorage` from
    `application/office_repository.py` (structural, not a formal
    subclass)."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls) -> RedOfficeLayoutSettings:
        """No live cog instance needed -- this store is reachable by any
        cog that knows its identifier, deliberately (`architect` and
        `painter` both call this)."""

        config = Config.get_conf(
            None,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="pixelagents",
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    async def layout(self) -> dict[str, object] | None:
        return cast("dict[str, object] | None", await self._config.layout())

    async def set_layout(self, layout: dict[str, object]) -> None:
        await self._config.layout.set(layout)


__all__ = ["CONFIG_IDENTIFIER", "GLOBAL_DEFAULTS", "RedOfficeLayoutSettings"]
