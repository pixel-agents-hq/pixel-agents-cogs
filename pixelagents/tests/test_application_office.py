"""Focused tests for OfficeService and the seat-assignment helpers."""

from __future__ import annotations

import logging
import unittest
from unittest.mock import AsyncMock

from pixelagents.application.office import JS_MAX_SAFE, OfficeService, merge_seat_patch
from pixelagents.domain import (
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    MessageSnapshot,
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
            hue_shift=lambda low, high: low + high,
        )

    async def test_reconcile_owns_agent_and_palette_state(self) -> None:
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)

        self.assertEqual(self.service.active_agents[(1, 2)], ("online", "Agent"))
        self.assertEqual(self.repository.values["-2"]["palette"], 0)
        self.assertEqual(
            [message["type"] for message in self.sent[:3]],
            ["agentCreated", "agentTeamInfo", "agentStatus"],
        )

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
            assets={"characters": []},
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
            assets={"characters": []},
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
            assets={"characters": []},
            seats={},
            layout={"version": 1},
        )
        settings_loaded = next(m for m in messages if m["type"] == "settingsLoaded")

        self.assertTrue(settings_loaded["ghostHeadlessAgents"])


class TestMergeSeatPatch(unittest.TestCase):
    def test_valid_fields_are_merged(self) -> None:
        seats: dict[str, dict[str, object]] = {}
        merge_seat_patch(seats, "-1", 6, {"palette": 2, "hueShift": 90, "seatId": "chair:1"})

        self.assertEqual(seats["-1"], {"palette": 2, "hueShift": 90, "seatId": "chair:1"})

    def test_out_of_range_or_wrong_typed_fields_are_dropped(self) -> None:
        seats: dict[str, dict[str, object]] = {}
        merge_seat_patch(
            seats,
            "-1",
            6,
            {"palette": 99, "hueShift": "not-an-int", "seatId": 42},
        )

        self.assertEqual(seats["-1"], {})

    def test_existing_record_fields_are_preserved_across_partial_patches(self) -> None:
        seats: dict[str, dict[str, object]] = {"-1": {"palette": 3, "hueShift": 10}}
        merge_seat_patch(seats, "-1", 6, {"seatId": "chair:2"})

        self.assertEqual(seats["-1"], {"palette": 3, "hueShift": 10, "seatId": "chair:2"})
