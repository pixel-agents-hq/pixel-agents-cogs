"""A real browser paints a tile through the real bundled UI and saves it --
proving the client-to-server WebSocket write path, not just the
server-to-client broadcasts every other e2e scenario observes.

Every boundary except the LLM API and the Discord gateway is real,
including -- unlike `test_live_office.py`'s architect-driven scenario --
the bundled webview's own UI: real toolbar clicks (enter edit mode, select
the floor-paint tool), the real `handleEditorTileAction` handler upstream's
own canvas click handler calls, and a real click on the real Save button,
which sends a real `SaveLayoutMessage` over the real (shimmed) WebSocket to
cctv's real `CctvServer` -> `pipeline.handle_message` ->
`pixelagents.set_office_layout()` -> a real corridor `Config` write ->
`OfficeStateChanged` -> a real `layoutLoaded` broadcast back to the same
browser tab.

The one thing bypassed is canvas pixel-to-tile-coordinate geometry: tile
targeting goes through `window.__pixelAgentsTestHooks.editorTileAction`,
the exact same test-only hook (and the exact same underlying handler) the
vendored Pixel Agents webview's *own* Playwright e2e suite
(`vendor/pixel-agents/e2e/helpers/editor.ts`, cloned into the real build
directory by `ensure_webview_built`) already uses for this. Tool selection
still goes through the real toolbar UI, same as upstream's own tests.

Gated identically to the other e2e scenarios: set
`PIXELAGENTS_REAL_WEBVIEW_BUILD=1` to run it.
"""

from __future__ import annotations

import unittest

from .fixtures import (
    FakeBot,
    capture_websocket_frames,
    construct_core_cogs,
    real_webview_build_enabled,
    start_frontend_app,
    wait_for_bootstrap,
    wait_for_frame,
)

# A single tile deep inside the bundled default layout's floor-7 room (see
# test_live_office.py's own comment on the same room) -- clear of every
# wall/void tile, so painting here can never collide with the editor's own
# ghost-border-expansion logic regardless of pin drift within the room's
# interior. Deliberately a different tile than test_live_office.py's 2x2
# span so a future reader never has to wonder whether the two scenarios'
# real Config writes could interact.
_EDIT_COL, _EDIT_ROW = 3, 13

# webview-ui/src/office/editor/editorState.ts's own default
# (`selectedTileType: TileTypeVal = TileType.FLOOR_1`) -- what a fresh
# "Paint floor tiles" tool selection paints with before any pattern swatch
# is clicked, matching `pixel_agents_adapter.py`'s floor-material
# numbering (1-9).
_DEFAULT_FLOOR_MATERIAL = 1


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestEditorUiSaveReachesTheServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()

        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.corridor = cogs.corridor
        self.pixelagents = cogs.pixelagents
        self.cctv = cogs.cctv

        self._port = await start_frontend_app(self.cctv, add_async_cleanup=self.addAsyncCleanup)

    async def test_painting_a_tile_via_the_real_ui_and_saving_reaches_the_server(self) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()
            # The same gate upstream's own Playwright harness sets via
            # addInitScript before any app code runs
            # (webview-ui/src/runtime.ts::isE2E) -- without this,
            # window.__pixelAgentsTestHooks is never installed at all, by
            # design (it must never run in a real user's session).
            await page.add_init_script("window.__PIXEL_AGENTS_E2E = true;")
            frames = capture_websocket_frames(page)

            await page.goto(f"http://127.0.0.1:{self._port}/e2e/page/editor")
            await wait_for_bootstrap(page, frames)

            # Real toolbar clicks -- the same path a user takes, mirroring
            # vendor/pixel-agents/e2e/helpers/editor.ts's own
            # enterEditMode/selectCarpetTool convention for its floor
            # equivalent. First-run tooltips (dismissed the same way
            # upstream's own harness's dismissFirstRunTooltips does) would
            # otherwise overlay the toolbar and intercept these clicks.
            for tooltip_text in ("Instant Detection Active", "Updated to v"):
                tooltip = page.locator("div", has_text=tooltip_text).first
                try:
                    if await tooltip.is_visible():
                        await tooltip.locator("button", has_text="x").first.click()
                except Exception:  # best-effort dismiss only
                    pass
            await page.locator('button[title="Edit office layout"]').click()
            await page.locator('button[title="Paint floor tiles"]').click()

            # Tile targeting goes through the test hook, bypassing only
            # canvas pixel->tile geometry -- it calls the exact same
            # handleEditorTileAction the canvas's own click handler calls
            # (webview-ui/src/App.tsx), which reads the real
            # activeTool/selectedTileType the toolbar clicks above just set.
            await page.evaluate(
                "([c, r]) => window.__pixelAgentsTestHooks.editorTileAction(c, r)",
                [_EDIT_COL, _EDIT_ROW],
            )

            # Real Save button click -- only visible while the editor is
            # dirty, same as vendor/pixel-agents/e2e/helpers/editor.ts's own
            # saveLayout(). Sends a real SaveLayoutMessage over the real
            # WebSocket.
            save_button = page.locator("button", has_text="Save")
            await save_button.wait_for(state="visible", timeout=5_000)
            await save_button.click()

            layout_loaded = await wait_for_frame(
                page, frames, lambda f: f.get("type") == "layoutLoaded"
            )
            self.assertIsNotNone(
                layout_loaded, "server never broadcast layoutLoaded after the real UI save"
            )
            assert layout_loaded is not None
            layout = layout_loaded["layout"]
            assert isinstance(layout, dict)
            cols = layout["cols"]
            tiles = layout["tiles"]
            painted_index = _EDIT_ROW * cols + _EDIT_COL
            self.assertEqual(tiles[painted_index], _DEFAULT_FLOOR_MATERIAL)


if __name__ == "__main__":
    unittest.main()
