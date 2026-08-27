"""corridor's own Discord gateway listeners: moved here from floorplan's
`discord_gateway.py` (see docs/corridor-pubsub-design.md). Publishes
unconditionally -- no guild-enabled/include_bots/office-tracking/
broadcast-toggle gating, unlike floorplan's old version of this module.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

import discord  # stubbed by ../conftest.py

from ..corridor import Corridor
from ..domain import AgentPresenceChanged, AgentReplied
from .conftest import FakeBot


def _activity(activity_type: object, name: str = "Some Game") -> MagicMock:
    a = MagicMock()
    a.type = activity_type
    a.name = name
    return a


def _member(
    guild_id: int = 100,
    user_id: int = 1,
    display_name: str = "Tin",
    status: str = "online",
    is_bot: bool = False,
    activities: tuple = (),
) -> MagicMock:
    m = MagicMock()
    m.guild.id = guild_id
    m.id = user_id
    m.display_name = display_name
    m.status = status
    m.bot = is_bot
    m.activities = list(activities)
    return m


def _recorder(sink: list) -> object:
    async def handler(event: object) -> None:
        sink.append(event)

    return handler


def _make_cog() -> Corridor:
    bot = FakeBot()
    bot.is_owner = AsyncMock(return_value=False)
    bot.user.id = 999  # distinct from every test member's user_id
    return Corridor(bot=bot)


class TestMemberUpdateListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        self.cog.subscribe_event(
            AgentPresenceChanged, _recorder(self.published), owner="test"
        )

    async def test_name_change_publishes_presence_changed(self) -> None:
        before = _member(display_name="Old")
        after = _member(display_name="New")
        await self.cog.on_member_update(before, after)
        self.assertEqual(len(self.published), 1)
        event = self.published[0]
        self.assertIsInstance(event, AgentPresenceChanged)
        self.assertEqual(event.display_name, "New")

    async def test_no_name_change_skips(self) -> None:
        before = _member(display_name="Same", status="online")
        after = _member(display_name="Same", status="dnd")
        await self.cog.on_member_update(before, after)
        self.assertEqual(self.published, [])


class TestPresenceUpdateListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        self.cog.subscribe_event(
            AgentPresenceChanged, _recorder(self.published), owner="test"
        )

    async def test_status_change_publishes_presence_changed(self) -> None:
        before = _member(status="online")
        after = _member(status="idle")
        await self.cog.on_presence_update(before, after)
        self.assertEqual(len(self.published), 1)
        self.assertIsInstance(self.published[0], AgentPresenceChanged)

    async def test_activity_change_publishes_presence_changed(self) -> None:
        before = _member(activities=())
        after = _member(activities=(_activity(discord.ActivityType.playing),))
        await self.cog.on_presence_update(before, after)
        self.assertEqual(len(self.published), 1)

    async def test_no_change_skips(self) -> None:
        before = _member(status="online", activities=())
        after = _member(status="online", activities=())
        await self.cog.on_presence_update(before, after)
        self.assertEqual(self.published, [])


class TestMemberJoinListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        self.cog.subscribe_event(
            AgentPresenceChanged, _recorder(self.published), owner="test"
        )

    async def test_publishes_presence_changed(self) -> None:
        m = _member(status="online")
        await self.cog.on_member_join(m)
        self.assertEqual(len(self.published), 1)
        self.assertIsInstance(self.published[0], AgentPresenceChanged)

    async def test_publishes_even_without_a_known_status(self) -> None:
        """Unlike floorplan's old listener, corridor publishes
        unconditionally -- a member with no resolvable presence status
        still gets an event (falling back to "offline"); filtering that
        out, if desired, is now a subscriber's own concern."""

        m = _member(status="offline")
        await self.cog.on_member_join(m)
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0].status, "offline")


class TestMemberRemoveListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        self.cog.subscribe_event(
            AgentPresenceChanged, _recorder(self.published), owner="test"
        )

    async def test_remove_publishes_offline_presence_changed(self) -> None:
        m = _member(guild_id=100, user_id=42)
        await self.cog.on_member_remove(m)
        self.assertEqual(len(self.published), 1)
        event = self.published[0]
        self.assertIsInstance(event, AgentPresenceChanged)
        self.assertEqual(event.status, "offline")
        self.assertEqual(event.agent.discord_user_id, 42)
        self.assertEqual(event.agent.guild_id, 100)


class TestOnMessage(unittest.IsolatedAsyncioTestCase):
    """What on_message publishes -- what a subscriber does with an
    AgentReplied is each subscriber's own concern."""

    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        self.cog.subscribe_event(AgentReplied, _recorder(self.published), owner="test")

    async def test_message_publishes_agent_replied(self) -> None:
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.author.bot = False
        msg.content = "Hello world"
        msg.clean_content = "Hello world"
        msg.id = 999
        await self.cog.on_message(msg)
        self.assertEqual(len(self.published), 1)
        event = self.published[0]
        self.assertIsInstance(event, AgentReplied)
        self.assertEqual(event.agent.discord_user_id, 1)
        self.assertEqual(event.agent.guild_id, 100)
        self.assertEqual(event.summary, "Hello world")

    async def test_message_ignored_in_dm(self) -> None:
        msg = MagicMock()
        msg.guild = None
        await self.cog.on_message(msg)
        self.assertEqual(self.published, [])

    async def test_message_from_this_bots_own_account_is_ignored(self) -> None:
        """Avoids double-publishing: pico's ReplyTool already publishes
        AgentReplied directly for its own reply, and that reply is itself a
        Discord message this same listener would otherwise also see and
        publish a second time."""

        self.cog.bot.user.id = 1
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.author.bot = True
        msg.content = "hello world"
        msg.clean_content = "hello world"
        await self.cog.on_message(msg)
        self.assertEqual(self.published, [])

    async def test_message_from_a_different_bot_still_publishes(self) -> None:
        self.cog.bot.user.id = 1
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 2
        msg.author.bot = True
        msg.content = "hello world"
        msg.clean_content = "hello world"
        await self.cog.on_message(msg)
        self.assertEqual(len(self.published), 1)
