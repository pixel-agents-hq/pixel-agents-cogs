"""Install stubs before any telephonepole module is imported.

Delegates to corridor's shared stub installer (corridor/testing.py) instead
of rolling a separate one here -- multiple packages each stubbing
sys.modules independently is a real conflict (whichever conftest.py imports
last silently wins for the whole pytest session), and every generated cog
already depends on corridor via required_cogs.
"""

from __future__ import annotations

from corridor.testing import install_stubs

install_stubs()
