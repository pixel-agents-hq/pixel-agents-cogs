from __future__ import annotations

import unittest
from typing import Any

from ..adapters.commands import CommandsMixin


class _Settings:
    def __init__(self) -> None:
        self.rich_presence: list[bool] = []

    async def set_broadcast_rich_presence(self, value: bool) -> None:
        self.rich_presence.append(value)


class _Office:
    def __init__(self) -> None:
        self.clear_calls = 0

    async def clear_presence(self) -> None:
        self.clear_calls += 1


class _Harness(CommandsMixin):
    def __init__(self) -> None:
        self._settings = _Settings()
        self._pipelines = {"discord": type("Pipeline", (), {"office": _Office()})()}
        self.sync_calls = 0
        self.replies: list[str] = []

    @property
    def discord_pipeline(self) -> Any:
        return self._pipelines["discord"]

    async def _sync_all_guilds(self) -> None:
        self.sync_calls += 1

    async def _reply(self, ctx: object, content: str | None = None, **kwargs: object) -> None:
        del ctx, kwargs
        if content is not None:
            self.replies.append(content)


class TestCctvDisplayCommands(unittest.IsolatedAsyncioTestCase):
    async def test_enabling_rich_presence_reconciles_enabled_guilds(self) -> None:
        cog = _Harness()

        await CommandsMixin.cmd_rich_presence.callback(cog, object(), True)

        self.assertEqual(cog._settings.rich_presence, [True])
        self.assertEqual(cog.sync_calls, 1)
        self.assertEqual(cog.discord_pipeline.office.clear_calls, 0)

    async def test_disabling_rich_presence_clears_existing_activity(self) -> None:
        cog = _Harness()

        await CommandsMixin.cmd_rich_presence.callback(cog, object(), False)

        self.assertEqual(cog._settings.rich_presence, [False])
        self.assertEqual(cog.sync_calls, 0)
        self.assertEqual(cog.discord_pipeline.office.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
