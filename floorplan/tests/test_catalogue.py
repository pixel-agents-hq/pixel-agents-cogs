"""Focused tests for the Pixel Index client, catalogue service, and views."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from corridor.domain import EMPLOYEE_KEY, llm_tool_spec
from floorplan.adapters.layout_views import LayoutBrowseView, LayoutDetailView
from floorplan.application.catalogue import (
    CatalogueBases,
    CatalogueError,
    CatalogueErrorCode,
    CatalogueResult,
    CatalogueService,
)
from floorplan.contracts.pixel_index import LayoutDetail, LayoutListResponse
from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.infrastructure.pixel_index import PixelIndexClient
from floorplan.tests.conftest import FakeCorridor, _FakeInteraction


def layout_summary(slug: str = "office") -> dict[str, object]:
    return {
        "slug": slug,
        "title": "Office",
        "description": "A tidy office",
        "tags": ["cozy"],
        "files": {
            "layout": f"/api/v1/layouts/{slug}/download",
            "preview": f"/api/v1/layouts/{slug}/preview.png",
            "thumbnail": f"/api/v1/layouts/{slug}/thumbnail.png",
        },
    }


def layout_detail(slug: str = "office", *, layout: dict[str, object] | None = None) -> LayoutDetail:
    raw = layout_summary(slug)
    raw["layout"] = layout or {
        "version": 1,
        "cols": 1,
        "rows": 1,
        "tiles": [1],
        "furniture": [],
    }
    return LayoutDetail.model_validate(raw)


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        payload: object = None,
        *,
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.json_error = json_error

    async def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class RecordingSession:
    def __init__(self, responses: list[FakeResponse | Exception], **kwargs: object) -> None:
        self.responses = responses
        self.created_with = kwargs
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


class TestPixelIndexClient(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_one_session_and_closes_it(self) -> None:
        session = RecordingSession(
            [
                FakeResponse(payload={"layouts": []}),
                FakeResponse(payload={**layout_summary(), "layout": layout_detail().layout}),
            ]
        )
        creations: list[dict[str, object]] = []

        def factory(**kwargs: object) -> RecordingSession:
            creations.append(kwargs)
            return session

        client = PixelIndexClient(session_factory=factory)
        await client.start()
        await client.start()
        search = await client.search(
            "https://index.example/",
            query="cozy & quiet",
            tag="pets/animals",
            sort="newest",
            cursor="next token",
        )
        detail = await client.detail("https://index.example", "office/with spaces")

        assert search.ok
        assert detail.ok
        assert len(creations) == 1
        assert len(session.calls) == 2
        assert session.calls[0] == (
            "https://index.example/api/v1/layouts",
            {
                "params": {
                    "sort": "newest",
                    "limit": 5,
                    "q": "cozy & quiet",
                    "tags": "pets/animals",
                    "cursor": "next token",
                }
            },
        )
        assert session.calls[1][0].endswith("/api/v1/layouts/office%2Fwith%20spaces")
        assert creations[0]["timeout"] == {
            "total": 10.0,
            "connect": 3.0,
            "sock_read": 7.0,
        }

        await client.close()
        await client.close()
        assert session.closed
        assert client.session is None

    async def test_request_after_close_does_not_reopen_until_explicit_start(self) -> None:
        sessions = [
            RecordingSession([]),
            RecordingSession([FakeResponse(payload={"layouts": []})]),
        ]
        creations = 0

        def factory(**_: object) -> RecordingSession:
            nonlocal creations
            session = sessions[creations]
            creations += 1
            return session

        client = PixelIndexClient(session_factory=factory)
        await client.start()
        await client.close()

        stopped = await client.search("https://index.example", query=None, tag=None, sort="newest")

        assert stopped.error is not None
        assert stopped.error.code is CatalogueErrorCode.TRANSPORT
        assert creations == 1

        await client.start()
        restarted = await client.search(
            "https://index.example", query=None, tag=None, sort="newest"
        )

        assert restarted.ok
        assert creations == 2
        await client.close()

    async def test_timeout_is_classified(self) -> None:
        client = PixelIndexClient(
            session_factory=lambda **_: RecordingSession([TimeoutError("slow")])
        )

        result = await client.search("https://index.example", query=None, tag=None, sort="newest")

        assert result.error is not None
        assert result.error.code is CatalogueErrorCode.TIMEOUT
        assert "timed out" in result.error.message

    async def test_transport_error_is_classified(self) -> None:
        client = PixelIndexClient(
            session_factory=lambda **_: RecordingSession([aiohttp.ClientError("down")])
        )

        result = await client.detail("https://index.example", "office")

        assert result.error is not None
        assert result.error.code is CatalogueErrorCode.TRANSPORT
        assert "down" in result.error.message

    async def test_non_success_status_is_classified(self) -> None:
        client = PixelIndexClient(
            session_factory=lambda **_: RecordingSession([FakeResponse(status=503)])
        )

        result = await client.detail("https://index.example", "office")

        assert result.error is not None
        assert result.error.code is CatalogueErrorCode.HTTP_STATUS
        assert result.error.status == 503

    async def test_invalid_json_is_distinct_from_contract_failure(self) -> None:
        invalid_json = json.JSONDecodeError("bad", "{", 0)
        session = RecordingSession(
            [
                FakeResponse(json_error=invalid_json),
                FakeResponse(payload={"layouts": [{"title": "missing slug"}]}),
            ]
        )
        client = PixelIndexClient(session_factory=lambda **_: session)

        malformed = await client.search(
            "https://index.example", query=None, tag=None, sort="newest"
        )
        wrong_shape = await client.search(
            "https://index.example", query=None, tag=None, sort="newest"
        )

        assert malformed.error is not None
        assert malformed.error.code is CatalogueErrorCode.INVALID_JSON
        assert wrong_shape.error is not None
        assert wrong_shape.error.code is CatalogueErrorCode.INVALID_RESPONSE

    async def test_health_uses_the_short_explicit_timeout(self) -> None:
        session = RecordingSession([FakeResponse(status=200)])
        client = PixelIndexClient(session_factory=lambda **_: session)

        result = await client.health("https://index.example/")

        assert result.value == "ok (https://index.example/health)"
        assert session.calls == [
            (
                "https://index.example/health",
                {"timeout": {"total": 5.0, "connect": 2.0, "sock_read": 4.0}},
            )
        ]


class MemoryCatalogueRepository:
    def __init__(self) -> None:
        self.api_url = "https://api.example"
        self.web_url = "https://web.example"
        self.layout: dict[str, object] | None = None

    async def pixel_index_api_url(self) -> str:
        return self.api_url

    async def pixel_index_web_url(self) -> str:
        return self.web_url

    async def set_guild_layout(self, guild_id: int, layout: dict[str, object] | None) -> None:
        self.layout = layout


class FakeGateway:
    def __init__(self) -> None:
        self.health_result = CatalogueResult(value="ok")
        self.search_result = CatalogueResult(value=LayoutListResponse(layouts=[]))
        self.detail_result = CatalogueResult(value=layout_detail())
        self.search_calls: list[tuple[str, dict[str, object]]] = []
        self.detail_calls: list[tuple[str, str]] = []

    async def health(self, base_url: str) -> CatalogueResult[str]:
        return self.health_result

    async def search(self, base_url: str, **kwargs: object) -> CatalogueResult[LayoutListResponse]:
        self.search_calls.append((base_url, kwargs))
        return self.search_result

    async def detail(self, base_url: str, slug: str) -> CatalogueResult[LayoutDetail]:
        self.detail_calls.append((base_url, slug))
        return self.detail_result


class TestCatalogueService(unittest.IsolatedAsyncioTestCase):
    def make_service(
        self,
        *,
        authorized: bool = True,
    ) -> tuple[
        CatalogueService,
        MemoryCatalogueRepository,
        FakeGateway,
        list[dict[str, object]],
    ]:
        repository = MemoryCatalogueRepository()
        gateway = FakeGateway()
        published: list[dict[str, object]] = []

        async def can_edit(_user_id: int, _guild_id: int) -> bool:
            return authorized

        async def publish(_guild_id: int, layout: dict[str, object]) -> None:
            published.append(layout)

        return (
            CatalogueService(
                repository,
                gateway,
                can_edit_layout=can_edit,
                publish_layout=publish,
            ),
            repository,
            gateway,
            published,
        )

    async def test_search_reads_the_current_base_url_for_each_request(self) -> None:
        service, repository, gateway, _ = self.make_service()

        await service.search(query="first", tag=None, sort="newest")
        repository.api_url = "https://other.example"
        await service.search(query="second", tag="cozy", sort="title", cursor="next")

        assert [call[0] for call in gateway.search_calls] == [
            "https://api.example",
            "https://other.example",
        ]
        assert gateway.search_calls[1][1]["limit"] == 5

    async def test_unauthorized_load_does_not_fetch_or_persist(self) -> None:
        service, repository, gateway, published = self.make_service(authorized=False)

        result = await service.load_layout(42, 100, "office")

        assert result.error is not None
        assert result.error.code is CatalogueErrorCode.UNAUTHORIZED
        assert gateway.detail_calls == []
        assert repository.layout is None
        assert published == []

    async def test_invalid_layout_is_rejected_by_the_canonical_contract(self) -> None:
        service, repository, gateway, published = self.make_service()
        gateway.detail_result = CatalogueResult(
            value=layout_detail(
                layout={"version": 1, "cols": True, "rows": 1, "tiles": [1], "furniture": []}
            )
        )

        result = await service.load_layout(42, 100, "office")

        assert result.error is not None
        assert result.error.code is CatalogueErrorCode.INVALID_LAYOUT
        assert repository.layout is None
        assert published == []

    async def test_valid_layout_is_preserved_persisted_and_published(self) -> None:
        service, repository, gateway, published = self.make_service()
        raw_layout = {
            "version": 1,
            "cols": 1,
            "rows": 1,
            "tiles": [1],
            "furniture": [],
            "future": {"kept": True},
        }
        gateway.detail_result = CatalogueResult(value=layout_detail(layout=raw_layout))

        result = await service.load_layout(42, 100, "office")

        assert result.value == "Loaded `Office` into the office."
        assert repository.layout == raw_layout
        assert published == [raw_layout]

    async def test_gateway_error_is_propagated_without_persistence(self) -> None:
        service, repository, gateway, published = self.make_service()
        error = CatalogueError(CatalogueErrorCode.HTTP_STATUS, "HTTP 404", status=404)
        gateway.detail_result = CatalogueResult(error=error)

        result = await service.load_layout(42, 100, "missing")

        assert result.error is error
        assert repository.layout is None
        assert published == []


class TestCatalogueViewsAndCommands(unittest.IsolatedAsyncioTestCase):
    def make_service(self) -> MagicMock:
        service = MagicMock(spec=CatalogueService)
        service.detail = AsyncMock()
        service.search = AsyncMock()
        service.load_layout = AsyncMock()
        return service

    def page(self, *, cursor: str | None = None) -> LayoutListResponse:
        return LayoutListResponse.model_validate(
            {"layouts": [layout_summary()], "total": 1, "nextCursor": cursor}
        )

    async def test_views_store_services_not_cog_backpointers(self) -> None:
        service = self.make_service()
        browse = LayoutBrowseView(
            service,
            1,
            query=None,
            tag=None,
            sort="newest",
            pages=[self.page()],
            page_index=0,
            api_base="https://api.example",
            web_base="https://web.example",
        )
        detail = LayoutDetailView(
            service,
            1,
            layout_detail(),
            api_base="https://api.example",
            web_base="https://web.example",
        )

        assert browse.catalogue is service
        assert detail.catalogue is service
        assert not hasattr(browse, "cog")
        assert not hasattr(detail, "cog")

    async def test_foreign_user_is_rejected_ephemerally(self) -> None:
        view = LayoutDetailView(
            self.make_service(),
            1,
            layout_detail(),
            api_base="https://api.example",
            web_base="https://web.example",
        )
        interaction = _FakeInteraction(user=SimpleNamespace(id=2))
        interaction.response.send_message = AsyncMock()

        allowed = await view.interaction_check(interaction)

        assert not allowed
        interaction.response.send_message.assert_awaited_once_with(
            "Only the person who ran this command can use these controls.",
            ephemeral=True,
        )

    async def test_select_failure_is_ephemeral(self) -> None:
        service = self.make_service()
        error = CatalogueError(CatalogueErrorCode.HTTP_STATUS, "HTTP 500", status=500)
        service.detail.return_value = CatalogueResult(error=error)
        view = LayoutBrowseView(
            service,
            1,
            query=None,
            tag=None,
            sort="newest",
            pages=[self.page()],
            page_index=0,
            api_base="https://api.example",
            web_base="https://web.example",
        )
        select = SimpleNamespace(values=["office"])
        interaction = _FakeInteraction(user=SimpleNamespace(id=1))
        interaction.response.send_message = AsyncMock()

        await view._make_select_callback(select)(interaction)

        service.detail.assert_awaited_once_with("office")
        interaction.response.send_message.assert_awaited_once_with("HTTP 500", ephemeral=True)

    async def test_search_command_keeps_response_public_and_normalizes_sort(self) -> None:
        bot = MagicMock(guilds=[])
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        cog._catalogue_service.search = AsyncMock(return_value=CatalogueResult(value=self.page()))
        cog._catalogue_service.bases = AsyncMock(
            return_value=CatalogueBases("https://api.example", "https://web.example")
        )
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))

        result = await cog.cmd_layout_search(context, query="cozy", tag=None, sort="invalid")

        cog._catalogue_service.search.assert_awaited_once_with(
            query="cozy", tag=None, sort="newest"
        )
        cog._send_public.assert_awaited_once()
        sent_view = cog._send_public.await_args.kwargs["view"]
        assert isinstance(sent_view, LayoutBrowseView)
        assert sent_view.catalogue is cog._catalogue_service
        assert result == {
            "status": "ok",
            "message": "Displayed 1 Pixel Index layout(s) in Discord.",
            "query": "cozy",
            "tag": None,
            "sort": "newest",
            "total": 1,
            "returned": 1,
            "has_more": False,
            "layouts": [
                {
                    "slug": "office",
                    "title": "Office",
                    "description": "A tidy office",
                    "tags": ["cozy"],
                    "author": None,
                    "visible_columns": None,
                    "visible_rows": None,
                    "furniture_count": None,
                }
            ],
        }
        assert cog._corridor.capability_checks == [(7, EMPLOYEE_KEY)]

    async def test_empty_search_returns_an_llm_readable_success(self) -> None:
        bot = MagicMock(guilds=[])
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        cog._catalogue_service.search = AsyncMock(
            return_value=CatalogueResult(value=LayoutListResponse(layouts=[], total=0))
        )
        cog._catalogue_service.bases = AsyncMock()
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))

        result = await cog.cmd_layout_search(context)

        assert result == {
            "status": "ok",
            "message": "No layouts found on Pixel Index.",
            "query": None,
            "tag": None,
            "sort": "newest",
            "total": 0,
            "returned": 0,
            "has_more": False,
            "layouts": [],
        }
        cog._send_public.assert_awaited_once_with(context, "No layouts found on Pixel Index.")
        cog._catalogue_service.bases.assert_not_awaited()

    async def test_search_error_is_returned_to_the_llm_and_posted(self) -> None:
        bot = MagicMock(guilds=[])
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        error = CatalogueError(CatalogueErrorCode.TIMEOUT, "Pixel Index timed out.")
        cog._catalogue_service.search = AsyncMock(return_value=CatalogueResult(error=error))
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))

        result = await cog.cmd_layout_search(context)

        assert result == {
            "status": "error",
            "error": "timeout",
            "message": "Pixel Index timed out.",
        }
        cog._send_public.assert_awaited_once_with(context, "Pixel Index timed out.")

    async def test_search_rejects_non_string_raw_tool_input(self) -> None:
        bot = MagicMock(guilds=[])
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        cog._catalogue_service.search = AsyncMock()
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))

        result = await cog.cmd_layout_search(context, query=7)  # type: ignore[arg-type]

        assert result["status"] == "error"
        assert result["error"] == "invalid_query"
        cog._catalogue_service.search.assert_not_awaited()

    async def test_view_command_normalizes_slug_and_uses_current_bases(self) -> None:
        bot = MagicMock(guilds=[])
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        cog._catalogue_service.detail = AsyncMock(
            return_value=CatalogueResult(value=layout_detail("default"))
        )
        cog._catalogue_service.bases = AsyncMock(
            return_value=CatalogueBases("https://api.example", "https://web.example")
        )
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))

        result = await cog.cmd_layout_view(context, "  DEFAULT  ")

        cog._catalogue_service.detail.assert_awaited_once_with("default")
        sent_view = cog._send_public.await_args.kwargs["view"]
        assert isinstance(sent_view, LayoutDetailView)
        assert sent_view.api_base == "https://api.example"
        assert result["status"] == "ok"
        assert result["message"] == "Displayed `Office` in Discord."
        detail_output = result["layout"]
        assert isinstance(detail_output, dict)
        assert detail_output["slug"] == "default"
        assert detail_output["preview_url"] == (
            "https://api.example/api/v1/layouts/default/preview.png"
        )
        assert detail_output["download_url"] == (
            "https://api.example/api/v1/layouts/default/download"
        )
        assert detail_output["web_url"] == "https://web.example/layouts/default"
        assert "layout" not in detail_output

    async def test_view_rejects_empty_raw_tool_slug(self) -> None:
        bot = MagicMock(guilds=[])
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        cog._catalogue_service.detail = AsyncMock()
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))

        result = await cog.cmd_layout_view(context, "   ")

        assert result["status"] == "error"
        assert result["error"] == "invalid_slug"
        cog._catalogue_service.detail.assert_not_awaited()

    async def test_employee_permission_denial_stops_before_search(self) -> None:
        bot = MagicMock(guilds=[])
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor(allow_employee=False)
        cog._catalogue_service.search = AsyncMock()
        cog._send_public = AsyncMock()
        context = MagicMock(interaction=None, author=SimpleNamespace(id=7))
        context.send = AsyncMock()

        result = await cog.cmd_layout_search(context)

        assert result["status"] == "error"
        assert result["error"] == "permission_denied"
        cog._catalogue_service.search.assert_not_awaited()
        context.send.assert_awaited_once_with("You don't have permission to do that.")


class TestLayoutCommandsAreLLMTools:
    def test_search_tool_schema_describes_filters_and_sort_enum(self) -> None:
        command = FloorplanCog.cmd_layout_search
        spec = llm_tool_spec(getattr(command, "callback", command))

        assert spec is not None
        assert spec.name == "floorplan_layout_search"
        assert spec.required_group == EMPLOYEE_KEY
        assert spec.parameters == {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Optional text to search for in layout titles and descriptions. "
                        "Omit to list available layouts."
                    ),
                },
                "tag": {
                    "type": "string",
                    "description": "Optional exact tag used to filter layouts.",
                },
                "sort": {
                    "type": "string",
                    "description": "Sort order for results.",
                    "enum": ["newest", "furniture", "largest", "title"],
                },
            },
            "required": [],
        }

    def test_view_tool_schema_requires_the_exact_slug(self) -> None:
        command = FloorplanCog.cmd_layout_view
        spec = llm_tool_spec(getattr(command, "callback", command))

        assert spec is not None
        assert spec.name == "floorplan_layout_view"
        assert spec.required_group == EMPLOYEE_KEY
        assert "use 'default'" in spec.description
        assert spec.parameters == {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": (
                        "Exact Pixel Index layout slug returned by search, e.g. 'default'."
                    ),
                }
            },
            "required": ["slug"],
        }
