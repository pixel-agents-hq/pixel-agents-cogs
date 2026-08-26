"""DashboardMixin: the webview page/static routes, and the
missing-dashboard owner notification. Mirrors the shape of floorplan's own
`TestDashboardWebviewHosting`/`TestDashboardMissingOwnerNotification`
(`floorplan/tests/test_floorplan.py`)."""

from __future__ import annotations

import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

from ..adapters.dashboard import DASHBOARD_DOCS_URL
from ..architect import Architect
from ..infrastructure.webview import WebviewAssetProvider
from .conftest import FakeBot, FakePixelAgents


class TestDashboardWebviewHosting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Architect(bot=self.bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)

    async def test_static_asset_returns_raw_response(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "index-test.js").write_text("console.log('ok');", encoding="utf-8")
            self.cog._webview_assets = WebviewAssetProvider(root)

            result = await self.cog.dashboard_static("assets/index-test.js")

        self.assertEqual(result["status"], 0)
        raw = result["raw_response"]  # type: ignore[index]
        self.assertEqual(raw["content_type"], "text/javascript; charset=utf-8")
        self.assertEqual(base64.b64decode(raw["body_base64"]).decode("utf-8"), "console.log('ok');")

    async def test_static_asset_rejects_path_traversal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("ok", encoding="utf-8")
            self.cog._webview_assets = WebviewAssetProvider(root)

            result = await self.cog.dashboard_static("../index.html")

        self.assertEqual(result["status"], 1)
        self.assertEqual(result["error_code"], 404)

    async def test_dashboard_webview_returns_index_html(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(
                '<!doctype html><div id="root"></div>', encoding="utf-8"
            )
            self.cog._webview_assets = WebviewAssetProvider(root)
            assert self.bot.pixelagents is not None
            self.bot.pixelagents = FakePixelAgents(dist_path=root)
            self.cog._pixelagents = self.bot.pixelagents

            result = await self.cog.dashboard_webview()

        self.assertEqual(result["status"], 0)
        self.assertTrue(result["web_content"]["standalone"])  # type: ignore[index]
        self.assertIn("root", result["web_content"]["source"])  # type: ignore[index]


class TestDashboardMissingOwnerNotification(unittest.IsolatedAsyncioTestCase):
    """cog_load() must never block on a missing Red Web Dashboard cog --
    unlike pixelagents (`ensure_loaded`, required for the webview to exist
    at all), dashboard loading *after* architect is already handled by
    `on_dashboard_cog_add`'s own registration listener. This only has to
    catch what that listener can't see: dashboard still missing right now,
    at architect's own cog_load time -- see architect/adapters/dashboard.py.
    """

    def setUp(self) -> None:
        self.cog = Architect(bot=FakeBot())
        self.cog.bot.send_to_owners = AsyncMock()  # type: ignore[method-assign]

    async def test_dashboard_loaded_sends_no_dm(self) -> None:
        self.cog.bot.get_cog = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

        await self.cog._notify_owners_dashboard_missing_if_unloaded()

        self.cog.bot.send_to_owners.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_a_same_named_cog_without_dashboards_shape_still_notifies(self) -> None:
        # Finding *something* registered under "Dashboard" isn't proof it's
        # really Red Web Dashboard -- only the same `.rpc.third_parties_handler`
        # shape `on_dashboard_cog_add` already trusts is.
        self.cog.bot.get_cog = MagicMock(return_value=object())  # type: ignore[method-assign]

        await self.cog._notify_owners_dashboard_missing_if_unloaded()

        self.cog.bot.send_to_owners.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_dashboard_missing_notifies_owners_once_with_docs_link(self) -> None:
        self.cog.bot.get_cog = MagicMock(return_value=None)  # type: ignore[method-assign]

        await self.cog._notify_owners_dashboard_missing_if_unloaded()

        self.cog.bot.send_to_owners.assert_awaited_once()  # type: ignore[attr-defined]
        (message,), _kwargs = self.cog.bot.send_to_owners.await_args  # type: ignore[attr-defined]
        self.assertIn(DASHBOARD_DOCS_URL, message)

    async def test_notify_never_raises_when_send_to_owners_fails(self) -> None:
        self.cog.bot.get_cog = MagicMock(return_value=None)  # type: ignore[method-assign]
        self.cog.bot.send_to_owners = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("no owners")
        )

        await self.cog._notify_owners_dashboard_missing_if_unloaded()  # must not raise


class TestOnDashboardCogAdd(unittest.IsolatedAsyncioTestCase):
    async def test_registers_as_a_third_party_when_the_shape_matches(self) -> None:
        cog = Architect(bot=FakeBot())
        dashboard_cog = MagicMock()

        await cog.on_dashboard_cog_add(dashboard_cog)

        dashboard_cog.rpc.third_parties_handler.add_third_party.assert_called_once_with(
            cog, overwrite=True
        )

    async def test_ignores_a_cog_with_no_rpc_attribute(self) -> None:
        cog = Architect(bot=FakeBot())

        await cog.on_dashboard_cog_add(object())  # type: ignore[arg-type]  # must not raise
