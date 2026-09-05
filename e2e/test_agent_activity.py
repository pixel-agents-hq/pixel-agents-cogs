"""Real corridor pub/sub -> cctv discord/editor rendering, observed by a
real Playwright browser.

Every boundary except the LLM API and the Discord gateway is real: real
corridor, pixelagents, and cctv cogs, `cog_load()`-ed together in one
process (see `e2e/fixtures.py::construct_core_cogs`); events are published
directly via `corridor.publish_event(...)` -- the same public API
`testbench` uses to manually publish events for testing -- rather than
through a full Discord message or LLM tool loop, since this suite is about
proving corridor's pub/sub -> cctv's rendering, not re-deriving
`test_live_office.py`'s already-covered "a real tool call happened" story.
No `Architect`/`Painter` cog is constructed here at all.

cctv's discord/editor routing (`cctv/adapters/cog_base.py::_event_targets`)
is per-agent-identity, not per-event-type: a genuine agent (an `AgentRef`
with `agent_key` set, no Discord ids) always reaches *both* pipelines for
any event type; only a real Discord member identity can be
discord-pipeline-exclusive. So:
- the "message" scenario uses a real Discord member and asserts discord-only
  delivery (the case where exclusivity is actually achievable);
- the "tool call" scenario uses a genuine agent and asserts delivery to
  *both* pipelines explicitly, rather than attempting (impossible)
  editor-exclusivity.

Gated identically to `test_live_office.py`: set
`PIXELAGENTS_REAL_WEBVIEW_BUILD=1` to run it (cctv's own `cog_load()` still
needs a real built webview to become ready and serve sprite assets, even
though nothing here paints anything).
"""

from __future__ import annotations

import unittest

from corridor.domain import AgentPresenceChanged, AgentRef, AgentReplied, AgentToolStarted

from .fixtures import (
    FakeBot,
    capture_websocket_frames,
    construct_core_cogs,
    real_webview_build_enabled,
    start_frontend_app,
    wait_for_frame,
)

# A genuine, non-Discord agent identity -- defined locally rather than
# imported from architect.adapters.cog_base.ARCHITECT_AGENT_REF, which would
# pull in architect's entire application/infrastructure/tools/a2a stack just
# for one constant, undercutting this file's "doesn't need Architect at all"
# point.
_GENUINE_AGENT_REF = AgentRef(
    discord_user_id=None, guild_id=None, is_bot=True, agent_key="e2e-genuine-agent"
)

_DISCORD_MEMBER_GUILD_ID = 999222
_DISCORD_MEMBER_REF = AgentRef(
    discord_user_id=555111, guild_id=_DISCORD_MEMBER_GUILD_ID, is_bot=False, agent_key=None
)


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+npm+vite build of the vendored webview; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestAgentActivityReachesCctv(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()

        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.corridor = cogs.corridor
        self.pixelagents = cogs.pixelagents
        self.cctv = cogs.cctv

        self._port = await start_frontend_app(self.cctv, add_async_cleanup=self.addAsyncCleanup)

    async def test_agent_replied_reaches_only_the_discord_pipeline(self) -> None:
        # GUILD_DEFAULTS["enabled"] is False (cctv/infrastructure/settings.py)
        # and _on_agent_presence_changed's real-Discord branch gates
        # discord_pipeline.reconcile_discord(...) on it -- without this, the
        # member below is never tracked and every event drops silently.
        await self.cctv._settings.set_guild_enabled(_DISCORD_MEMBER_GUILD_ID, True)  # noqa: SLF001

        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            discord_page = await browser.new_page()
            editor_page = await browser.new_page()
            discord_frames = capture_websocket_frames(discord_page)
            editor_frames = capture_websocket_frames(editor_page)

            await discord_page.goto(f"http://127.0.0.1:{self._port}/e2e/page/discord")
            await editor_page.goto(f"http://127.0.0.1:{self._port}/e2e/page/editor")
            # Give each page's own JS time to open its (shimmed) WebSocket
            # before publishing anything, so neither broadcast is sent to
            # zero connected clients.
            await discord_page.wait_for_timeout(500)
            await editor_page.wait_for_timeout(500)
            discord_frames.clear()
            editor_frames.clear()

            # No sleep needed between these two publishes: EventBusService.
            # publish() awaits every subscriber synchronously (no detached
            # task), so by the time the first publish_event(...) returns,
            # _on_agent_presence_changed has already run OfficeService.
            # reconcile() -> spawn(), and the member is tracked.
            await self.corridor.publish_event(
                AgentPresenceChanged(agent=_DISCORD_MEMBER_REF, display_name="Ada", status="online")
            )
            await self.corridor.publish_event(
                AgentReplied(agent=_DISCORD_MEMBER_REF, summary="a genuinely distinctive summary")
            )

            # broadcast_messages defaults to True (cctv/infrastructure/
            # settings.py) -- if that default ever changes, this assertion
            # (not the publish above) is where it would start failing.
            frame = await wait_for_frame(
                discord_page,
                discord_frames,
                lambda f: (
                    f.get("type") == "agentToolStart"
                    and f.get("toolName") == "Message"
                    and "a genuinely distinctive summary" in str(f.get("status", ""))
                ),
            )
            self.assertIsNotNone(
                frame, "discord pipeline never received the AgentReplied broadcast"
            )

            # A genuine negative assertion has to burn the full poll budget
            # to mean anything -- accept the added wall-clock cost here.
            leaked = await wait_for_frame(
                editor_page,
                editor_frames,
                lambda f: f.get("type") == "agentToolStart" and f.get("toolName") == "Message",
            )
            self.assertIsNone(
                leaked, "editor pipeline unexpectedly received a real Discord member's activity"
            )

    async def test_tool_started_reaches_both_pipelines(self) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            discord_page = await browser.new_page()
            editor_page = await browser.new_page()
            discord_frames = capture_websocket_frames(discord_page)
            editor_frames = capture_websocket_frames(editor_page)

            await discord_page.goto(f"http://127.0.0.1:{self._port}/e2e/page/discord")
            await editor_page.goto(f"http://127.0.0.1:{self._port}/e2e/page/editor")
            await discord_page.wait_for_timeout(500)
            await editor_page.wait_for_timeout(500)
            discord_frames.clear()
            editor_frames.clear()

            # A genuine agent identity always targets both pipelines
            # (_event_targets), unconditionally -- no presence event needed
            # first, unlike the real-Discord-member scenario above.
            await self.corridor.publish_event(
                AgentToolStarted(
                    agent=_GENUINE_AGENT_REF,
                    tool_id="test-tool-1",
                    status="painting",
                    tool_name="paint_tiles",
                )
            )

            def _matches(frame: dict[str, object]) -> bool:
                return (
                    frame.get("type") == "agentToolStart"
                    and frame.get("toolId") == "test-tool-1"
                    and frame.get("toolName") == "paint_tiles"
                    and frame.get("status") == "painting"
                )

            discord_frame = await wait_for_frame(discord_page, discord_frames, _matches)
            editor_frame = await wait_for_frame(editor_page, editor_frames, _matches)
            self.assertIsNotNone(
                discord_frame, "discord pipeline never received the genuine agent's tool-start"
            )
            self.assertIsNotNone(
                editor_frame, "editor pipeline never received the genuine agent's tool-start"
            )
            assert discord_frame is not None and editor_frame is not None
            self.assertEqual(discord_frame["id"], editor_frame["id"])


if __name__ == "__main__":
    unittest.main()
