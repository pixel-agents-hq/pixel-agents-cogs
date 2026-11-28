"""The office Activity's listener routes, against a stand-in for Discord's
OAuth API on loopback: off until the owner switches it on, the token
exchange, Discord-verified tickets on each office's own rules, and the
office sockets reached through the Activity's routes."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from aiohttp import ClientSession, WSMsgType, web

from ..adapters.cog_base import CctvBase
from ..infrastructure import (
    OFFICE_UNAVAILABLE_CLOSE_CODE,
    ActivityEndpoints,
    CctvServer,
    DiscordOAuth,
    TicketStore,
    WebviewAssets,
    activity_config_script,
)
from ..infrastructure.discord_oauth import SlidingWindow
from .test_server import GUILD, MEMBER, OTHER_GUILD, STRANGER, _Pipeline

CLIENT_ID = 99
OTHER_APP = 98
SECRET = "client-secret-value"
PAGE = '<html><head><script src="./activity-config.js"></script></head><body></body></html>'


class _FakeDiscord:
    """`POST /oauth2/token` and `GET /oauth2/@me`, as Discord answers them."""

    def __init__(self) -> None:
        self.exchanges: list[dict[str, str]] = []
        self.lookups = 0
        # access token -> (application id, user id)
        self.tokens = {"token-member": (CLIENT_ID, MEMBER), "token-stranger": (CLIENT_ID, STRANGER)}
        self.tokens["token-other-app"] = (OTHER_APP, MEMBER)
        self.runner: web.AppRunner | None = None
        self.base = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/oauth2/token", self.token)
        app.router.add_get("/oauth2/@me", self.me)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    async def token(self, request: web.Request) -> web.Response:
        form = {key: str(value) for key, value in (await request.post()).items()}
        self.exchanges.append(form)
        if form.get("client_secret") != SECRET or form.get("code") != "good-code":
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "token-member", "refresh_token": "r"})

    async def me(self, request: web.Request) -> web.Response:
        self.lookups += 1
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.tokens:
            return web.json_response({"message": "401: Unauthorized"}, status=401)
        application, user = self.tokens[token]
        return web.json_response(
            {"application": {"id": str(application)}, "user": {"id": str(user)}}
        )


class _ActivityFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dist = Path(tmp.name)
        (dist / "activity.html").write_text(PAGE)
        (dist / "assets").mkdir()
        (dist / "assets" / "index-abc.js").write_text("console.log('office')")
        self.assets = WebviewAssets()
        self.assets.root = dist.resolve()
        self.assets.ready = True

    async def asyncSetUp(self) -> None:
        self.discord = _FakeDiscord()
        await self.discord.start()
        self.addAsyncCleanup(self.discord.stop)
        self.oauth = DiscordOAuth(api_base=self.discord.base)
        self.addAsyncCleanup(self.oauth.close)

        self.office = _Pipeline(f"office/{GUILD}", guild_id=GUILD)
        self.private = _Pipeline(f"office/{OTHER_GUILD}", guild_id=OTHER_GUILD, private=True)
        self.offices = {GUILD: self.office, OTHER_GUILD: self.private}
        self.editor = _Pipeline("editor", guild_id=None)
        self.tickets = TicketStore()
        self.enabled = True
        self.secret: str | None = SECRET
        self.refreshed = 0

        async def enabled() -> bool:
            return self.enabled

        async def secret() -> str | None:
            return self.secret

        async def refresh() -> None:
            self.refreshed += 1

        async def issue(user_id: int, guild_id: int | None) -> str | None:
            # The cog's rule (CctvBase._activity_ticket), on the fixture's offices.
            if guild_id is None:
                return self.tickets.mint(user_id, None)
            office = self.offices.get(guild_id)
            if office is None or (office.private and user_id not in office.members):
                return None
            return self.tickets.mint(user_id, guild_id)

        self.endpoints = ActivityEndpoints(
            enabled=enabled,
            client_id=lambda: CLIENT_ID,
            client_secret=secret,
            issue_ticket=issue,
            assets=self.assets,
            oauth=self.oauth,
            refresh_assets=refresh,
        )
        self.server = CctvServer(
            self.offices.get,
            lambda: tuple(self.offices.values()),
            self.editor,
            self.tickets,
            lambda: {"status": "ok"},
            # A Dashboard origin: Activity sockets don't come from it.
            public_origin=self._origin,
            activity=self.endpoints,
            authorize_timeout=0.5,
        )
        self.assertTrue(await self.server.start("127.0.0.1", 0))
        self.addAsyncCleanup(self.server.stop)
        assert self.server.site is not None
        port = self.server.site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base = f"http://127.0.0.1:{port}/cctv/activity"
        self.http = ClientSession()
        self.addAsyncCleanup(self.http.close)

    @staticmethod
    async def _origin() -> str | None:
        return "https://dash.example.com"

    async def _ticket(self, token: str, body: dict[str, object]) -> tuple[int, dict[str, Any]]:
        async with self.http.post(
            f"{self.base}/api/ticket", json=body, headers={"Authorization": f"Bearer {token}"}
        ) as response:
            return response.status, await response.json()


class TestActivityIsOffUntilSwitchedOn(_ActivityFixture):
    async def test_every_activity_route_is_404_while_off(self) -> None:
        self.enabled = False
        for method, path in (
            ("GET", ""),
            ("GET", "/"),
            ("GET", "/activity-config.js"),
            ("GET", "/assets/index-abc.js"),
            ("POST", "/api/token"),
            ("POST", "/api/ticket"),
            ("GET", f"/ws/office/{GUILD}"),
            ("GET", "/ws/editor"),
        ):
            async with self.http.request(method, f"{self.base}{path}", json={}) as response:
                self.assertEqual(response.status, 404, path)
        self.assertEqual(self.discord.exchanges, [])

    async def test_the_browser_routes_are_untouched(self) -> None:
        self.enabled = False
        host = self.base.removesuffix("/cctv/activity")
        async with self.http.get(f"{host}/cctv/health") as response:
            self.assertEqual(response.status, 200)


class TestActivityPage(_ActivityFixture):
    async def test_the_page_config_and_assets_are_served_relative_to_one_prefix(self) -> None:
        for path in ("", "/", "/index.html"):
            async with self.http.get(f"{self.base}{path}") as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.content_type.startswith("text/html"))
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(await response.text(), PAGE)
        self.assertEqual(self.refreshed, 3)
        async with self.http.get(f"{self.base}/activity-config.js") as response:
            script = await response.text()
            self.assertEqual(response.content_type, "text/javascript")
        self.assertIn(f"clientId: '{CLIENT_ID}'", script)
        self.assertIn("tokenUrl: 'api/token'", script)
        self.assertIn('scope: ["identify"]', script)
        async with self.http.get(f"{self.base}/assets/index-abc.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.content_type)

    async def test_paths_outside_the_bundle_are_refused(self) -> None:
        for path in ("/../secret", "/assets/%2e%2e/x", "/missing.js", "/assets/"):
            async with self.http.get(f"{self.base}{path}") as response:
                self.assertEqual(response.status, 404, path)

    async def test_an_unbuilt_activity_answers_503(self) -> None:
        (self.assets.root / "activity.html").unlink()
        async with self.http.get(f"{self.base}/") as response:
            self.assertEqual(response.status, 503)
            self.assertIn("[p]cctv activity", await response.text())

    def test_the_config_script_only_splices_the_integer_client_id(self) -> None:
        script = activity_config_script(CLIENT_ID)
        self.assertNotIn("__CLIENT_ID__", script)
        self.assertNotIn("__SCOPES__", script)
        with self.assertRaises(ValueError):
            activity_config_script("1'; alert(1); '")  # type: ignore[arg-type]


class TestTokenExchange(_ActivityFixture):
    async def test_a_code_is_exchanged_with_the_dashboard_secret(self) -> None:
        async with self.http.post(f"{self.base}/api/token", json={"code": "good-code"}) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"access_token": "token-member"})
            self.assertEqual(r.headers["Cache-Control"], "no-store")
        exchange = self.discord.exchanges[0]
        self.assertEqual(exchange["client_id"], str(CLIENT_ID))
        self.assertEqual(exchange["client_secret"], SECRET)
        self.assertEqual(exchange["grant_type"], "authorization_code")

    async def test_a_refused_code_is_400_and_never_echoes_the_secret(self) -> None:
        async with self.http.post(f"{self.base}/api/token", json={"code": "bad-code"}) as r:
            self.assertEqual(r.status, 400)
            self.assertNotIn(SECRET, await r.text())

    async def test_malformed_requests_never_reach_discord(self) -> None:
        for body in (
            {},
            {"code": ""},
            {"code": 5},
            {"code": "a b"},
            {"code": "été"},
            {"code": "x" * 600},
        ):
            async with self.http.post(f"{self.base}/api/token", json=body) as r:
                self.assertEqual(r.status, 400, body)
        async with self.http.post(f"{self.base}/api/token", data=b"x" * 5000) as r:
            self.assertEqual(r.status, 400)
        self.assertEqual(self.discord.exchanges, [])

    async def test_without_the_dashboard_secret_it_is_unavailable(self) -> None:
        self.secret = None
        async with self.http.post(f"{self.base}/api/token", json={"code": "good-code"}) as r:
            self.assertEqual(r.status, 503)
        self.assertEqual(self.discord.exchanges, [])


class TestActivityTickets(_ActivityFixture):
    async def test_a_ticket_is_for_the_discord_verified_user_and_that_office(self) -> None:
        status, body = await self._ticket("token-member", {"office": "guild", "guild": str(GUILD)})
        self.assertEqual(status, 200)
        self.assertEqual(body["ws"], f"ws/office/{GUILD}")
        self.assertIsNone(self.tickets.consume(body["ticket"], OTHER_GUILD))

        status, body = await self._ticket("token-member", {"office": "guild", "guild": str(GUILD)})
        self.assertEqual(self.tickets.consume(body["ticket"], GUILD), MEMBER)

    async def test_the_editor_ticket_is_identity_only(self) -> None:
        status, body = await self._ticket("token-stranger", {"office": "editor"})
        self.assertEqual(status, 200)
        self.assertEqual(body["ws"], "ws/editor")
        self.assertEqual(self.tickets.consume(body["ticket"], None), STRANGER)

    async def test_a_private_office_is_for_members_and_answers_like_a_missing_one(self) -> None:
        member, _ = await self._ticket(
            "token-member", {"office": "guild", "guild": str(OTHER_GUILD)}
        )
        stranger = await self._ticket(
            "token-stranger", {"office": "guild", "guild": str(OTHER_GUILD)}
        )
        missing = await self._ticket("token-stranger", {"office": "guild", "guild": "12345"})
        self.assertEqual(member, 200)
        self.assertEqual(stranger, missing)
        self.assertEqual(stranger[0], 404)

    async def test_an_unverified_token_gets_nothing(self) -> None:
        for token in ("token-unknown", "token-other-app"):
            status, _ = await self._ticket(token, {"office": "editor"})
            self.assertEqual(status, 401, token)
        async with self.http.post(f"{self.base}/api/ticket", json={"office": "editor"}) as r:
            self.assertEqual(r.status, 401)

    async def test_a_body_naming_no_office_is_400(self) -> None:
        for body in ({}, {"office": "guild"}, {"office": "guild", "guild": "-1"}, {"office": 1}):
            status, _ = await self._ticket("token-member", body)  # type: ignore[arg-type]
            self.assertEqual(status, 400, body)

    async def test_the_users_token_is_checked_with_discord_once_per_cache_window(self) -> None:
        for _ in range(3):
            await self._ticket("token-member", {"office": "editor"})
        self.assertEqual(self.discord.lookups, 1)


class TestActivitySockets(_ActivityFixture):
    async def _connect(self, path: str) -> Any:
        url = f"{self.base.replace('http', 'ws', 1)}/{path}"
        return await self.http.ws_connect(
            url, headers={"Origin": "https://app.discordsays.example"}
        )

    async def test_a_private_office_opens_for_a_members_activity_ticket(self) -> None:
        _, body = await self._ticket("token-member", {"office": "guild", "guild": str(OTHER_GUILD)})
        socket = await self._connect(body["ws"])
        await socket.send_str(json.dumps({"type": "webviewReady"}))
        await socket.send_str(json.dumps({"type": "authorize", "ticket": body["ticket"]}))
        reply = await asyncio.wait_for(socket.receive(), 5)
        self.assertEqual(json.loads(reply.data), {"type": "layoutLoaded"})
        await socket.close()

    async def test_a_private_office_without_a_ticket_is_closed(self) -> None:
        socket = await self._connect(f"ws/office/{OTHER_GUILD}")
        reply = await asyncio.wait_for(socket.receive(), 5)
        self.assertEqual(reply.type, WSMsgType.CLOSE)
        self.assertEqual(socket.close_code, OFFICE_UNAVAILABLE_CLOSE_CODE)

    async def test_switching_it_off_disconnects_only_activity_sockets(self) -> None:
        activity = await self._connect("ws/editor")
        host = self.base.removesuffix("/cctv/activity").replace("http", "ws", 1)
        browser = await self.http.ws_connect(f"{host}/cctv/editor/ws")
        await asyncio.sleep(0.05)

        closed = await self.server.close_activity_sockets(code=OFFICE_UNAVAILABLE_CLOSE_CODE)

        self.assertEqual(closed, 1)
        reply = await asyncio.wait_for(activity.receive(), 5)
        self.assertEqual(reply.type, WSMsgType.CLOSE)
        self.assertFalse(browser.closed)
        await browser.close()


class TestSlidingWindow(unittest.TestCase):
    def test_allows_the_limit_per_window(self) -> None:
        now = [0.0]
        window = SlidingWindow(2, 60.0, clock=lambda: now[0])
        self.assertTrue(window.allow())
        self.assertTrue(window.allow())
        self.assertFalse(window.allow())
        now[0] = 61.0
        self.assertTrue(window.allow())


class TestRateLimit(_ActivityFixture):
    async def test_too_many_sign_ins_are_429(self) -> None:
        self.oauth._window = SlidingWindow(1, 60.0)  # noqa: SLF001
        async with self.http.post(f"{self.base}/api/token", json={"code": "good-code"}) as r:
            self.assertEqual(r.status, 200)
        async with self.http.post(f"{self.base}/api/token", json={"code": "good-code"}) as r:
            self.assertEqual(r.status, 429)


class _Cog(CctvBase):
    """Just the Activity's cog-side rules, without `cog_load`."""

    def __init__(self, *, dashboard: object = None, private: bool = False) -> None:
        self._tickets = TicketStore()
        self._office = SimpleNamespace(private=private)
        self.bot = SimpleNamespace(  # type: ignore[assignment]
            get_cog=lambda name: dashboard if name == "Dashboard" else None,
            application_id=CLIENT_ID,
        )

    def office(self, guild_id: int) -> Any:
        return self._office if guild_id == GUILD else None

    async def _is_member(self, guild_id: int, user_id: int) -> bool:
        return user_id == MEMBER


def _dashboard(secret: object) -> object:
    async def read() -> object:
        return secret

    return SimpleNamespace(
        config=SimpleNamespace(webserver=SimpleNamespace(core=SimpleNamespace(secret=read)))
    )


class TestCogActivityRules(unittest.IsolatedAsyncioTestCase):
    async def test_the_secret_is_read_from_the_loaded_dashboard_each_time(self) -> None:
        self.assertEqual(
            await _Cog(dashboard=_dashboard(SECRET))._dashboard_client_secret(), SECRET
        )
        self.assertIsNone(await _Cog(dashboard=_dashboard(None))._dashboard_client_secret())
        self.assertIsNone(await _Cog(dashboard=_dashboard(""))._dashboard_client_secret())
        self.assertIsNone(await _Cog()._dashboard_client_secret())

    async def test_the_client_id_is_the_bots_own_application(self) -> None:
        self.assertEqual(_Cog()._activity_client_id(), CLIENT_ID)

    async def test_tickets_follow_the_offices_own_rules(self) -> None:
        public = _Cog()
        ticket = await public._activity_ticket(STRANGER, GUILD)
        assert ticket is not None
        self.assertEqual(public._tickets.consume(ticket, GUILD), STRANGER)
        self.assertIsNone(await public._activity_ticket(STRANGER, OTHER_GUILD))

        private = _Cog(private=True)
        self.assertIsNone(await private._activity_ticket(STRANGER, GUILD))
        self.assertIsNotNone(await private._activity_ticket(MEMBER, GUILD))

        editor = await public._activity_ticket(STRANGER, None)
        assert editor is not None
        self.assertEqual(public._tickets.consume(editor, None), STRANGER)
        self.assertIsNone(await public._activity_ticket(0, None))


if __name__ == "__main__":
    unittest.main()
