"""Unit tests for framework-independent agent-visualization domain values."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    PresenceStatus,
)


def test_agent_snapshots_are_immutable_and_normalized() -> None:
    key = AgentKey(guild_id=10, user_id=20)
    activity = ActivitySnapshot(
        kind=ActivityKind.LISTENING,
        name="Spotify",
        title="Track",
        artist="Artist",
    )
    snapshot = AgentSnapshot(
        key=key,
        display_name="Tin",
        status=PresenceStatus.IDLE,
        is_bot=False,
        activities=(activity,),
    )

    assert snapshot.activities == (activity,)
    assert snapshot.status.value == "idle"
    with pytest.raises(FrozenInstanceError):
        snapshot.display_name = "Changed"  # type: ignore[misc]


def test_domain_office_module_has_no_framework_or_validation_imports() -> None:
    domain_root = Path(__file__).parents[1] / "domain"
    banned_roots = {"aiohttp", "discord", "pydantic", "redbot"}

    tree = ast.parse((domain_root / "office.py").read_text(encoding="utf-8"))
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
    assert imported_roots.isdisjoint(banned_roots)
