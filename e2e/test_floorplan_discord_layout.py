"""A real Floorplan cog loads a real Pixel Index layout onto the DISCORD
office-state kind, observed by a real Playwright browser on cctv's
discord pipeline -- the one real production edge (`Floorplan ->|set
DISCORD layout| Pixelagents`, see docs/architecture.md) no other e2e
scenario exercises, since `test_live_office.py`/`test_editor_ui_save.py`
both only ever write the EDITOR kind.

Every boundary except the LLM API and the Discord gateway is real,
including a real network call to Pixel Index's real staging environment
(`https://pixel-index-api-staging.nntin.xyz`, floorplan's own default --
the same environment `contracts/pixel_index` already checks against on a
schedule). Nothing about Pixel Index is mocked: this suite's mock
allowlist is LiteLLM and Discord only, and Pixel Index is neither.

Real chain: `CatalogueService.search()`/`.load_layout()` (a real HTTP
call) -> `Floorplan._apply_catalogue_layout()` ->
`pixelagents.set_office_layout(OfficeStateKind.DISCORD, ...)` -> a real
corridor `Config` write -> `OfficeStateChanged` -> cctv's real
`CctvPipeline` (discord, not editor) -> a real WebSocket broadcast.

Gated identically to the other e2e scenarios (`PIXELAGENTS_REAL_WEBVIEW_
BUILD=1`), plus an inherent dependency on Pixel Index staging actually
being reachable and non-empty -- an accepted tradeoff, the same one
`test_live_office.py` already accepts for the real webview clone.
"""

from __future__ import annotations

import unittest

from floorplan.floorplan import Floorplan

from .fixtures import (
    FakeBot,
    capture_websocket_frames,
    construct_core_cogs,
    real_webview_build_enabled,
    start_frontend_app,
    wait_for_bootstrap,
    wait_for_frame,
)


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestFloorplanLoadsARealLayoutOntoTheDiscordPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()

        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.corridor = cogs.corridor
        self.pixelagents = cogs.pixelagents
        self.cctv = cogs.cctv

        self.floorplan = Floorplan(self.bot)
        await self.floorplan.cog_load()
        self.bot.add_cog(self.floorplan)
        self.addAsyncCleanup(self.floorplan.cog_unload)

        self._port = await start_frontend_app(self.cctv, add_async_cleanup=self.addAsyncCleanup)

    async def test_loading_a_real_pixel_index_layout_reaches_the_discord_pipeline(self) -> None:
        # A real HTTP call to Pixel Index staging -- fails loudly (not
        # skipped) if staging is unreachable or empty, since a search
        # coming back empty is itself a real signal something's wrong with
        # the environment this suite depends on, not a reason to pretend
        # the scenario passed.
        search_result = await self.floorplan._catalogue_service.search(  # noqa: SLF001
            query=None, tag=None, sort="newest"
        )
        self.assertIsNone(
            search_result.error, f"Pixel Index staging search failed: {search_result.error}"
        )
        assert search_result.value is not None
        self.assertGreater(
            len(search_result.value.layouts),
            0,
            "Pixel Index staging returned no layouts to load",
        )
        slug = search_result.value.layouts[0].slug

        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()
            frames = capture_websocket_frames(page)

            # The discord pipeline, not editor -- DISCORD is the kind
            # Floorplan writes.
            await page.goto(f"http://127.0.0.1:{self._port}/e2e/page/discord")
            await wait_for_bootstrap(page, frames)

            load_result = await self.floorplan._catalogue_service.load_layout(  # noqa: SLF001
                self.bot.user.id, slug
            )
            self.assertIsNone(
                load_result.error, f"failed to load real layout {slug!r}: {load_result.error}"
            )

            layout_loaded = await wait_for_frame(
                page, frames, lambda f: f.get("type") == "layoutLoaded"
            )
            self.assertIsNotNone(
                layout_loaded,
                f"discord pipeline never received a layoutLoaded broadcast after loading {slug!r}",
            )


if __name__ == "__main__":
    unittest.main()
