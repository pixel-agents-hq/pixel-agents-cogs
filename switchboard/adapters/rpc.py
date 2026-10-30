"""Red RPC handlers, so automation (CI) can drive switchboard without Discord.

Red names each handler `<cog class>__<method>` in upper case, so these are
`SWITCHBOARD__STATUS`, `__INSTALL`, `__LOAD`, `__UNLOAD` and `__REPAIR`.
Each takes one positional list of cog names / tokens (`[["corridor"]]` as
JSON-RPC params, the same shape as Red's own `CORE__LOAD`) and returns a
JSON-safe dict. Red unregisters them itself when the cog is removed.
"""

from __future__ import annotations

from typing import Any

from ..application import SwitchboardService
from . import rendering


class RpcMixin:
    """Requires `self.bot` and `self._service` (provided by CogBase)."""

    bot: Any
    _service: SwitchboardService

    def _register_rpc_handlers(self) -> None:
        for handler in (self.status, self.install, self.load, self.unload, self.repair):
            self.bot.register_rpc_handler(handler)

    async def status(self, cogs: list[str] | None = None) -> dict[str, object]:
        return rendering.status_dict(await self._service.status(list(cogs or ())))

    async def install(self, cogs: list[str]) -> dict[str, object]:
        return rendering.install_dict(await self._service.install(list(cogs)))

    async def load(self, cogs: list[str]) -> dict[str, object]:
        return rendering.load_dict(await self._service.load(list(cogs)))

    async def unload(self, cogs: list[str]) -> dict[str, object]:
        return rendering.unload_dict(await self._service.unload(list(cogs)))

    async def repair(self) -> dict[str, object] | None:
        report = await self._service.repair()
        return rendering.load_dict(report) if report is not None else None
