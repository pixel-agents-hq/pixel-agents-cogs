"""Tests for Floorplan's framework-free endpoint value."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from floorplan.domain import normalize_http_url


def test_normalize_http_url_trims_and_removes_trailing_slashes() -> None:
    assert normalize_http_url(" https://index.example.test/path/// ") == (
        "https://index.example.test/path"
    )


@pytest.mark.parametrize("value", ["", "index.example.test", "ftp://index.example.test"])
def test_normalize_http_url_rejects_non_http_urls(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_http_url(value)


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
