"""Async adapter over the pixatar Python client (`pixatar`, installed from
`packages/pixatar/python` at the same Pixel Index commit as the CLI -- see
`pixatar_build.py`).

The client keeps one `pixatar serve` process and is blocking, so every call
runs on a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from ..domain.characters import PixatarCharacter, PixatarFailure


class PixatarClient:
    """Implements `application.characters.Pixatar` over the real CLI."""

    def __init__(self, command: Sequence[str] = ()) -> None:
        # An empty command lets the client find the CLI itself
        # (`$PIXATAR_CLI`, then `pixatar` on PATH).
        self._command = list(command) or None
        self._client: Any = None

    def _pixatar(self) -> Any:
        if self._client is None:
            try:
                import pixatar
            except ImportError as exc:
                raise PixatarFailure(
                    "cli-not-found", "the pixatar Python client is not installed"
                ) from exc
            try:
                self._client = pixatar.Pixatar(self._command)
            except pixatar.PixatarError as exc:
                raise PixatarFailure(exc.code, exc.message) from exc
        return self._client

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        def call() -> Any:
            client = self._pixatar()
            import pixatar

            try:
                return getattr(client, method)(*args, **kwargs)
            except pixatar.PixatarError as exc:
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
