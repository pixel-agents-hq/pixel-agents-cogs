"""Unit tests for the floorplan cog -- now Pixel Index browsing/catalogue
loading only; dashboard/WebSocket hosting and Discord presence mirroring
moved to `cctv` (docs/cctv-design.md).

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from corridor.domain import ReplyField
from floorplan.application.catalogue import CatalogueResult
from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.floorplan import _discord_id_to_agent_id
from floorplan.models import LayoutDetail, LayoutListResponse
from floorplan.tests.conftest import FakeCorridor, FakePixelAgents, _FakeInteraction, make_ctx

import aiohttp  # stubbed by conftest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cog():
    bot = MagicMock()
    bot.guilds = []
    bot.is_owner = AsyncMock(return_value=False)
    pixelagents = FakePixelAgents()
    # Both direct attribute access (cog._pixelagents, set below) and the
    # ensure_loaded()-driven lazy resolution _set_discord_layout performs
    # must resolve to the SAME instance.
    bot.get_cog = MagicMock(side_effect=lambda name: {"PixelAgents": pixelagents}.get(name))
    cog = FloorplanCog(bot)
    cog._corridor = FakeCorridor()
    cog._pixelagents = pixelagents
    return cog


def _layout_summary(slug="office", title="Office", **overrides):
    payload = {
        "slug": slug,
        "title": title,
        "description": "A tidy office",
        "tags": ["cozy"],
    }
    payload.update(overrides)
    return payload


def _layout_detail(slug="office", **overrides):
    payload = {
        "slug": slug,
        "title": "Office",
        "description": "A tidy office",
        "tags": ["cozy"],
        "layout": {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []},
    }
    payload.update(overrides)
    return payload


class _FakeHttpResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttpSession:
    def __init__(self, *, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.closed = False
        self.last_url = None
        self.last_params = None

    def get(self, url, params=None, **kwargs):
        self.last_url = url
        self.last_params = params
        if self._exc is not None:
            raise self._exc
        return self._response

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# Tests: ID mapping
# ---------------------------------------------------------------------------


class TestDiscordIdToAgentId(unittest.TestCase):
    def test_maps_positive_snowflake_to_negative_agent_id(self) -> None:
        self.assertEqual(_discord_id_to_agent_id(1), -1)

    def test_is_stable_across_calls(self) -> None:
        self.assertEqual(_discord_id_to_agent_id(42), _discord_id_to_agent_id(42))


# ---------------------------------------------------------------------------
# Tests: cog lifecycle
# ---------------------------------------------------------------------------


class TestPixelagentsResolution(unittest.IsolatedAsyncioTestCase):
    """floorplan is tested *before* pixelagents in the alphabetical
    Downloader smoke-test order (docs/dependency-loading.md) -- neither
    cog_load() nor setup() may eagerly fully-load pixelagents as a side
    effect, or the harness's later, independent load of pixelagents would
    find it already registered. pixelagents becomes a genuinely loaded
    Cog instance only lazily, the first time a Pixel Index catalogue load
    actually happens (`PixelAgentsBase._set_discord_layout`)."""

    async def test_cog_load_never_touches_pixelagents(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)

        with (
            patch(
                "floorplan.adapters.cog_base.ensure_corridor_loaded",
                new=AsyncMock(return_value=FakeCorridor()),
            ),
            patch("corridor.dependency_loader.ensure_loaded", new=AsyncMock()) as ensure_loaded,
            patch.object(cog._pixel_index_client, "start", new=AsyncMock()),
            patch.object(cog._pixel_index_client, "close", new=AsyncMock()),
        ):
            await cog.cog_load()
            ensure_loaded.assert_not_awaited()
            await cog.cog_unload()

    async def test_set_discord_layout_resolves_pixelagents_lazily(self) -> None:
        cog = _make_cog()

        await cog._set_discord_layout({"cols": 1})

        self.assertEqual(cog._pixelagents.office_state().set_discord_layout_calls, [{"cols": 1}])

    async def test_setup_never_touches_pixelagents_either(self) -> None:
        """The same bug also lives one layer up: floorplan/__init__.py's
        setup() -- Red's actual `[p]load floorplan` entrypoint -- must not
        call the pixelagents full-loader directly, before cog_load even
        runs. setup() does need pixelagents genuinely *importable* though
        (its agent-visualization modules are imported at module scope by
        `.floorplan`) -- see corridor.dependency_loader.ensure_importable,
        which stops short of the full Cog load this test guards against."""

        import floorplan as floorplan_package

        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        bot.add_cog = AsyncMock()

        with (
            patch("corridor.dependency_loader.ensure_loaded", new=AsyncMock()) as ensure_loaded,
            patch("floorplan.dependency_loader.ensure_corridor_loaded", new=AsyncMock()),
            patch(
                "corridor.dependency_loader.ensure_importable", new=AsyncMock()
            ) as ensure_importable,
        ):
            await floorplan_package.setup(bot)

        ensure_loaded.assert_not_awaited()
        ensure_importable.assert_awaited_once_with(bot, "pixelagents")


class TestLLMToolRegistration(unittest.IsolatedAsyncioTestCase):
    async def test_cog_lifecycle_registers_and_unregisters_layout_tools(self) -> None:
        bot = MagicMock(guilds=[])
        corridor = FakeCorridor()
        cog = FloorplanCog(bot)

        with (
            patch(
                "floorplan.adapters.cog_base.ensure_corridor_loaded",
                new=AsyncMock(return_value=corridor),
            ),
            patch.object(cog._pixel_index_client, "start", new=AsyncMock()),
            patch.object(cog._pixel_index_client, "close", new=AsyncMock()),
        ):
            await cog.cog_load()
            self.assertEqual(corridor.registered_llm_tools_calls, [(cog, "Floorplan")])

            await cog.cog_unload()

        self.assertEqual(corridor.unregistered_tool_owners, ["Floorplan"])

    async def test_failed_cog_load_unregisters_layout_tools(self) -> None:
        bot = MagicMock(guilds=[])
        corridor = FakeCorridor()
        cog = FloorplanCog(bot)

        with (
            patch(
                "floorplan.adapters.cog_base.ensure_corridor_loaded",
                new=AsyncMock(return_value=corridor),
            ),
            patch.object(
                cog._pixel_index_client, "start", new=AsyncMock(side_effect=RuntimeError("boom"))
            ),
            patch.object(cog._pixel_index_client, "close", new=AsyncMock()),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await cog.cog_load()

        self.assertEqual(corridor.registered_llm_tools_calls, [(cog, "Floorplan")])
        self.assertEqual(corridor.unregistered_tool_owners, ["Floorplan"])


# ---------------------------------------------------------------------------
# Tests: catalogue-load authorization
# ---------------------------------------------------------------------------


class TestCanEditLayoutUser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()

    async def test_zero_user_id_denied(self):
        self.assertFalse(await self.cog._can_edit_layout_user(0))

    async def test_bot_owner_allowed(self):
        self.cog.bot.is_owner = AsyncMock(return_value=True)
        self.assertTrue(await self.cog._can_edit_layout_user(12345))

    async def test_keyholder_denied_when_no_guild_membership(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        self.cog.bot.guilds = []

        self.assertFalse(await self.cog._can_edit_layout_user(12345))

    async def test_keyholder_allows(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        member = MagicMock()
        member.id = 12345
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self.cog.bot.guilds = [guild]

        self.assertTrue(await self.cog._can_edit_layout_user(12345))
        self.assertIn((12345, "keyholder"), self.cog._corridor.capability_checks)

    async def test_corridor_owner_allows_without_keyholder_role(self):
        self.cog._corridor = FakeCorridor(owners=frozenset({12345}))
        member = MagicMock()
        member.id = 12345
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self.cog.bot.guilds = [guild]

        self.assertTrue(await self.cog._can_edit_layout_user(12345))

    async def test_non_keyholder_denied(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({999}))
        member = MagicMock()
        member.id = 12345
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self.cog.bot.guilds = [guild]

        self.assertFalse(await self.cog._can_edit_layout_user(12345))

    async def test_no_matching_guild_member_denied(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        self.cog.bot.guilds = [guild]

        self.assertFalse(await self.cog._can_edit_layout_user(12345))


# ---------------------------------------------------------------------------
# Tests: commands
# ---------------------------------------------------------------------------


class TestPixelIndexSetwebCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    def _ctx(self):
        return make_ctx()

    async def test_sets_web_url(self):
        await self.cog.cmd_pixelindex_setweb(self._ctx(), "https://pixel-index.vercel.app/")
        self.assertEqual(
            await self.cog.config.pixel_index_web_url(), "https://pixel-index.vercel.app"
        )

    async def test_rejects_invalid_url(self):
        ctx = self._ctx()
        await self.cog.cmd_pixelindex_setweb(ctx, "not-a-url")
        self.assertIn("valid URL", ctx.send.call_args.kwargs["content"])


class TestPixelIndexGet(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_success_returns_json(self):
        session = _FakeHttpSession(response=_FakeHttpResponse(200, {"ok": True}))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_get("/api/v1/meta")
        self.assertTrue(ok)
        self.assertEqual(data, {"ok": True})
        self.assertTrue(session.last_url.endswith("/api/v1/meta"))

    async def test_non_200_status_is_reported(self):
        session = _FakeHttpSession(response=_FakeHttpResponse(500, None))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_get("/api/v1/meta")
        self.assertFalse(ok)
        self.assertIn("500", data)

    async def test_connection_error_is_reported(self):
        session = _FakeHttpSession(exc=OSError("boom"))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_get("/api/v1/meta")
        self.assertFalse(ok)
        self.assertIn("boom", data)

    async def test_search_builds_expected_params(self):
        session = _FakeHttpSession(response=_FakeHttpResponse(200, {"layouts": []}))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            await self.cog._pixel_index_search(
                query="cozy", tag="pets", sort="furniture", cursor="abc"
            )
        self.assertEqual(
            session.last_params,
            {"sort": "furniture", "limit": 5, "q": "cozy", "tags": "pets", "cursor": "abc"},
        )

    async def test_search_accepts_well_shaped_response(self):
        page = {"layouts": [_layout_summary("office")], "total": 1, "nextCursor": None}
        session = _FakeHttpSession(response=_FakeHttpResponse(200, page))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_search(query=None, tag=None, sort="newest")
        self.assertTrue(ok)
        self.assertEqual(data, LayoutListResponse.model_validate(page))

    async def test_search_rejects_response_missing_slug(self):
        page = {"layouts": [{"title": "Office"}], "total": 1}
        session = _FakeHttpSession(response=_FakeHttpResponse(200, page))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, message = await self.cog._pixel_index_search(query=None, tag=None, sort="newest")
        self.assertFalse(ok)
        self.assertIn("unexpected response", message)

    async def test_layout_accepts_well_shaped_response(self):
        detail = _layout_detail("office")
        session = _FakeHttpSession(response=_FakeHttpResponse(200, detail))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_layout("office")
        self.assertTrue(ok)
        self.assertEqual(data, LayoutDetail.model_validate(detail))

    async def test_layout_rejects_response_missing_layout_blob(self):
        detail = _layout_detail("office")
        del detail["layout"]
        session = _FakeHttpSession(response=_FakeHttpResponse(200, detail))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, message = await self.cog._pixel_index_layout("office")
        self.assertFalse(ok)
        self.assertIn("unexpected response", message)


class TestLoadPixelIndexLayout(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_rejects_unauthorized_user(self):
        self.cog.bot.is_owner = AsyncMock(return_value=False)
        ok, message = await self.cog._load_pixel_index_layout(12345, "office")
        self.assertFalse(ok)
        self.assertIn("not authorized", message)

    async def test_rejects_invalid_layout(self):
        self.cog.bot.is_owner = AsyncMock(return_value=True)
        detail = _layout_detail("office")
        detail["layout"] = {"not": "valid"}
        self.cog._catalogue_service.detail = AsyncMock(
            return_value=CatalogueResult(value=LayoutDetail.model_validate(detail))
        )
        ok, message = await self.cog._load_pixel_index_layout(12345, "office")
        self.assertFalse(ok)
        self.assertIn("invalid", message)

    async def test_loads_valid_layout_through_the_facade(self):
        """The one write path: pixelagents' OfficeStateFacade, not a
        private floorplan Config key or a local WebSocket broadcast --
        corridor's own OfficeStateChanged publish is what reaches any
        connected `cctv` dashboard page live (docs/cctv-design.md)."""

        self.cog.bot.is_owner = AsyncMock(return_value=True)
        detail = _layout_detail("office")
        model = LayoutDetail.model_validate(detail)
        self.cog._catalogue_service.detail = AsyncMock(return_value=CatalogueResult(value=model))

        ok, message = await self.cog._load_pixel_index_layout(12345, "office")

        self.assertTrue(ok)
        self.assertEqual(
            self.cog._pixelagents.office_state().set_discord_layout_calls, [model.layout]
        )


class TestLayoutBrowseView(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_search_command_keeps_response_public_and_normalizes_sort(self) -> None:
        self.cog._catalogue_service.search = AsyncMock(
            return_value=CatalogueResult(
                value=LayoutListResponse.model_validate(
                    {"layouts": [_layout_summary()], "total": 1, "nextCursor": None}
                )
            )
        )
        self.cog._catalogue_service.bases = AsyncMock()
        self.cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=MagicMock(id=7))

        await self.cog.cmd_layout_search(context, query="cozy", tag=None, sort="invalid")

        self.cog._catalogue_service.search.assert_awaited_once_with(
            query="cozy", tag=None, sort="newest"
        )
        self.cog._send_public.assert_awaited_once()


class TestLayoutDetailView(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_view_command_normalizes_slug_and_uses_current_bases(self) -> None:
        self.cog._catalogue_service.detail = AsyncMock(
            return_value=CatalogueResult(value=LayoutDetail.model_validate(_layout_detail()))
        )
        self.cog._catalogue_service.bases = AsyncMock(
            return_value=MagicMock(api="https://api.example", web="https://web.example")
        )
        self.cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=MagicMock(id=7))

        result = await self.cog.cmd_layout_view(context, "  OFFICE  ")

        self.cog._catalogue_service.detail.assert_awaited_once_with("office")
        assert result["status"] == "ok"


class TestReplyHelper(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_prefix_uses_ctx_send(self):
        ctx = make_ctx()
        await self.cog._reply(ctx, "hello")
        ctx.send.assert_awaited_once_with(content="**Floorplan:** hello")

    async def test_text_mode_renders_through_corridor(self):
        self.cog._corridor = FakeCorridor(reply_mode="text")
        ctx = make_ctx()
        await self.cog._reply(ctx, "hello", title="Pixel Agents")
        ctx.send.assert_awaited_once_with(content="**Floorplan:** hello")
        self.assertEqual(
            self.cog._corridor.rendered_replies,
            [(ctx.guild.id, "Pixel Agents", "hello", None, ())],
        )

    async def test_embed_mode_renders_through_corridor(self):
        self.cog._corridor = FakeCorridor(reply_mode="embed")
        ctx = make_ctx()
        await self.cog._reply(ctx, "hello", title="Pixel Agents")
        ctx.send.assert_awaited_once()
        self.assertIn("embed", ctx.send.call_args.kwargs)
        self.assertNotIn("content", ctx.send.call_args.kwargs)

    async def test_embed_mode_reply_carries_fields(self):
        self.cog._corridor = FakeCorridor(reply_mode="embed")
        ctx = make_ctx()
        fields = [ReplyField("Serving", "yes", False), ReplyField("Clients", "3")]

        await self.cog._reply(ctx, title="Status", fields=fields)

        embed = ctx.send.call_args.kwargs["embed"]
        self.assertEqual(
            [call.kwargs for call in embed.add_field.call_args_list],
            [
                {"name": "Serving", "value": "yes", "inline": False},
                {"name": "Clients", "value": "3", "inline": True},
            ],
        )

    async def test_text_mode_reply_flattens_fields_to_lines(self):
        self.cog._corridor = FakeCorridor(reply_mode="text")
        ctx = make_ctx()
        fields = [ReplyField("Serving", "yes"), ReplyField("Clients", "3")]

        await self.cog._reply(ctx, title="Status", fields=fields)

        ctx.send.assert_awaited_once_with(
            content="**Floorplan:** Status\n**Serving:** yes\n**Clients:** 3"
        )

    async def test_view_only_reply_bypasses_corridor(self):
        self.cog._corridor = FakeCorridor(reply_mode="embed")
        ctx = make_ctx()
        view = object()
        await self.cog._reply(ctx, view=view)
        ctx.send.assert_awaited_once_with(view=view)
        self.assertEqual(self.cog._corridor.rendered_replies, [])

    async def test_slash_uses_response_send_message(self):
        ctx = make_ctx()
        interaction = _FakeInteraction(guild=MagicMock())
        ctx.interaction = interaction
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))
            interaction.response._done = True

        interaction.response.send_message = _capture
        await self.cog._reply(ctx, "hello")
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_slash_after_defer_uses_followup(self):
        ctx = make_ctx()
        interaction = _FakeInteraction(guild=MagicMock())
        interaction.response._done = True
        ctx.interaction = interaction
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))

        interaction.followup.send = _capture
        await self.cog._reply(ctx, "after defer")
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))


if __name__ == "__main__":
    unittest.main()
