"""CogBase.on_cog_add / the cog_load startup catch-up loop: toolbox
re-wrapping selected-but-undecorated commands as other cogs (re)load.

Uses the same FakeBot/FakeCorridor/NodeService fakes test_cog_commands.py
does -- these tests exercise the tool-registration side of cog_load/
on_cog_add, not the Node.js install side already covered there."""

from __future__ import annotations

import unittest
from typing import Any

from ..application import NodeService, ToolSelectionService
from ..toolbox import Toolbox
from .conftest import FakeBot
from .test_application_service import FakeNodeInstaller, FakeNodeRepository
from .test_tool_selection_service import FakeToolSelectionRepository


class _StubCommand:
    def __init__(self, callback: object, *, qualified_name: str) -> None:
        self.callback = callback
        self.qualified_name = qualified_name

    async def can_run(self, ctx: object, *, check_all_parents: bool = False) -> bool:
        return True


async def _greet(cog: Any, ctx: object, name: str) -> None:
    """Greet someone."""


async def _wave(cog: Any, ctx: object, name: str) -> None:
    """Wave at someone."""


class _OtherCog:
    qualified_name = "OtherCog"

    def __init__(self) -> None:
        self.greet_command = _StubCommand(_greet, qualified_name="other greet")


def _make_cog(bot: FakeBot) -> Toolbox:
    cog = Toolbox(bot=bot)
    cog._service = NodeService(FakeNodeRepository(), FakeNodeInstaller())
    return cog


class TestOnCogAdd(unittest.IsolatedAsyncioTestCase):
    async def test_wraps_a_selected_command_from_the_newly_added_cog(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        cog._tool_selection_service = ToolSelectionService(
            FakeToolSelectionRepository(frozenset({"other greet"}))
        )
        await cog.cog_load()

        await cog.on_cog_add(_OtherCog())

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual({tool.name for tool in corridor.list_tools()}, {"other_greet"})

    async def test_ignores_itself(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        cog._tool_selection_service = ToolSelectionService(
            FakeToolSelectionRepository(frozenset({"toolbox greet"}))
        )
        await cog.cog_load()

        await cog.on_cog_add(cog)

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual(corridor.list_tools(), ())

    async def test_a_command_not_in_the_selected_set_is_left_alone(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        await cog.cog_load()

        await cog.on_cog_add(_OtherCog())

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual(corridor.list_tools(), ())

    async def test_a_name_collision_with_a_different_owner_is_skipped_not_raised(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        cog._tool_selection_service = ToolSelectionService(
            FakeToolSelectionRepository(frozenset({"other greet"}))
        )
        await cog.cog_load()
        corridor = bot.corridor
        assert corridor is not None

        class _ExistingTool:
            name = "other_greet"

        corridor.register_tool(_ExistingTool(), owner="SomeoneElse")

        await cog.on_cog_add(_OtherCog())  # must not raise

        owner, _tool = corridor._tools["other_greet"]
        self.assertEqual(owner, "SomeoneElse")


class TestSelectAndDeselectTool(unittest.IsolatedAsyncioTestCase):
    async def test_select_tool_makes_it_usable_immediately(self) -> None:
        other = _OtherCog()
        bot = FakeBot(cogs={"OtherCog": other})
        cog = _make_cog(bot)
        await cog.cog_load()

        await cog.select_tool("other greet")

        self.assertEqual(await cog._tool_selection_service.list_selected(), {"other greet"})
        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual({tool.name for tool in corridor.list_tools()}, {"other_greet"})

    async def test_select_tool_before_the_owning_cog_loads_is_picked_up_later(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        await cog.cog_load()

        await cog.select_tool("other greet")
        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual(corridor.list_tools(), ())

        await cog.on_cog_add(_OtherCog())

        self.assertEqual({tool.name for tool in corridor.list_tools()}, {"other_greet"})

    async def test_deselect_tool_removes_it_from_the_selection(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        await cog.cog_load()
        await cog.select_tool("other greet")

        await cog.deselect_tool("other greet")

        self.assertEqual(await cog._tool_selection_service.list_selected(), frozenset())

    async def test_deselect_tool_removes_it_from_corridor_immediately(self) -> None:
        other = _OtherCog()
        bot = FakeBot(cogs={"OtherCog": other})
        cog = _make_cog(bot)
        await cog.cog_load()
        await cog.select_tool("other greet")

        await cog.deselect_tool("other greet")

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual(corridor.list_tools(), ())

    async def test_deselecting_one_tool_does_not_touch_a_sibling_from_the_same_cog(self) -> None:
        class _CogWithTwoCommands:
            qualified_name = "OtherCog"

            def __init__(self) -> None:
                self.greet_command = _StubCommand(_greet, qualified_name="other greet")
                self.wave_command = _StubCommand(_wave, qualified_name="other wave")

        other = _CogWithTwoCommands()
        bot = FakeBot(cogs={"OtherCog": other})
        cog = _make_cog(bot)
        await cog.cog_load()
        await cog.select_tool("other greet")
        await cog.select_tool("other wave")

        await cog.deselect_tool("other greet")

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual({tool.name for tool in corridor.list_tools()}, {"other_wave"})

    async def test_select_tool_raises_and_does_not_select_on_a_name_collision(self) -> None:
        other = _OtherCog()
        bot = FakeBot(cogs={"OtherCog": other})
        cog = _make_cog(bot)
        await cog.cog_load()
        corridor = bot.corridor
        assert corridor is not None

        class _ExistingTool:
            name = "other_greet"

        corridor.register_tool(_ExistingTool(), owner="SomeoneElse")

        with self.assertRaises(ValueError):
            await cog.select_tool("other greet")

        self.assertEqual(await cog._tool_selection_service.list_selected(), frozenset())

    async def test_deselect_tool_before_corridor_is_loaded_does_not_raise(self) -> None:
        bot = FakeBot(preloaded=False, corridor_installable=False)
        cog = Toolbox(bot=bot)

        await cog.deselect_tool("never selected")  # must not raise


class TestCogLoadCatchesUpAlreadyLoadedCogs(unittest.IsolatedAsyncioTestCase):
    async def test_cog_load_resyncs_every_cog_already_on_the_bot(self) -> None:
        other = _OtherCog()
        bot = FakeBot(cogs={"OtherCog": other})
        cog = _make_cog(bot)
        cog._tool_selection_service = ToolSelectionService(
            FakeToolSelectionRepository(frozenset({"other greet"}))
        )

        await cog.cog_load()

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual({tool.name for tool in corridor.list_tools()}, {"other_greet"})

    async def test_cog_load_does_not_resync_itself(self) -> None:
        bot = FakeBot()
        cog = _make_cog(bot)
        bot.cogs["Toolbox"] = cog
        cog._tool_selection_service = ToolSelectionService(
            FakeToolSelectionRepository(frozenset({"toolbox greet"}))
        )

        await cog.cog_load()  # must not raise / double-resync itself

        corridor = bot.corridor
        assert corridor is not None
        self.assertEqual(corridor.list_tools(), ())


if __name__ == "__main__":
    unittest.main()
