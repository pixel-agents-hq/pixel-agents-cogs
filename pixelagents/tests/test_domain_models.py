"""Unit tests for framework-independent Pixel Agents domain values."""

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
    GlobalSettings,
    GuildSettings,
    PresenceStatus,
    SeatAssignment,
    SettingsSnapshot,
    TrackedAgent,
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
    tracked = TrackedAgent(key=key, display_name="Tin", status=PresenceStatus.IDLE)

    assert snapshot.activities == (activity,)
    assert snapshot.status.value == "idle"
    assert tracked.key == key
    with pytest.raises(FrozenInstanceError):
        snapshot.display_name = "Changed"  # type: ignore[misc]


def test_seat_assignment_represents_partial_persisted_metadata() -> None:
    assignment = SeatAssignment(agent_id=-42, palette=3, seat_id="chair:1")

    assert assignment.agent_id == -42
    assert assignment.palette == 3
    assert assignment.hue_shift is None
    assert assignment.seat_id == "chair:1"


def test_settings_snapshot_finds_a_guild_without_mutable_mappings() -> None:
    global_settings = GlobalSettings(
        ws_host="0.0.0.0",
        ws_port=3210,
        message_tool_clear_delay=2.0,
        editor_role_id=None,
        broadcast_rich_presence=True,
        broadcast_messages=True,
        pixel_index_api_url="https://api.example.test",
        pixel_index_web_url="https://example.test",
    )
    first = GuildSettings(guild_id=1, enabled=True, include_bots=False)
    second = GuildSettings(guild_id=2, enabled=False, include_bots=True)
    snapshot = SettingsSnapshot(global_settings=global_settings, guilds=(first, second))

    assert snapshot.for_guild(1) is first
    assert snapshot.for_guild(2) is second
    assert snapshot.for_guild(3) is None


def test_domain_package_has_no_framework_or_validation_imports() -> None:
    domain_root = Path(__file__).parents[1] / "domain"
    banned_roots = {"aiohttp", "discord", "pydantic", "redbot"}

    for path in domain_root.glob("*.py"):
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
