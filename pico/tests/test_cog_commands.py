"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_gate_service.py /
test_tool_loop_service.py / test_reply_tool.py / test_llm_client.py).

Owner-gated vs admin-gated commands are asserted by introspecting the
`__is_owner__`/`__admin_or_permissions__` tags the shared redbot stub
(`corridor/testing.py`) attaches -- Red's real check machinery isn't
exercised here, only which decorator each command carries.
"""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..infrastructure.settings_repository import DEFAULT_SYSTEM_PROMPT
from ..pico import Pico
from .conftest import FakeBot, FakeContext, FakeCorridor


def _descriptions(corridor: FakeCorridor) -> list[str | None]:
    return [reply["description"] for reply in corridor.replies]


class TestLLMSettingsAreOwnerGated(unittest.TestCase):
    """LLM connection settings are bot-owner scope -- only the connection
    itself, not turning Pico on for a given server (see `enabled` below,
    which is admin-gated instead). Mirrors floorplan's convention."""

    def setUp(self) -> None:
        self.cog = Pico(bot=FakeBot())

    def test_llm_endpoint_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.llm_endpoint.callback, "__is_owner__", False))

    def test_llm_key_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.llm_key.callback, "__is_owner__", False))

    def test_llm_model_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.llm_model.callback, "__is_owner__", False))

    def test_maxtoolcalls_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.maxtoolcalls.callback, "__is_owner__", False))

    def test_prompt_set_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.prompt_set.callback, "__is_owner__", False))

    def test_prompt_reset_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.prompt_reset.callback, "__is_owner__", False))

    def test_prompt_show_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.prompt_show.callback, "__is_owner__", False))


class TestEnabledIsAdminGated(unittest.TestCase):
    def setUp(self) -> None:
        self.cog = Pico(bot=FakeBot())

    def test_enabled_requires_administrator(self) -> None:
        permissions = getattr(self.cog.enabled.callback, "__admin_or_permissions__", None)

        self.assertEqual(permissions, {"administrator": True})

    def test_enabled_is_not_owner_gated(self) -> None:
        self.assertFalse(getattr(self.cog.enabled.callback, "__is_owner__", False))

    def test_status_is_neither_owner_nor_admin_gated(self) -> None:
        self.assertFalse(getattr(self.cog.status.callback, "__is_owner__", False))
        self.assertIsNone(getattr(self.cog.status.callback, "__admin_or_permissions__", None))


class TestLLMCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Pico(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_llm_endpoint_updates_and_replies(self) -> None:
        await self.cog.llm_endpoint.callback(self.cog, self.ctx, "https://example.test/")

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.llm_base_url, "https://example.test/")
        self.assertEqual(
            _descriptions(self.bot.corridor)[-1], "LLM endpoint set to `https://example.test/`."
        )

    async def test_llm_key_updates_and_deletes_the_invoking_message(self) -> None:
        await self.cog.llm_key.callback(self.cog, self.ctx, "sk-secret")

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.llm_api_key, "sk-secret")
        self.assertTrue(self.ctx.message.deleted)
        self.assertEqual(_descriptions(self.bot.corridor)[-1], "LLM virtual key updated.")
        sent_text = "".join(d or "" for d in _descriptions(self.bot.corridor))
        self.assertNotIn("sk-secret", sent_text)

    async def test_llm_model_updates_and_replies(self) -> None:
        await self.cog.llm_model.callback(self.cog, self.ctx, "gpt-test")

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.llm_model, "gpt-test")

    async def test_maxtoolcalls_updates(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, 3)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.max_tool_calls, 3)

    async def test_maxtoolcalls_rejects_non_positive_values(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, 0)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.max_tool_calls, 5)
        self.assertIn("positive", _descriptions(self.bot.corridor)[-1] or "")

    async def test_prompt_set_and_show(self) -> None:
        await self.cog.prompt_set.callback(self.cog, self.ctx, text="Be terse.")

        await self.cog.prompt_show.callback(self.cog, self.ctx)

        self.assertEqual(_descriptions(self.bot.corridor)[-1], "Be terse.")

    async def test_prompt_reset(self) -> None:
        await self.cog.prompt_set.callback(self.cog, self.ctx, text="Be terse.")

        await self.cog.prompt_reset.callback(self.cog, self.ctx)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.system_prompt, DEFAULT_SYSTEM_PROMPT)


class TestEnabledCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Pico(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_enabling_a_guild(self) -> None:
        await self.cog.enabled.callback(self.cog, self.ctx, True)

        assert self.ctx.guild is not None
        enabled = await self.cog._repository.guild_enabled(self.ctx.guild.id)
        self.assertTrue(enabled)

    async def test_disabling_a_guild(self) -> None:
        await self.cog.enabled.callback(self.cog, self.ctx, True)

        await self.cog.enabled.callback(self.cog, self.ctx, False)

        assert self.ctx.guild is not None
        enabled = await self.cog._repository.guild_enabled(self.ctx.guild.id)
        self.assertFalse(enabled)

    async def test_default_is_disabled(self) -> None:
        assert self.ctx.guild is not None
        enabled = await self.cog._repository.guild_enabled(self.ctx.guild.id)

        self.assertFalse(enabled)


class TestStatusCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Pico(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_status_masks_the_key_when_set(self) -> None:
        await self.cog.llm_key.callback(self.cog, self.ctx, "sk-super-secret")

        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        key_field = next(f for f in fields if f.name == "LLM Key")
        self.assertNotIn("sk-super-secret", key_field.value)
        self.assertNotEqual(key_field.value, "sk-super-secret")

    async def test_status_shows_unset_placeholder_when_no_key(self) -> None:
        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        key_field = next(f for f in fields if f.name == "LLM Key")
        self.assertEqual(key_field.value, "*(not set)*")

    async def test_status_shows_guild_enabled_state(self) -> None:
        await self.cog.enabled.callback(self.cog, self.ctx, True)

        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        enabled_field = next(f for f in fields if f.name == "Enabled (this server)")
        self.assertEqual(enabled_field.value, "True")


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Pico(bot=bot)
        self.assertIsNone(bot.get_cog("Corridor"))

        await cog.cog_load()

        self.assertEqual(bot._cog_mgr.find_cog_calls, ["corridor"])
        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertIsNotNone(cog._corridor)

    async def test_package_setup_loads_corridor_before_adding_the_cog(self) -> None:
        bot = FakeBot(preloaded=False)

        await setup(bot)

        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertEqual(len(bot.add_cog_calls), 1)
        self.assertIs(bot.add_cog_calls[0]._corridor, bot.corridor)

    async def test_missing_corridor_reports_a_user_facing_load_error(self) -> None:
        bot = FakeBot(preloaded=False, corridor_installable=False)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await Pico(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Pico(bot=bot)

        await cog.cog_load()

        self.assertIn("pico", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Pico(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("pico", bot.corridor.registered_dependents)
