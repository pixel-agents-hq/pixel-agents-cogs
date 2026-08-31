"""Filesystem-backed assets and Dashboard response builders for cctv's
webview -- one instance shared by both Dashboard pages (docs/cctv-design.md
§2.7: "static assets use one provider and one Dashboard static route").

A merge of floorplan's and architect's now-retired per-cog
`WebviewAssetProvider` classes: the file-serving logic (`resolve`/
`content_type`/`dashboard_static_response`/`load_assets`) is identical to
both; the two divergent pieces (per-page `<base href>`, and whether/where
the WebSocket-rewrite and ticket shims get injected) are now parameters
on `dashboard_webview_response` instead of being baked into two separate
classes, since cctv serves both pages from one process.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from collections.abc import Mapping, Sized
from pathlib import Path
from typing import cast

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


def ws_rewrite_shim(target_path: str) -> str:
    """Rewrites the bundle's own hardcoded `wss://<page host>/ws` connection
    (computed from `window.location.host` alone, with no cog- or page-
    specific path -- see docs/cctv-design.md §1.2's incident note) to
    `target_path` instead. Both of cctv's pages need this, each with its
    own distinct target (`/cctv/discord/ws` / `/cctv/editor/ws`) -- without
    it, the second page loaded in a browser session would silently
    reconnect to whichever pipeline's `ClientHub` the first page's shim
    already pointed at. Injected *before* `TICKET_SHIM`: that shim
    captures `window.WebSocket` at its own injection time as `Native`, so
    running this one first means it transparently wraps the rewriting
    constructor instead of the raw one -- both compose without either
    needing to know about the other. Only rewrites a URL ending in
    exactly `/ws` (optionally followed by a `?`/`#`), so it cannot mistake
    an unrelated path containing that substring for the bundle's own
    connection."""

    return f"""<script>
(function () {{
  var Native = window.WebSocket;
  var TARGET_PATH = {json.dumps(target_path)};
  function rewrite(url) {{
    if (typeof url !== 'string') {{ return url; }}
    var idx = url.indexOf('/ws');
    if (idx === -1) {{ return url; }}
    var tail = url.slice(idx + 3);
    if (tail !== '' && tail[0] !== '?' && tail[0] !== '#') {{ return url; }}
    return url.slice(0, idx) + TARGET_PATH + tail;
  }}
  function Patched(url, protocols) {{
    var target = rewrite(url);
    return protocols === undefined ? new Native(target) : new Native(target, protocols);
  }}
  Patched.prototype = Native.prototype;
  Patched.CONNECTING = Native.CONNECTING;
  Patched.OPEN = Native.OPEN;
  Patched.CLOSING = Native.CLOSING;
  Patched.CLOSED = Native.CLOSED;
  window.WebSocket = Patched;
}})();
</script>"""


# Injected before the bundle so a logged-in Dashboard visitor can upgrade
# the already-open socket without modifying the vendored browser bundle --
# only the Discord page injects this (docs/cctv-design.md §2.7: "Only the
# Discord page injects the ticket/session upgrade behavior"). Degrades to
# a harmless no-op against the editor page's own served HTML, since that
# page never includes this shim at all.
TICKET_SHIM = """<script>
(function () {
  var Native = window.WebSocket;
  var ticketPromise = fetch(location.pathname + '/session', {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { return (data && data.ticket) || null; })
    .catch(function () { return null; });

  function authorize(socket) {
    ticketPromise.then(function (ticket) {
      if (!ticket) { return; }
      var payload = JSON.stringify({ type: 'authorize', ticket: ticket });
      if (socket.readyState === Native.OPEN) {
        socket.send(payload);
        return;
      }
      if (socket.readyState === Native.CONNECTING) {
        socket.addEventListener('open', function once() {
          socket.removeEventListener('open', once);
          socket.send(payload);
        });
      }
    });
  }

  function Patched(url, protocols) {
    var socket = protocols === undefined ? new Native(url) : new Native(url, protocols);
    if (typeof url === 'string' && url.indexOf('/ws') !== -1) {
      authorize(socket);
    }
    return socket;
  }
  Patched.prototype = Native.prototype;
  Patched.CONNECTING = Native.CONNECTING;
  Patched.OPEN = Native.OPEN;
  Patched.CLOSING = Native.CLOSING;
  Patched.CLOSED = Native.CLOSED;
  window.WebSocket = Patched;
})();
</script>"""


class WebviewAssetProvider:
    """Resolve, load, and render files below one immutable webview root."""

    def __init__(self, root: Path, *, logger: logging.Logger | None = None) -> None:
        self.root = root.resolve()
        self.assets: dict[str, object] = {}
        self._log = logger or logging.getLogger(__name__)
        # Set by CogBase after each build sync attempt, so a visitor
        # hitting a dashboard page while assets are missing sees why.
        self.build_status: str | None = None

    def resolve(self, asset_path: str) -> Path | None:
        """Resolve a regular file without allowing traversal outside the root."""

        clean_path = asset_path.strip().lstrip("/")
        if not clean_path or "\x00" in clean_path:
            return None
        candidate = (self.root / clean_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def content_type(asset_path: str) -> str:
        """Return stable browser MIME types for bundled asset extensions."""

        suffixes = {
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".webmanifest": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".ttf": "font/ttf",
        }
        suffix = Path(asset_path).suffix.lower()
        if suffix in suffixes:
            return suffixes[suffix]
        guessed, _ = mimetypes.guess_type(asset_path)
        return guessed or "application/octet-stream"

    def dashboard_webview_response(
        self, *, base_href: str, ws_target_path: str, include_ticket_shim: bool
    ) -> dict[str, object]:
        """Build one Dashboard page: `<base href>` first (must precede any
        relative URL reference in `<head>` per the HTML spec), then the
        WebSocket-rewrite shim (always, one per page, pointed at
        `ws_target_path`), then the ticket shim (only when
        `include_ticket_shim` -- the Discord page only)."""

        index_path = self.resolve("index.html")
        if index_path is None:
            message = self.build_status or (
                "cctv's webview assets are not installed yet. "
                "Ask the bot owner to run `[p]pixelagents webview rebuild`."
            )
            return {
                "status": 1,
                "error_code": 503,
                "error_message": message,
            }
        source = index_path.read_text(encoding="utf-8")
        injection = f'<base href="{base_href}">' + ws_rewrite_shim(ws_target_path)
        if include_ticket_shim:
            injection += TICKET_SHIM
        match = re.search(r"<head[^>]*>", source, re.IGNORECASE)
        if match:
            source = source[: match.end()] + "\n" + injection + source[match.end() :]
        else:
            source = injection + source
        return {"status": 0, "web_content": {"standalone": True, "source": source}}

    def dashboard_static_response(
        self, asset_path: str, *, head_only: bool = False
    ) -> dict[str, object]:
        """Build the Dashboard raw-response envelope for a static asset."""

        resolved = self.resolve(asset_path)
        if resolved is None:
            return {
                "status": 1,
                "error_code": 404,
                "error_message": "cctv webview asset not found.",
            }
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

    def load_assets(self) -> None:
        """Load decoded sprite families independently so partial builds work."""

        loaded: dict[str, object] = {}
        for name in ("characters", "floors", "walls", "carpets", "furniture"):
            path = self.resolve(f"assets/decoded/{name}.json")
            if path is None:
                self._log.warning(
                    "cctv: missing assets/decoded/%s.json -- run [p]pixelagents webview rebuild",
                    name,
                )
                continue
            try:
                loaded[name] = cast(object, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("cctv: could not read decoded %s: %s", name, exc)

        catalog_path = self.resolve("assets/furniture-catalog.json")
        if catalog_path is not None:
            try:
                raw = cast(object, json.loads(catalog_path.read_text(encoding="utf-8")))
                if isinstance(raw, list):
                    loaded["catalog"] = [
                        {str(key): value for key, value in entry.items() if key in FURNITURE_KEYS}
                        for entry in raw
                        if isinstance(entry, Mapping)
                    ]
                else:
                    self._log.error("cctv: furniture catalog is not a JSON array")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("cctv: could not read furniture catalog: %s", exc)

        # Keep the mapping identity stable for consumers holding the asset view.
        self.assets.clear()
        self.assets.update(loaded)
        self._log.info(
            "cctv: loaded assets -- %d palettes, %d floors, %d wall sets, %d furniture sprites",
            self._size(loaded.get("characters")),
            self._size(loaded.get("floors")),
            self._size(loaded.get("walls")),
            self._size(loaded.get("furniture")),
        )

    @staticmethod
    def _size(value: object | None) -> int:
        return len(value) if isinstance(value, Sized) else 0


__all__ = ["FURNITURE_KEYS", "TICKET_SHIM", "WebviewAssetProvider", "ws_rewrite_shim"]
