"""Framework-neutral Pixel Index catalogue use cases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from pydantic import ValidationError

from ..contracts.layout import OfficeLayout, RawOfficeLayout
from ..contracts.pixel_index import LayoutDetail, LayoutListResponse

LAYOUT_SEARCH_PAGE_SIZE = 5
LAYOUT_SORT_CHOICES = ("newest", "furniture", "largest", "title")


class CatalogueErrorCode(StrEnum):
    """Stable error categories shared by the HTTP and Discord adapters."""

    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    INVALID_JSON = "invalid_json"
    INVALID_RESPONSE = "invalid_response"
    UNAUTHORIZED = "unauthorized"
    INVALID_LAYOUT = "invalid_layout"


@dataclass(frozen=True, slots=True)
class CatalogueError:
    code: CatalogueErrorCode
    message: str
    status: int | None = None
    detail: str | None = None


ResultValue = TypeVar("ResultValue")


@dataclass(frozen=True, slots=True)
class CatalogueResult(Generic[ResultValue]):
    """A successful value or a user-safe, classified failure."""

    value: ResultValue | None = None
    error: CatalogueError | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.error is None):
            raise ValueError("a catalogue result must contain exactly one value or error")

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class CatalogueBases:
    api: str
    web: str


class PixelIndexGateway(Protocol):
    async def health(self, base_url: str) -> CatalogueResult[str]: ...

    async def search(
        self,
        base_url: str,
        *,
        query: str | None,
        tag: str | None,
        sort: str,
        cursor: str | None = None,
        limit: int = LAYOUT_SEARCH_PAGE_SIZE,
    ) -> CatalogueResult[LayoutListResponse]: ...

    async def detail(self, base_url: str, slug: str) -> CatalogueResult[LayoutDetail]: ...


class CatalogueRepository(Protocol):
    async def pixel_index_api_url(self) -> str: ...

    async def pixel_index_web_url(self) -> str: ...

    async def set_guild_layout(self, guild_id: int, layout: RawOfficeLayout | None) -> None: ...


CanEditLayout = Callable[[int, int], Awaitable[bool]]
PublishLayout = Callable[[int, RawOfficeLayout], Awaitable[None]]


class CatalogueService:
    """Coordinate catalogue browsing and loading without Discord or HTTP details."""

    def __init__(
        self,
        repository: CatalogueRepository,
        gateway: PixelIndexGateway,
        *,
        can_edit_layout: CanEditLayout,
        publish_layout: PublishLayout,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._can_edit_layout = can_edit_layout
        self._publish_layout = publish_layout

    async def bases(self) -> CatalogueBases:
        return CatalogueBases(
            api=await self._repository.pixel_index_api_url(),
            web=await self._repository.pixel_index_web_url(),
        )

    async def health(self, base_url: str | None = None) -> CatalogueResult[str]:
        if base_url is None:
            base_url = await self._repository.pixel_index_api_url()
        return await self._gateway.health(base_url)

    async def search(
        self,
        *,
        query: str | None,
        tag: str | None,
        sort: str,
        cursor: str | None = None,
    ) -> CatalogueResult[LayoutListResponse]:
        base_url = await self._repository.pixel_index_api_url()
        return await self._gateway.search(
            base_url,
            query=query,
            tag=tag,
            sort=sort,
            cursor=cursor,
            limit=LAYOUT_SEARCH_PAGE_SIZE,
        )

    async def detail(self, slug: str) -> CatalogueResult[LayoutDetail]:
        base_url = await self._repository.pixel_index_api_url()
        return await self._gateway.detail(base_url, slug)

    async def load_layout(self, user_id: int, guild_id: int, slug: str) -> CatalogueResult[str]:
        """Authorize, validate, persist, and publish one indexed layout."""

        if not await self._can_edit_layout(user_id, guild_id):
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.UNAUTHORIZED,
                    "You are not authorized to manage Pixel Agents layouts.",
                )
            )

        detail_result = await self.detail(slug)
        if detail_result.error is not None:
            return CatalogueResult(error=detail_result.error)
        detail = detail_result.value
        if detail is None:  # Defensive narrowing for alternate gateway implementations.
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.INVALID_RESPONSE,
                    "Pixel Index returned an unexpected response. Try again later.",
                )
            )

        try:
            layout = OfficeLayout.model_validate(detail.layout).to_raw()
        except ValidationError as exc:
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.INVALID_LAYOUT,
                    "That layout is invalid and cannot be loaded.",
                    detail=str(exc),
                )
            )

        await self._repository.set_guild_layout(guild_id, layout)
        await self._publish_layout(guild_id, layout)
        return CatalogueResult(value=f"Loaded `{detail.title or slug}` into the office.")


__all__ = [
    "LAYOUT_SEARCH_PAGE_SIZE",
    "LAYOUT_SORT_CHOICES",
    "CatalogueBases",
    "CatalogueError",
    "CatalogueErrorCode",
    "CatalogueRepository",
    "CatalogueResult",
    "CatalogueService",
    "PixelIndexGateway",
]
