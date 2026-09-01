"""Architecture constraints for the Pixel Agents bundle and state boundary."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from redbot.core import commands

import pixelagents
from pixelagents.adapters.cog_base import PixelAgentsBase
from pixelagents.pixelagents import PixelAgents
from pixelagents.pixelagents import pixelagents as lowercase_cog

PACKAGE_ROOT = Path(__file__).parents[1]


def production_modules() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(PACKAGE_ROOT).parts and path.name != "conftest.py"
    )


def test_composition_entrypoint_is_genuinely_thin() -> None:
    lines = (PACKAGE_ROOT / "pixelagents.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 200


def test_framework_resources_have_one_owner_each() -> None:
    """Pixelagents has one Config identity; Corridor owns office state."""

    sources = {path: path.read_text(encoding="utf-8") for path in production_modules()}
    config_factories = [path for path, source in sources.items() if "Config.get_conf(" in source]

    assert config_factories == [PACKAGE_ROOT / "infrastructure" / "settings.py"]


def test_pascalcase_and_lowercase_public_classes_are_identical() -> None:
    assert PixelAgents is lowercase_cog
    assert pixelagents.__all__ == ["pixelagents"]
    assert issubclass(PixelAgents, commands.Cog)
    assert PixelAgents.__init__ is PixelAgentsBase.__init__


def test_command_root_is_inherited_once() -> None:
    root_owners = [base for base in PixelAgents.__mro__ if "pixelagents_group" in base.__dict__]
    assert len(root_owners) == 1


def test_production_config_access_does_not_bypass_repository() -> None:
    offenders: list[Path] = []
    for path in production_modules():
        if path == PACKAGE_ROOT / "infrastructure" / "settings.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "config":
                offenders.append(path)
                break
    assert offenders == []


def test_agent_visualization_modules_do_not_import_frameworks() -> None:
    """domain/office.py, application/office.py, application/presence.py, and
    contracts/outbound.py are consumed directly by every cog that drives the
    pixel-agents webview (floorplan today, more later) -- they must stay free
    of any one consumer's framework (Discord, Red, aiohttp, pydantic)."""

    banned_roots = {"aiohttp", "discord", "pydantic", "redbot"}
    targets = (
        PACKAGE_ROOT / "domain" / "office.py",
        PACKAGE_ROOT / "application" / "office.py",
        PACKAGE_ROOT / "application" / "presence.py",
        PACKAGE_ROOT / "contracts" / "outbound.py",
    )

    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert imported_roots.isdisjoint(banned_roots), path


def test_no_leftover_runtime_dependency_on_aiohttp() -> None:
    """HTTP/WebSocket ownership lives in cctv, never in pixelagents."""

    offenders: list[tuple[Path, str]] = []
    for path in production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module.split(".")[0]]
            for module in modules:
                if module == "aiohttp":
                    offenders.append((path, module))
    assert offenders == []


def test_pydantic_is_confined_to_the_office_schema_boundary() -> None:
    importers: list[Path] = []
    for path in production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.partition(".")[0] == "pydantic"
            for node in ast.walk(tree)
        ):
            importers.append(path)

    assert importers == [
        PACKAGE_ROOT / "application" / "office_state.py",
        PACKAGE_ROOT / "contracts" / "layout.py",
    ]


def test_importing_the_package_does_not_cache_the_corridor_coupled_cog_module() -> None:
    """Regression test for a real production incident: `ensure_importable`
    (corridor/dependency_loader.py) does `import pixelagents` so a dependent
    can reach its domain/application/contracts submodules without loading it
    as a Cog. If merely importing the top-level package also pulled in
    `pixelagents.pixelagents` (and so `adapters/replies.py`, which needs
    corridor already loaded), that submodule would get cached in
    `sys.modules`. Red's `_load` always calls `_cleanup_and_refresh_modules`
    before `setup()`, which unconditionally re-execs every already-cached
    `pixelagents.*` submodule -- bypassing this package's own lazy
    `__getattr__` resolution entirely -- so a later `[p]load pixelagents`,
    at a moment corridor isn't currently loaded, crashed with
    `ModuleNotFoundError: No module named 'corridor'` before `setup()` ever
    got a chance to load corridor. Run in a subprocess: this has to observe
    a genuinely fresh `sys.modules`, which the running test session's own
    imports have already populated.
    """

    script = (
        "import sys; "
        "import pixelagents.tests.conftest; "
        "import pixelagents; "
        "assert 'pixelagents.pixelagents' not in sys.modules, sorted(sys.modules); "
        "assert 'pixelagents.adapters.replies' not in sys.modules, sorted(sys.modules); "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PACKAGE_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
