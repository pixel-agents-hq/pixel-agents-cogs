"""Multi-cog end-to-end tests.

Not a cog: `info.json` declares `"type": "SHARED_LIBRARY"` specifically so
Red's Downloader excludes it from cog discovery -- without that marker,
Red would offer it as an installable cog (its own default `type` when
`info.json` is absent). This package loads corridor, pixelagents,
architect, and cctv as real, `cog_load()`-ed instances in one process
against a real built Pixel Agents webview, drives architect through a
scripted LLM double, and drives a real Playwright browser against cctv's
real aiohttp listener to observe the result -- the thing
docs/contract-testing.md's per-cog contract checks and each cog's own
mocked-boundary unit tests don't cover: whether the whole chain actually
cooperates. See e2e/README.md for how to run it and why it needs its own
conftest instead of any single cog's.
"""


async def setup(bot: object) -> None:
    """No-op, same reasoning as contracts/__init__.py's own setup(): dev-time
    hot reload tooling infers "reloadable cog" from any top-level package
    with an `info.json`, without checking its `type`/`hidden` fields the way
    Red's real Downloader does. Without this, Red's extension loader raises
    `ClientException: extension e2e does not have a setup function` on every
    reload attempt such tooling makes after a file change anywhere under
    `e2e/`."""
