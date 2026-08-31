"""Focused tests for floorplan's (now minimal) application layer.

Discord presence snapshotting (member_snapshot/message_snapshot),
TaskSupervisor, and the WebSocket-driven cog task lifecycle this file
used to cover all moved to `cctv` along with the dashboard/presence
mirroring they supported (docs/cctv-design.md) -- floorplan's own
application layer is down to `CatalogueService`, already covered by
`test_catalogue.py`. What's left here is the one still-meaningful
boundary check: floorplan's application layer stays framework-free.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


class TestApplicationBoundaries(unittest.TestCase):
    def test_application_modules_do_not_import_frameworks(self) -> None:
        application_root = Path(__file__).parents[1] / "application"
        banned_roots = {"aiohttp", "discord", "redbot"}

        for path in application_root.glob("*.py"):
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
            self.assertTrue(imported_roots.isdisjoint(banned_roots), path)


if __name__ == "__main__":
    unittest.main()
