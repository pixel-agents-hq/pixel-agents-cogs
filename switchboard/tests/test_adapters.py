from __future__ import annotations

import ast
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any

from ..adapters import rendering
from ..adapters.extensions import RedExtensions
from ..application import SwitchboardService
from ..domain import LoadOutcome, UnloadOutcome
from ..switchboard import Switchboard
from .conftest import (
    FakeCatalog,
    FakeContext,
    FakeDistributions,
    FakeExtensions,
    FakeInstaller,
    FakeSettings,
    package,
)

PACKAGE_DIR = Path(__file__).resolve().parent.parent


class FakeCore:
    def __init__(self, load_result: dict[str, Any], unload_result: dict[str, Any]) -> None:
        self.load_result = load_result
        self.unload_result = unload_result
        self.calls: list[tuple[str, list[str]]] = []

    async def _load(self, names: list[str]) -> dict[str, Any]:
        self.calls.append(("load", list(names)))
        return self.load_result

    async def _unload(self, names: list[str]) -> dict[str, Any]:
        self.calls.append(("unload", list(names)))
        return self.unload_result


class FakeBot:
    def __init__(self, core: FakeCore, extensions: dict[str, object] | None = None) -> None:
        self.core = core
        self.extensions = extensions or {}
        self._last_exception: str | None = None

    def get_cog(self, name: str) -> object:
        return self.core if name == "Core" else None


class TestRedExtensions(unittest.IsolatedAsyncioTestCase):
    async def test_maps_red_load_results(self) -> None:
        cases = [
            ({"loaded_packages": ["x"]}, LoadOutcome.LOADED, ""),
            ({"alreadyloaded_packages": ["x"]}, LoadOutcome.ALREADY_LOADED, ""),
            ({"notfound_packages": ["x"]}, LoadOutcome.NOT_FOUND, "not found in any cog path"),
            ({"failed_with_reason_packages": {"x": "nope"}}, LoadOutcome.FAILED, "nope"),
        ]
        for red_result, outcome, detail in cases:
            bot = FakeBot(FakeCore(red_result, {}))

            result = await RedExtensions(bot).load("x")

            self.assertEqual((result.outcome, result.detail), (outcome, detail))
            self.assertEqual(bot.core.calls, [("load", ["x"])])

    async def test_failure_without_reason_uses_the_last_traceback_line(self) -> None:
        bot = FakeBot(FakeCore({"failed_packages": ["corridor"]}, {}))
        bot._last_exception = (
            "Exception during loading of package\nTraceback...\n"
            "ModuleNotFoundError: No module named 'a2a'\n"
        )

        result = await RedExtensions(bot).load("corridor")

        self.assertEqual(result.detail, "ModuleNotFoundError: No module named 'a2a'")

    async def test_unload(self) -> None:
        bot = FakeBot(FakeCore({}, {"unloaded_packages": ["x"]}))
        self.assertEqual((await RedExtensions(bot).unload("x")).outcome, UnloadOutcome.UNLOADED)
        bot = FakeBot(FakeCore({}, {"notloaded_packages": ["x"]}))
        self.assertEqual((await RedExtensions(bot).unload("x")).outcome, UnloadOutcome.NOT_LOADED)

    def test_origin_is_the_loaded_modules_package_directory(self) -> None:
        module = types.ModuleType("fakecog_for_origin")
        module.__file__ = "/cogs/office-cogs/fakecog_for_origin/__init__.py"
        sys.modules["fakecog_for_origin"] = module
        try:
            extensions = RedExtensions(FakeBot(FakeCore({}, {}), {"fakecog_for_origin": module}))
            self.assertEqual(
                extensions.origin("fakecog_for_origin"),
                Path("/cogs/office-cogs/fakecog_for_origin").resolve(),
            )
            self.assertIsNone(RedExtensions(FakeBot(FakeCore({}, {}))).origin("fakecog_for_origin"))
        finally:
            del sys.modules["fakecog_for_origin"]


def make_service() -> tuple[SwitchboardService, FakeExtensions]:
    dists = FakeDistributions()
    extensions = FakeExtensions(loaded=["switchboard"])
    service = SwitchboardService(
        catalog=FakeCatalog(
            package("corridor", requirements=("a2a-sdk",)),
            package("deskutils", requires=("corridor",)),
            package("switchboard"),
        ),
        installer=FakeInstaller(dists),
        lookup_factory=lambda: dists.lookup,
        extensions=extensions,
        settings=FakeSettings(),
    )
    return service, extensions


class FakeCog:
    """Stands in for `self` when calling command/RPC callbacks directly."""

    def __init__(self, service: SwitchboardService) -> None:
        self._service = service


class TestCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service, self.extensions = make_service()
        self.cog = FakeCog(self.service)
        self.ctx = FakeContext()

    async def test_status_renders_a_boxed_table(self) -> None:
        await Switchboard.cmd_status.callback(self.cog, self.ctx, "corridor")

        self.assertEqual(len(self.ctx.sent), 1)
        self.assertTrue(self.ctx.sent[0].startswith("```"))
        self.assertIn("addpath", self.ctx.sent[0])
        self.assertIn("missing: a2a-sdk", self.ctx.sent[0])

    async def test_load_reports_order_and_outcomes(self) -> None:
        await Switchboard.cmd_load.callback(self.cog, self.ctx, "deskutils")

        text = "\n".join(self.ctx.sent)
        self.assertIn("Order: corridor → deskutils", text)
        self.assertIn("✅ deskutils loaded", text)
        self.assertEqual(self.extensions.load_calls, ["corridor", "deskutils"])

    async def test_commands_that_need_cogs_send_help_without_them(self) -> None:
        for command in (
            Switchboard.cmd_install,
            Switchboard.cmd_load,
            Switchboard.cmd_unload,
            Switchboard.cmd_reload,
        ):
            await command.callback(self.cog, self.ctx)

        self.assertEqual(self.ctx.help_sent, 4)
        self.assertEqual(self.extensions.load_calls, [])

    async def test_autorepair_toggle(self) -> None:
        await Switchboard.cmd_autorepair.callback(self.cog, self.ctx, False)
        await Switchboard.cmd_autorepair.callback(self.cog, self.ctx)

        self.assertEqual(self.ctx.sent, ["Autorepair is off.", "Autorepair is off."])
        await Switchboard.cmd_repair.callback(self.cog, self.ctx)
        self.assertIn("Autorepair is off", self.ctx.sent[-1])

    def test_every_command_is_owner_only(self) -> None:
        from redbot.core.commands.requires import PrivilegeLevel

        # Red records @commands.is_owner() on the group's Requires, and
        # subcommands inherit it.
        group = Switchboard.switchboard_group
        self.assertEqual(group.requires.privilege_level, PrivilegeLevel.BOT_OWNER)


class TestRpc(unittest.IsolatedAsyncioTestCase):
    async def test_handlers_return_json_safe_reports(self) -> None:
        service, _ = make_service()
        cog = FakeCog(service)

        status = await Switchboard.status(cog, ["corridor"])  # type: ignore[arg-type]
        load = await Switchboard.load(cog, ["deskutils"])  # type: ignore[arg-type]
        unload = await Switchboard.unload(cog, ["corridor"])  # type: ignore[arg-type]
        repair = await Switchboard.repair(cog)  # type: ignore[arg-type]

        for payload in (status, load, unload, repair):
            json.dumps(payload)
        self.assertEqual(status["cogs"][0]["source"], "addpath")  # type: ignore[index]
        self.assertEqual(load["order"], ["corridor", "deskutils"])
        self.assertTrue(load["ok"])
        self.assertEqual(unload["order"], ["deskutils", "corridor"])

    def test_handlers_get_red_rpc_names(self) -> None:
        registered: list[str] = []

        class Bot:
            def register_rpc_handler(self, method: Any) -> None:
                # Red: f"{cog class name}__{method name}".upper()
                registered.append(f"{type(method.__self__).__name__}__{method.__name__}".upper())

        cog = Switchboard.__new__(Switchboard)
        cog.bot = Bot()  # type: ignore[assignment]
        cog._register_rpc_handlers()

        self.assertEqual(
            registered,
            [
                "SWITCHBOARD__STATUS",
                "SWITCHBOARD__INSTALL",
                "SWITCHBOARD__LOAD",
                "SWITCHBOARD__UNLOAD",
                "SWITCHBOARD__REPAIR",
            ],
        )


class TestRendering(unittest.TestCase):
    def test_load_text_shows_skips_and_pip_tail_on_failure(self) -> None:
        from ..domain import InstallResult, LoadReport, LoadResult

        report = LoadReport(
            order=("corridor", "cctv"),
            results=(
                LoadResult("corridor", LoadOutcome.FAILED, "boom"),
                LoadResult("cctv", LoadOutcome.SKIPPED, "requires corridor, which did not load"),
            ),
            install=InstallResult(ok=False, requirements=("x",), output="ERROR: x", returncode=1),
            still_missing={"corridor": ("x",)},
        )

        text = rendering.load_text(report)

        self.assertIn("❌ pip install failed (exit code 1)", text)
        self.assertIn("⚠️ corridor still missing: x", text)
        self.assertIn("⛔ cctv skipped: requires corridor", text)
        self.assertIn("ERROR: x", text)


class TestPackageBoundaries(unittest.TestCase):
    """Switchboard has to load where corridor cannot even import, and must
    not need anything Red does not already ship."""

    def test_no_module_imports_another_office_cog(self) -> None:
        repo_root = PACKAGE_DIR.parent
        other_cogs = {
            path.parent.name
            for path in repo_root.glob("*/info.json")
            if path.parent.name != PACKAGE_DIR.name
        }
        offenders = []
        for path in PACKAGE_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in other_cogs:
                        offenders.append(f"{path.relative_to(PACKAGE_DIR)}: {name}")

        self.assertEqual(offenders, [])

    def test_info_json_declares_no_dependencies(self) -> None:
        info = json.loads((PACKAGE_DIR / "info.json").read_text(encoding="utf-8"))

        self.assertEqual(info["requirements"], [])
        self.assertEqual(info["required_cogs"], {})

    def test_domain_has_no_framework_imports(self) -> None:
        for path in (PACKAGE_DIR / "domain").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = {node.module.split(".")[0]}
                else:
                    continue
                self.assertFalse(roots & {"redbot", "discord"}, path.name)
