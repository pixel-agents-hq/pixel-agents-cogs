"""cctv's owner-notification path for a degraded (but still serving)
Pixel Agents webview.

Before this, `WebviewAssets._load_assets()` logged a missing/corrupt
sprite family or furniture catalog with `self._log.error(...)` and moved
on -- only a missing `characters` family flipped `ready`/`error`, so every
other family silently rendered missing/blank sprites with no owner-visible
signal at all. `CctvBase._notify_owners_if_assets_degraded` mirrors
pixelagents' own `_notify_owners_webview_build_failed` pattern to close
that gap.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ..adapters.cog_base import CctvBase


class _Corridor:
    def __init__(self) -> None:
        self.calls = 0

    async def substitute_default_prefix(self, text: str) -> str:
        self.calls += 1
        return text.replace("[p]", "!")


class _Bot:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_to_owners(self, message: str) -> None:
        self.messages.append(message)


class _RaisingCorridor:
    async def substitute_default_prefix(self, text: str) -> str:
        raise RuntimeError("boom")


class TestNotifyOwnersIfAssetsDegraded(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cctv = object.__new__(CctvBase)
        self.corridor = _Corridor()
        self.bot = _Bot()
        self.cctv._corridor = self.corridor  # type: ignore[attr-defined]
        self.cctv.bot = self.bot  # type: ignore[attr-defined]
        self.cctv._notified_degraded_commit = None  # type: ignore[attr-defined]
        self.cctv._assets = SimpleNamespace(degraded=(), built_commit="abc123")  # type: ignore[attr-defined]

    async def test_noop_when_nothing_is_degraded(self) -> None:
        await self.cctv._notify_owners_if_assets_degraded()

        self.assertEqual(self.bot.messages, [])

    async def test_notifies_owners_once_for_a_degraded_commit(self) -> None:
        self.cctv._assets.degraded = ("walls", "furniture-catalog")  # type: ignore[attr-defined]

        await self.cctv._notify_owners_if_assets_degraded()

        self.assertEqual(len(self.bot.messages), 1)
        self.assertIn("walls", self.bot.messages[0])
        self.assertIn("furniture-catalog", self.bot.messages[0])
        self.assertEqual(self.corridor.calls, 1)

    async def test_does_not_renotify_the_same_commit_on_repeat_calls(self) -> None:
        self.cctv._assets.degraded = ("walls",)  # type: ignore[attr-defined]

        await self.cctv._notify_owners_if_assets_degraded()
        await self.cctv._notify_owners_if_assets_degraded()
        await self.cctv._notify_owners_if_assets_degraded()

        self.assertEqual(len(self.bot.messages), 1)

    async def test_renotifies_after_a_new_commit_is_synced(self) -> None:
        self.cctv._assets.degraded = ("walls",)  # type: ignore[attr-defined]
        await self.cctv._notify_owners_if_assets_degraded()

        self.cctv._assets.built_commit = "def456"  # type: ignore[attr-defined]
        self.cctv._assets.degraded = ("carpets",)  # type: ignore[attr-defined]
        await self.cctv._notify_owners_if_assets_degraded()

        self.assertEqual(len(self.bot.messages), 2)

    async def test_notification_failure_is_swallowed_not_raised(self) -> None:
        self.cctv._corridor = _RaisingCorridor()  # type: ignore[attr-defined]
        self.cctv._assets.degraded = ("walls",)  # type: ignore[attr-defined]

        await self.cctv._notify_owners_if_assets_degraded()  # must not raise

        self.assertEqual(self.bot.messages, [])

    async def test_noop_when_corridor_is_not_loaded_yet(self) -> None:
        self.cctv._corridor = None  # type: ignore[attr-defined]
        self.cctv._assets.degraded = ("walls",)  # type: ignore[attr-defined]

        await self.cctv._notify_owners_if_assets_degraded()  # must not raise

        self.assertEqual(self.bot.messages, [])


if __name__ == "__main__":
    unittest.main()
