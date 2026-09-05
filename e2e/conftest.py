"""Install stubs before any e2e module is imported.

Delegates to corridor's shared stub installer for `discord`/`redbot.core`,
same as every cog -- but unlike a single cog's own conftest.py, installs
*nothing else* on top: no aiohttp faking, no pre-seeded fake webview_dist.
This suite's entire point is exercising the real cross-cog wiring (a real
built webview, a real aiohttp listener a real Playwright browser connects
to, real Config-mediated state shared across several real cog instances in
one process), so every one of those has to be the genuine article.
`e2e/fixtures.py::construct_core_cogs` layers one further override on top
of the bare stub installed here -- `PixelAgents`' own `cog_data_path`,
pointed at a real (optionally cached) build directory -- so look there,
not here, if a test's webview ever resolves to an unexpected path.
"""

from __future__ import annotations

from corridor.testing import install_stubs

install_stubs()
