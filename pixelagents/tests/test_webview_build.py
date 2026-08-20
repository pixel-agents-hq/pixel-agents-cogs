"""Unit tests for infrastructure.webview_build's orchestration logic.

The subprocess-heavy steps (`_checkout`, `_install_dependencies`,
`_build_bundle`, `_emit_decoded_assets`) are mocked here so these run fast
and offline. The real network + git + npm + vite path they stand in for is
exercised by `TestRealWebviewBuild` below, gated behind
`PIXELAGENTS_REAL_WEBVIEW_BUILD=1` -- what
`.github/workflows/vendor-update.yml` sets, since that is the one place this
repo can afford a slow, network-dependent test.
"""

from __future__ import annotations

import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pixelagents.infrastructure import webview_build
from pixelagents.infrastructure.webview import WebviewAssetProvider
from pixelagents.tests.conftest import write_fake_vite_build

_LOG = logging.getLogger("test.webview_build")


class TestPinnedCommit(unittest.TestCase):
    def test_pin_file_is_a_full_commit_hash(self) -> None:
        self.assertRegex(webview_build.pinned_commit(), r"^[0-9a-f]{40}$")


class TestMissingTools(unittest.TestCase):
    def test_reports_exactly_the_missing_tools(self) -> None:
        def fake_which(name: str) -> str | None:
            return None if name == "npm" else f"/usr/bin/{name}"

        with patch.object(webview_build.shutil, "which", side_effect=fake_which):
            self.assertEqual(webview_build.missing_tools(), ("npm",))

    def test_empty_when_everything_present(self) -> None:
        with patch.object(webview_build.shutil, "which", return_value="/usr/bin/tool"):
            self.assertEqual(webview_build.missing_tools(), ())


class TestIsUpToDate(unittest.TestCase):
    def test_false_when_index_html_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(webview_build.is_up_to_date(Path(tmp), "abc"))

    def test_false_when_marker_does_not_match(self) -> None:
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text("old")
            self.assertFalse(webview_build.is_up_to_date(dist, "new"))

    def test_true_when_marker_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text("abc\n")
            self.assertTrue(webview_build.is_up_to_date(dist, "abc"))


class TestEnsureWebviewBuilt(unittest.TestCase):
    def test_skips_the_build_when_already_up_to_date(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = cog_data_dir / "webview_dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("x")
            commit = webview_build.pinned_commit()
            (dist / ".built_commit").write_text(commit + "\n")

            with patch.object(webview_build, "_checkout") as checkout:
                result = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)

            checkout.assert_not_called()
            self.assertFalse(result.rebuilt)
            self.assertEqual(result.commit, commit)

    def test_force_rebuilds_even_when_already_up_to_date(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = cog_data_dir / "webview_dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("x")
            commit = webview_build.pinned_commit()
            (dist / ".built_commit").write_text(commit + "\n")
            build_out_dir = cog_data_dir / "vendor" / "pixel-agents" / "dist" / "webview"

            with (
                patch.object(webview_build, "_checkout"),
                patch.object(webview_build, "_install_dependencies"),
                patch.object(
                    webview_build,
                    "_build_bundle",
                    side_effect=lambda *a, **k: (
                        write_fake_vite_build(build_out_dir) or build_out_dir
                    ),
                ),
                patch.object(webview_build, "_emit_decoded_assets"),
            ):
                result = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG, force=True)

            self.assertTrue(result.rebuilt)

    def test_raises_with_missing_tools_before_touching_the_network(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            with (
                patch.object(webview_build, "missing_tools", return_value=("git", "npm")),
                patch.object(webview_build, "_checkout") as checkout,
            ):
                with self.assertRaises(webview_build.WebviewBuildError) as ctx:
                    webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)
            checkout.assert_not_called()
            self.assertEqual(ctx.exception.missing_tools, ("git", "npm"))

    def test_orchestrates_checkout_install_build_emit_sync_in_order(self) -> None:
        calls: list[str] = []
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            build_out_dir = cog_data_dir / "vendor" / "pixel-agents" / "dist" / "webview"

            def fake_checkout(vendor_dir, commit, log):
                calls.append("checkout")

            def fake_install(vendor_dir, log):
                calls.append("install")

            def fake_build(vendor_dir, log):
                calls.append("build")
                write_fake_vite_build(build_out_dir)
                return build_out_dir

            def fake_emit(vendor_dir, out_dir, log):
                calls.append("emit")

            with (
                patch.object(webview_build, "missing_tools", return_value=()),
                patch.object(webview_build, "_checkout", side_effect=fake_checkout),
                patch.object(webview_build, "_install_dependencies", side_effect=fake_install),
                patch.object(webview_build, "_build_bundle", side_effect=fake_build),
                patch.object(webview_build, "_emit_decoded_assets", side_effect=fake_emit),
            ):
                result = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)

            self.assertEqual(calls, ["checkout", "install", "build", "emit"])
            self.assertTrue(result.rebuilt)
            dist = cog_data_dir / "webview_dist"
            self.assertTrue((dist / "index.html").is_file())
            self.assertEqual(
                (dist / ".built_commit").read_text(encoding="utf-8").strip(), result.commit
            )


class TestEnsureWebviewBuiltWithCommitOverride(unittest.TestCase):
    def test_builds_from_the_override_instead_of_the_pin(self) -> None:
        override = "b" * 40
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            build_out_dir = cog_data_dir / "vendor" / "pixel-agents" / "dist" / "webview"

            with (
                patch.object(webview_build, "_checkout") as checkout,
                patch.object(webview_build, "_install_dependencies"),
                patch.object(
                    webview_build,
                    "_build_bundle",
                    side_effect=lambda *a, **k: (
                        write_fake_vite_build(build_out_dir) or build_out_dir
                    ),
                ),
                patch.object(webview_build, "_emit_decoded_assets"),
            ):
                result = webview_build.ensure_webview_built(
                    cog_data_dir, commit=override, logger=_LOG
                )

            checkout.assert_called_once_with(
                cog_data_dir / "vendor" / "pixel-agents", override, _LOG
            )
            self.assertEqual(result.commit, override)
            dist = cog_data_dir / "webview_dist"
            self.assertEqual((dist / ".built_commit").read_text(encoding="utf-8").strip(), override)

    def test_a_dist_built_from_the_default_pin_is_stale_once_an_override_is_set(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = cog_data_dir / "webview_dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text(webview_build.pinned_commit() + "\n")

            with patch.object(webview_build, "missing_tools", return_value=("git", "npm")):
                with self.assertRaises(webview_build.WebviewBuildError):
                    webview_build.ensure_webview_built(cog_data_dir, commit="c" * 40, logger=_LOG)


class TestBuildWebview(unittest.TestCase):
    """`build_webview` wraps ensure_webview_built and must never raise."""

    def test_reports_missing_tools_instead_of_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.object(webview_build, "missing_tools", return_value=("git",)):
                outcome = webview_build.build_webview(Path(tmp), logger=_LOG)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.missing_tools, ("git",))
        self.assertIn("git", outcome.status_line)

    def test_reports_success_when_already_up_to_date(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = cog_data_dir / "webview_dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("x")
            commit = webview_build.pinned_commit()
            (dist / ".built_commit").write_text(commit + "\n")
            outcome = webview_build.build_webview(cog_data_dir, logger=_LOG)
        self.assertTrue(outcome.ok)
        self.assertIn("up to date", outcome.status_line)
        # Discord markdown link, not a bare "@abc1234" -- the commit should
        # be one click from the reader, not something to copy-paste.
        expected_link = (
            f"[pixel-agents-hq/pixel-agents@{commit[:7]}]"
            f"(https://github.com/pixel-agents-hq/pixel-agents/tree/{commit})"
        )
        self.assertIn(expected_link, outcome.status_line)

    def test_reports_success_for_a_commit_override(self) -> None:
        override = "d" * 40
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = cog_data_dir / "webview_dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text(override + "\n")
            outcome = webview_build.build_webview(cog_data_dir, logger=_LOG, commit=override)
        self.assertTrue(outcome.ok)
        self.assertIn(override[:7], outcome.status_line)


class TestOwnerNotificationFor(unittest.TestCase):
    def test_names_missing_tools_and_the_rebuild_command(self) -> None:
        outcome = webview_build.BuildOutcome(
            ok=False, status_line="x", error="boom", missing_tools=("git", "npm")
        )
        message = webview_build.owner_notification_for(outcome)
        self.assertIn("git", message)
        self.assertIn("npm", message)
        self.assertIn("[p]pixelagents webview rebuild", message)

    def test_falls_back_to_the_error_when_no_tool_is_missing(self) -> None:
        outcome = webview_build.BuildOutcome(ok=False, status_line="x", error="disk full")
        message = webview_build.owner_notification_for(outcome)
        self.assertIn("disk full", message)


@unittest.skipUnless(
    os.environ.get("PIXELAGENTS_REAL_WEBVIEW_BUILD") == "1",
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run the real clone+build "
    "(network, git/node/npm required)",
)
class TestRealWebviewBuild(unittest.TestCase):
    """Exercises the actual network/git/npm/vite path -- see the module docstring."""

    def test_ensure_webview_built_produces_a_working_dist(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            result = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)
            self.assertTrue(result.rebuilt)
            self.assertEqual(result.commit, webview_build.pinned_commit())

            provider = WebviewAssetProvider(cog_data_dir / "webview_dist")
            provider.load_assets()
            for name in ("characters", "floors", "walls", "carpets", "furniture"):
                self.assertTrue(provider.assets.get(name))

            response = provider.dashboard_webview_response()
            self.assertEqual(response.get("status"), 0, response)

            second = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)
            self.assertFalse(second.rebuilt)


if __name__ == "__main__":
    unittest.main()
