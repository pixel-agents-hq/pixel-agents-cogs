"""Install stubs before any corridor module is imported. See testing.py for
why this lives in one shared place instead of being redefined per-package."""

from __future__ import annotations

from .testing import install_stubs

install_stubs()
