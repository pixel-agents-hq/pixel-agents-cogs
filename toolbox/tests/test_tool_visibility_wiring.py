"""CogBase installs its ToolVisibilityService as corridor's one visibility
filter at cog_load, and removes it at cog_unload -- see
docs/toolbox-command-tool-toggle-design.md."""

from __future__ import annotations

import unittest
from typing import Any

from ..application import NodeService
from ..toolbox import Toolbox
from .conftest import FakeBot, FakeContext
from .test_application_service import FakeNodeInstaller, FakeNodeRepository


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _make_cog(bot: FakeBot) -> Toolbox:
    cog = Toolbox(bot=bot)
    cog._service = NodeService(FakeNodeRepository(), FakeNodeInstaller())
    return cog


class TestVisibilityFilterInstallation(unittest.IsolatedAsyncioTestCase):
    async def test_cog_load_installs_the_filter_under_the_toolbox_owner(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)

        await cog.cog_load()

        corridor = bot.corridor
        assert corridor is not None
        self.assertIn("Toolbox", corridor.visibility_filters)

    async def test_cog_unload_removes_the_filter(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        await cog.cog_load()

        await cog.cog_unload()

        corridor = bot.corridor
        assert corridor is not None
        self.assertNotIn("Toolbox", corridor.visibility_filters)


class TestIsToolVisible(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = _make_cog(self.bot)
        await self.cog.cog_load()

    async def test_a_tool_with_no_configured_state_is_visible(self) -> None:
        visible = await self.cog._is_tool_visible(FakeContext(guild_id=10), _Tool("a_tool"))

        self.assertTrue(visible)

    async def test_a_disabled_default_hides_it_everywhere(self) -> None:
        await self.cog._tool_visibility_service.set_default("a_tool", False)

        self.assertFalse(await self.cog._is_tool_visible(FakeContext(guild_id=10), _Tool("a_tool")))
        self.assertFalse(
            await self.cog._is_tool_visible(FakeContext(guild_id=None), _Tool("a_tool"))
        )

    async def test_a_guild_override_wins_over_the_disabled_default(self) -> None:
        await self.cog._tool_visibility_service.set_default("a_tool", False)
        await self.cog._tool_visibility_service.set_override(10, "a_tool", True)

        self.assertTrue(await self.cog._is_tool_visible(FakeContext(guild_id=10), _Tool("a_tool")))
        self.assertFalse(await self.cog._is_tool_visible(FakeContext(guild_id=20), _Tool("a_tool")))

    async def test_a_dm_context_never_consults_a_guild_override(self) -> None:
        await self.cog._tool_visibility_service.set_override(10, "a_tool", False)

        self.assertTrue(
            await self.cog._is_tool_visible(FakeContext(guild_id=None), _Tool("a_tool"))
        )


class TestVisibilityFilterEndToEnd(unittest.IsolatedAsyncioTestCase):
    """The filter installed at cog_load actually gates corridor's own
    list_tools_for once invoked -- exercised through FakeCorridor's
    recorded predicate, not a real ToolRegistryService (that round trip is
    corridor's own test suite's job, see test_cog_api.py)."""

    async def test_the_installed_predicate_is_this_cogs_own_is_tool_visible(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        await cog.cog_load()
        await cog._tool_visibility_service.set_default("a_tool", False)

        corridor = bot.corridor
        assert corridor is not None
        predicate: Any = corridor.visibility_filters["Toolbox"]

        self.assertFalse(await predicate(FakeContext(guild_id=10), _Tool("a_tool")))


if __name__ == "__main__":
    unittest.main()
