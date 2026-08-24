"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py).

Commands are exercised against a fake Clock (the same FakeClock
application-layer tests use), never the real system clock -- a fixed
instant is what makes the expected `<t:...>` markup and formatted strings
assertable at all."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from redbot.core.errors import CogLoadError

from corridor.adapters.llm_tool_registration import collect_registered_tools
from corridor.domain import EMPLOYEE_KEY, llm_tool_spec

from .. import setup
from ..adapters import commands as commands_adapter
from ..application import TimeService
from ..deskutils import Deskutils
from .conftest import FakeBot, FakeChannel, FakeContext, FakeCorridor, FakePermissions
from .test_application_service import FIXED_INSTANT, FakeClock

EPOCH = int(FIXED_INSTANT.timestamp())


def _quoted_message(
    ctx: FakeContext,
    *,
    content: str = "A useful message",
    guild: object | None = None,
    channel: FakeChannel | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=987654321012345678,
        guild=guild if guild is not None else ctx.guild,
        channel=channel or ctx.channel,
        author=SimpleNamespace(id=42, display_name="Linley"),
        content=content,
        jump_url="https://discord.com/channels/12345/456/987654321012345678",
    )


class TestCountCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Deskutils(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_count_replies_with_character_and_word_totals(self) -> None:
        result = await self.cog.count_command.callback(self.cog, self.ctx, text="one  two\nthree")

        reply = self.bot.corridor.replies[0]
        self.assertEqual(reply["title"], "Text count")
        self.assertEqual(
            [(field.name, field.value) for field in reply["fields"]],
            [
                ("Characters", "14"),
                ("Words", "3"),
            ],
        )
        self.assertEqual(result, {"status": "ok", "characters": 14, "words": 3})

    async def test_count_rejects_non_string_raw_tool_input(self) -> None:
        result = await self.cog.count_command.callback(
            self.cog,
            self.ctx,
            text=12,  # type: ignore[arg-type]
        )

        self.assertEqual(result["error"], "invalid_text")
        self.assertEqual(self.bot.corridor.replies[0]["title"], "Text count")


class TestQuoteCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Deskutils(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_quote_uses_the_replied_to_message(self) -> None:
        target = _quoted_message(self.ctx)
        self.ctx.message.reference = SimpleNamespace(
            message_id=target.id,
            channel_id=target.channel.id,
            resolved=target,
        )

        result = await self.cog.quote_command.callback(self.cog, self.ctx)

        reply = self.bot.corridor.replies[0]
        self.assertEqual(reply["title"], "Quoted message")
        self.assertEqual(reply["description"], ">>> A useful message")
        self.assertEqual(
            [(field.name, field.value) for field in reply["fields"]],
            [
                ("Author", "Linley"),
                (
                    "Source",
                    "[Jump to message](https://discord.com/channels/12345/456/987654321012345678)",
                ),
            ],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "A useful message")

    async def test_quote_resolves_an_explicit_message_link(self) -> None:
        target = _quoted_message(self.ctx)
        converter = SimpleNamespace(convert=AsyncMock(return_value=target))

        with patch.object(commands_adapter.commands, "MessageConverter", return_value=converter):
            result = await self.cog.quote_command.callback(
                self.cog,
                self.ctx,
                "https://discord.com/channels/12345/456/987654321012345678",
            )

        self.assertEqual(result["message_id"], target.id)
        converter.convert.assert_awaited_once()

    async def test_quote_requires_a_reply_or_link(self) -> None:
        result = await self.cog.quote_command.callback(self.cog, self.ctx)

        self.assertEqual(result["error"], "message_required")

    async def test_quote_rejects_non_string_raw_tool_input(self) -> None:
        result = await self.cog.quote_command.callback(
            self.cog,
            self.ctx,
            12,  # type: ignore[arg-type]
        )

        self.assertEqual(result["error"], "invalid_message")

    async def test_quote_rejects_cross_guild_or_unreadable_messages(self) -> None:
        cross_guild = _quoted_message(self.ctx, guild=SimpleNamespace(id=999))
        self.ctx.message.reference = SimpleNamespace(
            message_id=cross_guild.id,
            channel_id=cross_guild.channel.id,
            resolved=cross_guild,
        )

        cross_guild_result = await self.cog.quote_command.callback(self.cog, self.ctx)

        self.assertEqual(cross_guild_result["error"], "message_not_accessible")

        unreadable_channel = FakeChannel(permissions=FakePermissions(view_channel=False))
        unreadable = _quoted_message(self.ctx, channel=unreadable_channel)
        self.ctx.message.reference = SimpleNamespace(
            message_id=unreadable.id,
            channel_id=unreadable.channel.id,
            resolved=unreadable,
        )

        unreadable_result = await self.cog.quote_command.callback(self.cog, self.ctx)

        self.assertEqual(unreadable_result["error"], "message_not_accessible")

    async def test_quote_rejects_deleted_and_textless_messages(self) -> None:
        self.ctx.message.reference = SimpleNamespace(
            message_id=987654321012345678,
            channel_id=self.ctx.channel.id,
            resolved=None,
        )
        converter = SimpleNamespace(convert=AsyncMock(side_effect=RuntimeError("deleted")))

        with patch.object(commands_adapter.commands, "MessageConverter", return_value=converter):
            deleted_result = await self.cog.quote_command.callback(self.cog, self.ctx)

        self.assertEqual(deleted_result["error"], "message_not_found")

        textless = _quoted_message(self.ctx, content="  \n")
        self.ctx.message.reference = SimpleNamespace(
            message_id=textless.id,
            channel_id=textless.channel.id,
            resolved=textless,
        )

        textless_result = await self.cog.quote_command.callback(self.cog, self.ctx)

        self.assertEqual(textless_result["error"], "empty_message")

    async def test_quote_escapes_mentions_and_truncates_only_the_rendered_text(self) -> None:
        content = "@everyone " + "x" * 1_800
        target = _quoted_message(self.ctx, content=content)
        self.ctx.message.reference = SimpleNamespace(
            message_id=target.id,
            channel_id=target.channel.id,
            resolved=target,
        )

        result = await self.cog.quote_command.callback(self.cog, self.ctx)

        rendered = self.bot.corridor.replies[0]["description"]
        self.assertIn("@\u200beveryone", rendered)
        self.assertTrue(rendered.endswith("…"))
        self.assertLessEqual(len(rendered.removeprefix(">>> ")), 1_750)
        self.assertEqual(result["content"], content)


class TestTimeCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Deskutils(bot=self.bot)
        self.cog._service = TimeService(FakeClock())
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_time_shows_discord_markup_and_utc(self) -> None:
        result = await self.cog.time_command.callback(self.cog, self.ctx, None)

        self.assertEqual(len(self.bot.corridor.replies), 1)
        reply = self.bot.corridor.replies[0]
        self.assertEqual(reply["title"], "Current time")
        self.assertEqual(
            reply["fields"][0].value,
            f"<t:{EPOCH}:F> (<t:{EPOCH}:R>)",
        )
        self.assertEqual(reply["fields"][1].value, "2026-08-23 12:30:00 UTC")
        self.assertEqual(
            result,
            {
                "status": "ok",
                "epoch_seconds": EPOCH,
                "utc": "2026-08-23 12:30:00 UTC",
                "discord_timestamp": f"<t:{EPOCH}:F> (<t:{EPOCH}:R>)",
            },
        )

    async def test_time_checks_employee_permission(self) -> None:
        await self.cog.time_command.callback(self.cog, self.ctx, None)

        self.assertEqual(self.bot.corridor.permission_checks, ["employee"])

    async def test_time_is_blocked_when_corridor_denies_permission(self) -> None:
        self.bot.corridor = FakeCorridor(allow_permission=False)
        await self.cog.cog_load()

        result = await self.cog.time_command.callback(self.cog, self.ctx, None)

        self.assertEqual(self.bot.corridor.replies, [])
        self.assertEqual(
            result,
            {
                "status": "error",
                "error": "permission_denied",
                "message": "The invoking member does not have permission to use this tool.",
            },
        )

    async def test_time_with_a_valid_zone_adds_a_localized_field(self) -> None:
        result = await self.cog.time_command.callback(self.cog, self.ctx, "America/New_York")

        reply = self.bot.corridor.replies[0]
        self.assertEqual(len(reply["fields"]), 3)
        zone_field = reply["fields"][2]
        self.assertEqual(zone_field.name, "America/New_York")
        self.assertEqual(zone_field.value, "2026-08-23 08:30:00 EDT")
        self.assertEqual(result["timezone"], "America/New_York")
        self.assertEqual(result["localized"], "2026-08-23 08:30:00 EDT")

    async def test_time_with_an_unknown_zone_replies_with_a_warning_instead(self) -> None:
        result = await self.cog.time_command.callback(self.cog, self.ctx, "Not/A_Real_Zone")

        self.assertEqual(len(self.bot.corridor.replies), 1)
        reply = self.bot.corridor.replies[0]
        self.assertEqual(reply["title"], "deskutils")
        self.assertIn("Unknown time zone", reply["description"])
        self.assertIn("Not/A_Real_Zone", reply["description"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "unknown_timezone")
        self.assertEqual(result["timezone"], "Not/A_Real_Zone")
        self.assertIn("Unknown time zone", result["message"])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Deskutils(bot=bot)
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
            await Deskutils(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)

        await cog.cog_load()

        self.assertIn("deskutils", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("deskutils", bot.corridor.registered_dependents)


class TestTimeCommandIsAnLLMTool(unittest.TestCase):
    """`time_command` carries `@llm_tool(...)` directly -- this is
    deskutils' own regression guard that the decoration is correct,
    without needing corridor's adapter-layer scanner (that's covered by
    corridor's own test suite)."""

    def test_decoration_matches_the_commands_own_permission_tier(self) -> None:
        spec = llm_tool_spec(Deskutils.time_command.callback)

        assert spec is not None
        self.assertEqual(spec.name, "deskutils_time")
        self.assertEqual(spec.required_group, EMPLOYEE_KEY)
        self.assertEqual(
            spec.parameters,
            {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": (
                            "An IANA time zone name, e.g. 'America/New_York' or 'Europe/London'."
                        ),
                    }
                },
                "required": [],
            },
        )


class TestInferredLLMTools(unittest.IsolatedAsyncioTestCase):
    def test_count_and_quote_omit_all_decorator_metadata(self) -> None:
        count_spec = llm_tool_spec(Deskutils.count_command.callback)
        quote_spec = llm_tool_spec(Deskutils.quote_command.callback)

        assert count_spec is not None
        assert quote_spec is not None
        self.assertIsNone(count_spec.name)
        self.assertIsNone(count_spec.required_group)
        self.assertEqual(
            count_spec.description,
            "Count all characters and whitespace-delimited words in text.",
        )
        self.assertEqual(
            count_spec.parameters,
            {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "value for text"}},
                "required": ["text"],
            },
        )
        self.assertIsNone(quote_spec.name)
        self.assertIsNone(quote_spec.required_group)
        self.assertEqual(
            quote_spec.description,
            "Quote a replied-to Discord message or one identified by a message link.",
        )
        self.assertEqual(
            quote_spec.parameters,
            {
                "type": "object",
                "properties": {
                    "message_link": {
                        "type": "string",
                        "description": "value for message_link",
                    }
                },
                "required": [],
            },
        )

    async def test_registration_infers_names_and_native_command_availability(self) -> None:
        cog = Deskutils(bot=FakeBot())
        tools = {tool.name: tool for tool in collect_registered_tools(cog)}

        self.assertEqual(set(tools), {"deskutils_time", "deskutils_count", "deskutils_quote"})
        assert tools["deskutils_count"].availability_check is not None
        assert tools["deskutils_quote"].availability_check is not None
        guild_ctx = FakeContext()
        dm_ctx = FakeContext(guild_id=None)
        self.assertTrue(await tools["deskutils_count"].availability_check(guild_ctx))
        self.assertTrue(await tools["deskutils_count"].availability_check(dm_ctx))
        self.assertTrue(await tools["deskutils_quote"].availability_check(guild_ctx))
        self.assertFalse(await tools["deskutils_quote"].availability_check(dm_ctx))


class TestToolRegistration(unittest.IsolatedAsyncioTestCase):
    """Deskutils' `@llm_tool` decorations are scanned and registered
    into corridor's cross-cog tool registry at cog_load -- inert unless
    something (pico) reads corridor's registry, but always
    registered/unregistered in step with the cog's own lifecycle
    regardless of whether anything ever does. What `register_llm_tools`
    actually does with a decorated command is corridor's own concern
    (corridor/tests/test_llm_tool_registration.py), not duplicated here --
    this only verifies deskutils asks for it correctly."""

    async def test_cog_load_registers_the_llm_tools(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)

        await cog.cog_load()

        self.assertEqual(bot.corridor.registered_llm_tools_calls, [(cog, "Deskutils")])

    async def test_cog_unload_unregisters_the_time_tool(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertIn("Deskutils", bot.corridor.unregistered_tool_owners)
