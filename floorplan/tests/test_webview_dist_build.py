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

from floorplan.infrastructure.webview import WebviewAssetProvider
from floorplan.tests.conftest import write_fake_vite_build
from pixelagents.infrastructure import webview_build

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
        self.base_path = webview_build.RELATIVE_BASE_PATH
        self.webview_dist = root / "webview_dist"
        webview_build._sync_dist(
            build_out_dir, self.webview_dist, self.commit, self.base_path, _LOG
        )
        self.provider = WebviewAssetProvider(self.webview_dist)
        self.base_href = "/third-party/floorplan/static/"
        self.provider.base_href = self.base_href

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
        # pixelagents builds relative asset URLs (RELATIVE_BASE_PATH) so any
        # cog can serve the same bundle; this cog resolves them against its
        # own injected <base href> (self.base_href), the same way a browser
        # would -- not against the physical webview_dist root directly.
        response = self.provider.dashboard_webview_response()
        self.assertEqual(response.get("status"), 0, response)
        source = str(response["web_content"]["source"])  # type: ignore[index]

        self.assertIn(f'<base href="{self.base_href}">', source)
        srcs = re.findall(r'(?:src|href)="([^"]+)"', source)
        bundle_paths = [s for s in srcs if s.startswith("./")]
        self.assertTrue(bundle_paths, "index.html referenced no relative bundled asset")
        for path in bundle_paths:
            asset_path = path.removeprefix("./")
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

    def test_sync_dist_writes_the_built_base_path_marker(self) -> None:
        marker = self.webview_dist / ".built_base_path"
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), self.base_path)


if __name__ == "__main__":
    unittest.main()
