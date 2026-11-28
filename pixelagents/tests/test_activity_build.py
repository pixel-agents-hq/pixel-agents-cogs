"""The Discord Activity build (`webview_build.build_activity`): the page
transform, its markers, the build orchestration (subprocesses mocked), and a
real npm + esbuild run gated like `TestRealWebviewBuild`."""

from __future__ import annotations

import json
import logging
import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pixelagents.infrastructure import webview_build

_LOG = logging.getLogger("test.activity_build")

INDEX = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <script type="module" crossorigin src="./assets/index-abc.js"></script>
    <link rel="modulepreload" crossorigin href="./assets/vendor-def.js">
    <link rel="stylesheet" crossorigin href="./assets/index-ghi.css">
  </head>
  <body><div id="root"></div></body>
</html>
"""


def _built_webview(cog_data_dir: Path, commit: str = "a" * 40) -> Path:
    dist = cog_data_dir / "webview_dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / ".built_commit").write_text(commit + "\n")
    return dist


def _fake_esbuild(args: list[str], *, cwd: Path | None, timeout: float, log: object) -> None:
    del timeout, log
    if args[0] == "npm":
        prefix = Path(args[args.index("--prefix") + 1])
        sdk = prefix / "node_modules" / "@discord" / "embedded-app-sdk"
        sdk.mkdir(parents=True, exist_ok=True)
        (sdk / "LICENSE").write_text("MIT License (sdk)")
        return
    assert cwd is not None
    out = Path(next(arg for arg in args if arg.startswith("--outfile=")).split("=", 1)[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("/* bootstrap */")


class TestActivityHtml(unittest.TestCase):
    def test_module_scripts_become_inert_and_the_bootstrap_loads_after_the_config(self) -> None:
        page = webview_build.activity_html(INDEX)
        self.assertNotIn('type="module"', page)
        self.assertIn(
            f'<script type="{webview_build.DEFERRED_SCRIPT_TYPE}" crossorigin '
            'src="./assets/index-abc.js"></script>',
            page,
        )
        config = page.index(f'src="./{webview_build.ACTIVITY_CONFIG_SCRIPT}"')
        bootstrap = page.index(f'src="./{webview_build.ACTIVITY_BOOTSTRAP}"')
        self.assertLess(config, bootstrap)
        self.assertLess(bootstrap, page.index("</head>"))
        # No inline script: the page runs under a strict script-src.
        self.assertNotIn("<script>", page)

    def test_a_page_without_a_module_script_is_refused(self) -> None:
        with self.assertRaises(webview_build.WebviewBuildError):
            webview_build.activity_html("<html><head></head><body></body></html>")


class TestActivityMarkers(unittest.TestCase):
    def test_requested_from_the_first_build_until_cancelled(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            _built_webview(cog_data_dir)
            self.assertFalse(webview_build.activity_requested(cog_data_dir))
            with patch.object(webview_build, "_run", side_effect=_fake_esbuild):
                webview_build.ensure_activity_built(cog_data_dir, logger=_LOG)
            self.assertTrue(webview_build.activity_requested(cog_data_dir))

            self.assertTrue(webview_build.cancel_activity(cog_data_dir))
            self.assertFalse(webview_build.activity_requested(cog_data_dir))
            self.assertFalse(webview_build.cancel_activity(cog_data_dir))
            # The npm install is kept for a later request.
            self.assertTrue((cog_data_dir / "activity_build" / "node_modules").is_dir())

    def test_stale_once_the_webview_is_rebuilt_from_another_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = _built_webview(cog_data_dir)
            with patch.object(webview_build, "_run", side_effect=_fake_esbuild):
                webview_build.ensure_activity_built(cog_data_dir, logger=_LOG)
            self.assertTrue(webview_build.activity_is_up_to_date(dist))
            (dist / ".built_commit").write_text("b" * 40 + "\n")
            self.assertFalse(webview_build.activity_is_up_to_date(dist))


class TestEnsureActivityBuilt(unittest.TestCase):
    def test_needs_a_built_webview(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(webview_build.WebviewBuildError):
                webview_build.ensure_activity_built(Path(tmp), logger=_LOG)

    def test_builds_the_page_bootstrap_and_licenses(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = _built_webview(cog_data_dir)
            with patch.object(webview_build, "_run", side_effect=_fake_esbuild) as run:
                self.assertTrue(webview_build.ensure_activity_built(cog_data_dir, logger=_LOG))
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0][:2], ["npm", "ci"])
            self.assertIn("--ignore-scripts", commands[0])
            self.assertTrue(commands[1][0].endswith("esbuild"))
            self.assertIn("--format=iife", commands[1])
            page = (dist / webview_build.ACTIVITY_PAGE).read_text()
            self.assertIn(webview_build.DEFERRED_SCRIPT_TYPE, page)
            self.assertEqual(
                (dist / webview_build.ACTIVITY_BOOTSTRAP).read_text(), "/* bootstrap */"
            )
            licenses = (dist / webview_build.ACTIVITY_LICENSES).read_text()
            self.assertIn("@discord/embedded-app-sdk@", licenses)
            self.assertIn("MIT License (sdk)", licenses)
            # esbuild only builds the bundle; it isn't shipped in it.
            self.assertNotIn("== esbuild@", licenses)
            self.assertNotIn("@esbuild/", licenses)

    def test_up_to_date_skips_everything_and_install_is_cached(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = _built_webview(cog_data_dir)
            with patch.object(webview_build, "_run", side_effect=_fake_esbuild) as run:
                webview_build.ensure_activity_built(cog_data_dir, logger=_LOG)
                self.assertFalse(webview_build.ensure_activity_built(cog_data_dir, logger=_LOG))
                self.assertEqual(run.call_count, 2)
                # A new webview commit rebundles, but the same lockfile
                # doesn't reinstall.
                (dist / ".built_commit").write_text("c" * 40 + "\n")
                self.assertTrue(webview_build.ensure_activity_built(cog_data_dir, logger=_LOG))
            self.assertEqual(
                [call.args[0][0] for call in run.call_args_list][2:],
                [str(cog_data_dir / "activity_build" / "node_modules" / ".bin" / "esbuild")],
            )

    def test_missing_node_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            _built_webview(cog_data_dir)
            with patch.object(webview_build.shutil, "which", return_value=None):
                outcome = webview_build.build_activity(cog_data_dir, logger=_LOG)
        self.assertFalse(outcome.ok)
        self.assertIn("node", outcome.status_line)


class TestBuildWebviewRebuildsTheActivity(unittest.TestCase):
    def _up_to_date_webview(self, cog_data_dir: Path) -> None:
        dist = _built_webview(cog_data_dir, webview_build.pinned_commit())
        (dist / "assets").mkdir()
        (dist / "assets" / "furniture-styles.json").write_text("{}")
        (dist / webview_build.LICENSE_FILENAME).write_text("MIT")
        (dist / ".built_base_path").write_text(webview_build.RELATIVE_BASE_PATH + "\n")
        (dist / ".built_manifest_version").write_text(f"{webview_build.MANIFEST_SCHEMA_VERSION}\n")

    def test_not_built_once_cancelled(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            self._up_to_date_webview(cog_data_dir)
            (cog_data_dir / "activity_build").mkdir()
            webview_build.cancel_activity(cog_data_dir)
            with patch.object(webview_build, "build_activity") as build:
                webview_build.build_webview(cog_data_dir, logger=_LOG)
        build.assert_not_called()

    def test_not_built_unless_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            self._up_to_date_webview(cog_data_dir)
            with patch.object(webview_build, "build_activity") as build:
                outcome = webview_build.build_webview(cog_data_dir, logger=_LOG)
        self.assertTrue(outcome.ok)
        build.assert_not_called()

    def test_built_with_the_webview_once_requested_and_a_failure_is_not_fatal(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            self._up_to_date_webview(cog_data_dir)
            (cog_data_dir / "activity_build").mkdir()
            (cog_data_dir / "activity_build" / ".requested").write_text("1")
            with patch.object(
                webview_build, "_run", side_effect=webview_build.WebviewBuildError("x")
            ):
                outcome = webview_build.build_webview(cog_data_dir, logger=_LOG)
        self.assertTrue(outcome.ok)
        self.assertIn("Could not build the Activity", outcome.status_line)


class TestActivitySources(unittest.TestCase):
    def test_the_lockfile_pins_exactly_the_declared_packages_from_npm(self) -> None:
        source = Path(webview_build.__file__).parent / "webview_build_scripts" / "activity"
        manifest = json.loads((source / "package.json").read_text())
        lock = json.loads((source / "package-lock.json").read_text())
        self.assertEqual(lock["packages"][""]["dependencies"], manifest["dependencies"])
        for version in manifest["dependencies"].values():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        for path, entry in lock["packages"].items():
            if path:
                self.assertTrue(entry["resolved"].startswith("https://registry.npmjs.org/"))


@unittest.skipUnless(
    os.environ.get("PIXELAGENTS_REAL_WEBVIEW_BUILD") == "1" and shutil.which("node") is not None,
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run the real npm + esbuild build",
)
class TestRealActivityBuild(unittest.TestCase):
    def test_builds_a_bootstrap_bundle_with_the_sdk(self) -> None:
        with TemporaryDirectory() as tmp:
            cog_data_dir = Path(tmp)
            dist = _built_webview(cog_data_dir)
            outcome = webview_build.build_activity(cog_data_dir, logger=_LOG)
            self.assertTrue(outcome.ok, outcome.status_line)
            bundle = (dist / webview_build.ACTIVITY_BOOTSTRAP).read_text()
            self.assertIn("frame_id query param is not defined", bundle)
            self.assertIn("PIXEL_AGENTS_ACTIVITY", bundle)


if __name__ == "__main__":
    unittest.main()
