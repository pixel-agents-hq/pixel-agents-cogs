"""Unit tests for framework-independent Floorplan settings values.

Agent-visualization domain values (AgentKey, AgentSnapshot, PresenceStatus,
etc.) moved to `pixelagents.domain` -- see
`pixelagents/tests/test_domain_office.py` for their tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

from floorplan.domain import GlobalSettings, GuildSettings, SettingsSnapshot


def test_settings_snapshot_finds_a_guild_without_mutable_mappings() -> None:
    global_settings = GlobalSettings(
        ws_host="0.0.0.0",
        ws_port=3210,
        message_tool_clear_delay=2.0,
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
