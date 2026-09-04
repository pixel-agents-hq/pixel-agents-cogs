"""Red Config-backed implementation of the `ServerRepository` protocol
(`application/service.py`). Everything here is global (bot-wide), never
per-guild -- a registered A2A agent's own tool call carries no guild
context, same rationale `suggestionbox/infrastructure/settings_repository.py`
documents for its own per-agent MCP-access toggle.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import ThirdPartyMcpServer

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and scopes are the canonical registration contract once real
# data exists under this identifier -- do not change casually after release.
CONFIG_IDENTIFIER = 6115939321

GLOBAL_DEFAULTS: dict[str, object] = {
    # name -> base_url
    "servers": {},
    # name -> {agent_key: enabled}
    "agent_access": {},
}


class RedTelephonepoleRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedTelephonepoleRepository:
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

    async def list_servers(self) -> tuple[ThirdPartyMcpServer, ...]:
        raw = cast("dict[str, str]", await self._config.servers())
        return tuple(
            ThirdPartyMcpServer(name=name, base_url=base_url) for name, base_url in raw.items()
        )

    async def get_server(self, name: str) -> ThirdPartyMcpServer | None:
        raw = cast("dict[str, str]", await self._config.servers())
        base_url = raw.get(name)
        if base_url is None:
            return None
        return ThirdPartyMcpServer(name=name, base_url=base_url)

    async def save_server(self, server: ThirdPartyMcpServer) -> None:
        servers = dict(cast("dict[str, str]", await self._config.servers()))
        servers[server.name] = server.base_url
        await self._config.servers.set(servers)

    async def delete_server(self, name: str) -> None:
        servers = dict(cast("dict[str, str]", await self._config.servers()))
        servers.pop(name, None)
        await self._config.servers.set(servers)

        access = dict(cast("dict[str, dict[str, bool]]", await self._config.agent_access()))
        access.pop(name, None)
        await self._config.agent_access.set(access)

    async def is_agent_enabled(self, name: str, agent_key: str) -> bool:
        """Off by default for a (server, agent) pair with no explicit entry
        yet -- same "off by default" rule
        `suggestionbox`'s own `is_agent_enabled` applies."""

        access = cast("dict[str, dict[str, bool]]", await self._config.agent_access())
        return bool(access.get(name, {}).get(agent_key, False))

    async def set_agent_enabled(self, name: str, agent_key: str, value: bool) -> None:
        access = dict(cast("dict[str, dict[str, bool]]", await self._config.agent_access()))
        per_server = dict(access.get(name, {}))
        per_server[agent_key] = value
        access[name] = per_server
        await self._config.agent_access.set(access)


__all__ = ["CONFIG_IDENTIFIER", "GLOBAL_DEFAULTS", "RedTelephonepoleRepository"]
