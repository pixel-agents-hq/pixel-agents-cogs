#!/usr/bin/env python3
"""Fail if PixelAgents calls a Pixel Index endpoint that isn't registered
in contracts/pixel_index/endpoints.py.

Every JSON endpoint PixelAgents hits goes through the single
`self._pixel_index_get(path)` chokepoint, so this walks the package's AST for
those call sites, extracts the literal path (including f-string
templates like f"/api/v1/layouts/{slug}"), and diffs against ENDPOINTS.
`/health` is checked directly by `_check_pixel_index_health` instead of
`_pixel_index_get` (it's a plain status check, no JSON body), so it's
special-cased below rather than generalizing the walk for a call site that's
unlikely to grow siblings.

This catches "a new endpoint was added but nobody registered it" — it does
NOT catch "an existing endpoint's response now has a field we read but
never modeled." That's a separate, harder static-analysis problem; see
contracts/pixel_index/mypy.ini and docs/contract-testing.md for how that one
is handled instead (via pixelagents/models.py + mypy).

Run: python -m contracts.pixel_index.lint_endpoints
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from contracts.pixel_index.endpoints import ENDPOINTS

PIXELAGENTS_PACKAGE = Path(__file__).resolve().parents[2] / "pixelagents"

_HAND_REGISTERED_PATHS = {"/health"}


def _shape(path: str) -> str:
    """Collapse '{anything}' path params to one placeholder, so a path
    template and an f-string built from a differently named variable still
    compare equal."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _template_from_fstring(node: ast.JoinedStr) -> str:
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
        else:
            parts.append("{}")
    return "".join(parts)


def find_called_paths(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_pixel_index_get"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(_shape(arg.value))
        elif isinstance(arg, ast.JoinedStr):
            found.add(_shape(_template_from_fstring(arg)))
        # A computed/variable path can't be statically resolved here — not
        # flagged either way, rather than risk a false positive.
    return found


def production_sources() -> list[Path]:
    """Return every production module, including modules added by the refactor."""
    return sorted(
        path
        for path in PIXELAGENTS_PACKAGE.rglob("*.py")
        if "tests" not in path.relative_to(PIXELAGENTS_PACKAGE).parts
        and path.name != "conftest.py"
    )


def main() -> int:
    called = set(_HAND_REGISTERED_PATHS)
    for path in production_sources():
        called.update(find_called_paths(path.read_text(encoding="utf-8")))
    registered = {_shape(ep.path) for ep in ENDPOINTS}

    missing = called - registered
    if missing:
        print(
            "Pixel Index endpoint(s) called by the pixelagents package but not "
            "registered in contracts/pixel_index/endpoints.py:"
        )
        for path in sorted(missing):
            print(f"  - {path}")
        print("\nAdd an EndpointContract entry for each in contracts/pixel_index/endpoints.py.")
        return 1

    print("All Pixel Index endpoints called by the pixelagents package are registered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
