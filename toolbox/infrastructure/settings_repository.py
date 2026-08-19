"""Red Config-backed implementation of the NodeRepository protocol.

Installing/uninstalling Node.js on the bot host affects every guild the bot
serves, not just whichever one the command was run from, so this is bot-wide
global Config -- not `register_guild`, which every other cookiecutter-based
cog here uses instead.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import NodeInstallation

# Config keys and scope are the canonical registration contract once real
# data exists under this identifier -- do not change casually after release.
CONFIG_IDENTIFIER = 2984016573

GLOBAL_DEFAULTS: dict[str, object] = {
    "installed_version": None,
    "installed_dir": None,
}


class RedNodeRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedNodeRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def get_installed(self) -> NodeInstallation | None:
        version = cast("str | None", await self._config.installed_version())
        install_dir = cast("str | None", await self._config.installed_dir())
        if not version or not install_dir:
            return None
        return NodeInstallation(version=version, install_dir=install_dir)

    async def set_installed(self, installation: NodeInstallation | None) -> None:
        if installation is None:
            await self._config.installed_version.set(None)
            await self._config.installed_dir.set(None)
            return
        await self._config.installed_version.set(installation.version)
        await self._config.installed_dir.set(installation.install_dir)
