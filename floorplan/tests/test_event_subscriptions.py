"""Unit tests for floorplan's corridor bus subscriber handlers
(floorplan/adapters/event_subscriptions.py).

These call `_on_agent_presence_changed`/`_on_agent_replied` directly with
hand-built corridor events -- decoupled from listener-level guard testing
(discord_gateway.py's own guards have their own tests in test_floorplan.py,
asserting what gets published, not what a handler does with it). This is
the direct port of what used to be TestReconcileMember/TestCloseAgent/half
of TestOnMessage in test_floorplan.py, before discord_gateway.py's
_reconcile_member/_close_agent/send_message_activity call sites moved here.

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""

from __future__ import annotations

import json
import unittest

from corridor.domain import (
    AgentActivity,
    AgentHighlighted,
    AgentPresenceChanged,
    AgentRef,
    AgentReplied,
    AgentStatusChanged,
    AgentToolStarted,
    AgentUnhighlighted,
)
from floorplan.tests.test_floorplan import _connect, _make_cog
from pixelagents.domain import GenuineAgentKey


async def _enable_guild(cog, guild_id, *, include_bots=True):
    """floorplan's real GUILD_DEFAULTS ship `enabled=False` -- the old
    _reconcile_member/_close_agent tests never needed to flip it (that gate
    lived only in the listener, not in reconcile()/close() themselves), but
    _on_agent_presence_changed now re-checks it too (see
    event_subscriptions.py's "each subscriber owns its own guild-enablement"
    rationale) -- so every ported test needs the guild enabled to see any
    wire effect at all."""

    await cog.config.guild_from_id(guild_id).enabled.set(True)
    await cog.config.guild_from_id(guild_id).include_bots.set(include_bots)


def _presence(
    guild_id=100, user_id=1, display_name="Tin", status="online", is_bot=False, activities=()
):
    return AgentPresenceChanged(
        agent=AgentRef(discord_user_id=user_id, guild_id=guild_id, is_bot=is_bot),
        display_name=display_name,
        status=status,
        activities=tuple(activities),
    )


class TestOnAgentPresenceChanged(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)
        await _enable_guild(self.cog, 100)

    async def test_new_visible_member_spawns(self):
        await self.cog._on_agent_presence_changed(_presence(status="online"))
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentCreated", sent_types)

    async def test_spawn_sets_folder_name(self):
        await self.cog._on_agent_presence_changed(_presence(status="dnd"))
        created = next(
            json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentCreated"
        )
        self.assertEqual(created["folderName"], "dnd")

    async def test_spawn_sets_agent_name_via_team_info(self):
        await self.cog._on_agent_presence_changed(_presence(status="online", display_name="Alice"))
        team_info = next(
            json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentTeamInfo"
        )
        self.assertEqual(team_info["agentName"], "Alice")

    async def test_spawn_sends_status(self):
        await self.cog._on_agent_presence_changed(
            _presence(status="online", activities=[AgentActivity(kind="playing", name="Some Game")])
        )
        status_msg = next(
            json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentStatus"
        )
        self.assertEqual(status_msg["status"], "active")

    async def test_offline_member_not_spawned(self):
        await self.cog._on_agent_presence_changed(_presence(status="offline"))
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentCreated", sent_types)

    async def test_offline_cached_member_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_presence_changed(_presence(status="offline"))
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_folder_change_closes_and_respawns(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_presence_changed(_presence(status="dnd"))
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertIn("agentCreated", sent_types)

    async def test_name_change_only_sends_team_info(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_presence_changed(
            _presence(status="online", display_name="Newname")
        )
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentClosed", sent_types)
        self.assertNotIn("agentCreated", sent_types)
        self.assertIn("agentTeamInfo", sent_types)

    async def test_no_change_sends_nothing(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_presence_changed(_presence(status="online", display_name="Tin"))
        self.assertEqual(len(self.ws._sent), 0)

    async def test_bot_excluded_when_include_bots_false(self):
        await _enable_guild(self.cog, 100, include_bots=False)
        await self.cog._on_agent_presence_changed(_presence(status="online", is_bot=True))
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_bot_cached_excluded_closes(self):
        await _enable_guild(self.cog, 100, include_bots=False)
        self.cog._agents[(100, 99)] = ("online", "BotName")
        await self.cog._on_agent_presence_changed(
            _presence(user_id=99, status="online", is_bot=True)
        )
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)

    async def test_disabled_guild_is_a_noop(self):
        """The redundant subscriber-side enabled re-check (see
        event_subscriptions.py) -- a guild that somehow reaches this handler
        disabled produces no wire effect, matching the listener's own gate."""

        await self.cog.config.guild_from_id(100).enabled.set(False)
        await self.cog._on_agent_presence_changed(_presence(status="online"))
        self.assertEqual(len(self.ws._sent), 0)


class TestOnAgentPresenceChangedClose(unittest.IsolatedAsyncioTestCase):
    """The former TestCloseAgent -- a member leaving publishes
    AgentPresenceChanged(status="offline"), same handler as any other
    presence change."""

    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)
        await _enable_guild(self.cog, 100)

    async def test_close_sends_agent_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_presence_changed(_presence(status="offline"))
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)

    async def test_close_removes_from_registry(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_presence_changed(_presence(status="offline"))
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_close_nonexistent_is_noop(self):
        await self.cog._on_agent_presence_changed(_presence(user_id=999, status="offline"))
        self.assertEqual(len(self.ws._sent), 0)

    async def test_close_user_active_in_other_guild_does_not_send_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._agents[(200, 1)] = ("idle", "Tin")
        await self.cog._on_agent_presence_changed(_presence(status="offline"))
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentClosed", sent_types)


class TestOnAgentReplied(unittest.IsolatedAsyncioTestCase):
    """The wire-effect half of the former TestOnMessage -- on_message's own
    guard-condition tests (not-tracked, DM, broadcast-disabled) stay in
    test_floorplan.py, asserting what gets published; these assert what the
    subscriber does with an already-published AgentReplied."""

    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)
        await _enable_guild(self.cog, 100)

    async def test_message_sends_tool_start(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                summary="Hello world",
            )
        )
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentToolStart", sent_types)

    async def test_message_selects_the_agent(self):
        """agentSelected must accompany the Message tool bubble so the label
        panel (with the message text) is visible immediately, without hover
        or "Always Show Labels"."""

        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                summary="Hello world",
            )
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual([m["type"] for m in sent], ["agentToolStart", "agentSelected"])
        self.assertEqual(sent[1]["id"], self.cog._office_service.agent_id(1))

    async def test_message_truncates_long_content(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False), summary="x" * 100
            )
        )
        tool_msg = next(
            json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentToolStart"
        )
        self.assertLessEqual(len(tool_msg["status"]), 45)

    async def test_not_tracked_is_a_noop(self):
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=False), summary="hi"
            )
        )
        self.assertEqual(len(self.ws._sent), 0)

    async def test_disabled_guild_is_a_noop(self):
        """corridor now publishes AgentReplied unconditionally -- the
        guild-enabled gate that used to happen before floorplan's own
        on_message even published now lives here instead."""

        await self.cog.config.guild_from_id(100).enabled.set(False)
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                summary="Hello world",
            )
        )
        self.assertEqual(len(self.ws._sent), 0)

    async def test_broadcast_messages_disabled_is_a_noop(self):
        await self.cog.config.broadcast_messages.set(False)
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                summary="Hello world",
            )
        )
        self.assertEqual(len(self.ws._sent), 0)

    async def test_message_clear_does_not_reping(self):
        """After the message-clear delay, only agentToolsClear is sent —
        agentSelected (sent at message-start) already made the message
        visible without needing hover, so clearing must not also emit a
        status ping."""

        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_replied(
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                summary="Hello world",
            )
        )
        self.ws._sent.clear()

        await self.cog._clear_tool_after_delay(
            self.cog._office_service.agent_id(1), 0, guild_id=100, user_id=1
        )

        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertEqual(sent_types, ["agentToolsClear"])


class TestOnAgentHighlighted(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_tracked_agent_sends_agent_selected(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_highlighted(
            AgentHighlighted(agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False))
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual(
            sent, [{"type": "agentSelected", "id": self.cog._office_service.agent_id(1)}]
        )

    async def test_not_tracked_is_a_noop(self):
        await self.cog._on_agent_highlighted(
            AgentHighlighted(agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=False))
        )
        self.assertEqual(len(self.ws._sent), 0)


class TestOnAgentUnhighlighted(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_tracked_agent_sends_agent_deselected(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_unhighlighted(
            AgentUnhighlighted(agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False))
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual(
            sent, [{"type": "agentDeselected", "id": self.cog._office_service.agent_id(1)}]
        )

    async def test_not_tracked_is_a_noop(self):
        await self.cog._on_agent_unhighlighted(
            AgentUnhighlighted(agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=False))
        )
        self.assertEqual(len(self.ws._sent), 0)


class TestOnAgentToolStarted(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_tracked_agent_sends_agent_tool_start(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_tool_started(
            AgentToolStarted(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                tool_id="tool-1",
                status="Running",
                tool_name="MyTool",
            )
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual(
            sent,
            [
                {
                    "type": "agentToolStart",
                    "id": self.cog._office_service.agent_id(1),
                    "toolId": "tool-1",
                    "toolName": "MyTool",
                    "status": "Running",
                }
            ],
        )

    async def test_missing_tool_name_defaults_to_empty_string(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_tool_started(
            AgentToolStarted(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                tool_id="tool-1",
                status="Running",
            )
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual(sent[0]["toolName"], "")

    async def test_not_tracked_is_a_noop(self):
        await self.cog._on_agent_tool_started(
            AgentToolStarted(
                agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=False),
                tool_id="tool-1",
                status="Running",
            )
        )
        self.assertEqual(len(self.ws._sent), 0)


class TestOnAgentStatusChanged(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_tracked_agent_sends_agent_status(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._on_agent_status_changed(
            AgentStatusChanged(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                status="waiting",
                awaiting_input=True,
            )
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual(
            sent,
            [
                {
                    "type": "agentStatus",
                    "id": self.cog._office_service.agent_id(1),
                    "status": "waiting",
                    "awaitingInput": True,
                }
            ],
        )

    async def test_not_tracked_is_a_noop(self):
        await self.cog._on_agent_status_changed(
            AgentStatusChanged(
                agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=False), status="active"
            )
        )
        self.assertEqual(len(self.ws._sent), 0)


class TestGenuineAgentDispatch(unittest.IsolatedAsyncioTestCase):
    """A genuine agent (e.g. architect, agent_key set, no Discord
    snowflakes) renders on the same shared canvas, dispatched through
    OfficeService's parallel genuine-agent entry points instead of being
    dropped. See docs/office-agent-identity-design.md."""

    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)
        self.ref = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="architect")

    async def test_presence_online_spawns_unconditionally(self):
        """No guild-enabled gate applies -- unlike a Discord presence
        event, there's no guild config repository call at all here."""

        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=self.ref, display_name="architect", status="online")
        )
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentCreated", sent_types)
        self.assertTrue(self.cog._office_service.is_tracked(GenuineAgentKey("architect")))

    async def test_presence_offline_closes(self):
        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=self.ref, display_name="architect", status="online")
        )
        self.ws._sent.clear()

        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=self.ref, display_name="architect", status="offline")
        )
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertFalse(self.cog._office_service.is_tracked(GenuineAgentKey("architect")))

    async def test_replied_sends_tool_start_and_selects(self):
        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=self.ref, display_name="architect", status="online")
        )
        self.ws._sent.clear()

        await self.cog._on_agent_replied(
            AgentReplied(agent=self.ref, summary="using tool describe_office")
        )
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual([m["type"] for m in sent], ["agentToolStart", "agentSelected"])
        self.assertEqual(sent[0]["status"], "using tool describe_office")

    async def test_replied_clear_after_delay_sends_only_agent_tools_clear(self):
        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=self.ref, display_name="architect", status="online")
        )
        await self.cog._on_agent_replied(
            AgentReplied(agent=self.ref, summary="using tool describe_office")
        )
        self.ws._sent.clear()
        agent_id = self.cog._office_service.genuine_agent_id("architect")

        await self.cog._clear_tool_after_delay(agent_id, 0)

        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertEqual(sent_types, ["agentToolsClear"])

    async def test_replied_not_tracked_is_a_noop(self):
        """Mirrors a Discord AgentReplied for an untracked member -- a
        genuine agent must be spawned (a presence event already seen)
        before its activity renders."""

        await self.cog._on_agent_replied(AgentReplied(agent=self.ref, summary="hello"))
        self.assertEqual(len(self.ws._sent), 0)

    async def test_highlighted_and_tool_started_and_status_changed(self):
        await self.cog._on_agent_presence_changed(
            AgentPresenceChanged(agent=self.ref, display_name="architect", status="online")
        )
        self.ws._sent.clear()

        await self.cog._on_agent_highlighted(AgentHighlighted(agent=self.ref))
        await self.cog._on_agent_tool_started(
            AgentToolStarted(agent=self.ref, tool_id="t1", status="thinking")
        )
        await self.cog._on_agent_status_changed(AgentStatusChanged(agent=self.ref, status="active"))

        agent_id = self.cog._office_service.genuine_agent_id("architect")
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual([m["id"] for m in sent], [agent_id, agent_id, agent_id])
        self.assertEqual(
            [m["type"] for m in sent], ["agentSelected", "agentToolStart", "agentStatus"]
        )


if __name__ == "__main__":
    unittest.main()
