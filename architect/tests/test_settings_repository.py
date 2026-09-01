"""Exercises RedArchitectRepository against the fake Config installed by the
package-root conftest.py."""

from __future__ import annotations

import unittest

import pytest

from ..infrastructure.settings_repository import (
    CONFIG_IDENTIFIER,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_SYSTEM_PROMPT,
    GLOBAL_DEFAULTS,
    RedArchitectRepository,
)


class TestRedArchitectRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedArchitectRepository.create(cog=object())

    async def test_defaults(self) -> None:
        settings = await self.repository.global_settings()

        self.assertEqual(settings.max_tool_calls, DEFAULT_MAX_TOOL_CALLS)
        self.assertEqual(settings.system_prompt, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(settings.debug_logging, DEFAULT_DEBUG_LOGGING)
        self.assertEqual(self.repository.config._global_data, GLOBAL_DEFAULTS)
        self.assertNotEqual(CONFIG_IDENTIFIER, 4172636869746374)

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
