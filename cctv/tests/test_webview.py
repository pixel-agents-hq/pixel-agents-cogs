from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.webview import (
    EDITOR_WS_PATH,
    PICKER_TEMPLATE,
    WEBVIEW_BASE_PATH,
    PickerEntry,
    WebviewAssets,
    degraded_asset_notification,
    office_ws_path,
    picker_response,
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
            "character-profiles": [{"id": "char-0", "body": "flat", "v": 1}],
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
        office = self.provider.page_response(office_ws_path(10), identify=True)
        other = self.provider.page_response(office_ws_path(20), identify=True)
        editor = self.provider.page_response(EDITOR_WS_PATH, identify=False)
        office_source = office["web_content"]["source"]  # type: ignore[index]
        other_source = other["web_content"]["source"]  # type: ignore[index]
        editor_source = editor["web_content"]["source"]  # type: ignore[index]

        self.assertIn(f'<base href="{WEBVIEW_BASE_PATH}">', office_source)
        self.assertIn("'/cctv/office/10/ws'", office_source)
        self.assertNotIn("/cctv/office/20/ws", office_source)
        self.assertIn("'/cctv/office/20/ws'", other_source)
        self.assertIn("/third-party/cctv/session", office_source)
        self.assertIn("/cctv/editor/ws", editor_source)
        self.assertNotIn("/third-party/cctv/session", editor_source)

    def test_an_office_ws_path_is_built_from_an_integer_only(self) -> None:
        self.assertEqual(office_ws_path(10), "/cctv/office/10/ws")
        with self.assertRaises(ValueError):
            office_ws_path("10'/x")  # type: ignore[arg-type]

    def test_traversal_is_rejected(self) -> None:
        self.assertIsNone(self.provider.resolve("../secret"))

    def test_missing_bundle_returns_503(self) -> None:
        provider = WebviewAssets()
        response = provider.page_response(office_ws_path(10), identify=True)
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
            "character-profiles": [{"id": "char-0", "body": "flat", "v": 1}],
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

    def test_missing_character_profiles_flips_ready_and_still_records_degraded(self) -> None:
        (self.decoded / "character-profiles.json").unlink()

        provider = WebviewAssets()
        provider.sync(_Status(self.root))

        self.assertIn("characterProfiles", provider.degraded)
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


class TestPicker(unittest.TestCase):
    def _render(self, entries: list[PickerEntry]) -> str:
        """Render the way the Dashboard does: Flask's render_template_string
        autoescapes, with the page's web_content as template variables."""

        import jinja2

        content = picker_response(entries)["web_content"]
        assert isinstance(content, dict)
        variables = {key: value for key, value in content.items() if key != "source"}
        environment = jinja2.Environment(autoescape=True)
        return environment.from_string(content["source"]).render(**variables)

    def test_lists_each_office_with_its_link_and_headcount(self) -> None:
        html = self._render(
            [
                PickerEntry(10, "Pixel Town", "https://cdn.example/icon.png", 3),
                PickerEntry(20, "Quiet Place", None, 0),
            ]
        )

        self.assertIn('href="/third-party/cctv/office?guild=10"', html)
        self.assertIn("Pixel Town", html)
        self.assertIn("3 in the office", html)
        self.assertIn('src="https://cdn.example/icon.png"', html)
        self.assertIn("Quiet Place", html)

    def test_a_guild_name_is_data_never_template_or_markup(self) -> None:
        name = "{{ 7*7 }}<script>alert(1)</script>{% raw %}"
        html = self._render([PickerEntry(10, name, None, 1)])

        self.assertIn("{{ 7*7 }}", html)
        self.assertNotIn("49", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn(name, PICKER_TEMPLATE)

    def test_no_offices_says_so(self) -> None:
        self.assertIn("No server has a public office yet.", self._render([]))
