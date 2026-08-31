"""Architecture constraints for the Floorplan Cog -- now Pixel Index
browsing/catalogue loading only; dashboard/WebSocket hosting and Discord
presence mirroring moved to `cctv` (docs/cctv-design.md)."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from redbot.core import commands

from floorplan.floorplan import Floorplan

PACKAGE_ROOT = Path(__file__).parents[1]
COMPOSED_ADAPTERS = (
    "admin_commands.py",
    "catalogue_commands.py",
    "cog_base.py",
    "replies.py",
)


def production_modules() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(PACKAGE_ROOT).parts and path.name != "conftest.py"
    )


def test_composition_entrypoint_is_genuinely_thin() -> None:
    lines = (PACKAGE_ROOT / "floorplan.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 200


def test_split_did_not_create_a_replacement_adapter_monolith() -> None:
    adapter_root = PACKAGE_ROOT / "adapters"
    counts = {
        name: len((adapter_root / name).read_text(encoding="utf-8").splitlines())
        for name in COMPOSED_ADAPTERS
    }
    assert max(counts.values()) <= 260, counts


def test_framework_resources_have_one_owner() -> None:
    sources = {path: path.read_text(encoding="utf-8") for path in production_modules()}
    config_factories = [path for path, source in sources.items() if "Config.get_conf(" in source]
    session_factories = [
        path for path, source in sources.items() if "aiohttp.ClientSession" in source
    ]
    # No asyncio.create_task factory anywhere in floorplan anymore --
    # TaskSupervisor (and everything that scheduled background tasks
    # through it) moved to cctv along with the WebSocket surface it
    # supported.
    task_factories = [path for path, source in sources.items() if "asyncio.create_task(" in source]

    assert config_factories == [PACKAGE_ROOT / "infrastructure" / "settings.py"]
    assert session_factories == [PACKAGE_ROOT / "infrastructure" / "pixel_index.py"]
    assert task_factories == []


def test_cog_class_is_the_sole_public_export() -> None:
    import floorplan

    assert floorplan.__all__ == ["Floorplan"]
    assert floorplan.Floorplan is Floorplan
    assert issubclass(Floorplan, commands.Cog)


def test_discord_cogmeta_reverse_mro_scan_finds_no_listeners() -> None:
    """Mirror discord.py 2.7 CogMeta's reversed-MRO listener discovery.

    floorplan no longer defines any `@commands.Cog.listener()` at all --
    `on_dashboard_cog_add` moved to `cctv` along with the Dashboard route
    registration it served; presence gateway listeners already lived in
    corridor, not floorplan, before this refactor."""

    discovered: dict[str, object] = {}
    for base in reversed(Floorplan.__mro__):
        for name, value in base.__dict__.items():
            discovered.pop(name, None)
            if getattr(value, "__cog_listener__", False):
                discovered[name] = value

    listener_names = [
        listener_name
        for value in discovered.values()
        for listener_name in value.__cog_listener_names__
    ]
    assert listener_names == []
    assert all(count == 1 for count in Counter(listener_names).values())


def test_command_root_is_inherited_once() -> None:
    root_owners = [base for base in Floorplan.__mro__ if "floorplan_group" in base.__dict__]
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


def test_application_layer_does_not_import_infrastructure_or_adapters() -> None:
    offenders: list[tuple[Path, str]] = []
    for path in (PACKAGE_ROOT / "application").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 2 or node.module is None:
                continue
            dependency = node.module.split(".", 1)[0]
            if dependency in {"infrastructure", "adapters"}:
                offenders.append((path, dependency))
    assert offenders == []
