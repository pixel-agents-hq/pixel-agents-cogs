"""Typed persistence adapter for Pixel Agents' one remaining Config value."""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import parse_commit_ref, parse_webview_base_path

# Freshly rolled for this cog's shrunk scope -- the runtime settings that
# used to share this identifier (ws_port, layout, seats, guild
# enabled/include_bots, pixel_index urls, ...) now live in floorplan's own
# Config store. Existing pre-split installations' webview_commit_override
# is not carried over.
CONFIG_IDENTIFIER = 0x7069786C6167656E7473

GLOBAL_DEFAULTS: dict[str, object] = {
    "webview_commit_override": None,
    # None means "use infrastructure.webview_build.DEFAULT_BASE_PATH" --
    # same None-means-default convention as webview_commit_override above.
    "webview_base_path": None,
}


class RedSettingsRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedSettingsRepository:
        """Create and register Pixel Agents' shrunk Config contract."""

        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="pixelagents",
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def webview_commit_override(self) -> str | None:
        return cast(str | None, await self._config.webview_commit_override())

    async def set_webview_commit_override(self, value: str) -> str:
        clean = parse_commit_ref(value)
        await self._config.webview_commit_override.set(clean)
        return clean

    async def reset_webview_commit_override(self) -> None:
        await self._config.webview_commit_override.set(None)

    async def webview_base_path(self) -> str | None:
        return cast(str | None, await self._config.webview_base_path())

    async def set_webview_base_path(self, value: str) -> str:
        clean = parse_webview_base_path(value)
        await self._config.webview_base_path.set(clean)
        return clean

    async def reset_webview_base_path(self) -> None:
        await self._config.webview_base_path.set(None)
