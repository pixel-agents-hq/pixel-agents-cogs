"""Smoke + behavior tests for the three-step publish UI. Deep event-building
logic is covered by test_event_builder.py -- this layer only checks the
right components get built at each step and the right calls happen when
they're used, mirroring corridor/tests/test_settings_ui.py's style."""

from __future__ import annotations

import unittest

import discord

from corridor import ui_limits
from corridor.domain import AgentHighlighted, AgentPresenceChanged, AgentRef
from corridor.testing import shown_modal

from ..adapters.views import EventDetailView, EventPickerView
from ..application import list_publishable_events
from .conftest import FakeBot, FakeGuild, FakeMember


def _first(root: object, predicate: object) -> object:
    for node in ui_limits.iter_ui_tree(root):
        if predicate(node):  # type: ignore[operator]
            return node
    raise AssertionError(f"no matching component found in {root!r}")


def _is_event_select(node: object) -> bool:
    return isinstance(node, discord.ui.Select) and hasattr(node, "options")


def _is_user_select(node: object) -> bool:
    return isinstance(node, discord.ui.UserSelect)


def _is_button(node: object) -> bool:
    return isinstance(node, discord.ui.Button)


def _spec(name: str) -> object:
    return next(spec for spec in list_publishable_events() if spec.name == name)


class TestEventPickerView(unittest.TestCase):
    def test_one_option_per_publishable_event(self) -> None:
        view = EventPickerView()
        select = _first(view, _is_event_select)

        names = {spec.name for spec in list_publishable_events()}
        self.assertEqual({option.value for option in select.options}, names)  # type: ignore[attr-defined]


class TestEventPickerSelectCallback(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_an_event_shows_its_detail_view(self) -> None:
        bot = FakeBot()
        interaction = discord.Interaction(guild=FakeGuild(100), client=bot)
        view = EventPickerView()
        select = _first(view, _is_event_select)
        select.values = ["AgentReplied"]  # type: ignore[attr-defined]

        await select.callback(interaction)  # type: ignore[misc]

        self.assertIsInstance(interaction.last_edited_view, EventDetailView)


class TestEventDetailViewConstruction(unittest.TestCase):
    def test_agent_replied_has_one_user_select_and_no_literal_select(self) -> None:
        view = EventDetailView(_spec("AgentReplied"))

        user_selects = [n for n in ui_limits.iter_ui_tree(view) if _is_user_select(n)]
        literal_selects = [
            n
            for n in ui_limits.iter_ui_tree(view)
            if _is_event_select(n) and not _is_user_select(n)
        ]
        self.assertEqual(len(user_selects), 1)
        self.assertEqual(literal_selects, [])

    def test_agent_presence_changed_has_a_user_select_and_a_literal_select(self) -> None:
        view = EventDetailView(_spec("AgentPresenceChanged"))

        user_selects = [n for n in ui_limits.iter_ui_tree(view) if _is_user_select(n)]
        literal_selects = [
            n
            for n in ui_limits.iter_ui_tree(view)
            if _is_event_select(n) and not _is_user_select(n)
        ]
        self.assertEqual(len(user_selects), 1)
        self.assertEqual(len(literal_selects), 1)
        self.assertEqual(
            {opt.value for opt in literal_selects[0].options},
            {"online", "idle", "dnd", "offline"},
        )

    def test_every_real_event_constructs_without_error(self) -> None:
        for spec in list_publishable_events():
            with self.subTest(event=spec.name):
                view = EventDetailView(spec)
                self.assertTrue(view.children)


class TestPublishButton(unittest.IsolatedAsyncioTestCase):
    async def test_missing_required_user_select_is_rejected(self) -> None:
        bot = FakeBot()
        interaction = discord.Interaction(guild=FakeGuild(100), client=bot)
        view = EventDetailView(_spec("AgentReplied"))
        button = _first(view, _is_button)

        await button.callback(interaction)  # type: ignore[misc]

        self.assertTrue(interaction.response.is_done())
        self.assertEqual(bot.corridor.published, [])

    async def test_zero_scalar_field_event_publishes_directly_without_a_modal(self) -> None:
        bot = FakeBot()
        interaction = discord.Interaction(guild=FakeGuild(100), client=bot)
        view = EventDetailView(_spec("AgentHighlighted"))
        user_select = _first(view, _is_user_select)
        user_select.values = [FakeMember(1, is_bot=False)]  # type: ignore[attr-defined]
        button = _first(view, _is_button)

        await button.callback(interaction)  # type: ignore[misc]

        self.assertIsNone(shown_modal(interaction))
        self.assertEqual(
            bot.corridor.published,
            [AgentHighlighted(agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False))],
        )

    async def test_event_with_scalar_fields_opens_a_modal(self) -> None:
        bot = FakeBot()
        interaction = discord.Interaction(guild=FakeGuild(100), client=bot)
        view = EventDetailView(_spec("AgentReplied"))
        user_select = _first(view, _is_user_select)
        user_select.values = [FakeMember(1, is_bot=False)]  # type: ignore[attr-defined]
        button = _first(view, _is_button)

        await button.callback(interaction)  # type: ignore[misc]

        modal = shown_modal(interaction)
        self.assertIsNotNone(modal)
        self.assertEqual(bot.corridor.published, [])


class TestModalSubmit(unittest.IsolatedAsyncioTestCase):
    async def test_submitting_publishes_the_constructed_event(self) -> None:
        bot = FakeBot()
        interaction = discord.Interaction(guild=FakeGuild(100), client=bot)
        view = EventDetailView(_spec("AgentPresenceChanged"))
        user_select = _first(view, _is_user_select)
        user_select.values = [FakeMember(7, is_bot=True)]  # type: ignore[attr-defined]
        literal_select = _first(view, lambda n: _is_event_select(n) and not _is_user_select(n))
        literal_select.values = ["dnd"]  # type: ignore[attr-defined]
        button = _first(view, _is_button)
        await button.callback(interaction)  # type: ignore[misc]
        modal = shown_modal(interaction)

        for text_input in modal.children:  # type: ignore[attr-defined]
            text_input.value = "Tin"

        submit_interaction = discord.Interaction(guild=FakeGuild(100), client=bot)
        await modal.on_submit(submit_interaction)  # type: ignore[attr-defined]

        self.assertEqual(
            bot.corridor.published,
            [
                AgentPresenceChanged(
                    agent=AgentRef(discord_user_id=7, guild_id=100, is_bot=True),
                    display_name="Tin",
                    status="dnd",
                    activities=(),
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
