"""Real cross-cog loop: architect paints a real tile, a real Playwright
browser watches the real CCTV editor page update over a real WebSocket.

Every boundary except the LLM API and the Discord gateway is real:
- a real, network-cloned, npm/vite-built Pixel Agents webview, built by
  `PixelAgents.cog_load()` itself (see `e2e/fixtures.py::construct_core_cogs`)
  -- not pre-built by this test -- the same way a real bot host builds it;
- real corridor, pixelagents, architect, and cctv cogs, `cog_load()`-ed
  together in one process (painter is deliberately not exercised here --
  see `e2e/README.md` for why one architect-driven scenario is enough to
  prove the loop, and painter's own contract coverage lives elsewhere);
- a real `OfficeLayoutService.paint_tiles()` -> `pixel_agents_adapter.
  encode()` -> corridor `Config` write -> `OfficeStateChanged` -> cctv's
  real `CctvPipeline` -> a real aiohttp WebSocket broadcast;
- a real headless Chromium tab (Playwright) loading the real served page
  and observing the real WebSocket frame.

Slow and network-dependent (a real git clone + npm ci + vite build), so
this is gated the same way `pixelagents/tests/test_webview_build.py::
TestRealWebviewBuild` is: set `PIXELAGENTS_REAL_WEBVIEW_BUILD=1` to run it.
See `e2e/README.md` for local iteration tips (caching the build) and the
CI job that runs this on a schedule.
"""

from __future__ import annotations

import unittest

from architect.application import ToolLoopService
from architect.architect import Architect

from .fixtures import (
    FakeBot,
    ScriptedLLM,
    capture_websocket_frames,
    construct_core_cogs,
    final_response,
    real_webview_build_enabled,
    start_frontend_app,
    tool_call_response,
    wait_for_frame,
)

# A 2x2 span deep inside the bundled default layout's floor-7 room (rows
# 11-20, cols 1-9 in the pinned commit's default-layout-1.json) -- clear of
# every wall/void tile bordering it, so painting here can never collide
# with `paint_tiles`' own "can't paint a wall over furniture"/out-of-bounds
# validation regardless of pin drift within that room's interior.
_PAINT_COL, _PAINT_ROW = 2, 12
_PAINT_WIDTH, _PAINT_HEIGHT = 2, 2
_PAINT_MATERIAL = 3


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestArchitectPaintReachesTheLiveEditor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()

        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.corridor = cogs.corridor
        self.pixelagents = cogs.pixelagents
        self.cctv = cogs.cctv

        self.architect = Architect(self.bot)
        await self.architect.cog_load()
        self.bot.add_cog(self.architect)
        self.addAsyncCleanup(self.architect.cog_unload)

        self._port = await start_frontend_app(self.cctv, add_async_cleanup=self.addAsyncCleanup)

    async def test_paint_tiles_broadcasts_the_new_material_to_a_real_browser(self) -> None:
        # Imported here, not at module scope, so this file (and the
        # skipUnless-decorated class above) stays importable/collectible
        # without `playwright` installed when the env var gate skips it.
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()
            frames = capture_websocket_frames(page)

            await page.goto(f"http://127.0.0.1:{self._port}/e2e/page/editor")

            # Give the page's own JS time to open its (shimmed) WebSocket
            # before triggering the mutation, so the broadcast isn't sent
            # to zero connected clients.
            await page.wait_for_timeout(500)
            # The initial connection already received its own bootstrap
            # layoutLoaded (the pre-paint state) -- only frames captured
            # from here on can be the post-paint broadcast.
            frames.clear()

            self.architect._tool_loop_service = ToolLoopService(  # noqa: SLF001
                ScriptedLLM(
                    [
                        tool_call_response(
                            "paint_tiles",
                            {
                                "col": _PAINT_COL,
                                "row": _PAINT_ROW,
                                "width": _PAINT_WIDTH,
                                "height": _PAINT_HEIGHT,
                                "kind": "floor",
                                "material": _PAINT_MATERIAL,
                            },
                        ),
                        final_response(),
                    ]
                )
            )
            result = await self.architect._tool_loop_service.run(  # noqa: SLF001
                base_url="http://e2e-fake-llm.invalid",
                api_key="unused",
                model="e2e-fake-model",
                system_prompt="You are Architect, an office layout agent.",
                user_input="Paint a small floor patch a different material.",
                tools=self.architect._tools,  # noqa: SLF001
                max_tool_calls=5,
            )
            self.assertEqual(result.tool_calls_made, 1)

            layout_loaded = await wait_for_frame(
                page, frames, lambda f: f.get("type") == "layoutLoaded"
            )

            self.assertIsNotNone(
                layout_loaded, "browser never received a layoutLoaded broadcast after painting"
            )
            assert layout_loaded is not None
            layout = layout_loaded["layout"]
            assert isinstance(layout, dict)
            cols = layout["cols"]
            tiles = layout["tiles"]
            painted_index = _PAINT_ROW * cols + _PAINT_COL
            self.assertEqual(tiles[painted_index], _PAINT_MATERIAL)


if __name__ == "__main__":
    unittest.main()
