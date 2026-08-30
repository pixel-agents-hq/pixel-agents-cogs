"""architect (and painter) share one office layout store, owned by
pixelagents (docs/painter-design.md part A) and entirely independent of
floorplan's per-guild office Config -- loading a Pixel Index layout into
floorplan's office must never affect this one. `_ensure_layout_seeded`
seeds it once from pixelagents' bundled default layout, on the first
successful webview sync, and never overwrites an already-stored value."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..architect import Architect
from .conftest import FakeBot, FakePixelAgents


def _write_bundle_with_default_layout(root: Path, *, tiles: list[int]) -> None:
    (root / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "asset-index.json").write_text(
        json.dumps({"defaultLayout": "default-layout.json"}), encoding="utf-8"
    )
    (root / "assets" / "default-layout.json").write_text(
        json.dumps({"tiles": tiles}), encoding="utf-8"
    )


class TestLegacyLayoutMigration(unittest.IsolatedAsyncioTestCase):
    """`_migrate_legacy_layout` (called from `cog_load`) one-time-copies
    an existing install's layout from architect's old, private Config key
    into pixelagents' shared store -- see docs/painter-design.md part A.
    Seeds the *old* key directly through `RedArchitectRepository.config`
    (the raw Config escape hatch), since nothing in production writes
    there anymore -- that's exactly the pre-migration state being
    simulated."""

    async def test_a_legacy_layout_is_copied_across_and_cleared(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog._repository.config.layout.set({"tiles": ["legacy"]})

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertEqual(await cog._office_layout_settings.layout(), {"tiles": ["legacy"]})
        self.assertIsNone(await cog._repository.legacy_layout())

    async def test_an_existing_new_store_is_never_overwritten_by_a_stale_legacy_value(
        self,
    ) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog._office_layout_settings.set_layout({"tiles": ["already", "migrated"]})
        await cog._repository.config.layout.set({"tiles": ["stale", "legacy"]})

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertEqual(
            await cog._office_layout_settings.layout(), {"tiles": ["already", "migrated"]}
        )

    async def test_a_fresh_install_with_neither_store_populated_migrates_nothing(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertIsNone(await cog._office_layout_settings.layout())


class TestLayoutSeeding(unittest.IsolatedAsyncioTestCase):
    async def test_first_sync_seeds_the_layout_from_the_bundled_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[1, 2, 3])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)

            await cog._sync_webview_assets()  # type: ignore[attr-defined]

            self.assertEqual(await cog._office_layout_settings.layout(), {"tiles": [1, 2, 3]})

    async def test_an_already_stored_layout_is_never_overwritten(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[1, 2, 3])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._office_layout_settings.set_layout({"tiles": ["already", "edited"]})

            await cog._sync_webview_assets()  # type: ignore[attr-defined]

            self.assertEqual(
                await cog._office_layout_settings.layout(), {"tiles": ["already", "edited"]}
            )

    async def test_sync_does_not_seed_or_raise_without_a_bundled_default_layout(self) -> None:
        # FakeBot's default FakePixelAgents dist_path is an empty temp dir --
        # ready, but with no asset-index.json/default-layout.json.
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._sync_webview_assets()  # type: ignore[attr-defined]  # must not raise

        self.assertIsNone(await cog._office_layout_settings.layout())

    async def test_config_identifier_is_pinned_distinct_from_floorplans(self) -> None:
        """architect's `RedArchitectRepository` is registered under its own
        Config identifier, verified here as a hardcoded literal distinct
        from floorplan's own (8364586608, per
        floorplan/infrastructure/settings.py) -- not imported directly,
        since the two packages' test suites install competing `redbot.core`
        stubs and must never share a pytest process (see
        docs/AGENTS.md's "Local quality gate" section). This is the actual
        storage-level guarantee behind "loading a layout into floorplan's
        office can never reach architect's own store": there is no shared
        Config namespace for it to land in even by accident."""

        from ..infrastructure.settings_repository import CONFIG_IDENTIFIER

        self.assertEqual(CONFIG_IDENTIFIER, 4172636869746374)
        self.assertNotEqual(CONFIG_IDENTIFIER, 8364586608)

    async def test_seeded_layout_is_only_ever_read_from_architects_own_store(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[1, 2, 3])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._sync_webview_assets()  # type: ignore[attr-defined]

            self.assertEqual(await cog._office_layout_settings.layout(), {"tiles": [1, 2, 3]})
