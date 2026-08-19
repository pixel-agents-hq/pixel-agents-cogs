"""Admin-facing surfaces for a webview build failure.

Covers the three places a missing git/node/npm on the bot's host has to be
visible: `[p]pixelagents status`'s Assets field, `[p]pixelagents webview
rebuild`'s reply, and the owner DM `cog_load` fires automatically -- see
issue #7's requirement that this degrade gracefully rather than breaking
cog_load or leaving the failure silent.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pixelagents.infrastructure import webview_build
from pixelagents.tests.test_pixelagents import _make_cog


class TestWebviewBuildSurfaces(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()
        self.cog.bot.send_to_owners = AsyncMock()

    async def test_rebuild_command_reports_missing_tools_and_updates_status(self) -> None:
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()

        with patch.object(webview_build, "missing_tools", return_value=("git", "npm")):
            await self.cog.cmd_webview_rebuild(ctx)

        messages = [call.args[0] for call in ctx.send.await_args_list]
        self.assertTrue(any("Rebuilding" in m for m in messages))
        self.assertTrue(any("git" in m and "npm" in m for m in messages))
        self.assertEqual(self.cog._webview_assets_status(), "⚠️ missing tool(s): git, npm")

    async def test_rebuild_command_reports_success(self) -> None:
        # The command always forces a rebuild (an explicit request should
        # not be a silent no-op), so this must stub the same steps
        # test_webview_build.py's orchestration tests do -- otherwise it
        # would perform a real clone+npm+vite build against the network.
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()

        with (
            patch.object(webview_build, "_checkout"),
            patch.object(webview_build, "_install_dependencies"),
            patch.object(
                webview_build,
                "_build_bundle",
                side_effect=lambda vendor_dir, log: vendor_dir / "dist" / "webview",
            ),
            patch.object(webview_build, "_emit_decoded_assets"),
            patch.object(webview_build, "_sync_dist") as sync_dist,
        ):
            await self.cog.cmd_webview_rebuild(ctx)
        sync_dist.assert_called_once()

        messages = [call.args[0] for call in ctx.send.await_args_list]
        self.assertTrue(any("✅" in m for m in messages))

    async def test_failed_build_notifies_owners_with_missing_tools(self) -> None:
        with patch.object(webview_build, "missing_tools", return_value=("git",)):
            status = await self.cog._rebuild_webview(force=True)
        self.assertIn("git", status)

        await self.cog._notify_owners_webview_build_failed()

        self.cog.bot.send_to_owners.assert_awaited_once()
        (message,), _kwargs = self.cog.bot.send_to_owners.await_args
        self.assertIn("git", message)
        self.assertIn("[p]pixelagents webview rebuild", message)

    async def test_notify_owners_is_a_noop_without_a_failed_build(self) -> None:
        await self.cog._notify_owners_webview_build_failed()
        self.cog.bot.send_to_owners.assert_not_awaited()

    async def test_notify_owners_never_raises_when_send_to_owners_fails(self) -> None:
        self.cog.bot.send_to_owners = AsyncMock(side_effect=RuntimeError("no owners"))
        with patch.object(webview_build, "missing_tools", return_value=("git",)):
            await self.cog._rebuild_webview(force=True)

        await self.cog._notify_owners_webview_build_failed()  # must not raise


if __name__ == "__main__":
    unittest.main()
