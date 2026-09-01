"""Tests for Floorplan's fresh Pixel Index-only Config identity."""

from __future__ import annotations

import unittest
from copy import deepcopy

from floorplan.infrastructure.settings import (
    CONFIG_IDENTIFIER,
    DEFAULT_PIXEL_INDEX_API_URL,
    DEFAULT_PIXEL_INDEX_WEB_URL,
    GLOBAL_DEFAULTS,
    RedSettingsRepository,
)
from floorplan.tests.conftest import _FakeConfig


class TestRedSettingsRepository(unittest.IsolatedAsyncioTestCase):
    def make_repository(self) -> tuple[RedSettingsRepository, _FakeConfig]:
        config = _FakeConfig()
        config.register_global(**deepcopy(GLOBAL_DEFAULTS))
        return RedSettingsRepository(config), config

    def test_create_uses_the_new_identity_and_only_endpoint_defaults(self) -> None:
        repository = RedSettingsRepository.create(object())
        config = repository.config

        self.assertEqual(config.identifier, CONFIG_IDENTIFIER)
        self.assertTrue(config.force_registration)
        self.assertEqual(config.cog_name, "floorplan")
        self.assertEqual(config._global, GLOBAL_DEFAULTS)
        self.assertEqual(config._guild_defaults, {})
        self.assertEqual(config._user_defaults, {})

    async def test_defaults_and_normalized_setters(self) -> None:
        repository, _ = self.make_repository()

        self.assertEqual(await repository.pixel_index_api_url(), DEFAULT_PIXEL_INDEX_API_URL)
        self.assertEqual(await repository.pixel_index_web_url(), DEFAULT_PIXEL_INDEX_WEB_URL)
        self.assertEqual(
            await repository.set_pixel_index_api_url(" https://api.example.test/v1/ "),
            "https://api.example.test/v1",
        )
        self.assertEqual(
            await repository.set_pixel_index_web_url("http://index.example.test/"),
            "http://index.example.test",
        )

    async def test_invalid_endpoint_does_not_mutate_config(self) -> None:
        repository, _ = self.make_repository()

        with self.assertRaises(ValueError):
            await repository.set_pixel_index_api_url("ftp://index.example.test")
        with self.assertRaises(ValueError):
            await repository.set_pixel_index_web_url("index.example.test")

        self.assertEqual(await repository.pixel_index_api_url(), DEFAULT_PIXEL_INDEX_API_URL)
        self.assertEqual(await repository.pixel_index_web_url(), DEFAULT_PIXEL_INDEX_WEB_URL)


if __name__ == "__main__":
    unittest.main()
