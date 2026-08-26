"""Filesystem-backed assets and Dashboard response builders for architect's
webview.

A deliberate parallel copy of `floorplan/infrastructure/webview.py`'s
`WebviewAssetProvider` -- see docs/architect-design.md section 5 on why:
two independent consumers of one build artifact (pixelagents'
`webview_dist/`), not a shared library. Importing the class from
`floorplan` directly would additionally force `floorplan`'s own package
onto disk for anyone installing `architect` alone (Red's Downloader only
guarantees a cog's *own* `required_cogs` are installed alongside it), when
`architect` only actually depends on `pixelagents`.

One real divergence from floorplan's copy: `default_layout()` returns a
plain `dict[str, object]` here instead of floorplan's `RawOfficeLayout`
(`floorplan.contracts.layout`) type alias, to avoid that same unwanted
floorplan import for what is, at runtime, the identical plain-JSON-object
shape. A second, functional one: this module also injects `WS_REWRITE_SHIM`
(see its own docstring) so this page's live WebSocket connection reaches
architect's own `WebSocketServer` (`infrastructure/websocket.py`) instead
of the bundle's hardcoded, page-path-independent `/ws` -- without it, this
page would silently render whatever *other* cog answers that shared path
on the same host (see docs/architect-design.md's incident note).
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

from .websocket import WEBSOCKET_PATH

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

# Rewrites the bundle's own hardcoded `wss://<page host>/ws` connection
# (computed from `window.location.host` alone, with no cog-specific path --
# see docs/architect-design.md's incident note) to architect's own,
# distinct WEBSOCKET_PATH instead -- otherwise this page would silently
# connect to whatever *other* cog's WebSocket server answers the shared
# `/ws` path on this host (floorplan's, in this repo). Injected *before*
# TICKET_SHIM below: TICKET_SHIM captures `window.WebSocket` at its own
# injection time as `Native`, so running this shim first means TICKET_SHIM
# transparently wraps the rewriting constructor instead of the raw one --
# both patches compose without either needing to know about the other.
# Only rewrites a URL ending in exactly `/ws` (optionally followed by a
# `?`/`#`), so it cannot mistake an unrelated path containing that
# substring for the bundle's own connection.
WS_REWRITE_SHIM = f"""<script>
(function () {{
  var Native = window.WebSocket;
  var TARGET_PATH = {json.dumps(WEBSOCKET_PATH)};
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

# Injected before the bundle so a logged-in Dashboard visitor can upgrade the
# already-open socket without modifying the vendored browser bundle. There
# is no `/session` login endpoint here yet (see adapters/dashboard.py's
# module docstring) -- its fetch/upgrade logic degrades to a harmless no-op
# ticket without one, same as it would against any page that never opens a
# matching socket. The socket itself is real: WS_REWRITE_SHIM above ensures
# it connects to architect's own WebSocketServer, not floorplan's.
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
        # Set by CogBase after each build attempt, so a visitor hitting the
        # webview page while assets are missing sees why (a specific
        # missing tool, a build failure) instead of a bare "not installed".
        # None once a build has actually succeeded.
        self.build_status: str | None = None
        # pixelagents builds one bundle with relative asset URLs (see
        # infrastructure.webview_build.RELATIVE_BASE_PATH in pixelagents) so
        # any cog can serve it under its own route -- this cog's own route
        # is injected as a `<base href>` at serve time (see
        # dashboard_webview_response) rather than baked into the build, the
        # same way any other consuming cog would inject its own.
        self.base_href: str | None = None

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

    def dashboard_webview_response(self) -> dict[str, object]:
        """Build the public Dashboard page with the ticket shim first."""

        index_path = self.resolve("index.html")
        if index_path is None:
            message = self.build_status or (
                "architect's webview assets are not installed yet. "
                "Ask the bot owner to run `[p]pixelagents webview rebuild`."
            )
            return {
                "status": 1,
                "error_code": 503,
                "error_message": message,
            }
        source = index_path.read_text(encoding="utf-8")
        # <base> first: it must precede any relative URL reference in <head>
        # (including the bundle's own <script>/<link> tags further down) to
        # affect how they resolve, per the HTML spec.
        base_tag = f'<base href="{self.base_href}">' if self.base_href else ""
        # Order matters: WS_REWRITE_SHIM must run before TICKET_SHIM so the
        # latter wraps the URL-rewriting constructor, not the raw one --
        # see WS_REWRITE_SHIM's own comment.
        injection = base_tag + WS_REWRITE_SHIM + TICKET_SHIM
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
                "error_message": "architect webview asset not found.",
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
                    "architect: missing assets/decoded/%s.json -- "
                    "run [p]pixelagents webview rebuild",
                    name,
                )
                continue
            try:
                loaded[name] = cast(object, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("architect: could not read decoded %s: %s", name, exc)

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
                    self._log.error("architect: furniture catalog is not a JSON array")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("architect: could not read furniture catalog: %s", exc)

        # Keep the mapping identity stable for consumers holding the asset view.
        self.assets.clear()
        self.assets.update(loaded)
        self._log.info(
            "architect: loaded assets -- %d palettes, %d floors, %d wall sets, %d furniture sprites",
            self._size(loaded.get("characters")),
            self._size(loaded.get("floors")),
            self._size(loaded.get("walls")),
            self._size(loaded.get("furniture")),
        )

    def default_layout(self) -> dict[str, object] | None:
        """Load the layout selected by the bundled asset index."""

        index_path = self.resolve("assets/asset-index.json")
        if index_path is None:
            return None
        try:
            index = cast(object, json.loads(index_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._log.warning("architect: could not read bundled asset index: %s", exc)
            return None
        if not isinstance(index, Mapping):
            return None
        name = index.get("defaultLayout")
        if not isinstance(name, str) or not name:
            return None
        layout_path = self.resolve(f"assets/{name}")
        if layout_path is None:
            return None
        try:
            layout = cast(object, json.loads(layout_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._log.warning("architect: could not read bundled default layout: %s", exc)
            return None
        if not isinstance(layout, dict):
            self._log.warning("architect: bundled default layout is not a JSON object")
            return None
        return cast("dict[str, object]", layout)

    @staticmethod
    def _size(value: object | None) -> int:
        return len(value) if isinstance(value, Sized) else 0


__all__ = ["WebviewAssetProvider"]
