"""Caches LiteLLM's model catalogue for the `[p]corridor llm model` picker.

Ports tinytinkerer's edge model-list caching
(apps/edge/src/lib/models-cache.ts) down to single-tenant scale: one
in-process cache entry keyed by the currently configured base URL --
there is exactly one LiteLLM connection here, not one per caller -- a
short in-memory TTL instead of a colo-wide Cache API, and no per-
credential rate-limit backoff bookkeeping (a single shared virtual key
getting rate limited is rare enough at this scale to just retry next
call, no durable state needed).

Graceful degradation, in order of preference, mirrors the edge's
list route: a fresh cache hit avoids touching LiteLLM at all; a failed
refresh with a previous success on record serves that last-known list
(`stale=True`); a failed refresh with nothing cached, or no virtual key
configured yet, falls back to just the currently-configured model (or an
empty tuple if even that is unset) so the picker still opens instead of
erroring.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..domain.models import LLMSettings, ModelCatalogResult
from ..infrastructure.llm_client import LLMRequestError

FRESH_TTL_SECONDS = 5 * 60.0


class ModelLister(Protocol):
    """The slice of `LiteLLMClient` this service depends on -- same
    "Protocol naming the slice of a concrete client a service depends on"
    shape `AgentToolServerRegistry.McpTools`/`architect`'s
    `ToolLoopService.ToolLLM` already use, so a test can stand in a plain
    fake without needing a real aiohttp session."""

    async def list_models(self, *, base_url: str, api_key: str) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    base_url: str
    models: tuple[str, ...]
    fetched_at: float


class ModelCatalogService:
    def __init__(
        self,
        llm_client: ModelLister,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._llm_client = llm_client
        self._clock = clock
        self._cache: _CacheEntry | None = None

    async def list_models(
        self, settings: LLMSettings, *, force_refresh: bool = False
    ) -> ModelCatalogResult:
        if settings.llm_api_key is None:
            return ModelCatalogResult(
                models=self._fallback(settings),
                stale=False,
                error="No LiteLLM virtual key is configured yet (`[p]corridor llm key`).",
            )

        cached = self._cache
        matching_cache = (
            cached if cached is not None and cached.base_url == settings.llm_base_url else None
        )
        if (
            matching_cache is not None
            and not force_refresh
            and (self._clock() - matching_cache.fetched_at) <= FRESH_TTL_SECONDS
        ):
            return ModelCatalogResult(models=matching_cache.models, stale=False, error=None)

        try:
            models = tuple(
                await self._llm_client.list_models(
                    base_url=settings.llm_base_url, api_key=settings.llm_api_key
                )
            )
        except LLMRequestError as exc:
            if matching_cache is not None:
                return ModelCatalogResult(models=matching_cache.models, stale=True, error=str(exc))
            return ModelCatalogResult(models=self._fallback(settings), stale=False, error=str(exc))

        if not models:
            # LiteLLM answered but the catalogue came back empty (or every
            # entry looked like an embedding model) -- fall back rather
            # than showing an unusable, permanently-empty picker.
            return ModelCatalogResult(models=self._fallback(settings), stale=False, error=None)

        self._cache = _CacheEntry(
            base_url=settings.llm_base_url, models=models, fetched_at=self._clock()
        )
        return ModelCatalogResult(models=models, stale=False, error=None)

    @staticmethod
    def _fallback(settings: LLMSettings) -> tuple[str, ...]:
        return (settings.llm_model,) if settings.llm_model else ()


__all__ = ["FRESH_TTL_SECONDS", "ModelCatalogService", "ModelLister"]
