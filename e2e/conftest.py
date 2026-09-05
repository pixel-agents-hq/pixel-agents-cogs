"""Install stubs before any e2e module is imported.

Delegates to corridor's shared stub installer for `discord`/`redbot.core`,
same as every cog -- but unlike a single cog's own conftest.py, installs
*nothing else* on top: no aiohttp faking, no pre-seeded fake webview_dist.
This suite's entire point is exercising the real cross-cog wiring (a real
built webview, a real aiohttp listener a real Playwright browser connects
to, real Config-mediated state shared across five real cog instances in
one process), so every one of those has to be the genuine article.
"""

from __future__ import annotations

from corridor.testing import install_stubs

install_stubs()
