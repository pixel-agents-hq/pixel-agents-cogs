from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from ..domain import CogSource
from ..infrastructure import CogCatalog, PipInstaller, build_lookup
from ..infrastructure.downloader_index import index_from_installed_cogs


def make_cog(root: Path, name: str, info: dict[str, object] | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("")
    if info is not None:
        (directory / "info.json").write_text(json.dumps(info))
    return directory


class FakeCogManager:
    def __init__(self, base: Path) -> None:
        self.install = base / "data" / "cogs" / "CogManager" / "cogs"
        self.cli = base / "ci-fixtures"
        self.user = base / "office-cogs"
        self.CORE_PATH = base / "redbot" / "cogs"
        for path in (self.install, self.cli, self.user, self.CORE_PATH):
            path.mkdir(parents=True)

    async def paths(self) -> list[Path]:
        # Red's own priority order.
        return [self.install, self.cli, self.user, self.CORE_PATH]

    async def install_path(self) -> Path:
        return self.install

    async def user_defined_paths(self) -> list[Path]:
        return [self.user]


class TestCogCatalog(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.manager = FakeCogManager(self.base)
        self.tracked: dict[str, str] = {}

        async def index() -> dict[str, str]:
            return self.tracked

        self.catalog = CogCatalog(self.manager, downloader_index=index)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_classifies_every_source(self) -> None:
        make_cog(self.manager.install, "tracked")
        make_cog(self.manager.install, "handcopy")
        make_cog(self.manager.cli, "dashboard")
        make_cog(self.manager.user, "corridor")
        make_cog(self.manager.CORE_PATH, "admin")
        self.tracked["tracked"] = "pixel_agents_cogs"

        packages = await self.catalog.scan()

        sources = {name: copies[0].source for name, copies in packages.items()}
        self.assertEqual(
            sources,
            {
                "tracked": CogSource.DOWNLOADER,
                "handcopy": CogSource.DOWNLOADER_UNTRACKED,
                "dashboard": CogSource.CLI_PATH,
                "corridor": CogSource.ADDPATH,
                "admin": CogSource.CORE,
            },
        )
        self.assertEqual(packages["tracked"][0].repo_name, "pixel_agents_cogs")

    async def test_copies_are_listed_in_red_priority_order(self) -> None:
        make_cog(self.manager.user, "corridor")
        make_cog(self.manager.install, "corridor")

        packages = await self.catalog.scan()

        self.assertEqual(
            [p.source for p in packages["corridor"]],
            [CogSource.DOWNLOADER_UNTRACKED, CogSource.ADDPATH],
        )

    async def test_reads_info_json(self) -> None:
        make_cog(
            self.manager.user,
            "pixelagents",
            {
                "requirements": ["pyyaml>=6.0"],
                "required_cogs": {"corridor": "https://example.invalid"},
                "hidden": True,
            },
        )

        (package,) = (await self.catalog.scan())["pixelagents"]

        self.assertEqual(package.requirements, ("pyyaml>=6.0",))
        self.assertEqual(package.required_cogs, ("corridor",))
        self.assertTrue(package.hidden)
        self.assertEqual(package.root, self.manager.user)

    async def test_skips_non_packages_shared_libraries_and_bad_names(self) -> None:
        make_cog(self.manager.user, "contracts", {"type": "SHARED_LIBRARY"})
        (self.manager.user / "my_cog").mkdir()  # no __init__.py
        make_cog(self.manager.user, "not-a-name")
        (self.manager.user / "notes.txt").write_text("")
        broken = make_cog(self.manager.user, "broken")
        (broken / "info.json").write_text("{not json")

        packages = await self.catalog.scan()

        self.assertEqual(set(packages), {"broken"})
        self.assertEqual(packages["broken"][0].requirements, ())

    async def test_missing_root_is_ignored(self) -> None:
        self.manager.cli.rmdir()

        self.assertEqual(await self.catalog.scan(), {})


class TestDistributionIndex(unittest.TestCase):
    def test_first_path_wins_and_names_are_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self._dist(Path(first), "Typing_Inspection", "0.4.1")
            self._dist(Path(second), "typing-inspection", "0.4.3")
            self._dist(
                Path(second),
                "a2a-sdk",
                "0.3.0",
                requires=['starlette>=0.40; extra == "http-server"'],
            )

            lookup = build_lookup([first, second])

            inspection = lookup("typing-inspection")
            assert inspection is not None
            self.assertEqual(inspection.version, "0.4.1")
            a2a = lookup("a2a-sdk")
            assert a2a is not None
            self.assertEqual(a2a.requires, ('starlette>=0.40; extra == "http-server"',))
            self.assertIsNone(lookup("uvicorn"))

    @staticmethod
    def _dist(path: Path, name: str, version: str, requires: list[str] | None = None) -> None:
        info = path / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        lines += [f"Requires-Dist: {line}" for line in requires or []]
        (info / "METADATA").write_text("\n".join(lines) + "\n")


class TestDownloaderIndex(unittest.TestCase):
    def test_maps_module_to_repo(self) -> None:
        raw = {"pixel_agents_cogs": {"corridor": {"commit": "abc"}, "cctv": {}}, "x": "junk"}

        self.assertEqual(
            index_from_installed_cogs(raw),
            {"corridor": "pixel_agents_cogs", "cctv": "pixel_agents_cogs"},
        )

    def test_never_used_downloader(self) -> None:
        self.assertEqual(index_from_installed_cogs(None), {})


@unittest.skipIf(os.name == "nt", "fake python is a POSIX shell script")
class TestPipInstaller(unittest.IsolatedAsyncioTestCase):
    """Runs a fake `python` that records its argv, instead of real pip."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.target = self.base / "Downloader" / "lib"
        self.argv_file = self.base / "argv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fake_python(self, exit_code: int) -> str:
        script = self.base / "python"
        script.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$@" > {self.argv_file}
                echo "pip says hi"
                exit {exit_code}
                """
            )
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    async def test_runs_downloaders_pip_command_once_deduplicated(self) -> None:
        installer = PipInstaller(lambda: self.target, python=self.fake_python(0))

        result = await installer.install(["uvicorn", "mcp>=1.29,<2", "uvicorn"])

        self.assertTrue(result.ok)
        self.assertEqual(result.requirements, ("uvicorn", "mcp>=1.29,<2"))
        self.assertIn("pip says hi", result.output)
        self.assertTrue(self.target.is_dir())
        argv = self.argv_file.read_text().splitlines()
        self.assertEqual(argv[:3], ["-m", "pip", "install"])
        self.assertIn("-U", argv)
        self.assertEqual(argv[argv.index("-t") + 1], str(self.target))
        self.assertEqual(argv[-2:], ["uvicorn", "mcp>=1.29,<2"])

    async def test_failure_keeps_the_exit_code(self) -> None:
        installer = PipInstaller(lambda: self.target, python=self.fake_python(3))

        result = await installer.install(["uvicorn"])

        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 3)

    async def test_nothing_to_install_does_not_run_pip(self) -> None:
        installer = PipInstaller(lambda: self.target, python=self.fake_python(0))

        result = await installer.install([])

        self.assertFalse(result.ran)
        self.assertFalse(self.argv_file.exists())

    async def test_default_interpreter_is_the_running_one(self) -> None:
        installer = PipInstaller(lambda: self.target)

        self.assertEqual(installer._python, sys.executable)
