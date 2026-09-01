"""docs/cctv-design.md §3.3's "ignore if revision is not newer" rule --
each pipeline's own OfficeStateChanged handler must never re-apply (or
regress to) a revision it has already seen, and must serialize against
its own bootstrap path via the same lock (§3.4). See
cctv/adapters/office_gateway_discord.py's/office_gateway_editor.py's
`_on_*_state_changed` docstrings for why this exists -- a stale delivery
racing a bootstrap-in-progress could otherwise leave a client displaying
an older snapshot than the one it already received."""

from __future__ import annotations

import unittest

from corridor.domain import OfficeState, OfficeStateChanged

from ..cctv import Cctv
from .conftest import FakeBot, FakeCorridor, FakePixelAgents


def _state(kind: str, revision: int, cols: int) -> OfficeState:
    return OfficeState(kind=kind, layout={"cols": cols}, seats={}, revision=revision)


class TestDiscordRevisionGuard(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        corridor = FakeCorridor()
        pixelagents = FakePixelAgents(corridor=corridor)
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        self.cog = Cctv(bot=bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)
        self.broadcasts: list[dict[str, object]] = []

        async def record(message: dict[str, object], **kwargs: object) -> int:
            self.broadcasts.append(message)
            return 0

        self.cog._discord_client_hub.broadcast = record  # type: ignore[method-assign]

    async def test_a_newer_revision_is_applied(self) -> None:
        await self.cog._on_discord_state_changed(OfficeStateChanged(state=_state("discord", 5, 1)))

        self.assertEqual(self.cog._discord_last_revision, 5)
        self.assertTrue(self.broadcasts)

    async def test_a_stale_revision_is_dropped(self) -> None:
        await self.cog._on_discord_state_changed(OfficeStateChanged(state=_state("discord", 5, 1)))
        self.broadcasts.clear()

        await self.cog._on_discord_state_changed(OfficeStateChanged(state=_state("discord", 3, 9)))

        self.assertEqual(self.cog._discord_last_revision, 5)
        self.assertEqual(self.broadcasts, [])

    async def test_the_same_revision_again_is_dropped(self) -> None:
        await self.cog._on_discord_state_changed(OfficeStateChanged(state=_state("discord", 5, 1)))
        self.broadcasts.clear()

        await self.cog._on_discord_state_changed(OfficeStateChanged(state=_state("discord", 5, 1)))

        self.assertEqual(self.broadcasts, [])


class TestEditorRevisionGuard(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        corridor = FakeCorridor()
        pixelagents = FakePixelAgents(corridor=corridor)
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        self.cog = Cctv(bot=bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)
        self.broadcasts: list[dict[str, object]] = []

        async def record(message: dict[str, object], **kwargs: object) -> int:
            self.broadcasts.append(message)
            return 0

        self.cog._editor_client_hub.broadcast = record  # type: ignore[method-assign]

    async def test_a_stale_revision_is_dropped(self) -> None:
        await self.cog._on_editor_state_changed(OfficeStateChanged(state=_state("editor", 5, 1)))
        self.broadcasts.clear()

        await self.cog._on_editor_state_changed(OfficeStateChanged(state=_state("editor", 2, 9)))

        self.assertEqual(self.cog._editor_last_revision, 5)
        self.assertEqual(self.broadcasts, [])


if __name__ == "__main__":
    unittest.main()
