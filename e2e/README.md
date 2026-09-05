# Multi-cog end-to-end tests

Not a cog: `info.json` declares `"type": "SHARED_LIBRARY"` (same pattern
as `contracts/`, see its own README) specifically so Red's Downloader
excludes it from cog discovery, and it's not collected by any per-cog
pytest invocation either. This package loads corridor, pixelagents,
architect, and cctv as real, `cog_load()`-ed instances in one process
against a real built Pixel Agents webview, drives architect through a
scripted LLM double, and drives a real Playwright browser against cctv's
real aiohttp listener to observe the result.

See each cog's own `docs/contract-testing.md`-style coverage and unit
tests for everything that's *already* checked with a mocked cross-cog
boundary. This suite exists for the one thing those can't cover: whether
the whole chain — a real tool call, real layout codec, real corridor
Config and pub/sub, real cctv projection, real browser — actually
cooperates end to end.

## Running it

Needs a real network clone + `npm ci` + `vite build` of the vendored
webview (typically single-digit seconds, not the "few minutes" a cold
`npm ci` might suggest, but still network-dependent), so it's gated the
same way `pixelagents/tests/test_webview_build.py::TestRealWebviewBuild`
is:

```sh
pip install playwright
python -m playwright install chromium
PIXELAGENTS_REAL_WEBVIEW_BUILD=1 python -m pytest -q e2e/
```

Without `PIXELAGENTS_REAL_WEBVIEW_BUILD=1` the whole suite is skipped
(not an error) — this keeps it out of every other cog's fast test run and
out of `python -m pytest` invocations that don't explicitly ask for it.

### Faster local iteration

Each run does a fresh clone+build into a throwaway directory by default,
matching `TestRealWebviewBuild`'s own behavior (a deliberately fresh
environment for the CI job this feeds). While iterating on the suite
itself, point it at a stable directory instead so repeat runs skip
rebuilding (`ensure_webview_built` is idempotent — it only rebuilds when
the pinned commit changes):

```sh
mkdir -p /tmp/e2e-webview-cache
PIXELAGENTS_REAL_WEBVIEW_BUILD=1 \
PIXELAGENTS_E2E_WEBVIEW_CACHE=/tmp/e2e-webview-cache \
python -m pytest -q e2e/
```

## Why this needs its own conftest.py

Every cog's own `conftest.py` layers something on top of corridor's
shared `discord`/`redbot.core` stub install — pixelagents fakes `aiohttp`
entirely and pre-seeds a fake `webview_dist`; this suite wants neither,
since its whole point is exercising the real build and a real listener a
real browser connects to. `e2e/conftest.py` installs only the bare shared
stub.

## Why only architect, not painter

One architect-driven scenario (`paint_tiles`) is enough to prove the
cross-cog loop cooperates — the mechanism a painter-driven scenario would
exercise (`OfficeLayoutRepository` load/decode/mutate/encode/save,
corridor `Config` write, `OfficeStateChanged`, cctv's pipeline, the real
WebSocket broadcast) is identical to architect's; painter would only add
a second, near-duplicate test of the same wiring. If painter's own tools
ever diverge from that shared path, extend this suite with a second
scenario then.

## Adding a scenario

1. Construct any additional real cog the scenario needs the same way
   `asyncSetUp` already does for corridor/pixelagents/architect/cctv:
   `Cog(bot)`, `await cog.cog_load()`, `bot.add_cog(cog)`.
2. Swap the driving cog's `_tool_loop_service` for one backed by
   `ScriptedLLM` (`e2e/fixtures.py`), script a `tool_call_response(...)`
   sequence ending in `final_response()`, then call
   `.run(tools=cog._tools, ...)` directly — this bypasses the A2A
   executor layer (real network listeners, `RequestContext`/`EventQueue`
   plumbing) without skipping any of the real tool/service/repository
   code that layer would otherwise call.
3. Observe the result either by reading real cog state back directly
   (e.g. `await pixelagents.office_state(OfficeStateKind.EDITOR)`) or, for
   anything that should reach a browser, by capturing WebSocket frames via
   Playwright's `page.on("websocket", ...)` the way
   `test_live_office.py` does.
