"""Smoke-test WebviewAssetProvider against a freshly built webview_dist.

pixelagents/webview_dist is no longer committed (see issue #7 and
pixelagents/infrastructure/webview_build.py) -- it is cloned and built into
Red's per-cog data directory at cog_load time. This module builds a
synthetic dist tree with webview_build's own `_sync_dist` (so it stays the
same shape a real build produces) and runs the render/smoke gate against
it: the one property a moved vendor pin could silently break is whether the
built dist can still serve the office.
"""

from __future__ import annotations

import logging
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pixelagents.infrastructure import webview_build
from pixelagents.infrastructure.webview import WebviewAssetProvider
from pixelagents.tests.conftest import write_fake_vite_build

_LOG = logging.getLogger("test.webview_dist_build")

SPRITE_FAMILIES = ("characters", "floors", "walls", "carpets", "furniture")


class TestWebviewDistBuild(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        build_out_dir = root / "build-out"
        write_fake_vite_build(build_out_dir)

        self.commit = "a" * 40
        self.webview_dist = root / "webview_dist"
        webview_build._sync_dist(build_out_dir, self.webview_dist, self.commit, _LOG)
        self.provider = WebviewAssetProvider(self.webview_dist)

    def test_sync_dist_drops_unserved_raw_passthrough_files(self) -> None:
        # Vite's public/ passthrough also copies raw per-tile PNGs nothing
        # here serves; _sync_dist must filter them, not happen to copy
        # everything (see webview_build.py's module docstring).
        self.assertFalse((self.webview_dist / "assets" / "characters").exists())

    def test_load_assets_populates_every_sprite_family_and_catalog(self) -> None:
        self.provider.load_assets()
        for name in SPRITE_FAMILIES:
            with self.subTest(family=name):
                self.assertIn(name, self.provider.assets)
                self.assertTrue(self.provider.assets[name])
        self.assertIn("catalog", self.provider.assets)
        self.assertTrue(self.provider.assets["catalog"])

    def test_dashboard_webview_response_serves_a_resolvable_bundle(self) -> None:
        response = self.provider.dashboard_webview_response()
        self.assertEqual(response.get("status"), 0, response)
        source = str(response["web_content"]["source"])  # type: ignore[index]

        srcs = re.findall(r'(?:src|href)="([^"]+)"', source)
        bundle_paths = [s for s in srcs if s.startswith("/third-party/pixelagents/static/")]
        self.assertTrue(bundle_paths, "index.html referenced no bundled asset")
        for path in bundle_paths:
            asset_path = path.removeprefix("/third-party/pixelagents/static/")
            with self.subTest(asset=asset_path):
                self.assertIsNotNone(self.provider.resolve(asset_path))

    def test_default_layout_round_trips(self) -> None:
        layout = self.provider.default_layout()
        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertIn("tiles", layout)
        self.assertTrue(layout["tiles"])

    def test_furniture_catalog_matches_decoded_sprites(self) -> None:
        self.provider.load_assets()
        catalog_ids = {entry["id"] for entry in self.provider.assets["catalog"]}  # type: ignore[union-attr]
        furniture = self.provider.assets["furniture"]
        assert isinstance(furniture, dict)
        self.assertTrue(catalog_ids)
        self.assertEqual(catalog_ids, set(furniture.keys()))

    def test_sync_dist_writes_the_built_commit_marker(self) -> None:
        marker = self.webview_dist / ".built_commit"
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), self.commit)


if __name__ == "__main__":
    unittest.main()
