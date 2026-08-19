"""Tests for the `[p]pixelagents webview {commit,setcommit,resetcommit}` commands.

See issue #15: building the webview must be overridable to any Pixel Agents
commit hash (or a link to one), revertible to the source-pinned default, and
gated so only the bot owner can change it while anyone can read it -- owner
enforcement itself is the `@commands.is_owner()` marker asserted in
`test_compatibility.py` (the decorator is a stub in this test environment,
same as every other owner-only command in the repo).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pixelagents.infrastructure import webview_build
from pixelagents.tests.test_pixelagents import _make_cog


def _context() -> MagicMock:
    ctx = MagicMock()
    ctx.interaction = None
    ctx.send = AsyncMock()
    return ctx


class TestWebviewCommitCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_reports_the_default_pin_when_no_override_is_set(self) -> None:
        ctx = _context()

        await self.cog.cmd_webview_commit(ctx)

        message = ctx.send.await_args.kwargs["content"]
        self.assertIn(webview_build.pinned_commit(), message)
        self.assertIn("default", message)

    async def test_reports_the_custom_commit_once_set(self) -> None:
        ctx = _context()
        await self.cog._settings_service.set_webview_commit_override("a" * 40)

        await self.cog.cmd_webview_commit(ctx)

        message = ctx.send.await_args.kwargs["content"]
        self.assertIn("a" * 40, message)
        self.assertIn(webview_build.pinned_commit(), message)


class TestWebviewSetCommitCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_accepts_a_raw_commit_hash(self) -> None:
        ctx = _context()

        await self.cog.cmd_webview_setcommit(ctx, "a" * 40)

        self.assertEqual(await self.cog._settings_service.webview_commit_override(), "a" * 40)
        self.assertIn("a" * 40, ctx.send.await_args.kwargs["content"])

    async def test_accepts_and_normalizes_a_commit_link(self) -> None:
        ctx = _context()
        commit = "3537e140c2094761beae748592aeb92ece8edfdd"

        await self.cog.cmd_webview_setcommit(
            ctx, f"https://github.com/pixel-agents-hq/pixel-agents/tree/{commit}"
        )

        self.assertEqual(await self.cog._settings_service.webview_commit_override(), commit)

    async def test_rejects_an_invalid_reference_without_mutating_settings(self) -> None:
        ctx = _context()

        await self.cog.cmd_webview_setcommit(ctx, "not-a-commit")

        self.assertIsNone(await self.cog._settings_service.webview_commit_override())
        self.assertIn("commit hash", ctx.send.await_args.kwargs["content"])


class TestWebviewResetCommitCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    async def test_clears_a_previously_set_override(self) -> None:
        ctx = _context()
        await self.cog._settings_service.set_webview_commit_override("a" * 40)

        await self.cog.cmd_webview_resetcommit(ctx)

        self.assertIsNone(await self.cog._settings_service.webview_commit_override())
        self.assertIn(webview_build.pinned_commit(), ctx.send.await_args.kwargs["content"])


class TestRebuildUsesTheConfiguredCommit(unittest.IsolatedAsyncioTestCase):
    async def test_rebuild_builds_from_the_override_once_set(self) -> None:
        cog = _make_cog()
        commit = "b" * 40
        await cog._settings_service.set_webview_commit_override(commit)

        with patch.object(webview_build, "ensure_webview_built") as ensure_webview_built:
            ensure_webview_built.return_value = webview_build.BuildResult(
                rebuilt=True, commit=commit
            )
            await cog._rebuild_webview(force=True)

        self.assertEqual(ensure_webview_built.call_args.kwargs["commit"], commit)

    async def test_rebuild_builds_from_the_default_pin_without_an_override(self) -> None:
        cog = _make_cog()

        with patch.object(webview_build, "ensure_webview_built") as ensure_webview_built:
            ensure_webview_built.return_value = webview_build.BuildResult(
                rebuilt=True, commit=webview_build.pinned_commit()
            )
            await cog._rebuild_webview(force=True)

        self.assertIsNone(ensure_webview_built.call_args.kwargs["commit"])


if __name__ == "__main__":
    unittest.main()
