from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.webview import (
    WEBVIEW_BASE_PATH,
    WebviewAssets,
    degraded_asset_notification,
)


@dataclass
class _Status:
    dist_path: Path
    ready: bool = True
    detail: str = "loaded"
    built_commit: str | None = "a"


class TestWebviewAssets(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        assets = self.root / "assets"
        decoded = assets / "decoded"
        decoded.mkdir(parents=True)
        (self.root / "index.html").write_text(
            "<html><head></head><body></body></html>", encoding="utf-8"
        )
        for name, value in {
            "characters": [[]],
            "floors": [],
            "walls": [],
            "carpets": [],
            "furniture": {},
        }.items():
            (decoded / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        (assets / "furniture-catalog.json").write_text("[]", encoding="utf-8")
        self.provider = WebviewAssets()
        self.provider.sync(_Status(self.root))

    def test_pages_share_base_but_rewrite_to_distinct_ws_routes(self) -> None:
        discord = self.provider.page_response("discord")
        editor = self.provider.page_response("editor")
        discord_source = discord["web_content"]["source"]
        editor_source = editor["web_content"]["source"]

        self.assertIn(f'<base href="{WEBVIEW_BASE_PATH}">', discord_source)
        self.assertIn("/cctv/discord/ws", discord_source)
        self.assertIn("/third-party/cctv/session", discord_source)
        self.assertIn("/cctv/editor/ws", editor_source)
        self.assertNotIn("/third-party/cctv/session", editor_source)

    def test_traversal_is_rejected(self) -> None:
        self.assertIsNone(self.provider.resolve("../secret"))

    def test_missing_bundle_returns_503(self) -> None:
        provider = WebviewAssets()
        response = provider.page_response("discord")
        self.assertEqual(response["error_code"], 503)

    def test_degraded_is_empty_when_every_family_and_catalog_decode(self) -> None:
        self.assertEqual(self.provider.degraded, ())


class TestWebviewAssetsDegraded(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.decoded = self.root / "assets" / "decoded"
        self.decoded.mkdir(parents=True)
        (self.root / "index.html").write_text(
            "<html><head></head><body></body></html>", encoding="utf-8"
        )
        for name, value in {
            "characters": [[]],
            "floors": [],
            "walls": [],
            "carpets": [],
            "furniture": {},
        }.items():
            (self.decoded / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        (self.root / "assets" / "furniture-catalog.json").write_text("[]", encoding="utf-8")

    def test_records_a_missing_non_character_family_without_flipping_ready(self) -> None:
        (self.decoded / "walls.json").unlink()

        provider = WebviewAssets()
        provider.sync(_Status(self.root))

        self.assertEqual(provider.degraded, ("walls",))
        self.assertTrue(provider.ready)

    def test_records_a_corrupt_family_alongside_a_missing_one(self) -> None:
        (self.decoded / "walls.json").unlink()
        (self.decoded / "carpets.json").write_text("not json", encoding="utf-8")

        provider = WebviewAssets()
        provider.sync(_Status(self.root))

        self.assertEqual(provider.degraded, ("walls", "carpets"))

    def test_missing_characters_flips_ready_and_still_records_degraded(self) -> None:
        (self.decoded / "characters.json").unlink()

        provider = WebviewAssets()
        provider.sync(_Status(self.root))

        self.assertIn("characters", provider.degraded)
        self.assertFalse(provider.ready)

    def test_missing_furniture_catalog_is_recorded(self) -> None:
        (self.root / "assets" / "furniture-catalog.json").unlink()

        provider = WebviewAssets()
        provider.sync(_Status(self.root))

        self.assertEqual(provider.degraded, ("furniture-catalog",))


class TestDegradedAssetNotification(unittest.TestCase):
    def test_names_every_degraded_family(self) -> None:
        message = degraded_asset_notification(("walls", "furniture-catalog"))

        self.assertIn("walls", message)
        self.assertIn("furniture-catalog", message)
        self.assertIn("[p]pixelagents webview rebuild", message)


if __name__ == "__main__":
    unittest.main()
