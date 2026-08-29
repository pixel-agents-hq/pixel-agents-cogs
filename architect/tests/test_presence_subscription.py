"""PresenceSubscriptionMixin feeds architect's own OfficeService instance
from corridor's AgentPresenceChanged events -- see
adapters/presence_subscription.py's module docstring and
docs/office-agent-identity-design.md."""

from __future__ import annotations

import unittest

from corridor.domain import AgentPresenceChanged, AgentRef
from pixelagents.domain import GenuineAgentKey

from ..architect import Architect
from .conftest import FakeBot, FakeUser


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


if __name__ == "__main__":
    unittest.main()
