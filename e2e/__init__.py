"""Multi-cog end-to-end tests.

Not a cog (no info.json, not installable): this package loads corridor,
pixelagents, architect, painter, and cctv as real, `cog_load()`-ed
instances in one process against a real built Pixel Agents webview, drives
architect/painter through a scripted LLM double, and drives a real
Playwright browser against cctv's real aiohttp listener to observe the
result -- the thing docs/contract-testing.md's per-cog contract checks and
each cog's own mocked-boundary unit tests don't cover: whether the whole
chain actually cooperates. See e2e/README.md for how to run it and why it
needs its own conftest instead of any single cog's.
"""
