"""Lifecycle-managed HTTP adapter for the public Pixel Index API."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import aiohttp
from pydantic import ValidationError

from ..application.catalogue import (
    CatalogueError,
    CatalogueErrorCode,
    CatalogueResult,
)
from ..contracts.pixel_index import LayoutDetail, LayoutListResponse

REQUEST_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 7.0
HEALTH_TIMEOUT_SECONDS = 5.0
HEALTH_CONNECT_TIMEOUT_SECONDS = 2.0
HEALTH_READ_TIMEOUT_SECONDS = 4.0

SessionFactory = Callable[..., aiohttp.ClientSession]
QueryValue = str | int | float


class _ClientUnavailableError(RuntimeError):
    """The lifecycle has stopped the client until its next explicit start."""


class PixelIndexClient:
    """Use one reusable session for all Pixel Index requests in a Cog lifetime."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._log = logger or logging.getLogger(__name__)
        self._session: aiohttp.ClientSession | None = None
        # Requests may lazily start a freshly constructed client for backwards
        # compatibility.  Once ``close`` runs, only an explicit lifecycle
        # ``start`` may reopen it; otherwise a still-live Discord view could
        # recreate and leak a session after its Cog unloaded.
        self._explicitly_closed = False
        self._request_timeout = aiohttp.ClientTimeout(
            total=REQUEST_TIMEOUT_SECONDS,
            connect=CONNECT_TIMEOUT_SECONDS,
            sock_read=READ_TIMEOUT_SECONDS,
        )
        self._health_timeout = aiohttp.ClientTimeout(
            total=HEALTH_TIMEOUT_SECONDS,
            connect=HEALTH_CONNECT_TIMEOUT_SECONDS,
            sock_read=HEALTH_READ_TIMEOUT_SECONDS,
        )

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    @property
    def running(self) -> bool:
        return self._session is not None and not self._session.closed

    async def start(self) -> None:
        """Open the shared session once; safe to call repeatedly."""

        if self.running:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory(timeout=self._request_timeout)
        self._explicitly_closed = False

    async def close(self) -> None:
        """Close the shared session once; safe before or after startup."""

        session = self._session
        self._session = None
        self._explicitly_closed = True
        if session is not None and not session.closed:
            await session.close()

    async def health(self, base_url: str) -> CatalogueResult[str]:
        url = self._url(base_url, "/health")
        try:
            session = await self._get_session()
            async with session.get(url, timeout=self._health_timeout) as response:
                if response.status == 200:
                    return CatalogueResult(value=f"ok ({url})")
                return CatalogueResult(
                    error=CatalogueError(
                        CatalogueErrorCode.HTTP_STATUS,
                        f"HTTP {response.status} ({url})",
                        status=response.status,
                    )
                )
        except TimeoutError as exc:
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.TIMEOUT,
                    f"unreachable (request timed out: {url})",
                    detail=str(exc),
                )
            )
        except (_ClientUnavailableError, aiohttp.ClientError, OSError) as exc:
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.TRANSPORT,
                    f"unreachable ({exc})",
                    detail=str(exc),
                )
            )

    async def get_json(
        self,
        base_url: str,
        path: str,
        params: Mapping[str, QueryValue] | None = None,
    ) -> CatalogueResult[Any]:
        """Compatibility entry point for an unmodeled JSON GET."""

        return await self._pixel_index_get(path, base_url=base_url, params=params)

    async def search(
        self,
        base_url: str,
        *,
        query: str | None,
        tag: str | None,
        sort: str,
        cursor: str | None = None,
        limit: int = 5,
    ) -> CatalogueResult[LayoutListResponse]:
        params: dict[str, QueryValue] = {"sort": sort, "limit": limit}
        if query:
            params["q"] = query
        if tag:
            params["tags"] = tag
        if cursor:
            params["cursor"] = cursor
        raw = await self._pixel_index_get("/api/v1/layouts", base_url=base_url, params=params)
        if raw.error is not None:
            return CatalogueResult(error=raw.error)
        try:
            return CatalogueResult(value=LayoutListResponse.model_validate(raw.value))
        except ValidationError as exc:
            self._log.warning(
                "floorplan: Pixel Index layout list response failed validation: %s", exc
            )
            return self._invalid_response(exc)

    async def detail(self, base_url: str, slug: str) -> CatalogueResult[LayoutDetail]:
        safe_slug = quote(slug, safe="")
        raw = await self._pixel_index_get(
            f"/api/v1/layouts/{safe_slug}",
            base_url=base_url,
        )
        if raw.error is not None:
            return CatalogueResult(error=raw.error)
        try:
            return CatalogueResult(value=LayoutDetail.model_validate(raw.value))
        except ValidationError as exc:
            self._log.warning(
                "floorplan: Pixel Index layout detail response failed validation: %s", exc
            )
            return self._invalid_response(exc)

    async def _pixel_index_get(
        self,
        path: str,
        *,
        base_url: str,
        params: Mapping[str, QueryValue] | None = None,
    ) -> CatalogueResult[Any]:
        url = self._url(base_url, path)
        try:
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return CatalogueResult(
                        error=CatalogueError(
                            CatalogueErrorCode.HTTP_STATUS,
                            f"Pixel Index API returned HTTP {response.status}.",
                            status=response.status,
                        )
                    )
                try:
                    payload = await response.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError, UnicodeDecodeError) as exc:
                    return CatalogueResult(
                        error=CatalogueError(
                            CatalogueErrorCode.INVALID_JSON,
                            "Pixel Index returned invalid JSON. Try again later.",
                            detail=str(exc),
                        )
                    )
                if payload is None:
                    return CatalogueResult(
                        error=CatalogueError(
                            CatalogueErrorCode.INVALID_RESPONSE,
                            "Pixel Index returned an unexpected response. Try again later.",
                        )
                    )
                return CatalogueResult(value=payload)
        except TimeoutError as exc:
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.TIMEOUT,
                    "Could not reach the Pixel Index API: request timed out.",
                    detail=str(exc),
                )
            )
        except (_ClientUnavailableError, aiohttp.ClientError, OSError) as exc:
            return CatalogueResult(
                error=CatalogueError(
                    CatalogueErrorCode.TRANSPORT,
                    f"Could not reach the Pixel Index API: {exc}",
                    detail=str(exc),
                )
            )

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.running:
            if self._explicitly_closed:
                raise _ClientUnavailableError("Pixel Index client is stopped")
            await self.start()
        if not self.running or self._session is None:
            self._session = None
            raise _ClientUnavailableError("Pixel Index client failed to start")
        return self._session

    @staticmethod
    def _url(base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _invalid_response(exc: ValidationError) -> CatalogueResult[Any]:
        return CatalogueResult(
            error=CatalogueError(
                CatalogueErrorCode.INVALID_RESPONSE,
                "Pixel Index returned an unexpected response. Try again later.",
                detail=str(exc),
            )
        )


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "HEALTH_CONNECT_TIMEOUT_SECONDS",
    "HEALTH_READ_TIMEOUT_SECONDS",
    "HEALTH_TIMEOUT_SECONDS",
    "PixelIndexClient",
    "READ_TIMEOUT_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
]
