"""The office as a Discord Activity, in a real browser: pixelagents' real
Activity build (the real Embedded App SDK, bundled by the real esbuild) runs
inside a stand-in for the Discord client's parent frame, which answers the
SDK's real postMessage handshake and its `authorize`/`authenticate`
commands. The Activity's real token exchange and ticket requests reach
cctv's real listener, which checks them against a loopback stand-in for
Discord's OAuth API, and the office then loads over the Activity's own
WebSocket route.

What only a real Discord client can show -- the proxy, URL mappings, the
consent prompt, mobile -- is on the owner test checklist instead.

Gated like the other e2e scenarios: `PIXELAGENTS_REAL_WEBVIEW_BUILD=1`.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from cctv.domain import OfficeAccess
from e2e.fixtures import (
    FakeBot,
    capture_websocket_frames,
    construct_core_cogs,
    open_guild_office,
    real_webview_build_enabled,
    wait_for_frame,
)

APPLICATION_ID = 99
SECRET = "e2e-client-secret"
GUILD = 4242
PRIVATE_GUILD = 4343
# `FakeBot`'s default bot owner, and someone who is no member of anything
# (the fake bot has no guild cache, so only the owner passes membership).
OWNER_ID = 1
STRANGER_ID = 5

# The Discord client, as far as the Activity can tell: it hosts the
# Activity in an iframe with the query Discord adds, and answers the SDK.
PARENT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"></head><body style="margin:0">
<script>
  var params = new URLSearchParams(location.search);
  var user = params.get('user');
  var query = { frame_id: 'e2e-frame', instance_id: 'e2e-instance', platform: 'desktop',
                channel_id: '777' };
  if (params.get('guild')) { query.guild_id = params.get('guild'); }
  var frame = document.createElement('iframe');
  frame.style.cssText = 'width:100vw;height:100vh;border:0';
  frame.src = '/cctv/activity/?' + new URLSearchParams(query);
  window.addEventListener('message', function (event) {
    if (event.source !== frame.contentWindow || !Array.isArray(event.data)) { return; }
    var op = event.data[0], payload = event.data[1];
    function reply(body) { frame.contentWindow.postMessage([1, body], '*'); }
    if (op === 0) {
      reply({ cmd: 'DISPATCH', evt: 'READY', nonce: null, data: { v: 1,
        config: { api_endpoint: '//discord.com/api', environment: 'production' } } });
      return;
    }
    if (op !== 1) { return; }
    if (payload.cmd === 'AUTHORIZE') {
      window.__authorize = payload.args;
      reply({ cmd: 'AUTHORIZE', nonce: payload.nonce, evt: null, data: { code: 'code-' + user } });
    } else if (payload.cmd === 'AUTHENTICATE') {
      reply({ cmd: 'AUTHENTICATE', nonce: payload.nonce, evt: null, data: {
        access_token: payload.args.access_token, scopes: ['identify'],
        expires: '2099-01-01T00:00:00.000Z',
        user: { username: 'e2e', discriminator: '0', id: user, public_flags: 0 },
        application: { id: '__APP__', description: '', name: 'e2e' } } });
    } else {
      reply({ cmd: payload.cmd, nonce: payload.nonce, evt: null, data: null });
    }
  });
  document.body.appendChild(frame);
</script></body></html>
""".replace("__APP__", str(APPLICATION_ID))


class _FakeDiscordOAuth:
    """Discord's token exchange and `GET /oauth2/@me`: `code-<user>` trades
    for `token-<user>`, which signs `<user>` in to this application."""

    def __init__(self) -> None:
        self.secrets: list[str] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/oauth2/token", self.token)
        app.router.add_get("/oauth2/@me", self.me)
        return app

    async def token(self, request: web.Request) -> web.Response:
        form = await request.post()
        self.secrets.append(str(form.get("client_secret")))
        code = str(form.get("code", ""))
        if not code.startswith("code-"):
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "token-" + code.removeprefix("code-")})

    async def me(self, request: web.Request) -> web.Response:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        user = token.removeprefix("token-")
        if not token.startswith("token-") or not user.isdecimal():
            return web.json_response({}, status=401)
        return web.json_response({"application": {"id": str(APPLICATION_ID)}, "user": {"id": user}})


def _dashboard() -> object:
    async def secret() -> str:
        return SECRET

    return SimpleNamespace(
        config=SimpleNamespace(webserver=SimpleNamespace(core=SimpleNamespace(secret=secret)))
    )


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestOfficeAsActivity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from playwright.async_api import async_playwright

        self.bot = FakeBot()
        self.bot.application_id = APPLICATION_ID  # type: ignore[attr-defined]
        self.bot._cogs["Dashboard"] = _dashboard()  # noqa: SLF001
        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.cctv = cogs.cctv
        self.pixelagents = cogs.pixelagents

        self.discord = _FakeDiscordOAuth()
        runner = web.AppRunner(self.discord.app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        discord_port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # noqa: SLF001
        self.cctv._oauth._api = f"http://127.0.0.1:{discord_port}"  # noqa: SLF001

        await self.cctv._settings.set_activity_enabled(True)  # noqa: SLF001
        status = await self.cctv._build_activity()  # noqa: SLF001
        self.assertTrue(self.pixelagents.activity_bundle_status().ready, status)
        await open_guild_office(self.cctv, GUILD)
        await open_guild_office(self.cctv, PRIVATE_GUILD)
        await self.cctv._set_office_access(PRIVATE_GUILD, OfficeAccess.PRIVATE)  # noqa: SLF001

        server = self.cctv._server  # noqa: SLF001
        assert server is not None and server.site is not None
        self.port = server.site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # noqa: SLF001

        self._playwright = await async_playwright().start()
        self.addAsyncCleanup(self._playwright.stop)
        self.browser = await self._playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)

    async def _open(
        self, *, user: int, guild: int | None
    ) -> tuple[Any, list[dict[str, object]], list[str]]:
        page = await self.browser.new_page()
        self.addAsyncCleanup(page.close)
        await page.route(
            "**/e2e-discord*",
            lambda route: route.fulfill(body=PARENT_PAGE, content_type="text/html"),
        )
        frames = capture_websocket_frames(page)
        urls: list[str] = []
        page.on("websocket", lambda socket: urls.append(socket.url))
        query = f"user={user}" + (f"&guild={guild}" if guild is not None else "")
        await page.goto(f"http://127.0.0.1:{self.port}/e2e-discord?{query}")
        return page, frames, urls

    async def test_a_public_office_opens_from_the_sdks_guild(self) -> None:
        page, frames, urls = await self._open(user=STRANGER_ID, guild=GUILD)

        frame = await wait_for_frame(page, frames, lambda f: f.get("type") == "layoutLoaded")
        self.assertIsNotNone(frame, "the Activity never loaded the office")
        self.assertTrue(urls and urls[0].endswith(f"/cctv/activity/ws/office/{GUILD}"), urls)
        authorize = await page.evaluate("window.__authorize")
        self.assertEqual(authorize["client_id"], str(APPLICATION_ID))
        self.assertEqual(authorize["scope"], ["identify"])
        self.assertEqual(self.discord.secrets, [SECRET])
        office = self.cctv.office(GUILD)
        assert office is not None
        self.assertEqual(await self._identified(page, office), [(STRANGER_ID, False)])

    async def test_a_private_office_is_refused_to_a_non_member(self) -> None:
        page, frames, urls = await self._open(user=STRANGER_ID, guild=PRIVATE_GUILD)

        notice = page.frame_locator("iframe").locator("#cctv-activity-notice")
        await notice.wait_for(timeout=10_000)
        self.assertIn("no Pixel Agents office you can watch", await notice.inner_text())
        self.assertEqual(urls, [])
        self.assertEqual(frames, [])

    async def test_a_private_office_opens_for_a_member(self) -> None:
        page, frames, urls = await self._open(user=OWNER_ID, guild=PRIVATE_GUILD)

        frame = await wait_for_frame(page, frames, lambda f: f.get("type") == "layoutLoaded")
        self.assertIsNotNone(frame, "a member's Activity never loaded the private office")
        self.assertTrue(urls[0].endswith(f"/cctv/activity/ws/office/{PRIVATE_GUILD}"), urls)

    async def test_outside_a_server_it_opens_the_editor_office(self) -> None:
        page, frames, urls = await self._open(user=OWNER_ID, guild=None)

        frame = await wait_for_frame(page, frames, lambda f: f.get("type") == "layoutLoaded")
        self.assertIsNotNone(frame, "the Activity never loaded the editor office")
        self.assertTrue(urls[0].endswith("/cctv/activity/ws/editor"), urls)
        self.assertEqual(
            await self._identified(page, self.cctv.editor_pipeline), [(OWNER_ID, True)]
        )

    @staticmethod
    async def _identified(page: Any, pipeline: Any) -> list[tuple[int | None, bool]]:
        """Each socket's (user, may edit), once every socket presented its ticket."""

        for _ in range(50):
            states = list(pipeline.clients.clients.values())
            if states and all(state.user_id is not None for state in states):
                break
            await page.wait_for_timeout(100)
        return [(state.user_id, state.is_editor) for state in states]


if __name__ == "__main__":
    unittest.main()
