"""ToolSelectionView / ToolGuildOverrideView button behavior -- needs the
package-root conftest.py's discord/redbot stubs, same reason as
test_cog_commands.py. Exercises button callbacks directly (mutating
service state, then re-rendering) rather than real Discord dispatch, same
approach corridor's/floorplan's own Components V2 UI tests use."""

from __future__ import annotations

import types
import unittest
from typing import Any

import discord

from ..adapters import tool_panel
from ..adapters.tool_candidates import CandidateCommand
from ..adapters.tool_panel import ToolGuildOverrideView, ToolSelectionView
from ..application import NodeService
from ..toolbox import Toolbox
from .conftest import FakeBot
from .test_application_service import FakeNodeInstaller, FakeNodeRepository
from .test_tool_registration_resync import _StubCommand


def _find_button(view: Any, label: str) -> Any:
    (container,) = view.children
    for item in container.children:
        for child in getattr(item, "children", []):
            if getattr(child, "label", None) == label:
                return child
    raise AssertionError(f"no button labelled {label!r} found")


def _make_interaction(bot: FakeBot, *, user_id: int, guild_id: int | None) -> Any:
    guild = types.SimpleNamespace(id=guild_id) if guild_id is not None else None
    user = types.SimpleNamespace(id=user_id)
    return discord.Interaction(guild=guild, user=user, client=bot)


class _PanelTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Toolbox(bot=self.bot)
        self.cog._service = NodeService(FakeNodeRepository(), FakeNodeInstaller())
        await self.cog.cog_load()
        self.bot.cogs["Toolbox"] = self.cog


class TestInteractionCheck(_PanelTestCase):
    async def test_denies_a_user_who_did_not_open_the_panel(self) -> None:
        view = ToolSelectionView([], 0, owner_id=1, enabled_defaults={})
        interaction = _make_interaction(self.bot, user_id=2, guild_id=None)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)

    async def test_allows_the_user_who_opened_the_panel(self) -> None:
        view = ToolSelectionView([], 0, owner_id=1, enabled_defaults={})
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)


class TestToolSelectionViewButtons(_PanelTestCase):
    def _candidate(self, **overrides: Any) -> CandidateCommand:
        defaults: dict[str, Any] = dict(
            qualified_name="toolbox greet",
            tool_name="toolbox_greet",
            short_doc="Greet someone.",
            already_decorated=False,
            selected=False,
        )
        defaults.update(overrides)
        return CandidateCommand(**defaults)

    async def test_select_button_selects_the_command(self) -> None:
        self.bot.walk_commands_result = [_StubCommand(None, qualified_name="toolbox greet")]
        view = ToolSelectionView([self._candidate()], 0, owner_id=1, enabled_defaults={})
        button = _find_button(view, "Select")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        await button.callback(interaction)

        self.assertEqual(await self.cog._tool_selection_service.list_selected(), {"toolbox greet"})

    async def test_deselect_button_deselects_the_command(self) -> None:
        await self.cog.select_tool("toolbox greet")
        self.bot.walk_commands_result = [_StubCommand(None, qualified_name="toolbox greet")]
        view = ToolSelectionView(
            [self._candidate(selected=True)], 0, owner_id=1, enabled_defaults={}
        )
        button = _find_button(view, "Deselect")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        await button.callback(interaction)

        self.assertEqual(await self.cog._tool_selection_service.list_selected(), frozenset())

    async def test_select_button_reports_a_collision_without_selecting(self) -> None:
        corridor = self.bot.corridor
        assert corridor is not None

        class _ExistingTool:
            name = "toolbox_greet"

        corridor.register_tool(_ExistingTool(), owner="SomeoneElse")
        self.bot.walk_commands_result = [_StubCommand(None, qualified_name="toolbox greet")]
        view = ToolSelectionView([self._candidate()], 0, owner_id=1, enabled_defaults={})
        button = _find_button(view, "Select")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        await button.callback(interaction)  # must not raise out of the callback

        self.assertEqual(await self.cog._tool_selection_service.list_selected(), frozenset())
        self.assertTrue(interaction.response.is_done())

    async def test_enabled_toggle_button_flips_the_global_default(self) -> None:
        self.bot.walk_commands_result = [_StubCommand(None, qualified_name="toolbox greet")]
        view = ToolSelectionView(
            [self._candidate(selected=True)], 0, owner_id=1, enabled_defaults={}
        )
        # No explicit default yet, so is_enabled() is True and the button
        # offers the action that flips it off.
        button = _find_button(view, "Disable by default")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        await button.callback(interaction)

        self.assertFalse(await self.cog._tool_visibility_service.is_enabled("toolbox_greet", None))
        refreshed = interaction.last_edited_view
        assert refreshed is not None
        self.assertEqual(refreshed.enabled_defaults, {"toolbox_greet": False})

    async def test_enabled_toggle_button_label_reflects_current_state(self) -> None:
        view = ToolSelectionView(
            [self._candidate(selected=True)],
            0,
            owner_id=1,
            enabled_defaults={"toolbox_greet": False},
        )

        self.assertIsNotNone(_find_button(view, "Enable by default"))

        with self.assertRaises(AssertionError):
            _find_button(view, "Disable by default")

    async def test_an_already_decorated_command_has_no_select_button(self) -> None:
        view = ToolSelectionView(
            [self._candidate(already_decorated=True)], 0, owner_id=1, enabled_defaults={}
        )

        with self.assertRaises(AssertionError):
            _find_button(view, "Select")


class TestPaginationDoesNotReScanCommands(_PanelTestCase):
    """Regression test: pagination and per-row toggles used to rebuild the
    candidate list via list_candidate_commands(bot.walk_commands(), ...),
    passing the *interaction* as ctx -- can_run(ctx) on real discord.py
    needs a commands.Context (.author, ...), not an Interaction (.user),
    so every command's can_run silently raised and got dropped, emptying
    the panel on the very first click. Pagination/toggles must reuse the
    already-computed candidate list instead of re-walking commands at
    all."""

    def _candidates(self) -> list[CandidateCommand]:
        return [
            CandidateCommand(
                qualified_name=f"toolbox cmd{i}",
                tool_name=f"toolbox_cmd{i}",
                short_doc="A command.",
                already_decorated=False,
                selected=False,
            )
            for i in range(tool_panel.PAGE_SIZE + 1)
        ]

    async def test_next_page_still_shows_commands_after_walk_commands_result_is_cleared(
        self,
    ) -> None:
        candidates = self._candidates()
        view = ToolSelectionView(candidates, 0, owner_id=1, enabled_defaults={})
        # If Next secretly re-scanned bot.walk_commands(), this would make
        # the resulting page empty.
        self.bot.walk_commands_result = []
        button = _find_button(view, "Next ▶")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        await button.callback(interaction)

        refreshed = interaction.last_edited_view
        assert refreshed is not None
        self.assertEqual(refreshed.candidates, candidates)
        self.assertEqual(refreshed.page_index, 1)

    async def test_select_button_click_does_not_touch_bot_walk_commands(self) -> None:
        candidates = self._candidates()
        view = ToolSelectionView(candidates, 0, owner_id=1, enabled_defaults={})
        self.bot.walk_commands_result = []  # must not be consulted
        button = _find_button(view, "Select")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=None)

        await button.callback(interaction)  # must not raise / must not empty the list

        refreshed = interaction.last_edited_view
        assert refreshed is not None
        self.assertEqual(len(refreshed.candidates), len(candidates))


class TestToolGuildOverrideViewButtons(_PanelTestCase):
    async def test_toggle_button_sets_a_guild_override(self) -> None:
        # No default/override yet, so effective is True (visible) and the
        # button offers the action that disables it here.
        view = ToolGuildOverrideView(
            ["a_tool"], 0, guild_id=10, admin_id=1, defaults={}, overrides={}
        )
        button = _find_button(view, "Disable here")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=10)

        await button.callback(interaction)

        self.assertEqual(await self.cog._tool_visibility_service.get_override(10, "a_tool"), False)
        refreshed = interaction.last_edited_view
        assert refreshed is not None
        self.assertEqual(refreshed.overrides, {"a_tool": False})

    async def test_toggle_button_label_reflects_current_state(self) -> None:
        view = ToolGuildOverrideView(
            ["a_tool"], 0, guild_id=10, admin_id=1, defaults={}, overrides={"a_tool": False}
        )

        self.assertIsNotNone(_find_button(view, "Enable here"))
        with self.assertRaises(AssertionError):
            _find_button(view, "Disable here")

    async def test_reset_button_is_disabled_without_an_override(self) -> None:
        view = ToolGuildOverrideView(
            ["a_tool"], 0, guild_id=10, admin_id=1, defaults={}, overrides={}
        )

        button = _find_button(view, "Reset to default")

        self.assertTrue(button.disabled)

    async def test_reset_button_clears_the_override(self) -> None:
        await self.cog._tool_visibility_service.set_override(10, "a_tool", False)
        view = ToolGuildOverrideView(
            ["a_tool"], 0, guild_id=10, admin_id=1, defaults={}, overrides={"a_tool": False}
        )
        button = _find_button(view, "Reset to default")
        interaction = _make_interaction(self.bot, user_id=1, guild_id=10)

        await button.callback(interaction)

        self.assertIsNone(await self.cog._tool_visibility_service.get_override(10, "a_tool"))
        refreshed = interaction.last_edited_view
        assert refreshed is not None
        self.assertEqual(refreshed.overrides, {})


if __name__ == "__main__":
    unittest.main()
