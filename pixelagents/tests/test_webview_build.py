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

import json
import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pixelagents.infrastructure import webview_build
from pixelagents.tests.conftest import write_fake_vite_build

_LOG = logging.getLogger("test.webview_build")


def _write_up_to_date_markers(dist: Path, commit: str) -> None:
    (dist / ".built_commit").write_text(commit + "\n")
    (dist / ".built_base_path").write_text(webview_build.RELATIVE_BASE_PATH + "\n")
    (dist / ".built_manifest_version").write_text(f"{webview_build.MANIFEST_SCHEMA_VERSION}\n")


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
            self.assertFalse(
                webview_build.is_up_to_date(Path(tmp), "abc", webview_build.RELATIVE_BASE_PATH)
            )

    def test_false_when_commit_marker_does_not_match(self) -> None:
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text("old")
            (dist / ".built_base_path").write_text(webview_build.RELATIVE_BASE_PATH)
            self.assertFalse(
                webview_build.is_up_to_date(dist, "new", webview_build.RELATIVE_BASE_PATH)
            )

    def test_false_when_base_path_marker_does_not_match(self) -> None:
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text("abc\n")
            (dist / ".built_base_path").write_text("/third-party/other/static/\n")
            self.assertFalse(
                webview_build.is_up_to_date(dist, "abc", webview_build.RELATIVE_BASE_PATH)
            )

    def test_true_when_both_markers_match_and_furniture_styles_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            _write_up_to_date_markers(dist, "abc")
            (dist / "assets").mkdir()
            (dist / "assets" / "furniture-styles.json").write_text("{}")
            self.assertTrue(
                webview_build.is_up_to_date(dist, "abc", webview_build.RELATIVE_BASE_PATH)
            )

    def test_false_when_furniture_styles_json_is_missing(self) -> None:
        """Regression test: a host whose webview_dist/ predates
        _build_furniture_styles must self-heal on its next cog_load(),
        even though its commit/base_path markers still match -- otherwise
        every real furniture asset id is silently unrecognized by
        architect's style manifest lookup forever. See is_up_to_date's own
        docstring for the real incident this guards against."""

        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            _write_up_to_date_markers(dist, "abc")
            # No assets/furniture-styles.json written -- pre-upgrade state.
            self.assertFalse(
                webview_build.is_up_to_date(dist, "abc", webview_build.RELATIVE_BASE_PATH)
            )

    def test_false_when_manifest_version_marker_is_stale(self) -> None:
        """Regression test: a host whose webview_dist/ was built before a
        furniture-styles.json *schema* change (a field added/removed/
        renamed on styles or facings) must self-heal even though its
        commit/base_path markers still match and the file itself still
        exists -- otherwise every consumer parsing the old-shaped JSON
        against the new schema crashes outright. This is the exact
        incident that made MANIFEST_SCHEMA_VERSION exist: the flat
        `{"south": "DESK_FRONT"}` facing shape becoming nested
        `{"south": {"catalog_id": ..., "footprint_width": ...}}` crashed
        FurnitureStyleManifest.from_raw() on any host whose vendored
        commit hadn't also changed."""

        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text("abc\n")
            (dist / ".built_base_path").write_text(webview_build.RELATIVE_BASE_PATH + "\n")
            (dist / ".built_manifest_version").write_text("1\n")  # stale, current is 2+
            (dist / "assets").mkdir()
            (dist / "assets" / "furniture-styles.json").write_text("{}")
            self.assertFalse(
                webview_build.is_up_to_date(dist, "abc", webview_build.RELATIVE_BASE_PATH)
            )

    def test_false_when_manifest_version_marker_is_missing(self) -> None:
        """Same incident as above, for a host built before this marker
        existed at all (no `.built_manifest_version` file, not merely a
        stale one)."""

        with TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("x")
            (dist / ".built_commit").write_text("abc\n")
            (dist / ".built_base_path").write_text(webview_build.RELATIVE_BASE_PATH + "\n")
            # No .built_manifest_version written -- pre-marker state.
            (dist / "assets").mkdir()
            (dist / "assets" / "furniture-styles.json").write_text("{}")
            self.assertFalse(
                webview_build.is_up_to_date(dist, "abc", webview_build.RELATIVE_BASE_PATH)
            )


class TestEnsureWebviewBuilt(unittest.TestCase):
    def test_skips_the_build_when_already_up_to_date(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = cog_data_dir / "webview_dist"
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("x")
            (dist / "assets" / "furniture-styles.json").write_text("{}")
            commit = webview_build.pinned_commit()
            _write_up_to_date_markers(dist, commit)

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
            self.assertEqual(
                (dist / ".built_base_path").read_text(encoding="utf-8").strip(),
                result.base_path,
            )

    def test_sync_generates_furniture_styles_json_from_the_catalog(self) -> None:
        """docs/architect-semantic-ir-design.md §6.4: furniture-styles.json
        is a generated artifact, produced fresh on every build from
        whatever furniture-catalog.json the vendored commit produced."""

        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            build_out_dir = cog_data_dir / "vendor" / "pixel-agents" / "dist" / "webview"

            def fake_build(vendor_dir, log):
                write_fake_vite_build(build_out_dir)
                return build_out_dir

            with (
                patch.object(webview_build, "missing_tools", return_value=()),
                patch.object(webview_build, "_checkout"),
                patch.object(webview_build, "_install_dependencies"),
                patch.object(webview_build, "_build_bundle", side_effect=fake_build),
                patch.object(webview_build, "_emit_decoded_assets"),
            ):
                webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)

            styles_path = cog_data_dir / "webview_dist" / "assets" / "furniture-styles.json"
            self.assertTrue(styles_path.is_file())
            manifest = json.loads(styles_path.read_text(encoding="utf-8"))
            self.assertIn("styles", manifest)


class TestEnsureWebviewBuiltConcurrency(unittest.TestCase):
    """Regression test for a real incident: two independent builds against
    the same vendor checkout collided on `.git/index.lock` because nothing
    serialized them. See `_build_lock`'s docstring and
    docs/dependency-loading.md for why this has to be a real OS-level lock
    rather than an asyncio one."""

    def test_concurrent_builds_do_not_interleave_their_steps(self) -> None:
        import threading
        import time

        state_lock = threading.Lock()
        in_progress = 0
        max_concurrent = 0
        violations: list[str] = []

        def _enter(step: str) -> None:
            nonlocal in_progress, max_concurrent
            with state_lock:
                in_progress += 1
                max_concurrent = max(max_concurrent, in_progress)
                if in_progress > 1:
                    violations.append(step)

        def _leave() -> None:
            nonlocal in_progress
            with state_lock:
                in_progress -= 1

        def fake_checkout(vendor_dir, commit, log):
            _enter("checkout")
            try:
                # Widen the window: if the real lock didn't serialize
                # callers, the other thread would run its own steps in here.
                time.sleep(0.05)
            finally:
                _leave()

        def fake_install(vendor_dir, log):
            _enter("install")
            _leave()

        def fake_build(vendor_dir, log):
            _enter("build")
            try:
                build_out_dir = vendor_dir / "dist" / "webview"
                if build_out_dir.exists():
                    import shutil

                    shutil.rmtree(build_out_dir)
                write_fake_vite_build(build_out_dir)
                return build_out_dir
            finally:
                _leave()

        def fake_emit(vendor_dir, out_dir, log):
            _enter("emit")
            _leave()

        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            commit_a = "a" * 40
            commit_b = "b" * 40

            with (
                patch.object(webview_build, "missing_tools", return_value=()),
                patch.object(webview_build, "_checkout", side_effect=fake_checkout),
                patch.object(webview_build, "_install_dependencies", side_effect=fake_install),
                patch.object(webview_build, "_build_bundle", side_effect=fake_build),
                patch.object(webview_build, "_emit_decoded_assets", side_effect=fake_emit),
            ):
                results: list[webview_build.BuildResult] = []
                errors: list[BaseException] = []

                def run(commit: str) -> None:
                    try:
                        results.append(
                            webview_build.ensure_webview_built(
                                cog_data_dir, commit=commit, logger=_LOG, force=True
                            )
                        )
                    except BaseException as exc:  # surfaced via errors, not lost in the thread
                        errors.append(exc)

                threads = [
                    threading.Thread(target=run, args=(commit_a,)),
                    threading.Thread(target=run, args=(commit_b,)),
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=5)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(violations, [], "a build step ran while another was in progress")
            self.assertEqual(max_concurrent, 1)


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
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("x")
            (dist / "assets" / "furniture-styles.json").write_text("{}")
            commit = webview_build.pinned_commit()
            _write_up_to_date_markers(dist, commit)
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
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("x")
            (dist / "assets" / "furniture-styles.json").write_text("{}")
            _write_up_to_date_markers(dist, override)
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
        # Left as the literal placeholder -- substituting it is corridor's
        # job (corridor.substitute_default_prefix), not this pure function's.
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
        # WebviewAssetProvider (which actually parses/serves this output) is
        # floorplan's now -- contracts/pixel_agents/verify.py exercises the
        # full clone-build-serve path across both packages. This stays
        # scoped to what pixelagents itself owns: the build produces the
        # files WebviewAssetProvider is documented to read.
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            result = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)
            self.assertTrue(result.rebuilt)
            self.assertEqual(result.commit, webview_build.pinned_commit())

            dist = cog_data_dir / "webview_dist"
            self.assertTrue((dist / "index.html").is_file())
            for name in ("characters", "floors", "walls", "carpets", "furniture"):
                self.assertTrue((dist / "assets" / "decoded" / f"{name}.json").is_file())
            self.assertTrue((dist / "assets" / "furniture-catalog.json").is_file())
            self.assertEqual(webview_build.built_commit(dist), webview_build.pinned_commit())
            self.assertEqual(webview_build.built_base_path(dist), webview_build.RELATIVE_BASE_PATH)

            second = webview_build.ensure_webview_built(cog_data_dir, logger=_LOG)
            self.assertFalse(second.rebuilt)


if __name__ == "__main__":
    unittest.main()
