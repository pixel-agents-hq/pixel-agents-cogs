"""Tests for the `[p]pixelagents webview {commit,setcommit,resetcommit,rebuild}`
commands -- the whole Pixel Agents command surface.

See issue #15: building the webview must be overridable to any Pixel Agents
commit hash (or a link to one), revertible to the source-pinned default.
Owner enforcement itself is the `@commands.is_owner()` marker asserted in
`test_architecture.py` (the decorator is a stub in this test environment,
same as every other owner-only command in the repo).

See issue #7 for the admin-facing surfaces of a webview build failure:
`[p]pixelagents webview rebuild`'s reply, `webview_bundle_status()` (which
floorplan's own status field reads), and the owner DM `cog_load` fires
automatically.
"""

from __future__ import annotations

import shutil
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pixelagents.infrastructure import webview_build
from pixelagents.pixelagents import pixelagents as PixelAgentsCog
from pixelagents.tests.conftest import FakeCorridor


def _make_cog() -> PixelAgentsCog:
    bot = MagicMock()
    bot.guilds = []
    bot.is_owner = AsyncMock(return_value=False)
    cog = PixelAgentsCog(bot)
    cog._corridor = FakeCorridor()
    return cog


def _context() -> MagicMock:
    ctx = MagicMock()
    ctx.interaction = None
    ctx.send = AsyncMock()
    ctx.clean_prefix = ";"
    return ctx


class TestWebviewCommitCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_reports_the_default_pin_when_no_override_is_set(self) -> None:
        ctx = _context()

        await self.cog.cmd_webview_commit.callback(self.cog, ctx)

        message = ctx.send.await_args.kwargs["content"]
        self.assertIn(webview_build.pinned_commit(), message)
        self.assertIn("default", message)

    async def test_reports_the_custom_commit_once_set(self) -> None:
        ctx = _context()
        await self.cog._settings_repository.set_webview_commit_override("a" * 40)

        await self.cog.cmd_webview_commit.callback(self.cog, ctx)

        message = ctx.send.await_args.kwargs["content"]
        self.assertIn("a" * 40, message)
        self.assertIn(webview_build.pinned_commit(), message)


class TestWebviewSetCommitCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_accepts_a_raw_commit_hash(self) -> None:
        ctx = _context()

        await self.cog.cmd_webview_setcommit.callback(self.cog, ctx, "a" * 40)

        self.assertEqual(await self.cog._settings_repository.webview_commit_override(), "a" * 40)
        self.assertIn("a" * 40, ctx.send.await_args.kwargs["content"])

    async def test_accepts_and_normalizes_a_commit_link(self) -> None:
        ctx = _context()
        commit = "3537e140c2094761beae748592aeb92ece8edfdd"

        await self.cog.cmd_webview_setcommit.callback(
            self.cog, ctx, f"https://github.com/pixel-agents-hq/pixel-agents/tree/{commit}"
        )

        self.assertEqual(await self.cog._settings_repository.webview_commit_override(), commit)

    async def test_rejects_an_invalid_reference_without_mutating_settings(self) -> None:
        ctx = _context()

        await self.cog.cmd_webview_setcommit.callback(self.cog, ctx, "not-a-commit")

        self.assertIsNone(await self.cog._settings_repository.webview_commit_override())
        self.assertIn("commit hash", ctx.send.await_args.kwargs["content"])


class TestWebviewResetCommitCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_clears_a_previously_set_override(self) -> None:
        ctx = _context()
        await self.cog._settings_repository.set_webview_commit_override("a" * 40)

        await self.cog.cmd_webview_resetcommit.callback(self.cog, ctx)

        self.assertIsNone(await self.cog._settings_repository.webview_commit_override())
        self.assertIn(webview_build.pinned_commit(), ctx.send.await_args.kwargs["content"])


class TestRebuildUsesTheConfiguredCommit(unittest.IsolatedAsyncioTestCase):
    async def test_rebuild_builds_from_the_override_once_set(self) -> None:
        cog = _make_cog()
        commit = "b" * 40
        await cog._settings_repository.set_webview_commit_override(commit)

        with patch.object(webview_build, "ensure_webview_built") as ensure_webview_built:
            ensure_webview_built.return_value = webview_build.BuildResult(
                rebuilt=True, commit=commit, base_path=webview_build.RELATIVE_BASE_PATH
            )
            await cog._rebuild_webview(force=True)

        self.assertEqual(ensure_webview_built.call_args.kwargs["commit"], commit)
        self.assertNotIn("base_path", ensure_webview_built.call_args.kwargs)

    async def test_rebuild_builds_from_the_default_pin_without_an_override(self) -> None:
        cog = _make_cog()

        with patch.object(webview_build, "ensure_webview_built") as ensure_webview_built:
            ensure_webview_built.return_value = webview_build.BuildResult(
                rebuilt=True,
                commit=webview_build.pinned_commit(),
                base_path=webview_build.RELATIVE_BASE_PATH,
            )
            await cog._rebuild_webview(force=True)

        self.assertIsNone(ensure_webview_built.call_args.kwargs["commit"])
        self.assertNotIn("base_path", ensure_webview_built.call_args.kwargs)


class TestWebviewBuildSurfaces(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()
        self.cog.bot.send_to_owners = AsyncMock()

    async def test_rebuild_command_reports_missing_tools_and_updates_status(self) -> None:
        ctx = _context()
        # conftest.py pre-seeds an already-built webview_dist so constructing
        # a cog never triggers a real clone+build -- remove it so this
        # exercises "never successfully built" rather than "a stale build
        # from before this failed rebuild is still being served" (also a
        # real, and correct, outcome -- just not the one this test covers).
        shutil.rmtree(self.cog._webview_dist_path())

        with patch.object(webview_build, "missing_tools", return_value=("git", "npm")):
            await self.cog.cmd_webview_rebuild.callback(self.cog, ctx)

        messages = [call.kwargs["content"] for call in ctx.send.await_args_list]
        self.assertTrue(any("Rebuilding" in m for m in messages))
        self.assertTrue(any("git" in m and "npm" in m for m in messages))
        self.assertEqual(self.cog.webview_bundle_status().detail, "⚠️ missing tool(s): git, npm")

    async def test_rebuild_command_reports_success(self) -> None:
        # The command always forces a rebuild (an explicit request should
        # not be a silent no-op), so this must stub the same steps
        # test_webview_build.py's orchestration tests do -- otherwise it
        # would perform a real clone+npm+vite build against the network.
        ctx = _context()

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
            await self.cog.cmd_webview_rebuild.callback(self.cog, ctx)
        sync_dist.assert_called_once()

        messages = [call.kwargs["content"] for call in ctx.send.await_args_list]
        self.assertTrue(any("✅" in m for m in messages))

    async def test_failed_build_notifies_owners_with_missing_tools(self) -> None:
        with patch.object(webview_build, "missing_tools", return_value=("git",)):
            status = await self.cog._rebuild_webview(force=True)
        self.assertIn("git", status)

        await self.cog._notify_owners_webview_build_failed()

        self.cog.bot.send_to_owners.assert_awaited_once()
        (message,), _kwargs = self.cog.bot.send_to_owners.await_args
        self.assertIn("git", message)
        self.assertIn(";pixelagents webview rebuild", message)
        self.assertNotIn("[p]", message)

    async def test_notify_owners_is_a_noop_without_a_failed_build(self) -> None:
        await self.cog._notify_owners_webview_build_failed()
        self.cog.bot.send_to_owners.assert_not_awaited()

    async def test_notify_owners_never_raises_when_send_to_owners_fails(self) -> None:
        self.cog.bot.send_to_owners = AsyncMock(side_effect=RuntimeError("no owners"))
        with patch.object(webview_build, "missing_tools", return_value=("git",)):
            await self.cog._rebuild_webview(force=True)

        await self.cog._notify_owners_webview_build_failed()  # must not raise


class TestRefreshDependents(unittest.IsolatedAsyncioTestCase):
    """A dependent (cctv, architect, painter, floorplan) resolves its own
    `self._pixelagents` reference once, in its own `cog_load`, via
    `ensure_loaded`. If pixelagents itself reloads independently afterward,
    that reference goes stale and its cached `_office_state` facade stays
    permanently unloaded (issue: "pixelagents office-state facade is not
    loaded" surfaced through cctv's editor websocket). `_refresh_dependents`
    is pixelagents' half of the fix: push the new Cog instance to every
    loaded cog exposing `refresh_pixelagents`, duck-typed so a new dependent
    never needs wiring in here by name."""

    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_calls_refresh_pixelagents_on_every_cog_that_defines_it(self) -> None:
        dependent = MagicMock()
        dependent.refresh_pixelagents = AsyncMock()
        unrelated = MagicMock(spec=[])  # no refresh_pixelagents attribute at all
        self.cog.bot.cogs = {"Cctv": dependent, "Unrelated": unrelated}

        await self.cog._refresh_dependents()

        dependent.refresh_pixelagents.assert_awaited_once_with(self.cog)

    async def test_one_dependent_raising_does_not_block_another_or_the_caller(self) -> None:
        broken = MagicMock()
        broken.refresh_pixelagents = AsyncMock(side_effect=RuntimeError("boom"))
        healthy = MagicMock()
        healthy.refresh_pixelagents = AsyncMock()
        self.cog.bot.cogs = {"Broken": broken, "Healthy": healthy}

        await self.cog._refresh_dependents()  # must not raise

        healthy.refresh_pixelagents.assert_awaited_once_with(self.cog)

    async def test_noop_when_nothing_is_loaded_yet(self) -> None:
        self.cog.bot.cogs = {}

        await self.cog._refresh_dependents()  # must not raise


if __name__ == "__main__":
    unittest.main()
