"""The office Activity's listener routes, against a stand-in for Discord's
OAuth API on loopback: off until the owner switches it on, the token
exchange, Discord-verified tickets on each office's own rules, and the
office sockets reached through the Activity's routes."""

from __future__ import annotations

import asyncio
import json
import secrets
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
from .test_server import GUILD, MEMBER, OTHER_GUILD, STRANGER, _Pipeline

CLIENT_ID = 99
OTHER_APP = 98
SECRET = "client-secret-value"
PAGE = '<html><head><script src="./activity-config.js"></script></head><body></body></html>'


class _FakeDiscord:
    """`POST /oauth2/token` and `GET /oauth2/@me`, as Discord answers them."""

    def __init__(self) -> None:
        self.exchanges: list[dict[str, str]] = []
        self.token_calls = 0
        self.lookups = 0
        # What Discord says a new token lives for.
        self.expires_in = 604800
        # code -> the access token it trades for
        self.codes = {
            "good-code": "token-member",
            "stranger-code": "token-stranger",
            "other-app-code": "token-other-app",
        }
        # Seconds Discord's next answer asks to wait (a 429), if set, and the
        # rate limit's scope it names.
        self.retry_after: str | None = None
        self.scope = "user"
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

    def _limited(self) -> web.Response | None:
        if self.retry_after is None:
            return None
        retry, self.retry_after = self.retry_after, None
        return web.json_response(
            {"message": "You are being rate limited."},
            status=429,
            headers={"Retry-After": retry, "X-RateLimit-Scope": self.scope},
        )

    async def token(self, request: web.Request) -> web.Response:
        self.token_calls += 1
        if (limited := self._limited()) is not None:
            return limited
        form = {key: str(value) for key, value in (await request.post()).items()}
        self.exchanges.append(form)
        if form.get("client_secret") != SECRET:
            return web.json_response({"error": "invalid_client"}, status=401)
        token = self.codes.get(form.get("code", ""))
        if token is None:
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response(
            {"access_token": token, "refresh_token": "r", "expires_in": self.expires_in}
        )

    async def me(self, request: web.Request) -> web.Response:
        if (limited := self._limited()) is not None:
            return limited
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
        self.now = 1000.0
        self.sessions: dict[str, str] = {}
        self.oauth = DiscordOAuth(api_base=self.discord.base, clock=lambda: self.now)
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

    async def _sign_in(self, code: str, headers: dict[str, str] | None = None) -> int:
        """Sign in; the session id the listener issues is kept under the
        Discord access token it came with, for `_bearer`."""

        async with self.http.post(
            f"{self.base}/api/token", json={"code": code}, headers=headers or {}
        ) as r:
            if r.status == 200:
                body = await r.json()
                self.sessions[body["access_token"]] = body["session"]
            return r.status

    def _bearer(self, token: str) -> str:
        """The session id a sign-in with `token` was issued, else `token`."""

        return self.sessions.get(token, token)

    async def _ticket(self, token: str, body: dict[str, object]) -> tuple[int, dict[str, Any]]:
        async with self.http.post(
            f"{self.base}/api/ticket",
            json=body,
            headers={"Authorization": f"Bearer {self._bearer(token)}"},
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
            body = await r.json()
            self.assertEqual(body["access_token"], "token-member")
            # A session id the listener issued, unrelated to the token.
            self.assertGreaterEqual(len(body["session"]), 40)
            self.assertNotIn("token-member", body["session"])
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


class _SignedIn(_ActivityFixture):
    """The member and the stranger have each signed in through `api/token`."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.assertEqual(await self._sign_in("good-code"), 200)
        self.assertEqual(await self._sign_in("stranger-code"), 200)


class TestActivityTickets(_SignedIn):
    async def test_a_ticket_is_for_the_signed_in_user_and_that_office(self) -> None:
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

    async def test_a_token_of_another_application_never_signs_in(self) -> None:
        self.assertEqual(await self._sign_in("other-app-code"), 503)
        status, _ = await self._ticket("token-other-app", {"office": "editor"})
        self.assertEqual(status, 401)
        async with self.http.post(f"{self.base}/api/ticket", json={"office": "editor"}) as r:
            self.assertEqual(r.status, 401)

    async def test_a_body_naming_no_office_is_400(self) -> None:
        for body in ({}, {"office": "guild"}, {"office": "guild", "guild": "-1"}, {"office": 1}):
            status, _ = await self._ticket("token-member", body)  # type: ignore[arg-type]
            self.assertEqual(status, 400, body)

    async def test_tickets_never_call_discord(self) -> None:
        lookups = self.discord.lookups
        for _ in range(3):
            await self._ticket("token-member", {"office": "editor"})
        self.assertEqual(self.discord.lookups, lookups)


class TestActivitySessions(_SignedIn):
    async def test_a_sign_in_stays_alive_while_used(self) -> None:
        for _ in range(4):
            self.now += 3600
            status, _ = await self._ticket("token-member", {"office": "editor"})
            self.assertEqual(status, 200)

    async def test_an_idle_sign_in_ends(self) -> None:
        self.now += 2 * 3600 + 1
        status, _ = await self._ticket("token-member", {"office": "editor"})
        self.assertEqual(status, 401)

    async def test_a_sign_in_ends_with_its_token(self) -> None:
        self.discord.expires_in = 5000
        self.assertEqual(await self._sign_in("good-code"), 200)
        self.now += 3000
        self.assertEqual((await self._ticket("token-member", {"office": "editor"}))[0], 200)
        self.now += 3000
        self.assertEqual((await self._ticket("token-member", {"office": "editor"}))[0], 401)


class TestActivitySockets(_SignedIn):
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


class TestJunkNeverBlocksARealSignIn(_ActivityFixture):
    """Every Activity user arrives through Discord's proxy, from one shared
    upstream address. Junk from that same address must never stop a valid
    sign-in, nor make Discord answer this listener with refusals."""

    SHARED = {"X-Forwarded-For": "203.0.113.7"}

    async def _shared_ticket(self, token: str) -> int:
        async with self.http.post(
            f"{self.base}/api/ticket",
            json={"office": "editor"},
            headers={**self.SHARED, "Authorization": f"Bearer {self._bearer(token)}"},
        ) as r:
            return r.status

    async def test_a_flood_of_junk_from_the_same_address_doesnt_stop_a_valid_sign_in(
        self,
    ) -> None:
        for n in range(100):
            self.assertEqual(await self._sign_in(f"junk-code-{n}", self.SHARED), 400)
            self.assertEqual(await self._shared_ticket(f"junk-token-{n}"), 401)
        self.assertEqual(await self._sign_in("good-code", self.SHARED), 200)
        self.assertEqual(await self._shared_ticket("token-member"), 200)
        # Only the real sign-in checked a token with Discord.
        self.assertEqual(self.discord.lookups, 1)

    async def test_unknown_bearers_never_reach_discord(self) -> None:
        for token in [secrets.token_urlsafe(30) for _ in range(50)] + ["a b", "x" * 600, "étè"]:
            self.assertEqual(await self._shared_ticket(token), 401)
        self.assertEqual(self.discord.lookups, 0)
        self.assertEqual(self.discord.token_calls, 0)

    async def test_a_repeated_junk_code_costs_no_further_call(self) -> None:
        for _ in range(5):
            self.assertEqual(await self._sign_in("bad-code", self.SHARED), 400)
        self.assertEqual(len(self.discord.exchanges), 1)
        # Remembered for a while, then asked about again.
        self.now += 601
        self.assertEqual(await self._sign_in("bad-code", self.SHARED), 400)
        self.assertEqual(len(self.discord.exchanges), 2)

    async def test_discords_own_429_is_honoured_then_lifted(self) -> None:
        self.assertEqual(await self._sign_in("stranger-code"), 200)
        self.discord.retry_after = "30"
        self.assertEqual(await self._sign_in("good-code"), 429)
        self.assertEqual(await self._sign_in("good-code"), 429)
        # The second one wasn't sent: the route waits out Retry-After.
        self.assertEqual(self.discord.token_calls, 2)
        # Signed-in users' tickets don't depend on Discord at all.
        self.assertEqual(await self._shared_ticket("token-stranger"), 200)
        self.now += 31
        self.assertEqual(await self._sign_in("good-code"), 200)

    async def test_a_wrong_client_secret_stops_asking(self) -> None:
        self.secret = "not-the-secret"
        self.assertEqual(await self._sign_in("good-code"), 503)
        self.assertEqual(await self._sign_in("good-code"), 429)
        self.assertEqual(self.discord.token_calls, 1)

    async def test_a_flood_of_400_refused_codes_never_shields_a_real_sign_in(self) -> None:
        self.oauth._rejections_limit = 5  # noqa: SLF001
        for n in range(50):
            self.assertEqual(await self._sign_in(f"junk-{n}", self.SHARED), 400)
        # Discord's invalid-request limit doesn't count 400s: nothing to shield.
        self.assertEqual(self.discord.token_calls, 50)
        self.assertEqual(await self._sign_in("good-code", self.SHARED), 200)
        self.assertEqual(await self._shared_ticket("token-member"), 200)

    async def test_refusals_the_limit_counts_raise_the_shield(self) -> None:
        self.oauth._rejections_limit = 3  # noqa: SLF001
        self.secret = "not-the-secret"
        for _ in range(3):
            # 401 invalid_client; the minute's back-off is waited out each time.
            self.assertEqual(await self._sign_in("good-code"), 503)
            self.now += 61
        self.secret = SECRET
        # Past the cap: refused here, with how long to wait, without a call.
        async with self.http.post(f"{self.base}/api/token", json={"code": "good-code"}) as r:
            self.assertEqual(r.status, 503)
            self.assertEqual(int(r.headers["Retry-After"]), 600 - 3 * 61)
        self.assertEqual(self.discord.token_calls, 3)
        # The bot's address comes first; once the oldest ages out, it lifts.
        self.now += 600
        self.assertEqual(await self._sign_in("good-code"), 200)

    async def test_shared_scope_429s_dont_count(self) -> None:
        self.oauth._rejections_limit = 2  # noqa: SLF001
        self.discord.scope = "shared"
        for _ in range(3):
            self.discord.retry_after = "1"
            self.assertEqual(await self._sign_in("good-code"), 429)
            self.now += 2
        self.assertEqual(await self._sign_in("good-code"), 200)

    async def test_discords_429s_count_toward_the_cap(self) -> None:
        self.oauth._rejections_limit = 2  # noqa: SLF001
        for _ in range(2):
            self.discord.retry_after = "1"
            self.assertEqual(await self._sign_in("good-code"), 429)
            self.now += 2
        self.assertEqual(await self._sign_in("good-code"), 503)
        self.assertEqual(self.discord.token_calls, 2)

    async def test_the_discord_access_token_is_no_ticket_bearer(self) -> None:
        self.assertEqual(await self._sign_in("good-code"), 200)
        async with self.http.post(
            f"{self.base}/api/ticket",
            json={"office": "editor"},
            headers={"Authorization": "Bearer token-member"},
        ) as r:
            self.assertEqual(r.status, 401)
        self.assertEqual(self.discord.lookups, 1)


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
