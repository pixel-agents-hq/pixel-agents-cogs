"""Async adapter over the pixatar Python client.

The client is `packages/pixatar/python` of the same Pixel Index checkout the
CLI is built from (`pixatar_build.client_src`), so the two are always the
same commit and nothing has to be pip-installed on the bot host. Without a
checkout (an explicit `PIXATAR_CLI`), an installed `pixatar` package is used.

The client keeps one `pixatar serve` process and is blocking, so every call
runs on a worker thread.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from ..domain.characters import PixatarCharacter, PixatarFailure

# Not "pixatar": an installed package of that name (another version) stays
# importable alongside the checkout's.
_MODULE_NAME = "pixelagents_vendored_pixatar"


def load_pixatar(src: Path | None) -> ModuleType:
    """The pixatar client package under `src`, or the installed one."""

    package = src / "pixatar" if src is not None else None
    if package is not None and (package / "__init__.py").is_file():
        loaded = sys.modules.get(_MODULE_NAME)
        if loaded is not None and Path(loaded.__file__ or "").parent == package:
            return loaded
        spec = importlib.util.spec_from_file_location(
            _MODULE_NAME, package / "__init__.py", submodule_search_locations=[str(package)]
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(_MODULE_NAME, None)
            raise
        return module
    try:
        import pixatar
    except ImportError as exc:
        raise PixatarFailure(
            "cli-not-found", "the pixatar Python client is missing (rebuild pixatar)"
        ) from exc
    return pixatar


class PixatarClient:
    """Implements `application.characters.Pixatar` over the real CLI."""

    def __init__(self, command: Sequence[str] = (), client_src: Path | None = None) -> None:
        # An empty command lets the client find the CLI itself
        # (`$PIXATAR_CLI`, then `pixatar` on PATH).
        self._command = list(command) or None
        self._client_src = client_src
        self._module: ModuleType | None = None
        self._client: Any = None

    def _pixatar(self) -> tuple[ModuleType, Any]:
        if self._module is None:
            self._module = load_pixatar(self._client_src)
        if self._client is None:
            try:
                self._client = self._module.Pixatar(self._command)
            except self._module.PixatarError as exc:
                raise PixatarFailure(exc.code, exc.message) from exc
        return self._module, self._client

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        def call() -> Any:
            module, client = self._pixatar()
            try:
                return getattr(client, method)(*args, **kwargs)
            except module.PixatarError as exc:
                raise PixatarFailure(exc.code, exc.message) from exc

        return await asyncio.to_thread(call)

    async def from_text(self, text: str) -> PixatarCharacter:
        character = await self._call("info", text, text=True)
        return PixatarCharacter(seed=character.seed, profile=dict(character.profile))

    async def from_seed(self, seed: str) -> PixatarCharacter:
        character = await self._call("info", seed)
        return PixatarCharacter(seed=character.seed, profile=dict(character.profile))

    async def avatar_png(self, seed: str, *, scale: int) -> bytes:
        return bytes(await self._call("avatar_png", seed, scale=scale))

    async def vendor_commit(self) -> str:
        version = await self._call("version")
        return str(version["vendor"]["commit"])

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["PixatarClient"]
