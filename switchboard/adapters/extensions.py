"""The Extensions protocol backed by Red's own load/unload logic.

Loading goes through Red's `CoreLogic._load` / `_unload` (the same code
behind `[p]load` / `[p]unload` and the `CORE__LOAD` RPC method), so a cog
loaded by switchboard gets Red's module refresh and lands in Red's autoload
list exactly as if an owner had typed `[p]load`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..domain import LoadOutcome, LoadResult, UnloadOutcome, UnloadResult


class RedExtensions:
    def __init__(self, bot: Any) -> None:
        self._bot = bot

    def loaded(self) -> set[str]:
        return set(self._bot.extensions)

    def origin(self, name: str) -> Path | None:
        module = sys.modules.get(name)
        file = getattr(module, "__file__", None)
        if name not in self._bot.extensions or not file:
            return None
        return Path(file).resolve().parent

    async def load(self, name: str) -> LoadResult:
        result = await self._core()._load([name])
        if name in result.get("loaded_packages", ()):
            return LoadResult(name, LoadOutcome.LOADED)
        if name in result.get("alreadyloaded_packages", ()):
            return LoadResult(name, LoadOutcome.ALREADY_LOADED)
        if name in result.get("notfound_packages", ()):
            return LoadResult(name, LoadOutcome.NOT_FOUND, "not found in any cog path")
        reasons = result.get("failed_with_reason_packages", {})
        if name in reasons:
            return LoadResult(name, LoadOutcome.FAILED, str(reasons[name]))
        return LoadResult(name, LoadOutcome.FAILED, self._last_exception_line())

    async def unload(self, name: str) -> UnloadResult:
        result = await self._core()._unload([name])
        if name in result.get("unloaded_packages", ()):
            return UnloadResult(name, UnloadOutcome.UNLOADED)
        return UnloadResult(name, UnloadOutcome.NOT_LOADED)

    def _core(self) -> Any:
        # Red's Core cog subclasses CoreLogic and is always loaded.
        core = self._bot.get_cog("Core")
        if core is None:
            raise RuntimeError("Red's Core cog is not loaded")
        return core

    def _last_exception_line(self) -> str:
        """The final `Error: message` line of the traceback Red stored."""

        text = getattr(self._bot, "_last_exception", None) or ""
        lines = [line for line in str(text).strip().splitlines() if line.strip()]
        return lines[-1].strip() if lines else "see the bot log"
