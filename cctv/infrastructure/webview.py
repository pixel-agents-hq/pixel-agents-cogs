"""Shared Pixel Agents assets and per-page HTML injection."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from collections.abc import Mapping, Sequence, Sized
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

WEBVIEW_BASE_PATH = "/third-party/cctv/static/"
WEBVIEW_CACHE_CONTROL = "public, max-age=3600"

FURNITURE_KEYS = frozenset(
    {
        "id",
        "name",
        "label",
        "category",
        "file",
        "width",
        "height",
        "footprintW",
        "footprintH",
        "isDesk",
        "canPlaceOnWalls",
        "groupId",
        "canPlaceOnSurfaces",
        "backgroundTiles",
        "orientation",
        "state",
        "mirrorSide",
        "rotationScheme",
        "animationGroup",
        "frame",
    }
)


# Asset key -> the file pixelagents' build writes under `assets/decoded/`
# (webview_build_scripts/emit_decoded_assets.ts). Every one but the cast is
# a decoded sprite family; the cast is upstream's default character
# profiles, which the webview composes into sprites itself.
DECODED_ASSETS = {
    "characterProfiles": "character-profiles.json",
    "floors": "floors.json",
    "walls": "walls.json",
    "carpets": "carpets.json",
    "furniture": "furniture.json",
}


class WebviewStatus(Protocol):
    dist_path: Path
    ready: bool
    detail: str
    built_commit: str | None


def office_ws_path(guild_id: int) -> str:
    """The WebSocket path of one guild's office."""

    return f"/cctv/office/{int(guild_id)}/ws"


EDITOR_WS_PATH = "/cctv/editor/ws"
PICKER_PATH = "/third-party/cctv"


def office_page_path(guild_id: int) -> str:
    """The Dashboard path of one guild's office page."""

    return f"/third-party/cctv/office?guild={int(guild_id)}"


def _connection_shim(ws_path: str, *, identify: bool) -> str:
    # The Dashboard renders a page's source as a Jinja template, so nothing
    # user-controlled may ever be spliced in here: `ws_path` is built only
    # from an integer guild id (office_ws_path) or a constant.
    ticket = (
        """
  var ticketPromise = fetch('/third-party/cctv/session', {
    credentials: 'same-origin', headers: { Accept: 'application/json' }
  }).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { return (data && data.ticket) || null; })
    .catch(function () { return null; });
"""
        if identify
        else ""
    )
    authorize = (
        """
    ticketPromise.then(function (ticket) {
      if (!ticket) { return; }
      var payload = JSON.stringify({ type: 'authorize', ticket: ticket });
      if (socket.readyState === Native.OPEN) { socket.send(payload); }
      else if (socket.readyState === Native.CONNECTING) {
        socket.addEventListener('open', function once() {
          socket.removeEventListener('open', once); socket.send(payload);
        });
      }
    });
"""
        if identify
        else ""
    )
    return f"""<script>
(function () {{
  var Native = window.WebSocket;
  {ticket}
  function Patched(url, protocols) {{
    var target = new URL(url, location.href);
    target.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    target.host = location.host;
    target.pathname = '{ws_path}';
    target.search = '';
    var socket = protocols === undefined
      ? new Native(target.toString()) : new Native(target.toString(), protocols);
    {authorize}
    return socket;
  }}
  Patched.prototype = Native.prototype;
  Patched.CONNECTING = Native.CONNECTING; Patched.OPEN = Native.OPEN;
  Patched.CLOSING = Native.CLOSING; Patched.CLOSED = Native.CLOSED;
  window.WebSocket = Patched;
}})();
</script>"""


class WebviewAssets:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.root = Path()
        self.assets: dict[str, object] = {}
        self.ready = False
        self.error: str | None = "Pixel Agents bundle has not been synchronized."
        self.built_commit: str | None = None
        # Names of sprite families/catalogs that failed to decode on the
        # most recent `_load_assets()` run -- populated even when `ready`
        # stays True, since only a missing `characters` family currently
        # flips that. Lets a caller (CctvBase) notify the bot owner about a
        # degraded-but-serving webview instead of leaving it to a log line
        # nobody watches. See `degraded_asset_notification()`.
        self.degraded: tuple[str, ...] = ()
        self._log = logger or logging.getLogger(__name__)

    def sync(self, status: WebviewStatus) -> None:
        root = status.dist_path.resolve()
        ready = status.ready
        commit = status.built_commit
        self.root = root
        self.ready = ready
        self.error = None if ready else status.detail
        if ready and (commit != self.built_commit or not self.assets):
            self._load_assets()
            self.built_commit = commit

    def resolve(self, asset_path: str) -> Path | None:
        clean = asset_path.strip().lstrip("/")
        if not clean or "\x00" in clean:
            return None
        candidate = (self.root / clean).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def content_type(asset_path: str) -> str:
        explicit = {
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".webmanifest": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".ttf": "font/ttf",
        }
        suffix = Path(asset_path).suffix.lower()
        if suffix in explicit:
            return explicit[suffix]
        guessed, _ = mimetypes.guess_type(asset_path)
        return guessed or "application/octet-stream"

    def page_response(self, ws_path: str, *, identify: bool) -> dict[str, object]:
        index = self.resolve("index.html") if self.ready else None
        if index is None:
            return self.unavailable_response(self.error or "Pixel Agents bundle is missing.")
        source = index.read_text("utf-8")
        injection = f'<base href="{WEBVIEW_BASE_PATH}">' + _connection_shim(
            ws_path, identify=identify
        )
        match = re.search(r"<head[^>]*>", source, re.IGNORECASE)
        source = (
            source[: match.end()] + "\n" + injection + source[match.end() :]
            if match
            else injection + source
        )
        return {"status": 0, "web_content": {"standalone": True, "source": source}}

    @staticmethod
    def unavailable_response(message: str) -> dict[str, object]:
        return {"status": 1, "error_code": 503, "error_message": message}

    @staticmethod
    def not_found_response() -> dict[str, object]:
        return {"status": 1, "error_code": 404, "error_message": "There is no such office."}

    @staticmethod
    def redirect_response(path: str) -> dict[str, object]:
        return {"status": 0, "redirect_url": path}

    def static_response(self, asset_path: str, *, head_only: bool = False) -> dict[str, object]:
        resolved = self.resolve(asset_path)
        if resolved is None:
            return {"status": 1, "error_code": 404, "error_message": "Asset not found."}
        body = b"" if head_only else resolved.read_bytes()
        return {
            "status": 0,
            "raw_response": {
                "status": 200,
                "content_type": self.content_type(asset_path),
                "body_base64": base64.b64encode(body).decode("ascii"),
                "headers": {"Cache-Control": WEBVIEW_CACHE_CONTROL},
            },
        }

    def _load_assets(self) -> None:
        loaded: dict[str, object] = {}
        degraded: list[str] = []
        for name, filename in DECODED_ASSETS.items():
            path = self.resolve(f"assets/decoded/{filename}")
            if path is None:
                degraded.append(name)
                continue
            try:
                loaded[name] = cast(object, json.loads(path.read_text("utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("cctv: could not load decoded %s: %s", name, exc)
                degraded.append(name)
        catalog = self.resolve("assets/furniture-catalog.json")
        if catalog is not None:
            try:
                raw = cast(object, json.loads(catalog.read_text("utf-8")))
                if isinstance(raw, list):
                    loaded["catalog"] = [
                        {str(key): value for key, value in entry.items() if key in FURNITURE_KEYS}
                        for entry in raw
                        if isinstance(entry, Mapping)
                    ]
                else:
                    degraded.append("furniture-catalog")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("cctv: could not load furniture catalog: %s", exc)
                degraded.append("furniture-catalog")
        else:
            degraded.append("furniture-catalog")
        self.assets.clear()
        self.assets.update(loaded)
        self.degraded = tuple(degraded)
        if not self.assets.get("characterProfiles"):
            self.error = "The Pixel Agents character profiles are missing."
            self.ready = False
        self._log.info(
            "cctv: loaded %d character profiles and %d furniture sprites",
            self._size(loaded.get("characterProfiles")),
            self._size(loaded.get("furniture")),
        )

    @staticmethod
    def _size(value: object) -> int:
        return len(value) if isinstance(value, Sized) else 0


@dataclass(frozen=True, slots=True)
class PickerEntry:
    """One guild office listed on the server picker."""

    guild_id: int
    name: str
    icon_url: str | None
    headcount: int

    @property
    def href(self) -> str:
        return office_page_path(self.guild_id)


# Rendered by the Dashboard's Jinja (`render_template_string`, autoescaped):
# guild names reach it only as the `cctv_guilds` template variable, never
# spliced into this source, since any guild's admins choose its name.
PICKER_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Pixel Agents offices</title>
<style>
  :root { color-scheme: dark; --bg: #1e1e2e; --card: #2a2a3c; --text: #e6e6f0; --muted: #a6a6c0; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 16px/1.4 system-ui, sans-serif; padding: 24px 16px; }
  h1 { font-size: 1.4rem; margin: 0 0 16px; }
  ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px;
       grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  a.office { display: flex; gap: 12px; align-items: center; padding: 12px;
             background: var(--card); border-radius: 10px; color: inherit; text-decoration: none; }
  a.office:hover, a.office:focus-visible { outline: 2px solid var(--muted); }
  img, .blank { width: 48px; height: 48px; border-radius: 50%; flex: none; background: #3a3a50; }
  .name { font-weight: 600; overflow-wrap: anywhere; }
  .count, .empty { color: var(--muted); font-size: 0.9rem; }
</style>
</head>
<body>
<h1>Pixel Agents offices</h1>
{% if cctv_guilds %}
<ul>
{% for g in cctv_guilds %}
  <li><a class="office" href="{{ g.href }}">
    {% if g.icon_url %}<img src="{{ g.icon_url }}" alt="">{% else %}<span class="blank"></span>{% endif %}
    <span><span class="name">{{ g.name }}</span><br>
    <span class="count">{{ g.headcount }} in the office</span></span>
  </a></li>
{% endfor %}
</ul>
{% else %}
<p class="empty">No server has a public office yet.</p>
{% endif %}
</body>
</html>
"""


def picker_response(entries: Sequence[PickerEntry]) -> dict[str, object]:
    """The server picker page: every listed guild office, as template data."""

    return {
        "status": 0,
        "web_content": {
            "standalone": True,
            "source": PICKER_TEMPLATE,
            "cctv_guilds": [
                {
                    "href": entry.href,
                    "name": entry.name,
                    "icon_url": entry.icon_url,
                    "headcount": entry.headcount,
                }
                for entry in entries
            ],
        },
    }


def degraded_asset_notification(degraded: tuple[str, ...]) -> str:
    """DM text for `Red.send_to_owners` when `WebviewAssets.degraded` is
    non-empty. Mirrors `pixelagents.infrastructure.webview_build.
    owner_notification_for` -- a pure, framework-agnostic function the
    caller passes through `corridor.substitute_default_prefix(...)` before
    sending, since this module has no `ctx`/bot to resolve a real prefix
    from. A missing/corrupt sprite family or furniture catalog degrades the
    webview (missing sprites, blank furniture) without making it
    unavailable, so it wouldn't otherwise surface anywhere but a log line."""

    names = ", ".join(degraded)
    return (
        f"⚠️ The Pixel Agents webview is serving in a degraded state: {names} "
        "failed to decode from the current build and will render missing or "
        "blank. Run `[p]pixelagents webview rebuild` after investigating, or "
        "check the logs."
    )


__all__ = [
    "EDITOR_WS_PATH",
    "PICKER_PATH",
    "PICKER_TEMPLATE",
    "PickerEntry",
    "WEBVIEW_BASE_PATH",
    "WEBVIEW_CACHE_CONTROL",
    "WebviewAssets",
    "degraded_asset_notification",
    "office_page_path",
    "office_ws_path",
    "picker_response",
]
