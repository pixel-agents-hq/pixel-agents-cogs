"""Repository boundaries required by the CCTV extraction."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from architect.infrastructure.settings_repository import (
    CONFIG_IDENTIFIER as ARCHITECT_CONFIG,
)
from cctv.infrastructure.settings import CONFIG_IDENTIFIER as CCTV_CONFIG
from corridor.infrastructure.office_state_repository import (
    CONFIG_IDENTIFIER as OFFICE_STATE_CONFIG,
)
from floorplan.infrastructure.settings import CONFIG_IDENTIFIER as FLOORPLAN_CONFIG

REPO_ROOT = Path(__file__).parents[2]
WRITERS = ("floorplan", "architect", "painter")
OLD_CONFIG_IDENTIFIERS = {
    8_364_586_608,  # former Floorplan combined settings/state
    4_172_636_869_746_374,  # former Architect settings/state
    6_850_347_610_142_909_695,  # former Pixelagents editor layout
}


def _production_modules(package: str) -> list[Path]:
    root = REPO_ROOT / package
    return [
        path
        for path in root.rglob("*.py")
        if "tests" not in path.relative_to(root).parts and path.name != "conftest.py"
    ]


def test_state_writers_do_not_depend_on_cctv() -> None:
    offenders: list[tuple[Path, str]] = []
    for package in WRITERS:
        info = json.loads((REPO_ROOT / package / "info.json").read_text(encoding="utf-8"))
        assert "cctv" not in info.get("required_cogs", {})
        for path in _production_modules(package):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                roots: list[str] = []
                if isinstance(node, ast.Import):
                    roots = [alias.name.partition(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    roots = [node.module.partition(".")[0]]
                if "cctv" in roots:
                    offenders.append((path, package))
    assert offenders == []


def test_old_browser_routes_exist_only_as_documented_absences() -> None:
    old_routes = ("/third-party/floorplan", "/third-party/architect", "/architect/ws")
    offenders = [
        (path, route)
        for package in WRITERS
        for path in _production_modules(package)
        for route in old_routes
        if route in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_old_browser_commands_are_absent_from_floorplan_and_architect() -> None:
    floorplan = "\n".join(
        path.read_text(encoding="utf-8") for path in _production_modules("floorplan")
    )
    architect = "\n".join(
        path.read_text(encoding="utf-8") for path in _production_modules("architect")
    )

    for command in (
        'command(name="settings")',
        'command(name="wsport")',
        'command(name="toolcleardelay")',
        'command(name="richpresence")',
        'command(name="messages")',
        'command(name="enable")',
        'command(name="disable")',
        'command(name="includebots")',
        'command(name="sync")',
        'command(name="despawnall")',
    ):
        assert command not in floorplan
    assert 'group(name="ws")' not in architect


def test_new_config_identities_are_distinct_and_do_not_reuse_legacy_ids() -> None:
    identifiers = {OFFICE_STATE_CONFIG, CCTV_CONFIG, FLOORPLAN_CONFIG, ARCHITECT_CONFIG}
    assert len(identifiers) == 4
    assert identifiers.isdisjoint(OLD_CONFIG_IDENTIFIERS)
