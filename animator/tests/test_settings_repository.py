"""Exercises RedAnimatorRepository against the fake Config installed by the
package-root conftest.py."""

from __future__ import annotations

import unittest

import pytest

from ..infrastructure.settings_repository import (
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_SYSTEM_PROMPT,
    RedAnimatorRepository,
)


class TestRedAnimatorRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedAnimatorRepository.create(cog=object())

    async def test_defaults(self) -> None:
        settings = await self.repository.global_settings()

        self.assertEqual(settings.max_tool_calls, DEFAULT_MAX_TOOL_CALLS)
        self.assertEqual(settings.system_prompt, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(settings.debug_logging, DEFAULT_DEBUG_LOGGING)
        self.assertIsNone(settings.request_timeout_seconds)

    async def test_set_max_tool_calls_persists(self) -> None:
        await self.repository.set_max_tool_calls(3)

        settings = await self.repository.global_settings()

        self.assertEqual(settings.max_tool_calls, 3)

    async def test_set_max_tool_calls_rejects_non_positive_values(self) -> None:
        with pytest.raises(ValueError):
            await self.repository.set_max_tool_calls(0)

    async def test_set_system_prompt_persists(self) -> None:
        await self.repository.set_system_prompt("Be terse.")

        settings = await self.repository.global_settings()

        self.assertEqual(settings.system_prompt, "Be terse.")

    async def test_reset_system_prompt_restores_default(self) -> None:
        await self.repository.set_system_prompt("Be terse.")

        await self.repository.reset_system_prompt()

        settings = await self.repository.global_settings()
        self.assertEqual(settings.system_prompt, DEFAULT_SYSTEM_PROMPT)

    async def test_set_debug_logging_persists(self) -> None:
        await self.repository.set_debug_logging(True)

        settings = await self.repository.global_settings()

        self.assertTrue(settings.debug_logging)

    async def test_set_request_timeout_persists(self) -> None:
        await self.repository.set_request_timeout(120.0)

        settings = await self.repository.global_settings()

        self.assertEqual(settings.request_timeout_seconds, 120.0)

    async def test_set_request_timeout_none_resets_to_the_default(self) -> None:
        await self.repository.set_request_timeout(120.0)

        await self.repository.set_request_timeout(None)

        settings = await self.repository.global_settings()
        self.assertIsNone(settings.request_timeout_seconds)

    async def test_set_request_timeout_rejects_non_positive_values(self) -> None:
        with pytest.raises(ValueError):
            await self.repository.set_request_timeout(0)

    async def test_set_request_timeout_rejects_negative_values(self) -> None:
        with pytest.raises(ValueError):
            await self.repository.set_request_timeout(-5.0)

    async def test_set_request_timeout_rejects_bool(self) -> None:
        with pytest.raises(ValueError):
            await self.repository.set_request_timeout(True)
