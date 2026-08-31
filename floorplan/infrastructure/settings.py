"""Typed persistence adapter for Floorplan's Red Config values.

Floorplan's own Config identity is now minimal: Pixel Index endpoints
only. Presence/layout/seats/WebSocket settings moved to `cctv`; layout
storage itself moved to corridor, reached through pixelagents'
`OfficeStateFacade` (docs/cctv-design.md). Fresh identifier, deliberately
not migrated from the old one -- see that doc's §2.9.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

# Freshly rolled -- floorplan's previous identifier (8364586608) carried
# ws_host/ws_port/layout/seats/guild enabled/include_bots, none of which
# floorplan owns anymore. That old data is orphaned, not migrated.
CONFIG_IDENTIFIER = 9536417792
DEFAULT_PIXEL_INDEX_API_URL = "https://pixel-index-api-staging.nntin.xyz"
DEFAULT_PIXEL_INDEX_WEB_URL = "https://pixel-index.vercel.app"

GLOBAL_DEFAULTS: dict[str, object] = {
    "pixel_index_api_url": DEFAULT_PIXEL_INDEX_API_URL,
    "pixel_index_web_url": DEFAULT_PIXEL_INDEX_WEB_URL,
}


class RedSettingsRepository:
    """The typed boundary around floorplan's own (now minimal) Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedSettingsRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        return self._config

    async def pixel_index_api_url(self) -> str:
        return cast(str, await self._config.pixel_index_api_url())

    async def pixel_index_web_url(self) -> str:
        return cast(str, await self._config.pixel_index_web_url())

    async def set_pixel_index_api_url(self, value: str) -> str:
        await self._config.pixel_index_api_url.set(value)
        return value

    async def set_pixel_index_web_url(self, value: str) -> str:
        await self._config.pixel_index_web_url.set(value)
        return value


__all__ = [
    "CONFIG_IDENTIFIER",
    "DEFAULT_PIXEL_INDEX_API_URL",
    "DEFAULT_PIXEL_INDEX_WEB_URL",
    "RedSettingsRepository",
]
