"""cctv never fails cog_load solely because the listener can't bind, the
webview bundle is missing, or an aggregate fails validation -- it stays
loaded so status/configuration commands still work, and reports the
failure instead (docs/cctv-design.md §2.11)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..cctv import Cctv
from .conftest import FakeBot, FakeCorridor, FakePixelAgents


class TestListenerBindFailure(unittest.IsolatedAsyncioTestCase):
    async def test_a_port_already_in_use_does_not_fail_cog_load(self) -> None:
        corridor = FakeCorridor()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pixelagents = FakePixelAgents(
            corridor=corridor, dist_path=Path(tmp.name), default_layout=None
        )
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)

        blocker = Cctv(bot=FakeBot(corridor=FakeCorridor(), pixelagents=pixelagents))
        await blocker.cog_load()
        self.addAsyncCleanup(blocker.cog_unload)
        port = (await blocker._repository.global_settings()).port

        cog = Cctv(bot=bot)
        await cog._repository.set_port(port)
        await cog.cog_load()  # must not raise
        self.addAsyncCleanup(cog.cog_unload)

        self.assertFalse(cog._websocket_server.running)
        self.assertTrue(bot.owner_notifications)


class TestMissingWebviewBundle(unittest.IsolatedAsyncioTestCase):
    async def test_no_bundle_on_disk_does_not_fail_cog_load_or_webview_ready(self) -> None:
        corridor = FakeCorridor()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Deliberately empty dist_path -- no index.html, no assets/*.
        pixelagents = FakePixelAgents(
            corridor=corridor, dist_path=Path(tmp.name), ready=False, default_layout=None
        )
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        cog = Cctv(bot=bot)

        await cog.cog_load()  # must not raise
        self.addAsyncCleanup(cog.cog_unload)

        response = await cog.dashboard_webview_discord()
        self.assertEqual(response["status"], 1)
        self.assertEqual(response["error_code"], 503)

        # webviewReady over a real socket must also degrade gracefully --
        # an empty/unseeded layout, never a crashed connection.
        state = await cog._pixelagents.office_state().read("discord")
        self.assertEqual(state.layout, {})


class TestInvalidAggregateNeverCrashesTheWriter(unittest.IsolatedAsyncioTestCase):
    async def test_an_invalid_editor_save_never_persists_or_raises(self) -> None:
        corridor = FakeCorridor()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pixelagents = FakePixelAgents(
            corridor=corridor, dist_path=Path(tmp.name), default_layout=None
        )
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        cog = Cctv(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._on_save_layout_editor({"not": "a valid layout"})  # must not raise

        state = await pixelagents.office_state().read("editor")
        self.assertEqual(state.layout, {})


if __name__ == "__main__":
    unittest.main()
