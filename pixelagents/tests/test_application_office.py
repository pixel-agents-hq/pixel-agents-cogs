"""Focused tests for OfficeService and the seat-assignment helpers."""

from __future__ import annotations

import logging
import unittest
from unittest.mock import AsyncMock

from pixelagents.application.office import (
    JS_MAX_SAFE,
    OfficeService,
    merge_seat_patch,
    seat_repeat,
)
from pixelagents.domain import (
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    GenuineAgentKey,
    MessageSnapshot,
    OfficeVisibility,
    PresenceStatus,
)


def agent(
    *,
    guild_id: int = 1,
    user_id: int = 2,
    name: str = "Agent",
    status: PresenceStatus | None = PresenceStatus.ONLINE,
    activities: tuple[ActivitySnapshot, ...] = (),
    is_bot: bool = False,
) -> AgentSnapshot:
    return AgentSnapshot(
        key=AgentKey(guild_id, user_id),
        display_name=name,
        status=status,
        is_bot=is_bot,
        activities=activities,
    )


class MemorySeats:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def seats(self) -> dict[str, dict[str, object]]:
        return {key: dict(value) for key, value in self.values.items()}

    async def mutate_seats(self, mutation):
        result = mutation(self.values)
        return result


class TestOfficeService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = MemorySeats()
        self.sent: list[dict[str, object]] = []

        async def send(message) -> None:
            self.sent.append(dict(message))

        self.service = OfficeService(
            self.repository,
            send,
            choose=lambda choices: choices[0],
        )

    async def test_reconcile_owns_agent_and_palette_state(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)

        self.assertEqual(self.service.active_agents[(1, 2)], ("online", "Agent"))
        self.assertEqual(self.repository.values["-2"]["palette"], 0)
        self.assertEqual(
            [message["type"] for message in self.sent[:3]],
            ["agentCreated", "agentTeamInfo", "agentStatus"],
        )

    async def test_a_seventh_agent_wears_the_first_repeat_of_its_character(self) -> None:
        for user_id in range(2, 9):
            await self.service.reconcile(
                agent(user_id=user_id), include_bots=True, rich_presence_enabled=True
            )

        created = [m for m in self.sent if m["type"] == "agentCreated"]
        self.assertEqual([m["repeat"] for m in created], [0, 0, 0, 0, 0, 0, 1])
        self.assertEqual(created[-1]["palette"], 0)
        self.assertNotIn("hueShift", self.repository.values[str(self.service.agent_id(8))])

    async def test_existing_agents_meta_reads_a_legacy_hue_shift_as_a_repeat(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        agent_id = str(self.service.agent_id(2))

        message = self.service.existing_agents_message(
            {agent_id: {"palette": 1, "hueShift": 200, "seatId": "chair:1"}}
        )

        self.assertEqual(
            message["agentMeta"][agent_id], {"palette": 1, "seatId": "chair:1", "repeat": 1}
        )
        self.assertEqual(message["directoryNames"], {agent_id: "online"})

    async def test_spawn_marks_a_bot_agent_created_headless_and_external(self) -> None:
        await self.service.reconcile(
            agent(is_bot=True), include_bots=True, rich_presence_enabled=True
        )

        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertTrue(created["isHeadless"])
        self.assertTrue(created["isExternal"])

    async def test_spawn_marks_a_human_agent_created_not_headless_but_external(self) -> None:
        await self.service.reconcile(
            agent(is_bot=False), include_bots=True, rich_presence_enabled=True
        )

        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertFalse(created["isHeadless"])
        self.assertTrue(created["isExternal"])

    async def test_existing_agents_message_reports_headless_and_external_per_agent(self) -> None:
        await self.service.reconcile(
            agent(user_id=2, is_bot=False), include_bots=True, rich_presence_enabled=True
        )
        await self.service.reconcile(
            agent(user_id=3, is_bot=True), include_bots=True, rich_presence_enabled=True
        )

        message = self.service.existing_agents_message(await self.repository.seats())

        human_id = str(self.service.agent_id(2))
        bot_id = str(self.service.agent_id(3))
        self.assertEqual(message["headlessAgents"], {human_id: False, bot_id: True})
        self.assertEqual(message["externalAgents"], {human_id: True, bot_id: True})

    async def test_closing_the_bots_only_guild_drops_it_from_headless_agents(self) -> None:
        await self.service.reconcile(
            agent(user_id=3, is_bot=True), include_bots=True, rich_presence_enabled=True
        )

        await self.service.close(AgentKey(1, 3))

        message = self.service.existing_agents_message(await self.repository.seats())
        self.assertEqual(message["headlessAgents"], {})

    async def test_a_user_tracked_in_two_guilds_keeps_is_bot_after_closing_one(self) -> None:
        await self.service.reconcile(
            agent(guild_id=1, user_id=3, is_bot=True), include_bots=True, rich_presence_enabled=True
        )
        await self.service.reconcile(
            agent(guild_id=2, user_id=3, is_bot=True), include_bots=True, rich_presence_enabled=True
        )

        await self.service.close(AgentKey(1, 3))

        message = self.service.existing_agents_message(await self.repository.seats())
        self.assertEqual(message["headlessAgents"], {str(self.service.agent_id(3)): True})

    async def test_an_offline_member_is_hidden_by_default(self) -> None:
        await self.service.reconcile(
            agent(status=None), include_bots=True, rich_presence_enabled=True
        )

        self.assertEqual(self.sent, [])
        self.assertFalse(self.service.is_tracked(AgentKey(1, 2)))

    async def test_show_offline_draws_an_offline_member_translucent(self) -> None:
        await self.service.reconcile(
            agent(status=None),
            include_bots=True,
            rich_presence_enabled=True,
            visibility=OfficeVisibility(show_offline=True),
        )

        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertEqual(created["directoryName"], "offline")
        self.assertTrue(created["isHeadless"])
        message = self.service.existing_agents_message(await self.repository.seats())
        self.assertEqual(message["headlessAgents"], {str(self.service.agent_id(2)): True})

    async def test_show_offline_without_ghosting_draws_an_offline_member_opaque(self) -> None:
        await self.service.reconcile(
            agent(status=None),
            include_bots=True,
            rich_presence_enabled=True,
            visibility=OfficeVisibility(show_offline=True, ghost_offline=False),
        )

        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertEqual(created["directoryName"], "offline")
        self.assertFalse(created["isHeadless"])

    async def test_coming_online_recreates_an_offline_member_opaque(self) -> None:
        visibility = OfficeVisibility(show_offline=True)
        await self.service.reconcile(
            agent(status=None), include_bots=True, rich_presence_enabled=True, visibility=visibility
        )
        self.sent.clear()

        await self.service.reconcile(
            agent(), include_bots=True, rich_presence_enabled=True, visibility=visibility
        )

        types = [m["type"] for m in self.sent]
        self.assertLess(types.index("agentClosed"), types.index("agentCreated"))
        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertEqual(created["directoryName"], "online")
        self.assertFalse(created["isHeadless"])
        self.assertEqual(self.service.active_agents[(1, 2)], ("online", "Agent"))

    async def test_going_offline_keeps_a_shown_member_on_canvas(self) -> None:
        visibility = OfficeVisibility(show_offline=True)
        await self.service.reconcile(
            agent(), include_bots=True, rich_presence_enabled=True, visibility=visibility
        )

        await self.service.reconcile(
            agent(status=None), include_bots=True, rich_presence_enabled=True, visibility=visibility
        )

        self.assertEqual(self.service.active_agents[(1, 2)], ("offline", "Agent"))
        created = [m for m in self.sent if m["type"] == "agentCreated"]
        self.assertEqual([m["isHeadless"] for m in created], [False, True])

    async def test_ghost_bots_off_draws_a_bot_opaque(self) -> None:
        await self.service.reconcile(
            agent(is_bot=True),
            include_bots=True,
            rich_presence_enabled=True,
            visibility=OfficeVisibility(ghost_bots=False),
        )

        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertFalse(created["isHeadless"])
        message = self.service.existing_agents_message(await self.repository.seats())
        self.assertEqual(message["headlessAgents"], {str(self.service.agent_id(2)): False})

    async def test_a_ghosting_change_alone_recreates_the_character(self) -> None:
        """The webview only reads isHeadless when it creates a character, so
        flipping ghost_bots for an unchanged member must re-create it."""

        await self.service.reconcile(
            agent(is_bot=True), include_bots=True, rich_presence_enabled=True
        )
        self.sent.clear()

        await self.service.reconcile(
            agent(is_bot=True),
            include_bots=True,
            rich_presence_enabled=True,
            visibility=OfficeVisibility(ghost_bots=False),
        )

        self.assertIn("agentClosed", [m["type"] for m in self.sent])
        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertFalse(created["isHeadless"])

    async def test_an_unchanged_member_is_not_recreated(self) -> None:
        visibility = OfficeVisibility(show_offline=True)
        await self.service.reconcile(
            agent(status=None), include_bots=True, rich_presence_enabled=True, visibility=visibility
        )
        self.sent.clear()

        await self.service.reconcile(
            agent(status=None), include_bots=True, rich_presence_enabled=True, visibility=visibility
        )

        self.assertNotIn("agentCreated", [m["type"] for m in self.sent])

    async def test_sync_guild_shows_offline_members_from_the_member_list(self) -> None:
        await self.service.sync_guild(
            1,
            (agent(user_id=2), agent(user_id=3, status=None)),
            include_bots=True,
            rich_presence_enabled=True,
            visibility=OfficeVisibility(show_offline=True),
        )

        self.assertEqual(
            self.service.active_agents,
            {(1, 2): ("online", "Agent"), (1, 3): ("offline", "Agent")},
        )

    async def test_send_message_activity_selects_the_agent(self) -> None:
        """send_message_activity must send agentSelected right alongside the
        Message tool bubble, so the label panel (with the message text) is
        visible immediately without hover or "Always Show Labels"."""
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.send_message_activity(
            MessageSnapshot(key=AgentKey(1, 2), message_id=1, content="hello")
        )

        types = [message["type"] for message in self.sent]
        self.assertEqual(types, ["agentToolStart", "agentSelected"])
        self.assertEqual(self.sent[1]["id"], self.service.agent_id(2))

    async def test_clear_message_activity_does_not_reping(self) -> None:
        """clear_message_activity only clears the tool bubble (and replays any
        cached rich-presence label) — it must not also send a status ping,
        since agentSelected (sent at message-start) already made the message
        visible without needing hover."""
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.clear_message_activity(AgentKey(1, 2))

        types = [message["type"] for message in self.sent]
        self.assertEqual(types, ["agentToolsClear"])

    async def test_highlight_agent_sends_agent_selected(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.highlight_agent(AgentKey(1, 2))

        self.assertEqual(self.sent, [{"type": "agentSelected", "id": self.service.agent_id(2)}])

    async def test_unhighlight_agent_sends_agent_deselected(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.unhighlight_agent(AgentKey(1, 2))

        self.assertEqual(self.sent, [{"type": "agentDeselected", "id": self.service.agent_id(2)}])

    async def test_start_tool_activity_sends_agent_tool_start(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.start_tool_activity(AgentKey(1, 2), "tool-1", "Running", "MyTool")

        self.assertEqual(
            self.sent,
            [
                {
                    "type": "agentToolStart",
                    "id": self.service.agent_id(2),
                    "toolId": "tool-1",
                    "toolName": "MyTool",
                    "status": "Running",
                }
            ],
        )

    async def test_start_tool_activity_defaults_missing_tool_name_to_empty_string(self) -> None:
        """tool_name is optional on corridor's AgentToolStarted, but
        AgentToolStartMessage's wire toolName is required -- None must not
        reach the builder as a literal None."""
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.start_tool_activity(AgentKey(1, 2), "tool-1", "Running")

        self.assertEqual(self.sent[0]["toolName"], "")

    async def test_set_status_sends_agent_status(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.set_status(AgentKey(1, 2), "waiting", awaiting_input=True)

        self.assertEqual(
            self.sent,
            [
                {
                    "type": "agentStatus",
                    "id": self.service.agent_id(2),
                    "status": "waiting",
                    "awaitingInput": True,
                }
            ],
        )

    async def test_same_user_in_two_guilds_has_one_rendered_agent(self) -> None:
        await self.service.spawn(agent(guild_id=1), rich_presence_enabled=False)
        self.sent.clear()

        await self.service.spawn(agent(guild_id=2), rich_presence_enabled=False)

        types = [message["type"] for message in self.sent]
        self.assertNotIn("agentCreated", types)
        self.assertEqual(self.service.tracked_user_ids(), [2])

    async def test_sync_isolates_one_member_failure(self) -> None:
        self.service.reconcile = AsyncMock(side_effect=(RuntimeError("broken"), None))

        result = await self.service.sync_guild(
            1,
            (agent(user_id=1), agent(user_id=2)),
            include_bots=True,
            rich_presence_enabled=True,
        )

        self.assertEqual(result, "Sync complete. Errors: 1.")
        self.assertEqual(self.service.reconcile.await_count, 2)

    def test_collision_diagnostic_is_logged_once(self) -> None:
        self.service.active_agents[(1, 1)] = ("online", "First")

        with self.assertLogs("pixelagents.application.office", level=logging.WARNING) as logs:
            self.service.detect_collision(JS_MAX_SAFE + 1)
            self.service.detect_collision(JS_MAX_SAFE + 1)

        self.assertEqual(len(logs.output), 1)

    async def test_bootstrap_keeps_layout_after_agent_metadata(self) -> None:
        self.service.active_agents[(1, 2)] = ("online", "Agent")
        messages = self.service.bootstrap_messages(
            assets={"characterProfiles": []},
            seats={},
            layout={"version": 1},
        )
        types = [message["type"] for message in messages]

        self.assertEqual(types[0], "providerCapabilities")
        self.assertLess(types.index("existingAgents"), types.index("layoutLoaded"))

    async def test_bootstrap_sends_agent_team_info_after_layout(self) -> None:
        """The webview only creates character objects for existingAgents once it
        processes layoutLoaded (buffering restored agents until then). Sent any
        earlier, the webview's setTeamInfo silently no-ops on the not-yet-created
        character and the display name never appears — see pixel-agents'
        existingAgents.ts and the VS Code adapter's "Send agent statuses AFTER
        layoutLoaded so characters exist when messages arrive" convention."""
        self.service.active_agents[(1, 2)] = ("online", "Agent")
        messages = self.service.bootstrap_messages(
            assets={"characterProfiles": []},
            seats={},
            layout={"version": 1},
        )
        types = [message["type"] for message in messages]

        self.assertLess(types.index("layoutLoaded"), types.index("agentTeamInfo"))

    async def test_bootstrap_enables_ghost_rendering_for_headless_agents(self) -> None:
        """isHeadless (agent_created/existingAgents) is inert in the webview
        unless settingsLoaded.ghostHeadlessAgents is also true -- see
        renderer.ts's `ch.isHeadless && ghostHeadlessAgents` alpha check."""
        messages = self.service.bootstrap_messages(
            assets={"characterProfiles": []},
            seats={},
            layout={"version": 1},
        )
        settings_loaded = next(m for m in messages if m["type"] == "settingsLoaded")

        self.assertTrue(settings_loaded["ghostHeadlessAgents"])


class TestGenuineAgents(unittest.IsolatedAsyncioTestCase):
    """A genuine agent (no Discord account, e.g. architect) -- see
    docs/office-agent-identity-design.md. Uses the same OfficeService
    instance and roster as Discord agents; no per-guild concept applies."""

    async def asyncSetUp(self) -> None:
        self.repository = MemorySeats()
        self.sent: list[dict[str, object]] = []

        async def send(message) -> None:
            self.sent.append(dict(message))

        self.service = OfficeService(
            self.repository,
            send,
            choose=lambda choices: choices[0],
        )
        self.identity = GenuineAgentKey("architect")

    async def test_reconcile_spawns_a_headless_external_agent(self) -> None:
        await self.service.reconcile_genuine_agent(self.identity, "architect", "online")

        self.assertTrue(self.service.is_tracked(self.identity))
        created = next(m for m in self.sent if m["type"] == "agentCreated")
        self.assertTrue(created["isHeadless"])
        self.assertTrue(created["isExternal"])
        team_info = next(m for m in self.sent if m["type"] == "agentTeamInfo")
        self.assertEqual(team_info["agentName"], "architect")

    async def test_agent_id_is_positive_and_disjoint_from_discord_ids(self) -> None:
        agent_id = self.service.genuine_agent_id("architect")

        self.assertGreater(agent_id, 0)
        self.assertNotEqual(agent_id, self.service.agent_id(123))

    async def test_offline_closes_a_spawned_agent(self) -> None:
        await self.service.reconcile_genuine_agent(self.identity, "architect", "online")
        self.sent.clear()

        await self.service.reconcile_genuine_agent(self.identity, "architect", "offline")

        self.assertFalse(self.service.is_tracked(self.identity))
        self.assertEqual([m["type"] for m in self.sent], ["agentClosed", "existingAgents"])

    async def test_offline_with_no_prior_spawn_is_a_noop(self) -> None:
        await self.service.reconcile_genuine_agent(self.identity, "architect", "offline")

        self.assertEqual(self.sent, [])

    async def test_status_change_recreates_the_agent(self) -> None:
        await self.service.reconcile_genuine_agent(self.identity, "architect", "online")
        self.sent.clear()

        await self.service.reconcile_genuine_agent(self.identity, "architect", "idle")

        self.assertEqual([m["type"] for m in self.sent[:2]], ["agentClosed", "agentCreated"])

    async def test_unchanged_status_and_name_sends_nothing(self) -> None:
        await self.service.reconcile_genuine_agent(self.identity, "architect", "online")
        self.sent.clear()

        await self.service.reconcile_genuine_agent(self.identity, "architect", "online")

        self.assertEqual(self.sent, [])

    async def test_appears_in_existing_agents_message_alongside_discord_agents(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        await self.service.reconcile_genuine_agent(self.identity, "architect", "online")

        message = self.service.existing_agents_message({})
        genuine_id = self.service.genuine_agent_id("architect")
        discord_id = self.service.agent_id(2)

        self.assertIn(genuine_id, message["agents"])
        self.assertIn(discord_id, message["agents"])
        self.assertTrue(message["headlessAgents"][str(genuine_id)])

    async def test_highlight_and_tool_activity_use_the_genuine_agent_id(self) -> None:
        await self.service.highlight_agent(self.identity)
        await self.service.start_tool_activity(self.identity, "t1", "thinking")
        await self.service.set_status(self.identity, "active")

        agent_id = self.service.genuine_agent_id("architect")
        self.assertEqual(
            [m["id"] for m in self.sent],
            [agent_id, agent_id, agent_id],
        )

    async def test_send_and_clear_genuine_agent_activity(self) -> None:
        await self.service.send_genuine_agent_activity(self.identity, "using tool describe_office")
        tool_start = next(m for m in self.sent if m["type"] == "agentToolStart")
        self.assertEqual(tool_start["status"], "using tool describe_office")
        self.assertIn("agentSelected", [m["type"] for m in self.sent])

        self.sent.clear()
        await self.service.clear_genuine_agent_activity(self.identity)

        self.assertEqual([m["type"] for m in self.sent], ["agentToolsClear"])


class TestMergeSeatPatch(unittest.TestCase):
    def test_valid_fields_are_merged(self) -> None:
        seats: dict[str, dict[str, object]] = {}
        merge_seat_patch(seats, "-1", 6, {"palette": 2, "repeat": 1, "seatId": "chair:1"})

        self.assertEqual(seats["-1"], {"palette": 2, "repeat": 1, "seatId": "chair:1"})

    def test_out_of_range_or_wrong_typed_fields_are_dropped(self) -> None:
        seats: dict[str, dict[str, object]] = {}
        merge_seat_patch(
            seats,
            "-1",
            6,
            {"palette": 99, "repeat": "not-an-int", "seatId": 42},
        )

        self.assertEqual(seats["-1"], {})

    def test_boolean_numeric_fields_are_dropped(self) -> None:
        seats: dict[str, dict[str, object]] = {}
        merge_seat_patch(seats, "-1", 6, {"palette": True, "repeat": False})

        self.assertEqual(seats["-1"], {})

    def test_existing_record_fields_are_preserved_across_partial_patches(self) -> None:
        seats: dict[str, dict[str, object]] = {"-1": {"palette": 3, "repeat": 2}}
        merge_seat_patch(seats, "-1", 6, {"seatId": "chair:2"})

        self.assertEqual(seats["-1"], {"palette": 3, "repeat": 2, "seatId": "chair:2"})

    def test_a_repeat_replaces_a_legacy_hue_shift(self) -> None:
        seats: dict[str, dict[str, object]] = {"-1": {"palette": 3, "hueShift": 90}}
        merge_seat_patch(seats, "-1", 6, {"repeat": 0})

        self.assertEqual(seats["-1"], {"palette": 3, "repeat": 0})


class TestSeatRepeat(unittest.TestCase):
    def test_reads_repeat(self) -> None:
        self.assertEqual(seat_repeat({"repeat": 2}), 2)

    def test_reads_a_legacy_hue_shift_as_upstream_does(self) -> None:
        self.assertEqual(seat_repeat({"hueShift": 0}), 0)
        self.assertEqual(seat_repeat({"hueShift": 135}), 1)

    def test_none_without_either(self) -> None:
        self.assertIsNone(seat_repeat({"palette": 1}))
