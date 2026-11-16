"""corridor's own Discord gateway listeners: moved here from floorplan's
`discord_gateway.py` (see docs/corridor-pubsub-design.md). Publishes
unconditionally -- no guild-enabled/include_bots/office-tracking/
broadcast-toggle gating, unlike floorplan's old version of this module.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord  # stubbed by ../conftest.py

from ..corridor import Corridor
from ..domain import AgentPresenceChanged, AgentReplied, AgentToolStarted, AgentRef, activity_guild
from .conftest import FakeBot


def _channel(*, everyone_can_view: bool = True, private_thread: bool | None = None) -> object:
    """A channel `@everyone` can (or can't) view; `private_thread` set
    makes it a thread that is (or isn't) private."""

    everyone = object()
    channel = SimpleNamespace(
        guild=SimpleNamespace(default_role=everyone),
        permissions_for=lambda role: SimpleNamespace(
            view_channel=everyone_can_view and role is everyone
        ),
    )
    if private_thread is not None:
        channel.is_private = lambda: private_thread
    return channel


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
        self.cog.subscribe_event(AgentPresenceChanged, _recorder(self.published), owner="test")

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
        self.cog.subscribe_event(AgentPresenceChanged, _recorder(self.published), owner="test")

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
        self.cog.subscribe_event(AgentPresenceChanged, _recorder(self.published), owner="test")

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
        # Offline, but still a member: not the member-remove shape.
        self.assertFalse(self.published[0].left_guild)


class TestMemberRemoveListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        self.cog.subscribe_event(AgentPresenceChanged, _recorder(self.published), owner="test")

    async def test_remove_publishes_offline_presence_changed(self) -> None:
        m = _member(guild_id=100, user_id=42)
        await self.cog.on_member_remove(m)
        self.assertEqual(len(self.published), 1)
        event = self.published[0]
        self.assertIsInstance(event, AgentPresenceChanged)
        self.assertEqual(event.status, "offline")
        self.assertEqual(event.agent.discord_user_id, 42)
        self.assertEqual(event.agent.guild_id, 100)
        # Lets a subscriber that shows offline members remove a leaver
        # instead of drawing them offline.
        self.assertTrue(event.left_guild)


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
        msg.channel = _channel()
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
        msg.channel = _channel()
        await self.cog.on_message(msg)
        self.assertEqual(len(self.published), 1)

    async def test_messages_in_channels_everyone_cannot_read_are_not_published(self) -> None:
        for channel in (
            _channel(everyone_can_view=False),
            _channel(private_thread=True),
            SimpleNamespace(guild=None),
        ):
            msg = MagicMock()
            msg.guild.id = 100
            msg.author.id = 2
            msg.clean_content = "staff only"
            msg.channel = channel
            await self.cog.on_message(msg)
        self.assertEqual(self.published, [])

    async def test_a_public_thread_still_publishes(self) -> None:
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 2
        msg.clean_content = "hi"
        msg.channel = _channel(private_thread=False)
        await self.cog.on_message(msg)
        self.assertEqual(len(self.published), 1)


class TestPrivateTurnActivity(unittest.IsolatedAsyncioTestCase):
    """A turn asked for from a private channel publishes no activity text."""

    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.published: list[object] = []
        for event_type in (AgentReplied, AgentToolStarted):
            self.cog.subscribe_event(event_type, _recorder(self.published), owner="test")
        self.agent = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="a")

    async def test_activity_inside_a_private_turn_is_dropped(self) -> None:
        with activity_guild(100, private=True):
            await self.cog.publish_event(AgentReplied(agent=self.agent, summary="secret"))
            await self.cog.publish_event(AgentToolStarted(agent=self.agent, tool_id="1", status="working"))
            # A nested turn can't make it public again.
            with activity_guild(100):
                await self.cog.publish_event(AgentReplied(agent=self.agent, summary="nested"))
        self.assertEqual(self.published, [])

    async def test_activity_outside_a_private_turn_is_published(self) -> None:
        with activity_guild(100):
            await self.cog.publish_event(AgentReplied(agent=self.agent, summary="hi"))
        self.assertEqual(len(self.published), 1)


class TestDisabledInGuild(unittest.IsolatedAsyncioTestCase):
    """`[p]command disablecog Corridor` in a guild stops every gateway
    listener publishing for that guild; other guilds are unaffected."""

    async def asyncSetUp(self) -> None:
        self.cog = _make_cog()
        self.cog.bot.disabled_cogs.add(("Corridor", 100))
        self.published: list[object] = []
        for event_type in (AgentPresenceChanged, AgentReplied):
            self.cog.subscribe_event(event_type, _recorder(self.published), owner="test")

    async def test_every_listener_skips_a_disabled_guild(self) -> None:
        await self.cog.on_member_update(_member(display_name="Old"), _member(display_name="New"))
        await self.cog.on_presence_update(_member(status="offline"), _member(status="online"))
        await self.cog.on_member_join(_member())
        await self.cog.on_member_remove(_member())
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.clean_content = "hello"
        await self.cog.on_message(msg)
        self.assertEqual(self.published, [])

    async def test_other_guilds_still_publish(self) -> None:
        await self.cog.on_member_join(_member(guild_id=200))
        self.assertEqual(len(self.published), 1)
