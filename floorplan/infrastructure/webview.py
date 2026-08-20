"""Filesystem-backed assets and Dashboard response builders for the office."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from collections.abc import Mapping, Sized
from pathlib import Path
from typing import cast

from floorplan.contracts.layout import RawOfficeLayout

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

# Injected before the bundle so a logged-in Dashboard visitor can upgrade the
# already-open public socket without modifying the vendored browser bundle.
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
        # Set by PixelAgentsBase after each build attempt, so a visitor
        # hitting the public office page while assets are missing sees why
        # (a specific missing tool, a build failure) instead of a bare
        # "not installed". None once a build has actually succeeded.
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
                "Pixel Agents webview assets are not installed yet. "
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
        injection = base_tag + TICKET_SHIM
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
                "error_message": "Pixel Agents asset not found.",
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
                    "floorplan: missing assets/decoded/%s.json — "
                    "run [p]pixelagents webview rebuild",
                    name,
                )
                continue
            try:
                loaded[name] = cast(object, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("floorplan: could not read decoded %s: %s", name, exc)

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
                    self._log.error("floorplan: furniture catalog is not a JSON array")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._log.error("floorplan: could not read furniture catalog: %s", exc)

        # Keep the mapping identity stable for consumers holding the asset view.
        self.assets.clear()
        self.assets.update(loaded)
        self._log.info(
            "floorplan: loaded assets — %d palettes, %d floors, %d wall sets, %d furniture sprites",
            self._size(loaded.get("characters")),
            self._size(loaded.get("floors")),
            self._size(loaded.get("walls")),
            self._size(loaded.get("furniture")),
        )

    def default_layout(self) -> RawOfficeLayout | None:
        """Load the layout selected by the bundled asset index."""

        index_path = self.resolve("assets/asset-index.json")
        if index_path is None:
            return None
        try:
            index = cast(object, json.loads(index_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._log.warning("floorplan: could not read bundled asset index: %s", exc)
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
            self._log.warning("floorplan: could not read bundled default layout: %s", exc)
            return None
        if not isinstance(layout, dict):
            self._log.warning("floorplan: bundled default layout is not a JSON object")
            return None
        return cast(RawOfficeLayout, layout)

    @staticmethod
    def _size(value: object | None) -> int:
        return len(value) if isinstance(value, Sized) else 0
