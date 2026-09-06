"""ModelCatalogService's caching/degradation policy in isolation from any
real HTTP -- a fake `LiteLLMClient.list_models` stand-in and a controllable
clock, no aiohttp involved (that's `test_llm_client.py`'s job)."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from ..application.model_catalog_service import FRESH_TTL_SECONDS, ModelCatalogService
from ..domain.models import LLMSettings
from ..infrastructure.llm_client import LLMRequestError


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeLiteLLMClient:
    def __init__(self, results: Sequence[list[str] | Exception]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str]] = []

    async def list_models(self, *, base_url: str, api_key: str) -> list[str]:
        self.calls.append((base_url, api_key))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _settings(
    *, base_url: str = "https://litellm.example/", model: str | None = "gpt-4o"
) -> LLMSettings:
    return LLMSettings(llm_base_url=base_url, llm_api_key="sk-test", llm_model=model)


class TestModelCatalogServiceHappyPath(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_and_returns_models(self) -> None:
        client = FakeLiteLLMClient([["a", "b"]])
        service = ModelCatalogService(client, clock=FakeClock())

        result = await service.list_models(_settings())

        self.assertEqual(result.models, ("a", "b"))
        self.assertFalse(result.stale)
        self.assertIsNone(result.error)

    async def test_second_call_within_ttl_serves_the_cache_without_refetching(self) -> None:
        client = FakeLiteLLMClient([["a"]])
        clock = FakeClock()
        service = ModelCatalogService(client, clock=clock)

        await service.list_models(_settings())
        clock.advance(FRESH_TTL_SECONDS - 1)
        result = await service.list_models(_settings())

        self.assertEqual(result.models, ("a",))
        self.assertEqual(len(client.calls), 1)

    async def test_call_past_the_ttl_refetches(self) -> None:
        client = FakeLiteLLMClient([["a"], ["b"]])
        clock = FakeClock()
        service = ModelCatalogService(client, clock=clock)

        await service.list_models(_settings())
        clock.advance(FRESH_TTL_SECONDS + 1)
        result = await service.list_models(_settings())

        self.assertEqual(result.models, ("b",))
        self.assertEqual(len(client.calls), 2)

    async def test_force_refresh_bypasses_a_fresh_cache(self) -> None:
        client = FakeLiteLLMClient([["a"], ["b"]])
        service = ModelCatalogService(client, clock=FakeClock())

        await service.list_models(_settings())
        result = await service.list_models(_settings(), force_refresh=True)

        self.assertEqual(result.models, ("b",))
        self.assertEqual(len(client.calls), 2)

    async def test_a_base_url_change_is_not_served_from_the_old_cache(self) -> None:
        client = FakeLiteLLMClient([["a"], ["b"]])
        service = ModelCatalogService(client, clock=FakeClock())

        await service.list_models(_settings(base_url="https://one.example/"))
        result = await service.list_models(_settings(base_url="https://two.example/"))

        self.assertEqual(result.models, ("b",))
        self.assertEqual(len(client.calls), 2)


class TestModelCatalogServiceDegradation(unittest.IsolatedAsyncioTestCase):
    async def test_no_api_key_falls_back_without_any_network_call(self) -> None:
        client = FakeLiteLLMClient([])
        service = ModelCatalogService(client, clock=FakeClock())

        result = await service.list_models(
            LLMSettings(llm_base_url="https://x/", llm_api_key=None, llm_model="gpt-4o")
        )

        self.assertEqual(result.models, ("gpt-4o",))
        self.assertFalse(result.stale)
        self.assertIsNotNone(result.error)
        self.assertEqual(client.calls, [])

    async def test_failed_refetch_with_a_prior_success_serves_the_stale_cache(self) -> None:
        client = FakeLiteLLMClient([["a", "b"], LLMRequestError("upstream 503")])
        clock = FakeClock()
        service = ModelCatalogService(client, clock=clock)

        await service.list_models(_settings())
        clock.advance(FRESH_TTL_SECONDS + 1)
        result = await service.list_models(_settings())

        self.assertEqual(result.models, ("a", "b"))
        self.assertTrue(result.stale)
        self.assertEqual(result.error, "upstream 503")

    async def test_failed_refetch_with_nothing_cached_falls_back_to_the_configured_model(
        self,
    ) -> None:
        client = FakeLiteLLMClient([LLMRequestError("could not reach litellm")])
        service = ModelCatalogService(client, clock=FakeClock())

        result = await service.list_models(_settings(model="gpt-4o"))

        self.assertEqual(result.models, ("gpt-4o",))
        self.assertFalse(result.stale)
        self.assertEqual(result.error, "could not reach litellm")

    async def test_failed_refetch_with_no_configured_model_and_nothing_cached_is_empty(
        self,
    ) -> None:
        client = FakeLiteLLMClient([LLMRequestError("could not reach litellm")])
        service = ModelCatalogService(client, clock=FakeClock())

        result = await service.list_models(_settings(model=None))

        self.assertEqual(result.models, ())
        self.assertEqual(result.error, "could not reach litellm")

    async def test_an_empty_catalogue_falls_back_to_the_configured_model(self) -> None:
        client = FakeLiteLLMClient([[]])
        service = ModelCatalogService(client, clock=FakeClock())

        result = await service.list_models(_settings(model="gpt-4o"))

        self.assertEqual(result.models, ("gpt-4o",))
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
