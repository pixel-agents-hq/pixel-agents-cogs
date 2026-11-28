"""The office as a Discord Activity: every route the Activity uses, under one
listener prefix (`ACTIVITY_PREFIX`), so a single Discord URL mapping
(`/` -> `<public host>/cctv/activity`) reaches all of it.

The page is pixelagents' Activity build (`activity.html`), served as is. It
loads `activity-config.js` from here, then pixelagents' SDK bootstrap, which
runs the Embedded App SDK handshake and OAuth `authorize()`, trades the code
for a token at `api/token`, and calls `authenticate()`. The config's `start`
hook then picks the office from the SDK's guild (or the editor office) and
asks `api/ticket` for a ticket, the same single-use ticket a Dashboard page
gets, which each office socket presents in-band. Every URL the page uses is
relative to it, so it works under any mapping target.

Nothing the client sends is trusted: the user comes from Discord
(`GET /oauth2/@me` with the token, which must belong to this bot's
application), and the office's own rules -- membership for a private office,
the edit gate on every editor write -- are checked bot-side as for a browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import string
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from aiohttp import web

from .discord_oauth import DiscordOAuth, OAuthError, RateLimited, Refused, Shielded

ACTIVITY_PREFIX = "/cctv/activity"
ACTIVITY_OFFICE_WS_ROUTE = f"{ACTIVITY_PREFIX}/ws/office/{{guild_id}}"
ACTIVITY_EDITOR_WS_ROUTE = f"{ACTIVITY_PREFIX}/ws/editor"
# Files of pixelagents' Activity build (pixelagents/infrastructure/
# webview_build.py `ACTIVITY_PAGE`, `ACTIVITY_CONFIG_SCRIPT`).
ACTIVITY_PAGE = "activity.html"
ACTIVITY_CONFIG_SCRIPT = "activity-config.js"
# The OAuth scope the Activity asks for: who the user is, nothing more.
# Membership is checked bot-side, as for the Dashboard pages.
ACTIVITY_SCOPES = ("identify",)
MAX_BODY_BYTES = 4096
_MAX_CODE_LENGTH = 512
_CODE_CHARACTERS = frozenset(string.ascii_letters + string.digits + "-_.")
_NO_STORE = {"Cache-Control": "no-store"}

TicketIssuer = Callable[[int, "int | None"], Awaitable["str | None"]]
SecretSource = Callable[[], Awaitable["str | None"]]


class ActivityAssets(Protocol):
    ready: bool

    def resolve(self, asset_path: str) -> object: ...
    @staticmethod
    def content_type(asset_path: str) -> str: ...


def activity_office_ws(guild_id: int) -> str:
    """A guild office's socket, relative to the Activity page."""

    return f"ws/office/{int(guild_id)}"


ACTIVITY_EDITOR_WS = "ws/editor"

_UNAVAILABLE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Pixel Agents</title></head>
<body style="margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;
background:#1e1e2e;color:#e6e6f0;font:16px/1.5 system-ui,sans-serif;text-align:center">
<p>The Pixel Agents Activity isn't available right now. Ask the bot owner to check
<code>[p]cctv activity</code>.</p></body></html>
"""

# Served as `activity-config.js`. Only the integer client id is spliced in;
# every message is a constant.
_CONFIG_SCRIPT = """(function () {
  'use strict';
  var Native = window.WebSocket;
  var SNOWFLAKE = /^[0-9]{1,20}$/;
  var token = null;
  var office = null;
  var spare = null;
  var NO_OFFICE = "This server has no Pixel Agents office you can watch here.";
  var NO_EDITOR = "The editor office isn't available right now.";
  var GONE = "This office is no longer available to you here.";
  var SIGNED_OUT = "Your sign-in here has ended. Close the Activity and open it again.";

  function notice(text, label, action) {
    var note = document.getElementById('cctv-activity-notice');
    if (!note) {
      note = document.createElement('div');
      note.id = 'cctv-activity-notice';
      note.setAttribute('role', 'alert');
      note.style.cssText = 'position:fixed;inset:0;display:flex;flex-direction:column;gap:16px;'
        + 'align-items:center;justify-content:center;padding:24px;text-align:center;'
        + 'z-index:2147483647;background:#1e1e2e;color:#e6e6f0;font:16px/1.5 system-ui,sans-serif';
      (document.body || document.documentElement).appendChild(note);
    }
    note.textContent = '';
    var line = document.createElement('p');
    line.textContent = text;
    note.appendChild(line);
    if (label) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.style.cssText = 'font:inherit;padding:8px 16px;border-radius:8px;border:0;'
        + 'background:#5865f2;color:#fff;cursor:pointer';
      button.addEventListener('click', action);
      note.appendChild(button);
    }
  }

  function show(editor) {
    location.hash = editor ? 'editor' : '';
    location.reload();
  }

  function requestTicket() {
    return fetch(new URL('api/ticket', location.href).href, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json', Accept: 'application/json',
        Authorization: 'Bearer ' + token
      },
      body: JSON.stringify(office.request)
    }).then(function (r) {
      if (r.status === 401) { notice(SIGNED_OUT); return { signedOut: true }; }
      return r.ok ? r.json() : null;
    }).catch(function () { return null; });
  }

  function nextTicket() {
    if (spare) { var ticket = spare; spare = null; return Promise.resolve(ticket); }
    return requestTicket().then(function (data) { return (data && data.ticket) || null; });
  }

  // Every socket the webview opens goes to this office's route, relative to
  // the page, and presents its own single-use ticket in-band.
  function Patched(url, protocols) {
    var target = new URL(office.ws, location.href);
    target.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var socket = protocols === undefined
      ? new Native(target.href) : new Native(target.href, protocols);
    nextTicket().then(function (ticket) {
      if (!ticket) { return; }
      var payload = JSON.stringify({ type: 'authorize', ticket: ticket });
      if (socket.readyState === Native.OPEN) { socket.send(payload); }
      else if (socket.readyState === Native.CONNECTING) {
        socket.addEventListener('open', function once() {
          socket.removeEventListener('open', once); socket.send(payload);
        });
      }
    });
    socket.addEventListener('close', function (event) {
      if (event.code === 4404) { notice(GONE); }
    });
    return socket;
  }
  Patched.prototype = Native.prototype;
  Patched.CONNECTING = Native.CONNECTING; Patched.OPEN = Native.OPEN;
  Patched.CLOSING = Native.CLOSING; Patched.CLOSED = Native.CLOSED;

  function switcher(editor) {
    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = editor ? 'Server office' : 'Editor office';
    button.title = editor ? "Back to this server's office" : "Watch the agents' editor office";
    button.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483646;opacity:.8;'
      + 'font:12px/1 system-ui,sans-serif;padding:6px 10px;border-radius:6px;border:0;'
      + 'background:#2a2a3c;color:#e6e6f0;cursor:pointer';
    button.addEventListener('click', function () { show(!editor); });
    (document.body || document.documentElement).appendChild(button);
  }

  window.PIXEL_AGENTS_ACTIVITY = {
    clientId: '__CLIENT_ID__',
    tokenUrl: 'api/token',
    scope: __SCOPES__,
    start: function (context) {
      token = context.signIn && typeof context.signIn.session === 'string'
        ? context.signIn.session : null;
      if (!token) { notice(SIGNED_OUT); return false; }
      var guild = context.guildId && SNOWFLAKE.test(context.guildId) ? context.guildId : null;
      var editor = !guild || location.hash === '#editor';
      office = { request: editor ? { office: 'editor' } : { office: 'guild', guild: guild } };
      return requestTicket().then(function (data) {
        if (data && data.signedOut) { return false; }
        if (!data || !data.ticket || typeof data.ws !== 'string') {
          if (editor) { notice(NO_EDITOR); }
          else { notice(NO_OFFICE, 'Open the editor office', function () { show(true); }); }
          return false;
        }
        office.ws = data.ws;
        spare = data.ticket;
        window.WebSocket = Patched;
        if (guild) { switcher(editor); }
        return true;
      });
    }
  };
})();
"""


def activity_config_script(client_id: int) -> str:
    return _CONFIG_SCRIPT.replace("__CLIENT_ID__", str(int(client_id))).replace(
        "__SCOPES__", json.dumps(list(ACTIVITY_SCOPES))
    )


def _error(status: int, error: str, *, retry_after: float | None = None) -> web.Response:
    headers = dict(_NO_STORE)
    if retry_after is not None:
        headers["Retry-After"] = str(max(1, math.ceil(retry_after)))
    return web.json_response({"error": error}, status=status, headers=headers)


async def _json_body(request: web.Request) -> dict[str, object] | None:
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        return None
    raw = await request.content.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        return None
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _well_formed(value: str) -> bool:
    """An OAuth code or access token's shape: 1-512 ASCII URL-safe
    characters. Anything else is refused here, without asking Discord."""

    return 0 < len(value) <= _MAX_CODE_LENGTH and all(
        character in _CODE_CHARACTERS for character in value
    )


def _snowflake(raw: object) -> int | None:
    text = raw if isinstance(raw, str) else ""
    return int(text) if text.isdecimal() and 0 < len(text) <= 20 and int(text) > 0 else None


@dataclass(frozen=True, slots=True)
class OfficeChoice:
    """The office an Activity asks for: a guild's, or (None) the editor's."""

    guild_id: int | None


def parse_office_request(body: dict[str, object]) -> OfficeChoice | None:
    """`{"office": "guild", "guild": "<id>"}` or `{"office": "editor"}`."""

    kind = body.get("office")
    if kind == "editor":
        return OfficeChoice(None)
    guild_id = _snowflake(body.get("guild")) if kind == "guild" else None
    return OfficeChoice(guild_id) if guild_id is not None else None


class ActivityEndpoints:
    def __init__(
        self,
        *,
        enabled: Callable[[], Awaitable[bool]],
        client_id: Callable[[], int | None],
        client_secret: SecretSource,
        issue_ticket: TicketIssuer,
        assets: ActivityAssets,
        oauth: DiscordOAuth,
        refresh_assets: Callable[[], Awaitable[object]] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._refresh_assets = refresh_assets
        self._enabled = enabled
        self._client_id = client_id
        self._client_secret = client_secret
        self._issue_ticket = issue_ticket
        self._assets = assets
        self._oauth = oauth
        self._log = logger or logging.getLogger(f"red.pixel-agents-cogs.{__name__}")

    def add_routes(
        self,
        router: web.UrlDispatcher,
        *,
        office_ws: Callable[[web.Request], Awaitable[web.StreamResponse]],
        editor_ws: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> None:
        router.add_get(ACTIVITY_PREFIX, self.handle_page)
        router.add_get(f"{ACTIVITY_PREFIX}/", self.handle_page)
        router.add_get(f"{ACTIVITY_PREFIX}/index.html", self.handle_page)
        router.add_get(f"{ACTIVITY_PREFIX}/{ACTIVITY_CONFIG_SCRIPT}", self.handle_config)
        router.add_post(f"{ACTIVITY_PREFIX}/api/token", self.handle_token)
        router.add_post(f"{ACTIVITY_PREFIX}/api/ticket", self.handle_ticket)
        router.add_get(ACTIVITY_OFFICE_WS_ROUTE, self._gate(office_ws))
        router.add_get(ACTIVITY_EDITOR_WS_ROUTE, self._gate(editor_ws))
        router.add_get(f"{ACTIVITY_PREFIX}/{{asset_path:.+}}", self.handle_static)

    def _gate(
        self, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
        async def gated(request: web.Request) -> web.StreamResponse:
            if not await self._available():
                raise web.HTTPNotFound()
            return await handler(request)

        return gated

    async def _available(self) -> bool:
        """Whether the bot owner has switched the Activity on. Off, every
        Activity route answers 404, as if it didn't exist."""

        try:
            return bool(await self._enabled()) and self._client_id() is not None
        except Exception:
            self._log.error("cctv/activity: could not read whether it is on", exc_info=True)
            return False

    async def handle_page(self, request: web.Request) -> web.StreamResponse:
        del request
        if not await self._available():
            raise web.HTTPNotFound()
        if self._refresh_assets is not None:
            # Picks up a rebuilt bundle, as a Dashboard page load does.
            await self._refresh_assets()
        page = (
            await asyncio.to_thread(self._assets.resolve, ACTIVITY_PAGE)
            if self._assets.ready
            else None
        )
        if page is None:
            return web.Response(
                text=_UNAVAILABLE_PAGE, status=503, content_type="text/html", headers=_NO_STORE
            )
        return web.FileResponse(
            str(page), headers={"Content-Type": "text/html; charset=utf-8", **_NO_STORE}
        )

    async def handle_config(self, request: web.Request) -> web.StreamResponse:
        del request
        client_id = self._client_id()
        if client_id is None or not await self._available():
            raise web.HTTPNotFound()
        return web.Response(
            text=activity_config_script(client_id),
            content_type="text/javascript",
            charset="utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    async def handle_static(self, request: web.Request) -> web.StreamResponse:
        if not await self._available():
            raise web.HTTPNotFound()
        asset_path = request.match_info.get("asset_path", "")
        resolved = await asyncio.to_thread(self._assets.resolve, asset_path)
        if resolved is None:
            raise web.HTTPNotFound()
        # Content-hashed bundle files can be cached; the rest revalidate.
        hashed = asset_path.startswith("assets/") and not asset_path.startswith("assets/decoded/")
        return web.FileResponse(
            str(resolved),
            headers={
                "Content-Type": self._assets.content_type(asset_path),
                "Cache-Control": "public, max-age=3600" if hashed else "no-cache",
            },
        )

    async def handle_token(self, request: web.Request) -> web.Response:
        """`{code}` from the SDK's `authorize()` -> `{access_token, session}`, exchanged
        with the client secret Red Web Dashboard holds for this application."""

        if not await self._available():
            raise web.HTTPNotFound()
        body = await _json_body(request)
        code = body.get("code") if body is not None else None
        if not isinstance(code, str) or not _well_formed(code):
            return _error(400, "invalid request")
        client_id = self._client_id()
        secret = await self._secret()
        if client_id is None or secret is None:
            return _error(503, "unavailable")
        try:
            token, session, _ = await self._oauth.sign_in(client_id, secret, code)
        except Shielded as exc:
            return _error(503, "unavailable", retry_after=exc.retry_after)
        except RateLimited as exc:
            return _error(429, "busy", retry_after=exc.retry_after)
        except Refused:
            return _error(400, "sign-in failed")
        except OAuthError as exc:
            self._log.info("cctv/activity: %s", exc)
            return _error(503, "unavailable")
        # The access token is only for the SDK's `authenticate()`; tickets
        # take the session id this listener issued.
        return web.json_response({"access_token": token, "session": session}, headers=_NO_STORE)

    async def _secret(self) -> str | None:
        try:
            secret = await self._client_secret()
        except Exception:
            self._log.error("cctv/activity: could not read the client secret", exc_info=True)
            return None
        return secret if isinstance(secret, str) and secret else None

    async def handle_ticket(self, request: web.Request) -> web.Response:
        """A ticket for one office's socket, for the user the bearer token
        signs in. Only a session id this listener issued (`api/token`) is
        accepted, looked up without calling Discord; any other bearer is 401.
        Every office the user may not watch -- none open, a private one they
        aren't a member of -- answers the same 404."""

        if not await self._available():
            raise web.HTTPNotFound()
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        client_id = self._client_id()
        if scheme.lower() != "bearer" or not _well_formed(token) or client_id is None:
            return _error(401, "sign in first")
        body = await _json_body(request)
        parsed = parse_office_request(body) if body is not None else None
        if parsed is None:
            return _error(400, "invalid request")
        guild_id = parsed.guild_id
        user_id = self._oauth.signed_in_user(token)
        if user_id is None:
            return _error(401, "sign in first")
        try:
            ticket = await self._issue_ticket(user_id, guild_id)
        except Exception:
            self._log.error("cctv/activity: ticket check failed for %d", user_id, exc_info=True)
            ticket = None
        if ticket is None:
            return _error(404, "unavailable")
        ws = activity_office_ws(guild_id) if guild_id is not None else ACTIVITY_EDITOR_WS
        return web.json_response({"ticket": ticket, "ws": ws}, headers=_NO_STORE)


__all__ = [
    "ACTIVITY_CONFIG_SCRIPT",
    "ACTIVITY_EDITOR_WS",
    "ACTIVITY_EDITOR_WS_ROUTE",
    "ACTIVITY_OFFICE_WS_ROUTE",
    "ACTIVITY_PAGE",
    "ACTIVITY_PREFIX",
    "ACTIVITY_SCOPES",
    "ActivityEndpoints",
    "OfficeChoice",
    "activity_config_script",
    "activity_office_ws",
    "parse_office_request",
]
