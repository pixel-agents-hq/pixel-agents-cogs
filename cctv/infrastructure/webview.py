"""Shared Pixel Agents assets and per-page HTML injection."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from collections.abc import Mapping, Sized
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


class WebviewStatus(Protocol):
    dist_path: Path
    ready: bool
    detail: str
    built_commit: str | None


def _connection_shim(page: str) -> str:
    ticket = (
        """
  var ticketPromise = fetch('/third-party/cctv/session', {
    credentials: 'same-origin', headers: { Accept: 'application/json' }
  }).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { return (data && data.ticket) || null; })
    .catch(function () { return null; });
"""
        if page == "discord"
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
        if page == "discord"
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
    target.pathname = '/cctv/{page}/ws';
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

    def page_response(self, page: str) -> dict[str, object]:
        index = self.resolve("index.html") if self.ready else None
        if index is None:
            return self.unavailable_response(self.error or "Pixel Agents bundle is missing.")
        source = index.read_text("utf-8")
        injection = f'<base href="{WEBVIEW_BASE_PATH}">' + _connection_shim(page)
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
        for name in ("characters", "floors", "walls", "carpets", "furniture"):
            path = self.resolve(f"assets/decoded/{name}.json")
            if path is None:
                continue
            try:
                loaded[name] = cast(object, json.loads(path.read_text("utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("cctv: could not load decoded %s: %s", name, exc)
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
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("cctv: could not load furniture catalog: %s", exc)
        self.assets.clear()
        self.assets.update(loaded)
        if not self.assets.get("characters"):
            self.error = "The Pixel Agents decoded character assets are missing."
            self.ready = False
        self._log.info(
            "cctv: loaded %d character palettes and %d furniture sprites",
            self._size(loaded.get("characters")),
            self._size(loaded.get("furniture")),
        )

    @staticmethod
    def _size(value: object) -> int:
        return len(value) if isinstance(value, Sized) else 0


__all__ = ["WEBVIEW_BASE_PATH", "WEBVIEW_CACHE_CONTROL", "WebviewAssets"]
