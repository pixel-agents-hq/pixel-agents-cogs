"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py)."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from corridor.domain import EMPLOYEE_KEY, llm_tool_spec

from .. import setup
from ..{{cookiecutter.cog_name}} import {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}
from .conftest import FakeBot, FakeContext, FakeCorridor


class TestCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_count_reports_zero_initially(self) -> None:
        await self.cog.count.callback(self.cog, self.ctx)

        self.assertEqual(
            self.bot.corridor.replies, [{"title": "Count", "description": "0", "content": None}]
        )

    async def test_bump_increments_and_replies_through_corridor(self) -> None:
        first = await self.cog.bump.callback(self.cog, self.ctx)
        second = await self.cog.bump.callback(self.cog, self.ctx)

        descriptions = [reply["description"] for reply in self.bot.corridor.replies]
        self.assertEqual(descriptions, ["Now: 1", "Now: 2"])
        self.assertEqual(first, {"status": "ok", "count": 1})
        self.assertEqual(second, {"status": "ok", "count": 2})

    async def test_bump_checks_keyholder_permission(self) -> None:
        await self.cog.bump.callback(self.cog, self.ctx)

        self.assertEqual(self.bot.corridor.permission_checks, ["keyholder"])

    async def test_bump_is_blocked_when_corridor_denies_permission(self) -> None:
        self.bot.corridor = FakeCorridor(allow_permission=False)
        await self.cog.cog_load()

        result = await self.cog.bump.callback(self.cog, self.ctx)

        self.assertEqual(self.bot.corridor.replies, [])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "permission_denied")

    async def test_project_returns_a_bounded_numeric_projection_without_mutating(self) -> None:
        result = await self.cog.project.callback(self.cog, self.ctx, 3)

        self.assertEqual(
            result,
            {
                "status": "ok",
                "current_count": 0,
                "amount": 3,
                "projected_count": 3,
            },
        )
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Count projection")
        self.assertEqual(self.bot.corridor.replies[-1]["description"], "Current: 0; after 3: 3")
        snapshot = await self.cog._service.show(self.ctx.guild.id)
        self.assertEqual(snapshot.count, 0)

    async def test_project_rejects_a_raw_non_integer_tool_argument(self) -> None:
        result = await self.cog.project.callback(self.cog, self.ctx, "3")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "invalid_amount")

    async def test_project_enforces_its_advertised_bounds_at_runtime(self) -> None:
        result = await self.cog.project.callback(self.cog, self.ctx, 11)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "invalid_amount")
        self.assertIn("1 through 10", self.bot.corridor.replies[-1]["description"])

    async def test_report_supports_an_enum_constrained_style(self) -> None:
        result = await self.cog.report.callback(self.cog, self.ctx, "detailed")

        self.assertEqual(
            result,
            {"status": "ok", "guild_id": self.ctx.guild.id, "count": 0, "style": "detailed"},
        )
        self.assertIn(
            f"Server {self.ctx.guild.id} currently has a count of 0",
            self.bot.corridor.replies[-1]["description"],
        )

    async def test_report_rejects_a_style_outside_its_advertised_enum(self) -> None:
        result = await self.cog.report.callback(self.cog, self.ctx, "verbose")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "invalid_style")
        self.assertIn("compact", self.bot.corridor.replies[-1]["description"])

    async def test_read_only_tools_check_employee_permission(self) -> None:
        await self.cog.project.callback(self.cog, self.ctx, 1)
        await self.cog.report.callback(self.cog, self.ctx, "compact")

        self.assertEqual(self.bot.corridor.permission_checks, [EMPLOYEE_KEY, EMPLOYEE_KEY])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)
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
            await {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)

        await cog.cog_load()

        self.assertIn("{{cookiecutter.cog_name}}", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("{{cookiecutter.cog_name}}", bot.corridor.registered_dependents)


class TestCommandsAreLLMTools(unittest.TestCase):
    """The example commands carry `@llm_tool(...)` directly -- this cog's own
    regression guard that the decoration is correct, without needing
    corridor's adapter-layer scanner (that's covered by corridor's own
    test suite)."""

    def test_bump_has_no_input_and_uses_the_keyholder_tier(self) -> None:
        spec = llm_tool_spec({{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}.bump.callback)

        assert spec is not None
        self.assertEqual(spec.name, "{{cookiecutter.cog_name}}_bump")
        self.assertEqual(spec.required_group, "keyholder")
        self.assertEqual(spec.parameters, {"type": "object", "properties": {}, "required": []})

    def test_project_has_a_required_bounded_integer(self) -> None:
        spec = llm_tool_spec({{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}.project.callback)

        assert spec is not None
        self.assertEqual(spec.name, "{{cookiecutter.cog_name}}_project")
        self.assertEqual(spec.required_group, EMPLOYEE_KEY)
        self.assertEqual(
            spec.parameters,
            {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "integer",
                        "description": "The number of increments to project.",
                        "minimum": 1,
                        "maximum": 10,
                    }
                },
                "required": ["amount"],
            },
        )

    def test_report_has_an_optional_string_enum(self) -> None:
        spec = llm_tool_spec({{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}.report.callback)

        assert spec is not None
        self.assertEqual(spec.name, "{{cookiecutter.cog_name}}_report")
        self.assertEqual(spec.required_group, EMPLOYEE_KEY)
        self.assertEqual(
            spec.parameters,
            {
                "type": "object",
                "properties": {
                    "style": {
                        "type": "string",
                        "description": "How much context to include.",
                        "enum": ["compact", "detailed"],
                    }
                },
                "required": [],
            },
        )


class TestToolRegistration(unittest.IsolatedAsyncioTestCase):
    """The commands' `@llm_tool` decorations are scanned and registered into
    corridor's cross-cog tool registry at cog_load -- inert unless
    something (pico) reads corridor's registry, but always
    registered/unregistered in step with the cog's own lifecycle
    regardless of whether anything ever does. What `register_llm_tools`
    actually does with a decorated command is corridor's own concern, not
    duplicated here -- this only verifies this cog asks for it correctly."""

    async def test_cog_load_registers_the_llm_tools(self) -> None:
        bot = FakeBot()
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)

        await cog.cog_load()

        self.assertEqual(
            bot.corridor.registered_llm_tools_calls,
            [(cog, "{{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}")],
        )

    async def test_cog_unload_unregisters_the_llm_tools(self) -> None:
        bot = FakeBot()
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertIn(
            "{{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}",
            bot.corridor.unregistered_tool_owners,
        )
