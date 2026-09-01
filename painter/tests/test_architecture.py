"""Painter's editor-state ownership boundaries."""

from __future__ import annotations

from pathlib import Path

from ..adapters.cog_base import CogBase

PACKAGE_ROOT = Path(__file__).parents[1]


def test_painter_has_no_architect_refresh_hook() -> None:
    assert not hasattr(CogBase, "_notify_architect_layout_changed")
    source = (PACKAGE_ROOT / "application" / "painter_layout_service.py").read_text(
        encoding="utf-8"
    )
    assert "on_layout_changed" not in source
    assert "notify_shared_layout_changed" not in source


def test_editor_repository_uses_pixelagents_facade() -> None:
    source = (PACKAGE_ROOT / "infrastructure" / "office_layout_repository.py").read_text(
        encoding="utf-8"
    )
    assert "OfficeStateKind.EDITOR" in source
    assert ".office_state(" in source
    assert ".set_office_layout(" in source
    assert "Config.get_conf(" not in source
