"""Painter's editor-state ownership boundaries."""

from __future__ import annotations

from pathlib import Path

from ..adapters.cog_base import CogBase
from ..painter import Painter

PACKAGE_ROOT = Path(__file__).parents[1]


def test_painter_has_no_architect_refresh_hook() -> None:
    assert not hasattr(CogBase, "_notify_architect_layout_changed")
    source = (PACKAGE_ROOT / "application" / "painter_layout_service.py").read_text(
        encoding="utf-8"
    )
    assert "on_layout_changed" not in source
    assert "notify_shared_layout_changed" not in source


def test_editor_repository_reexports_pixelagents_shared_repository() -> None:
    """Painter's own copy is a thin re-export shim -- the real load/save
    logic (and its own `Config.get_conf`-free guarantee) lives once in
    pixelagents, shared with architect. See that module's own docstring."""

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
    """`register_agent`'s `owner=` must equal `Painter.__name__` -- that's
    what `CogBase.on_cog_remove`'s crash-safety fallback keys off via
    `cog.qualified_name` (see `corridor/domain/agent_directory.py`'s
    `register()` docstring: "the registering cog's class name"). A
    lowercase `owner="painter"` here was a real bug: if `cog_unload()`
    ever raised before reaching its own `unregister_agent_owner` call,
    `on_cog_remove`'s fallback would find nothing registered under
    `"Painter"` and silently leave this agent a permanent "ghost" --
    registered, but never told offline."""

    source = (PACKAGE_ROOT / "adapters" / "cog_base.py").read_text(encoding="utf-8")
    assert f'owner="{Painter.__name__}"' in source
