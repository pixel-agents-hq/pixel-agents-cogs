from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from contracts.pixel_index.generate_status_site import generate_site, load_results
from contracts.pixel_index.verify import build_result_document


def write_result(directory: Path, environment: str, status: str, endpoints: list[dict], detail: str = "") -> None:
    document = {
        "schema_version": 1,
        "environment": environment,
        "base_url": f"https://pixel-index-api-{environment}.example.test",
        "status": status,
        "checked_at": "2026-08-15T18:17:16Z" if status != "unknown" else None,
        "endpoints": endpoints,
    }
    if detail:
        document["detail"] = detail
    (directory / f"{environment}.json").write_text(json.dumps(document), encoding="utf-8")


class VerifyResultDocumentTests(unittest.TestCase):
    @patch("contracts.pixel_index.verify._utc_now", return_value="2026-08-15T18:17:16Z")
    def test_builds_machine_readable_result_before_exit(self, _utc_now) -> None:
        endpoints = [
            {"name": "health", "status": "pass", "detail": ""},
            {"name": "layout_detail", "status": "skipped", "detail": "no layout"},
        ]

        result = build_result_document("staging", "https://staging.example.test", True, endpoints)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["checked_at"], "2026-08-15T18:17:16Z")
        self.assertEqual(result["counts"], {"pass": 1, "fail": 0, "skipped": 1})
        self.assertEqual(result["endpoints"], endpoints)


class StatusSiteTests(unittest.TestCase):
    def test_generates_human_api_and_badge_views_from_one_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results_dir = root / "results"
            output_dir = root / "site"
            results_dir.mkdir()
            write_result(
                results_dir,
                "production",
                "pass",
                [
                    {"name": "health", "status": "pass", "detail": ""},
                    {"name": "list_layouts", "status": "pass", "detail": ""},
                ],
            )
            write_result(
                results_dir,
                "staging",
                "fail",
                [
                    {"name": "health", "status": "pass", "detail": ""},
                    {"name": "list_layouts", "status": "fail", "detail": '<script>alert("unsafe")</script>'},
                ],
            )
            generated_at = datetime(2026, 8, 15, 19, 0, tzinfo=timezone.utc)

            snapshot = generate_site(
                results_dir,
                output_dir,
                repository="NNTin/office-cogs",
                branch="develop",
                commit="320b76d1234567890",
                run_id=31900696223,
                run_url="https://github.com/NNTin/office-cogs/actions/runs/31900696223",
                event="schedule",
                generated_at=generated_at,
            )

            self.assertEqual(snapshot["overall"], "fail")
            self.assertEqual(snapshot["valid_until"], "2026-08-16T07:00:00Z")
            self.assertEqual(list(snapshot["environments"]), ["production", "staging"])
            self.assertEqual(
                json.loads((output_dir / "status.json").read_text(encoding="utf-8")),
                json.loads((output_dir / "api/v1/status.json").read_text(encoding="utf-8")),
            )

            html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("Pixel Index compatibility", html)
            self.assertIn("Production", html)
            self.assertIn("Staging", html)
            self.assertIn("&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;", html)
            self.assertNotIn('<script>alert("unsafe")</script>', html)

            production_badge = json.loads((output_dir / "api/v1/badges/production.json").read_text(encoding="utf-8"))
            staging_badge = json.loads((output_dir / "api/v1/badges/staging.json").read_text(encoding="utf-8"))
            self.assertEqual(production_badge["message"], "compatible")
            self.assertEqual(production_badge["color"], "brightgreen")
            self.assertEqual(staging_badge["message"], "incompatible")
            self.assertEqual(staging_badge["color"], "red")
            self.assertTrue(staging_badge["isError"])

    def test_unknown_result_is_never_presented_as_passing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results_dir = root / "results"
            output_dir = root / "site"
            results_dir.mkdir()
            write_result(results_dir, "production", "unknown", [], detail="Setup failed")

            snapshot = generate_site(
                results_dir,
                output_dir,
                repository="NNTin/office-cogs",
                branch="develop",
                commit="320b76d1234567890",
                run_id=42,
                run_url="https://github.com/NNTin/office-cogs/actions/runs/42",
                event="push",
            )
            badge = json.loads((output_dir / "api/v1/badges/production.json").read_text(encoding="utf-8"))
            html = (output_dir / "index.html").read_text(encoding="utf-8")

            self.assertEqual(snapshot["overall"], "unknown")
            self.assertEqual(snapshot["environments"]["production"]["counts"], {"pass": 0, "fail": 0, "skipped": 0})
            self.assertEqual(badge["message"], "unknown")
            self.assertTrue(badge["isError"])
            self.assertIn("Setup failed", html)

    def test_rejects_environment_names_that_could_escape_api_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results_dir = Path(temporary)
            (results_dir / "unsafe.json").write_text(
                json.dumps({"environment": "../production", "status": "pass", "endpoints": []}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsafe environment name"):
                load_results(results_dir)


if __name__ == "__main__":
    unittest.main()
