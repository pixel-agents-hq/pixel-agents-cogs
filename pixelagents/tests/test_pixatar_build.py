"""The pixatar CLI build (infrastructure/pixatar_build.py).

`TestRealPixatarBuild` clones and builds the pinned Pixel Index commit for
real (network, git/node/npm) and is skipped unless
PIXELAGENTS_REAL_PIXATAR_BUILD=1, like test_webview_build.py's real build.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pixelagents.domain.characters import PixatarFailure
from pixelagents.infrastructure import pixatar_build, pixatar_client, webview_build

_LOG = logging.getLogger("test_pixatar_build")


class TestLoadPixatar(unittest.TestCase):
    def test_the_client_is_loaded_from_the_checkout_not_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            package = Path(tmp) / "pixatar"
            package.mkdir()
            (package / "__init__.py").write_text(
                "from ._client import Pixatar, PixatarError\n", encoding="utf-8"
            )
            (package / "_client.py").write_text(
                "class PixatarError(Exception):\n    pass\n\nclass Pixatar:\n    checkout = True\n",
                encoding="utf-8",
            )
            module = pixatar_client.load_pixatar(Path(tmp))
            self.assertTrue(module.Pixatar.checkout)
            # Loaded once, then reused.
            self.assertIs(pixatar_client.load_pixatar(Path(tmp)), module)

    def test_without_a_checkout_or_an_installed_client_pixatar_is_unavailable(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            mock.patch.dict(sys.modules, {"pixatar": None}),
            self.assertRaises(PixatarFailure) as caught,
        ):
            pixatar_client.load_pixatar(Path(tmp))
        self.assertEqual(caught.exception.code, "cli-not-found")


class TestBuildPixatar(unittest.TestCase):
    def test_an_explicit_cli_is_used_without_building(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {pixatar_build.CLI_ENV_VAR: "node /opt/cli.js"}),
            mock.patch.object(pixatar_build, "ensure_pixatar_built") as ensure,
        ):
            outcome = pixatar_build.build_pixatar(Path(tmp), logger=_LOG)
        ensure.assert_not_called()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.command, ())

    def test_missing_tools_are_reported_not_raised(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {pixatar_build.CLI_ENV_VAR: ""}),
            mock.patch.object(pixatar_build.shutil, "which", return_value=None),
        ):
            outcome = pixatar_build.build_pixatar(Path(tmp), logger=_LOG)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.missing_tools, ("git", "node", "npm"))

    def test_an_up_to_date_build_is_kept(self) -> None:
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            cli = pixatar_build.cli_path(data)
            cli.parent.mkdir(parents=True)
            cli.write_text("", encoding="utf-8")
            marker = pixatar_build.pixatar_dir(data) / ".built_commit"
            marker.write_text(pixatar_build.pinned_commit() + "\n", encoding="utf-8")
            with mock.patch.object(pixatar_build, "_run") as run:
                rebuilt, command = pixatar_build.ensure_pixatar_built(data, logger=_LOG)
            run.assert_not_called()
            self.assertFalse(rebuilt)
            self.assertEqual(command[-1], str(cli))

            marker.write_text("0" * 40 + "\n", encoding="utf-8")
            self.assertFalse(pixatar_build.is_up_to_date(data, pixatar_build.pinned_commit()))

    def test_the_build_runs_clone_submodule_install_build_and_a_version_check(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            mock.patch.object(pixatar_build.shutil, "which", side_effect=lambda t: f"/bin/{t}"),
            mock.patch.object(pixatar_build, "_run") as run,
        ):
            rebuilt, _ = pixatar_build.ensure_pixatar_built(Path(tmp), logger=_LOG)
            self.assertTrue(rebuilt)
            self.assertEqual(pixatar_build.built_commit(Path(tmp)), pixatar_build.pinned_commit())
        steps = [call.args[0] for call in run.call_args_list]
        self.assertEqual(steps[0][:2], ["git", "clone"])
        self.assertIn(["submodule", "update", "--init"], [s[3:6] for s in steps])
        self.assertIn("--ignore-scripts", next(s for s in steps if s[:2] == ["npm", "ci"]))
        self.assertEqual(steps[-1][-1], "--version")


def _webview_pin_contains(repo: Path, commit: str) -> bool:
    """Whether `webview_vendor.commit` has `commit` in its history (a
    blobless fetch: only the commit graph is needed)."""

    pin = webview_build.pinned_commit()
    git = ["git", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
    subprocess.run(
        [*git, "fetch", "-q", "--filter=blob:none", webview_build.pinned_repo_url(), pin],
        check=True,
    )
    return subprocess.run([*git, "merge-base", "--is-ancestor", commit, pin]).returncode == 0


@unittest.skipUnless(
    os.environ.get("PIXELAGENTS_REAL_PIXATAR_BUILD") == "1",
    "set PIXELAGENTS_REAL_PIXATAR_BUILD=1 to run the real clone+build "
    "(network, git/node/npm required)",
)
class TestRealPixatarBuild(unittest.IsolatedAsyncioTestCase):
    async def test_the_built_cli_deals_with_a_wardrobe_the_office_has(self) -> None:
        from pixelagents.infrastructure.pixatar_client import PixatarClient

        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.dict(os.environ, {pixatar_build.CLI_ENV_VAR: ""}):
                outcome = pixatar_build.build_pixatar(data, logger=_LOG)
            self.assertTrue(outcome.ok, outcome.error)

            assert outcome.client_src is not None
            self.assertTrue((outcome.client_src / "pixatar" / "__init__.py").is_file())
            client = PixatarClient(outcome.command, outcome.client_src)
            try:
                vendor = await client.vendor_commit()
                dealt = await client.from_text("123456789012345678")
            finally:
                client.close()
            self.assertEqual(
                dealt.seed,
                "px.ASMBNkHtAcOaAj2AbyDFgBthZAUGfQQ53_gJfRRQ_-kKfYgBHdT6AEIAZQh9VUX8AQ",
            )

            # The wardrobe pixatar draws with is one the vendored webview has.
            self.assertTrue(
                _webview_pin_contains(data / "webview-history", vendor),
                f"pixatar draws with {vendor}, which webview_vendor.commit doesn't contain",
            )


if __name__ == "__main__":
    unittest.main()
