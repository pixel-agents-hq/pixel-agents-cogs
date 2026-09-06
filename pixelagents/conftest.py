"""Install stubs before any pixelagents module is imported.

Delegates to corridor's shared stub installer (corridor/testing.py) for
discord/redbot.core instead of rolling a separate one here -- multiple
packages each stubbing sys.modules independently is a real conflict
(whichever conftest.py imports last silently wins for the whole pytest
session), and every generated cog already depends on corridor via
required_cogs. The one thing pixelagents needs beyond the shared stub is
below: `cog_data_path` pre-seeded with a fake `webview_dist` already
matching the packaged vendor pin, so constructing a cog in a test never
triggers a real clone+build -- see infrastructure/webview_build.py's
`.built_commit` marker convention.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from corridor.testing import install_stubs

install_stubs()

import redbot.core.data_manager as _data_manager  # noqa: E402

_FAKE_DATA_ROOT = Path(tempfile.mkdtemp(prefix="pixelagents-test-data-"))
_PIN_COMMIT = (
    (Path(__file__).parent / "infrastructure" / "webview_vendor.commit")
    .read_text(encoding="utf-8")
    .strip()
)


def _fake_cog_data_path(cog_instance: object) -> Path:
    path = _FAKE_DATA_ROOT / type(cog_instance).__name__
    if not path.exists():
        path.mkdir(parents=True)
        webview_dist = path / "webview_dist"
        webview_dist.mkdir()
        (webview_dist / "index.html").write_text("<html><head></head><body></body></html>")
        (webview_dist / ".built_commit").write_text(_PIN_COMMIT + "\n")
    return path


_data_manager.cog_data_path = _fake_cog_data_path
