from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.webview import WEBVIEW_BASE_PATH, WebviewAssets


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


if __name__ == "__main__":
    unittest.main()
