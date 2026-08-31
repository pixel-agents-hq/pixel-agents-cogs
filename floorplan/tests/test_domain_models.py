"""Unit tests for framework-independent Floorplan domain values.

Agent-visualization domain values (AgentKey, AgentSnapshot, PresenceStatus,
etc.) live in `pixelagents.domain`. The former settings dataclasses
(GlobalSettings/GuildSettings/SettingsSnapshot) moved to `cctv` along
with the dashboard/WebSocket settings they described (docs/cctv-design.md)
-- floorplan's own domain package is down to `SnowflakeId` and
`normalize_http_url`, neither of which needs a dedicated value test
beyond the framework-purity check below.
"""

from __future__ import annotations

import ast
from pathlib import Path


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
