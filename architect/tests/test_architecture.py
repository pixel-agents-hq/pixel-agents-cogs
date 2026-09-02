"""Architect's post-extraction ownership boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from ..architect import Architect
from ..infrastructure.settings_repository import GLOBAL_DEFAULTS

PACKAGE_ROOT = Path(__file__).parents[1]


def production_modules() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(PACKAGE_ROOT).parts and path.name != "conftest.py"
    )


def test_browser_and_presence_modules_are_removed() -> None:
    removed = (
        PACKAGE_ROOT / "adapters" / "dashboard.py",
        PACKAGE_ROOT / "adapters" / "office_gateway.py",
        PACKAGE_ROOT / "adapters" / "presence_subscription.py",
        PACKAGE_ROOT / "infrastructure" / "client_hub.py",
        PACKAGE_ROOT / "infrastructure" / "seat_repository.py",
        PACKAGE_ROOT / "infrastructure" / "websocket.py",
        PACKAGE_ROOT / "infrastructure" / "webview.py",
    )
    assert all(not path.exists() for path in removed)


def test_cog_has_no_browser_commands_routes_or_listeners() -> None:
    for name in (
        "ws_group",
        "ws_host",
        "ws_port",
        "dashboard_webview",
        "dashboard_session",
        "dashboard_static",
        "notify_shared_layout_changed",
    ):
        assert not hasattr(Architect, name)
    listeners = [
        value
        for base in Architect.__mro__
        for value in base.__dict__.values()
        if getattr(value, "__cog_listener__", False)
    ]
    assert listeners == []


def test_settings_contain_only_prompt_tool_and_debug_fields() -> None:
    assert set(GLOBAL_DEFAULTS) == {
        "max_tool_calls",
        "system_prompt",
        "debug_logging",
    }


def test_architect_has_no_aiohttp_or_dashboard_imports() -> None:
    banned = {"aiohttp", "dashboard"}
    offenders: list[tuple[Path, str]] = []
    for path in production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name.partition(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module.partition(".")[0]]
            offenders.extend((path, module) for module in modules if module in banned)
    assert offenders == []


def test_editor_repository_reexports_pixelagents_shared_repository() -> None:
    """Architect's own copy is a thin re-export shim -- the real
    load/save logic (and its own `Config.get_conf`-free guarantee) lives
    once in pixelagents, shared with painter. See that module's own
    docstring."""

    shim_source = (PACKAGE_ROOT / "infrastructure" / "office_layout_repository.py").read_text(
        encoding="utf-8"
    )
    assert "from pixelagents.infrastructure.office_layout_repository import" in shim_source
    assert "Config.get_conf(" not in shim_source

    shared_source = (
        PACKAGE_ROOT.parent / "pixelagents" / "infrastructure" / "office_layout_repository.py"
    ).read_text(encoding="utf-8")
    assert "OfficeStateKind.EDITOR" in shared_source
    assert ".office_state(" in shared_source
    assert ".set_office_layout(" in shared_source
    assert "Config.get_conf(" not in shared_source


def test_agent_registration_owner_matches_the_cogs_qualified_name() -> None:
    """`register_agent`'s `owner=` must equal `Architect.__name__` --
    that's what `CogBase.on_cog_remove`'s crash-safety fallback keys off
    via `cog.qualified_name` (see
    `corridor/domain/agent_directory.py`'s `register()` docstring: "the
    registering cog's class name"). A lowercase `owner="architect"` here
    was a real bug: if `cog_unload()` ever raised before reaching its own
    `unregister_agent_owner` call, `on_cog_remove`'s fallback would find
    nothing registered under `"Architect"` and silently leave this agent
    a permanent "ghost" -- registered, but never told offline."""

    source = (PACKAGE_ROOT / "adapters" / "cog_base.py").read_text(encoding="utf-8")
    assert f'owner="{Architect.__name__}"' in source
