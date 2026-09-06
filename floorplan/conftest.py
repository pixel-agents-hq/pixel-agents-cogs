"""Install stubs before any floorplan module is imported.

Delegates to corridor's shared stub installer (corridor/testing.py) instead
of rolling a separate one here -- multiple packages each stubbing
sys.modules independently is a real conflict (whichever conftest.py imports
last silently wins for the whole pytest session), and every generated cog
already depends on corridor via required_cogs. floorplan needs nothing
beyond the shared stub: it gets the built webview bundle's path from
pixelagents' webview_bundle_status() rather than reading `cog_data_path`
itself (see adapters/cog_base.py).
"""

from __future__ import annotations

from corridor.testing import install_stubs

install_stubs()
