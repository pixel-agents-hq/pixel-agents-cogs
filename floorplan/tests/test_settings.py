"""Focused tests for floorplan's own (now minimal) settings persistence.

Everything else this file used to cover -- ws_port/message_tool_clear_delay/
broadcast_rich_presence/broadcast_messages/guild enabled/include_bots, and
`SettingsService`'s side-effecting setters -- moved to `cctv` along with
the dashboard/WebSocket settings they configured (docs/cctv-design.md).
"""

from __future__ import annotations

import unittest
from copy import deepcopy

from floorplan.infrastructure.settings import (
    CONFIG_IDENTIFIER,
    GLOBAL_DEFAULTS,
    RedSettingsRepository,
)
from floorplan.tests.conftest import _FakeConfig


def make_repository() -> tuple[RedSettingsRepository, _FakeConfig]:
    config = _FakeConfig()
    config.register_global(**deepcopy(GLOBAL_DEFAULTS))
    return RedSettingsRepository(config), config


class TestRedSettingsRepository(unittest.IsolatedAsyncioTestCase):
    def test_create_preserves_identifier_scope_and_defaults(self) -> None:
        repository = RedSettingsRepository.create(object())
        config = repository.config

        self.assertEqual(config.identifier, CONFIG_IDENTIFIER)
        self.assertTrue(config.force_registration)
        self.assertEqual(config._global, GLOBAL_DEFAULTS)

    async def test_defaults(self) -> None:
        repository, _ = make_repository()

        self.assertEqual(
            await repository.pixel_index_api_url(), GLOBAL_DEFAULTS["pixel_index_api_url"]
        )
        self.assertEqual(
            await repository.pixel_index_web_url(), GLOBAL_DEFAULTS["pixel_index_web_url"]
        )

    async def test_set_pixel_index_api_url_persists_and_returns_the_value(self) -> None:
        repository, _ = make_repository()

        returned = await repository.set_pixel_index_api_url("https://example.test/api")

        self.assertEqual(returned, "https://example.test/api")
        self.assertEqual(await repository.pixel_index_api_url(), "https://example.test/api")

    async def test_set_pixel_index_web_url_persists_and_returns_the_value(self) -> None:
        repository, _ = make_repository()

        returned = await repository.set_pixel_index_web_url("https://example.test/web")

        self.assertEqual(returned, "https://example.test/web")
        self.assertEqual(await repository.pixel_index_web_url(), "https://example.test/web")


if __name__ == "__main__":
    unittest.main()
