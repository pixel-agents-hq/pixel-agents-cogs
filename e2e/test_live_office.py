"""Real cross-cog loop: architect paints a real tile, a real Playwright
browser watches the real CCTV editor page update over a real WebSocket.

Every boundary except the LLM API and the Discord gateway is real:
- a real, network-cloned, npm/vite-built Pixel Agents webview
  (`pixelagents.infrastructure.webview_build.ensure_webview_built`, the
  same function `contracts/pixel_agents/verify.py` and
  `vendor-update.yml`'s gate use -- not a reimplementation);
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

import base64
import json
import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from aiohttp import web

from architect.application import ToolLoopService
from architect.architect import Architect
from cctv.cctv import CCTV
from cctv.infrastructure import settings as cctv_settings
from cctv.infrastructure.webview import WEBVIEW_BASE_PATH
from corridor.corridor import Corridor
from pixelagents.infrastructure import webview_build
from pixelagents.pixelagents import PixelAgents

from .fixtures import FakeBot, ScriptedLLM, final_response, tool_call_response

_LOG = logging.getLogger("e2e.live_office")

# A 2x2 span deep inside the bundled default layout's floor-7 room (rows
# 11-20, cols 1-9 in the pinned commit's default-layout-1.json) -- clear of
# every wall/void tile bordering it, so painting here can never collide
# with `paint_tiles`' own "can't paint a wall over furniture"/out-of-bounds
# validation regardless of pin drift within that room's interior.
_PAINT_COL, _PAINT_ROW = 2, 12
_PAINT_WIDTH, _PAINT_HEIGHT = 2, 2
_PAINT_MATERIAL = 3


def _real_webview_build_enabled() -> bool:
    return os.environ.get("PIXELAGENTS_REAL_WEBVIEW_BUILD") == "1"


async def _build_frontend_app(cctv_cog: CCTV, server: object) -> web.Application:
    """A tiny, test-only aiohttp app that serves the real page/asset
    responses `cctv.infrastructure.webview.WebviewAssets` already produces
    in production (real Discord Dashboard integration serves these via
    Red's RPC page-provider protocol instead of a plain HTTP route, which
    is out of scope to stand up here) alongside the *real* WebSocket
    handlers `CctvServer` binds in production -- reusing the same bound
    methods, not reimplementing them, so this never drifts from what a
    real client actually talks to."""

    app = web.Application()
    app.router.add_get("/cctv/discord/ws", server.handle_discord)  # type: ignore[attr-defined]
    app.router.add_get("/cctv/editor/ws", server.handle_editor)  # type: ignore[attr-defined]
    app.router.add_get("/cctv/health", server.handle_health)  # type: ignore[attr-defined]

    assets = cctv_cog._assets  # noqa: SLF001

    async def page_handler(request: web.Request) -> web.Response:
        page = request.match_info["page"]
        response = assets.page_response(page)
        if response.get("status") != 0:
            return web.Response(status=503, text=str(response.get("error_message")))
        web_content = cast("dict[str, object]", response["web_content"])
        return web.Response(text=str(web_content["source"]), content_type="text/html")

    async def static_handler(request: web.Request) -> web.Response:
        tail = request.match_info["tail"]
        response = assets.static_response(tail)
        if response.get("status") != 0:
            return web.Response(status=404)
        raw = cast("dict[str, object]", response["raw_response"])
        body = base64.b64decode(cast(str, raw["body_base64"]))
        # `web.Response(content_type=...)` rejects a charset baked into the
        # value (it wants that split out separately), and WebviewAssets'
        # content types (e.g. "text/javascript; charset=utf-8") already
        # carry one -- set the header directly instead of fighting aiohttp's
        # parsing of it.
        return web.Response(body=body, headers={"Content-Type": str(raw["content_type"])})

    app.router.add_get("/e2e/page/{page}", page_handler)
    app.router.add_get(WEBVIEW_BASE_PATH + "{tail:.*}", static_handler)
    return app


@unittest.skipUnless(
    _real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestArchitectPaintReachesTheLiveEditor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._build_dir = TemporaryDirectory()
        cache = os.environ.get("PIXELAGENTS_E2E_WEBVIEW_CACHE")
        self._build_path = Path(cache) if cache else Path(self._build_dir.name)
        if not cache:
            self.addCleanup(self._build_dir.cleanup)

        self.bot = FakeBot()

        self.corridor = Corridor(self.bot)
        await self.corridor.cog_load()
        self.bot.add_cog(self.corridor)
        self.addAsyncCleanup(self.corridor.cog_unload)

        self.pixelagents = PixelAgents(self.bot)
        # Real clone+npm+vite build -- not a reimplementation of
        # ensure_webview_built, the exact function cog_load() below also
        # calls (and finds already up to date, so it doesn't rebuild).
        webview_build.ensure_webview_built(self._build_path, logger=_LOG)
        self.pixelagents._cog_data_dir = self._build_path  # noqa: SLF001
        await self.pixelagents.cog_load()
        self.bot.add_cog(self.pixelagents)
        self.addAsyncCleanup(self.pixelagents.cog_unload)

        self.architect = Architect(self.bot)
        await self.architect.cog_load()
        self.bot.add_cog(self.architect)
        self.addAsyncCleanup(self.architect.cog_unload)

        # Ephemeral port -- this suite owns its whole process, but a fixed
        # default (3210) would still collide with a second e2e run on the
        # same host.
        cctv_settings.GLOBAL_DEFAULTS["listener_port"] = 0
        self.cctv = CCTV(self.bot)
        await self.cctv.cog_load()
        self.bot.add_cog(self.cctv)
        self.addAsyncCleanup(self.cctv.cog_unload)

        server = self.cctv._server  # noqa: SLF001
        assert server is not None

        self._frontend_runner = web.AppRunner(await _build_frontend_app(self.cctv, server))
        await self._frontend_runner.setup()
        site = web.TCPSite(self._frontend_runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(self._frontend_runner.cleanup)
        raw_server = site._server  # noqa: SLF001
        assert raw_server is not None
        self._port = raw_server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]

    async def test_paint_tiles_broadcasts_the_new_material_to_a_real_browser(self) -> None:
        # Imported here, not at module scope, so this file (and the
        # skipUnless-decorated class above) stays importable/collectible
        # without `playwright` installed when the env var gate skips it.
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()

            frames: list[dict[str, object]] = []

            def on_websocket(ws: object) -> None:
                def on_frame(payload: str) -> None:
                    try:
                        message = json.loads(payload)
                    except (TypeError, ValueError):
                        return
                    if isinstance(message, dict):
                        frames.append(message)

                ws.on("framereceived", on_frame)  # type: ignore[attr-defined]

            page.on("websocket", on_websocket)

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

            layout_loaded = None
            for _ in range(50):
                layout_loaded = next((f for f in frames if f.get("type") == "layoutLoaded"), None)
                if layout_loaded is not None:
                    break
                await page.wait_for_timeout(100)

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
