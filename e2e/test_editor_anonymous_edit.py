"""An anonymous visitor to the editor office watches it but cannot edit it:
the edit is refused on the server, not just hidden in the page.

A real browser that never signed in opens the editor page, gets the live
office (the read-only view), paints a tile through the real bundled UI and
clicks the real Save button -- the same steps `test_editor_ui_save.py`
takes signed in as the bot owner. The real `SaveLayoutMessage` reaches
cctv's real `CctvServer`, which drops it because the socket never presented
an editor ticket: the persisted editor layout is unchanged and no
`layoutLoaded` broadcast follows. An agent edit through pixelagents' facade
(what architect, painter and animator do) still lands and still reaches the
anonymous viewer.

Gated identically to the other e2e scenarios: set
`PIXELAGENTS_REAL_WEBVIEW_BUILD=1` to run it.
"""

from __future__ import annotations

import unittest
from copy import deepcopy

from corridor.domain import OfficeRef
from e2e.fixtures import (
    FakeBot,
    capture_websocket_frames,
    construct_core_cogs,
    paint_a_tile_and_save,
    real_webview_build_enabled,
    start_frontend_app,
    wait_for_bootstrap,
    wait_for_frame,
)

# The same interior floor tile test_editor_ui_save.py paints.
_EDIT_COL, _EDIT_ROW = 3, 13
# A floor material the bundled default layout doesn't use at that tile, so a
# landed agent edit is unmistakable.
_AGENT_FLOOR_MATERIAL = 9


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestAnonymousEditorEditIsRefused(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()

        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.pixelagents = cogs.pixelagents
        self.cctv = cogs.cctv

        self._port = await start_frontend_app(self.cctv, add_async_cleanup=self.addAsyncCleanup)

    async def test_an_anonymous_save_is_refused_and_agent_edits_still_land(self) -> None:
        from playwright.async_api import async_playwright

        before = await self.pixelagents.office_state(OfficeRef.editor())

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()
            await page.add_init_script("window.__PIXEL_AGENTS_E2E = true;")
            frames = capture_websocket_frames(page)

            # No sign-in: the page's editor ticket fetch is refused.
            await page.goto(f"http://127.0.0.1:{self._port}/e2e/page/editor")
            # The read-only view: the live office still arrives.
            await wait_for_bootstrap(page, frames)
            self.assertEqual(self.cctv.editor_pipeline.clients.editor_count, 0)

            await paint_a_tile_and_save(page, _EDIT_COL, _EDIT_ROW)

            # Runs the full wait: nothing may come back for a refused save.
            refused = await wait_for_frame(
                page, frames, lambda f: f.get("type") == "layoutLoaded", attempts=20
            )
            self.assertIsNone(refused, "an anonymous save was broadcast as a layout change")
            after = await self.pixelagents.office_state(OfficeRef.editor())
            self.assertEqual(after.revision, before.revision)
            self.assertEqual(after.layout, before.layout)

            # An agent edit goes through pixelagents' facade, not the socket,
            # and reaches the anonymous viewer.
            layout = deepcopy(before.layout)
            painted_index = _EDIT_ROW * layout["cols"] + _EDIT_COL
            layout["tiles"][painted_index] = _AGENT_FLOOR_MATERIAL
            await self.pixelagents.set_office_layout(OfficeRef.editor(), layout)

            landed = await wait_for_frame(page, frames, lambda f: f.get("type") == "layoutLoaded")
            self.assertIsNotNone(landed, "an agent edit never reached the anonymous viewer")
            assert landed is not None
            landed_layout = landed["layout"]
            assert isinstance(landed_layout, dict)
            self.assertEqual(landed_layout["tiles"][painted_index], _AGENT_FLOOR_MATERIAL)


if __name__ == "__main__":
    unittest.main()
