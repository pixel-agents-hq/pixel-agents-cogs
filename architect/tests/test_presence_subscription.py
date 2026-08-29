"""PresenceSubscriptionMixin feeds architect's own OfficeService instance
from corridor's AgentPresenceChanged and AgentReplied events -- see
adapters/presence_subscription.py's module docstring and
docs/office-agent-identity-design.md."""

from __future__ import annotations

import unittest
from typing import Any

from corridor.domain import AgentPresenceChanged, AgentRef, AgentReplied
from pixelagents.domain import GenuineAgentKey

from ..adapters import presence_subscription
from ..architect import Architect
from .conftest import FakeBot, FakeUser


def _connect(cog: Architect) -> list[dict[str, Any]]:
    """Captures every message OfficeService would have broadcast to a
    live webview client, without spinning a real WebSocketServer --
    OfficeService._send is a plain callback attribute (pixelagents/
    application/office.py), same swap-in-a-fake approach floorplan's own
    test conftest (`_connect`) uses for its ClientHub."""

    sent: list[dict[str, Any]] = []

    async def _capture(message: dict[str, Any]) -> None:
        sent.append(message)

    cog._office_service._send = _capture  # type: ignore[assignment]
    return sent


class TestPresenceSubscription(unittest.IsolatedAsyncioTestCase):
    async def test_a_genuine_agent_presence_event_is_reconciled_onto_architects_own_roster(
        self,
    ) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        ref = AgentRef(
            discord_user_id=None, guild_id=None, is_bot=True, agent_key="some-other-agent"
        )
        await cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=ref, display_name="Some Other Agent", status="online")
        )

        self.assertTrue(cog._office_service.is_tracked(GenuineAgentKey("some-other-agent")))

    async def test_a_genuine_agent_going_offline_is_removed_from_the_roster(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)
        ref = AgentRef(
            discord_user_id=None, guild_id=None, is_bot=True, agent_key="some-other-agent"
        )
        await cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=ref, display_name="Some Other Agent", status="online")
        )

        await cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=ref, display_name="Some Other Agent", status="offline")
        )

        self.assertFalse(cog._office_service.is_tracked(GenuineAgentKey("some-other-agent")))

    async def test_a_discord_account_shaped_presence_event_is_ignored(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        ref = AgentRef(discord_user_id=123, guild_id=456, is_bot=False)
        await cog._on_agent_presence_changed(  # must not raise
            AgentPresenceChanged(agent=ref, display_name="A Human", status="online")
        )

    async def test_architects_own_self_registration_is_reconciled_without_being_missed(
        self,
    ) -> None:
        """Regression test for the cog_load ordering hazard: architect's
        own self-registration (_register_with_corridor) is what triggers
        corridor's auto-published "architect online" event, so the
        presence subscription must be installed before that call runs --
        this must fail if _start_presence_tracking is ever moved back to
        after _register_with_corridor()."""

        bot = FakeBot()
        cog = Architect(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertTrue(cog._office_service.is_tracked(GenuineAgentKey("architect")))

    async def test_own_bot_account_is_reconciled_at_cog_load(self) -> None:
        bot = FakeBot(user=FakeUser(user_id=42, name="PixelBot"))
        cog = Architect(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertTrue(cog._office_service.is_tracked(GenuineAgentKey("discord-bot-42")))

    async def test_cog_load_does_not_raise_when_bot_user_is_none(self) -> None:
        bot = FakeBot()
        bot.user = None  # not yet logged in -- FakeBot.__init__'s default is only a convenience
        cog = Architect(bot=bot)

        await cog.cog_load()  # must not raise
        self.addAsyncCleanup(cog.cog_unload)


class TestOnAgentReplied(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Architect(bot=self.bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)
        self.sent = _connect(self.cog)
        ref = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="pico")
        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=ref, display_name="pico", status="online")
        )
        self.sent.clear()

    async def test_a_tracked_genuine_agents_reply_sends_a_message_activity(self) -> None:
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="pico"),
                summary="Hello from pico",
            )
        )

        sent_types = [message["type"] for message in self.sent]
        self.assertEqual(sent_types, ["agentToolStart", "agentSelected"])

    async def test_an_untracked_genuine_agents_reply_is_a_noop(self) -> None:
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(
                    discord_user_id=None, guild_id=None, is_bot=True, agent_key="unknown-agent"
                ),
                summary="Hello",
            )
        )

        self.assertEqual(self.sent, [])

    async def test_a_discord_account_shaped_reply_is_a_noop(self) -> None:
        await self.cog._on_agent_replied(  # must not raise
            AgentReplied(
                agent=AgentRef(discord_user_id=123, guild_id=456, is_bot=False), summary="Hello"
            )
        )

        self.assertEqual(self.sent, [])

    async def test_the_message_activity_clears_itself_after_the_delay(self) -> None:
        original_delay = presence_subscription._MESSAGE_ACTIVITY_CLEAR_DELAY
        presence_subscription._MESSAGE_ACTIVITY_CLEAR_DELAY = 0
        self.addCleanup(
            setattr, presence_subscription, "_MESSAGE_ACTIVITY_CLEAR_DELAY", original_delay
        )

        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="pico"),
                summary="Hello from pico",
            )
        )
        (task,) = self.cog._background_tasks
        await task

        sent_types = [message["type"] for message in self.sent]
        self.assertEqual(sent_types, ["agentToolStart", "agentSelected", "agentToolsClear"])


if __name__ == "__main__":
    unittest.main()
