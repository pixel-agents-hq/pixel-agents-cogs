# Multi-cog end-to-end tests

Not a cog: `info.json` declares `"type": "SHARED_LIBRARY"` (same pattern
as `contracts/`, see its own README) specifically so Red's Downloader
excludes it from cog discovery, and it's not collected by any per-cog
pytest invocation either. This package loads corridor, pixelagents,
architect, and cctv as real, `cog_load()`-ed instances in one process
against a real built Pixel Agents webview, drives architect through a
scripted LLM double, publishes real corridor pub/sub events directly, and
drives a real Playwright browser against cctv's real aiohttp listener to
observe the result.

See each cog's own `docs/contract-testing.md`-style coverage and unit
tests for everything that's *already* checked with a mocked cross-cog
boundary. This suite exists for the one thing those can't cover: whether
the whole chain — a real tool call, real layout codec, real corridor
Config and pub/sub, real cctv projection, real browser — actually
cooperates end to end.

- `test_live_office.py` — a real `paint_tiles` architect tool call reaches
  a real browser over a real WebSocket broadcast.
- `test_agent_activity.py` — corridor's agent-activity pub/sub
  (`AgentReplied`, `AgentToolStarted`, `AgentPresenceChanged`) reaches
  cctv's real discord/editor pipelines, published directly via
  `corridor.publish_event(...)` (no `Architect`/`Painter` cog needed).

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
cross-cog loop cooperates — the mechanism a painter-driven *mutation*
scenario (`recolor_tiles` and friends) would exercise
(`OfficeLayoutRepository` load/decode/mutate/encode/save, corridor
`Config` write, `OfficeStateChanged`, cctv's pipeline, the real WebSocket
broadcast) is identical to architect's `paint_tiles`; painter would only
add a second, near-duplicate test of *that* wiring.

This does **not** cover painter's `consult_architect` tool
(`painter/tools/consult_architect_tool.py`), which goes through
`painter/infrastructure/architect_client.py`'s real `a2a-sdk` client over
`httpx` to corridor's real A2A listener — a genuinely different mechanism
from anything this suite currently exercises. Corridor's own A2A listener
is already running for real in every scenario here (`construct_core_cogs`
calls `corridor.cog_load()`), but nothing sends it a real request; the
real A2A request/response path is currently untested end-to-end. If
painter's own *mutation* tools ever diverge from architect's shared path,
extend this suite with a second scenario for those specifically.

## Adding a scenario

1. Call `construct_core_cogs(bot, add_cleanup=self.addCleanup,
   add_async_cleanup=self.addAsyncCleanup)` (`e2e/fixtures.py`) — it
   constructs and `cog_load()`s real `Corridor`, `PixelAgents` (a real
   clone+npm+vite build, pinned via a `cog_data_path` override rather than
   a pre-built shortcut, with an assertion that the built commit matches
   `webview_build.pinned_commit()`), and `CCTV`, registering
   `cog_unload()`/cleanup for each. Only construct additional cogs (e.g.
   `Architect`) manually, the same way — `Cog(bot)`, `await
   cog.cog_load()`, `bot.add_cog(cog)` — if the scenario needs them beyond
   corridor/pixelagents/cctv.
2. To drive a real tool call: swap the driving cog's `_tool_loop_service`
   for one backed by `ScriptedLLM` (`e2e/fixtures.py`), script a
   `tool_call_response(...)` sequence ending in `final_response()`, then
   call `.run(tools=cog._tools, ...)` directly — this bypasses the A2A
   executor layer (real network listeners, `RequestContext`/`EventQueue`
   plumbing) without skipping any of the real tool/service/repository
   code that layer would otherwise call. To exercise corridor's pub/sub
   directly instead (no tool call at all): `await
   corridor.publish_event(SomeEvent(...))`, the same public API
   `testbench` uses to manually publish events for testing — see
   `test_agent_activity.py`.
3. Call `start_frontend_app(cctv, add_async_cleanup=...)` (`e2e/fixtures.py`)
   once per test to get a real port serving both `/e2e/page/discord` and
   `/e2e/page/editor` plus both pages' real WebSocket routes. Observe the
   result either by reading real cog state back directly (e.g. `await
   pixelagents.office_state(OfficeStateKind.EDITOR)`) or, for anything
   that should reach a browser, by capturing WebSocket frames with
   `capture_websocket_frames(page)` and polling for one with
   `wait_for_frame(page, frames, predicate)` (`e2e/fixtures.py`) — a
   `None` result after the full poll budget is also how you assert an
   event did *not* reach a given page (see `test_agent_activity.py`'s
   discord-only scenario).
