"""WebviewAssetProvider: resolve/content-type/response-building against a
synthetic dist tree -- a lighter smoke test than floorplan's own
`test_webview_dist_build.py` (which builds a real vite-shaped dist via
pixelagents' own sync helper); this only exercises the class's own logic,
since the underlying bundle format itself is already covered there."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..infrastructure.webview import WebviewAssetProvider


class TestWebviewAssetProvider(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "index.html").write_text(
            '<!doctype html><head></head><div id="root"></div>', encoding="utf-8"
        )
        (self.root / "assets").mkdir()
        (self.root / "assets" / "index-test.js").write_text("console.log('ok');", encoding="utf-8")
        self.provider = WebviewAssetProvider(self.root)
        self.provider.base_href = "/third-party/architect/static/"

    def test_resolve_finds_a_real_file(self) -> None:
        self.assertIsNotNone(self.provider.resolve("assets/index-test.js"))

    def test_resolve_rejects_path_traversal(self) -> None:
        self.assertIsNone(self.provider.resolve("../index.html"))

    def test_resolve_rejects_missing_files(self) -> None:
        self.assertIsNone(self.provider.resolve("assets/does-not-exist.js"))

    def test_content_type_known_and_unknown_extensions(self) -> None:
        self.assertEqual(
            self.provider.content_type("assets/index-test.js"), "text/javascript; charset=utf-8"
        )
        self.assertEqual(
            self.provider.content_type("assets/unknown.bin"), "application/octet-stream"
        )

    def test_dashboard_webview_response_injects_base_href(self) -> None:
        response = self.provider.dashboard_webview_response()

        self.assertEqual(response.get("status"), 0, response)
        source = str(response["web_content"]["source"])  # type: ignore[index]
        self.assertIn('<base href="/third-party/architect/static/">', source)
        self.assertIn("root", source)

    def test_dashboard_webview_response_reports_missing_bundle(self) -> None:
        empty_provider = WebviewAssetProvider(self.root / "does-not-exist")

        response = empty_provider.dashboard_webview_response()

        self.assertEqual(response.get("status"), 1)
        self.assertEqual(response.get("error_code"), 503)

    def test_dashboard_static_response_serves_a_real_asset(self) -> None:
        response = self.provider.dashboard_static_response("assets/index-test.js")

        self.assertEqual(response.get("status"), 0)
        raw = response["raw_response"]  # type: ignore[index]
        self.assertEqual(raw["content_type"], "text/javascript; charset=utf-8")
        self.assertEqual(raw["headers"]["Cache-Control"], "public, max-age=3600")
        self.assertEqual(base64.b64decode(raw["body_base64"]).decode("utf-8"), "console.log('ok');")

    def test_dashboard_static_response_reports_a_missing_asset(self) -> None:
        response = self.provider.dashboard_static_response("assets/nope.js")

        self.assertEqual(response.get("status"), 1)
        self.assertEqual(response.get("error_code"), 404)

    def test_load_assets_tolerates_a_completely_missing_decoded_directory(self) -> None:
        self.provider.load_assets()  # must not raise

        self.assertEqual(self.provider.assets, {})

    def test_default_layout_round_trips(self) -> None:
        (self.root / "assets").mkdir(exist_ok=True)
        (self.root / "assets" / "asset-index.json").write_text(
            json.dumps({"defaultLayout": "default-layout.json"}), encoding="utf-8"
        )
        (self.root / "assets" / "default-layout.json").write_text(
            json.dumps({"tiles": [1, 2, 3]}), encoding="utf-8"
        )

        layout = self.provider.default_layout()

        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertEqual(layout["tiles"], [1, 2, 3])

    def test_default_layout_is_none_without_an_asset_index(self) -> None:
        self.assertIsNone(self.provider.default_layout())
