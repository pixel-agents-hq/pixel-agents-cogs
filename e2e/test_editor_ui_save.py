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
browser tab. The browser is signed in as the bot owner, the one user the
editor office's default edit gate lets edit.

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

from e2e.fixtures import (
    FakeBot,
    capture_websocket_frames,
    construct_core_cogs,
    paint_a_tile_and_save,
    real_webview_build_enabled,
    sign_in,
    start_frontend_app,
    wait_for_bootstrap,
    wait_for_frame,
)

# `FakeBot`'s default bot owner.
OWNER_ID = 1

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

            # Editing the editor office needs a sign-in as someone the edit
            # gate lets in: the bot owner, by default. See
            # test_editor_anonymous_edit.py for the refused anonymous edit.
            await sign_in(page, self._port, OWNER_ID)
            await page.goto(f"http://127.0.0.1:{self._port}/e2e/page/editor")
            await wait_for_bootstrap(page, frames)
            # The page presents its ticket in-band; wait until cctv has
            # checked it, so the save below can't race it.
            editor = self.cctv.editor_pipeline
            for _ in range(50):
                if editor.clients.editor_count:
                    break
                await page.wait_for_timeout(100)
            self.assertEqual(editor.clients.editor_count, 1, "the owner's socket never signed in")

            await paint_a_tile_and_save(page, _EDIT_COL, _EDIT_ROW)

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
