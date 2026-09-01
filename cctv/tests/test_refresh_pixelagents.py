"""cctv's half of the pixelagents dependent-refresh fix.

cctv resolves `self._pixelagents` once, in its own `cog_load`, and then
hands that same reference to each `CctvPipeline` it constructs -- so a
pixelagents reload that happens independently of cctv's own reload used
to leave both `CctvBase._pixelagents` and every live pipeline's private
copy pointing at the discarded old Cog instance forever (its
`_office_state` included), surfacing as "pixelagents office-state facade
is not loaded" on every subsequent editor websocket message. See
pixelagents' `PixelAgentsBase._refresh_dependents` docstring for the other
half.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from corridor.domain import OfficeStateKind

from ..adapters.cog_base import CctvBase


class _Pipeline:
    def __init__(self) -> None:
        self.pixelagents: object | None = None

    def set_pixelagents(self, pixelagents: object) -> None:
        self.pixelagents = pixelagents


class TestRefreshPixelagents(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cctv = object.__new__(CctvBase)
        self.discord = _Pipeline()
        self.editor = _Pipeline()
        self.cctv._pipelines = {  # type: ignore[attr-defined]
            OfficeStateKind.DISCORD: self.discord,
            OfficeStateKind.EDITOR: self.editor,
        }
        self.cctv._pixelagents = SimpleNamespace(name="stale")  # type: ignore[attr-defined]

    async def test_updates_the_cog_level_reference_and_every_live_pipeline(self) -> None:
        fresh = SimpleNamespace(name="fresh")

        await self.cctv.refresh_pixelagents(fresh)

        self.assertIs(self.cctv._pixelagents, fresh)  # type: ignore[attr-defined]
        self.assertIs(self.discord.pixelagents, fresh)
        self.assertIs(self.editor.pixelagents, fresh)

    async def test_noop_on_pipelines_when_none_have_been_created_yet(self) -> None:
        self.cctv._pipelines = {}  # type: ignore[attr-defined]
        fresh = SimpleNamespace(name="fresh")

        await self.cctv.refresh_pixelagents(fresh)  # must not raise

        self.assertIs(self.cctv._pixelagents, fresh)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
