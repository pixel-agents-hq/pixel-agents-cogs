"""Fresh Pixel Index-only Config storage for Floorplan."""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import normalize_http_url

CONFIG_IDENTIFIER = 0x666C6F6F72706C616E5F696E646578  # "floorplan_index"
DEFAULT_PIXEL_INDEX_API_URL = "https://pixel-index-api-staging.nntin.xyz"
DEFAULT_PIXEL_INDEX_WEB_URL = "https://pixel-index.vercel.app"
GLOBAL_DEFAULTS: dict[str, object] = {
    "pixel_index_api_url": DEFAULT_PIXEL_INDEX_API_URL,
    "pixel_index_web_url": DEFAULT_PIXEL_INDEX_WEB_URL,
}


class RedSettingsRepository:
    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedSettingsRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="floorplan",
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
        clean = normalize_http_url(value)
        await self._config.pixel_index_api_url.set(clean)
        return clean

    async def set_pixel_index_web_url(self, value: str) -> str:
        clean = normalize_http_url(value)
        await self._config.pixel_index_web_url.set(clean)
        return clean


__all__ = [
    "CONFIG_IDENTIFIER",
    "DEFAULT_PIXEL_INDEX_API_URL",
    "DEFAULT_PIXEL_INDEX_WEB_URL",
    "GLOBAL_DEFAULTS",
    "RedSettingsRepository",
]
