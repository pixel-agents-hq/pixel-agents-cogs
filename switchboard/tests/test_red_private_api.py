"""Pin the private Red APIs this repo relies on, against the installed Red.

Red has no public equivalent for any of them (docs/dependency-loading.md,
"Private Red APIs"), so every cog's info.json locks min/max_bot_version to
the range CI tests, and CI runs this suite on both ends of that range
(.github/workflows/switchboard.yml). Switchboard's tests are the only ones
here that import the real red-discordbot package, so the check lives here
even though every cog's dependency_loader.py uses `bot._cog_mgr.find_cog`.
"""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

import redbot
from redbot.cogs.downloader.downloader import Downloader
from redbot.core._cog_manager import CogManager
from redbot.core.bot import Red
from redbot.core.core_commands import CoreLogic

from ..infrastructure.downloader_index import DOWNLOADER_CONFIG_IDENTIFIER

INFO_JSON = Path(__file__).resolve().parents[1] / "info.json"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".")[:3])


class TestRedVersion(unittest.TestCase):
    def test_installed_red_is_inside_the_pinned_range(self) -> None:
        info = json.loads(INFO_JSON.read_text(encoding="utf-8"))
        low = _version(info["min_bot_version"])
        high = _version(info["max_bot_version"])
        installed = redbot.version_info

        self.assertGreaterEqual(
            (installed.major, installed.minor, installed.micro),
            low,
            f"Red {redbot.__version__} is older than min_bot_version",
        )
        self.assertLessEqual(
            (installed.major, installed.minor, installed.micro),
            high,
            f"Red {redbot.__version__} is newer than max_bot_version: re-test the private "
            "APIs below on it, then raise max_bot_version in every info.json",
        )


class TestPrivateCogManager(unittest.TestCase):
    """`bot._cog_mgr`: every dependency_loader.py and switchboard's CogCatalog."""

    def test_red_creates_its_cog_manager_as_cog_mgr(self) -> None:
        self.assertIn("self._cog_mgr = CogManager()", inspect.getsource(Red.__init__))

    def test_find_cog_takes_a_package_name(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(CogManager.find_cog))
        self.assertEqual(list(inspect.signature(CogManager.find_cog).parameters), ["self", "name"])

    def test_catalog_methods_exist(self) -> None:
        for name in ("paths", "install_path", "user_defined_paths"):
            with self.subTest(name=name):
                method = getattr(CogManager, name)
                self.assertTrue(inspect.iscoroutinefunction(method))
                self.assertEqual(list(inspect.signature(method).parameters), ["self"])
        self.assertIsInstance(CogManager.CORE_PATH, Path)


class TestPrivateCoreLogic(unittest.TestCase):
    """`CoreLogic._load` / `_unload` and `bot._last_exception`: RedExtensions."""

    def test_load_and_unload_take_package_names(self) -> None:
        for method in (CoreLogic._load, CoreLogic._unload):
            with self.subTest(method=method.__name__):
                self.assertTrue(inspect.iscoroutinefunction(method))
                self.assertEqual(list(inspect.signature(method).parameters), ["self", "pkg_names"])

    def test_load_reports_the_keys_switchboard_reads(self) -> None:
        source = inspect.getsource(CoreLogic._load)
        for key in (
            "loaded_packages",
            "alreadyloaded_packages",
            "notfound_packages",
            "failed_with_reason_packages",
        ):
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', source)
        self.assertIn('"unloaded_packages"', inspect.getsource(CoreLogic._unload))

    def test_red_keeps_the_last_exception(self) -> None:
        self.assertIn("self._last_exception = None", inspect.getsource(Red.__init__))


class TestPrivateDownloaderConfig(unittest.TestCase):
    """Downloader's Config identifier and `installed_cogs`: read_downloader_index's
    fallback while the Downloader cog is unloaded."""

    def test_public_installed_cogs_is_a_coroutine(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(Downloader.installed_cogs))

    def test_identifier_and_key_match_downloader(self) -> None:
        source = inspect.getsource(Downloader.__init__)

        self.assertIn(f"identifier={DOWNLOADER_CONFIG_IDENTIFIER}", source)
        self.assertIn("installed_cogs=", source)


if __name__ == "__main__":
    unittest.main()
