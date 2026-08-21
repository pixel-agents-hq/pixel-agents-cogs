"""Focused tests for office policy, Discord adapters, and task supervision."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from floorplan.application import OfficeService, PresenceService, TaskSupervisor
from floorplan.application.office import JS_MAX_SAFE
from floorplan.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    MessageSnapshot,
    PresenceStatus,
)
from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.infrastructure.discord import member_snapshot, message_snapshot
from floorplan.tests.conftest import _FakeClientWebSocketResponse


class MemorySeats:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def seats(self) -> dict[str, dict[str, object]]:
        return {key: dict(value) for key, value in self.values.items()}

    async def mutate_seats(self, mutation):
        result = mutation(self.values)
        return result


def agent(
    *,
    guild_id: int = 1,
    user_id: int = 2,
    name: str = "Agent",
    status: PresenceStatus | None = PresenceStatus.ONLINE,
    activities: tuple[ActivitySnapshot, ...] = (),
) -> AgentSnapshot:
    return AgentSnapshot(
        key=AgentKey(guild_id, user_id),
        display_name=name,
        status=status,
        is_bot=False,
        activities=activities,
    )


class TestDiscordSnapshots(unittest.TestCase):
    def test_member_is_normalized_to_immutable_values(self) -> None:
        activity = SimpleNamespace(
            type=discord.ActivityType.listening,
            name="Spotify",
            details="Track",
            state="Artist",
            title="Track",
            artist="Artist",
        )
        member = SimpleNamespace(
            id=10,
            guild=SimpleNamespace(id=20),
            display_name="Listener",
            status="idle",
            bot=False,
            activities=[activity],
        )

        snapshot = member_snapshot(member)

        self.assertEqual(snapshot.status, PresenceStatus.IDLE)
        self.assertEqual(snapshot.activities[0].kind, ActivityKind.LISTENING)
        self.assertEqual(snapshot.activities[0].title, "Track")

    def test_bot_member_status_is_forced_online(self) -> None:
        member = SimpleNamespace(
            id=10,
            guild=SimpleNamespace(id=20),
            display_name="Office Bot",
            status="offline",
            bot=True,
            activities=[],
        )

        self.assertEqual(member_snapshot(member, bot_user_id=10).status, PresenceStatus.ONLINE)

    def test_message_adapter_rejects_direct_messages(self) -> None:
        direct_message = SimpleNamespace(guild=None)
        self.assertIsNone(message_snapshot(direct_message))


class TestApplicationBoundaries(unittest.TestCase):
    def test_office_policy_modules_do_not_import_frameworks(self) -> None:
        application_root = Path(__file__).parents[1] / "application"
        banned_roots = {"aiohttp", "discord", "redbot"}

        for filename in ("catalogue.py", "office.py", "presence.py", "tasks.py"):
            tree = ast.parse((application_root / filename).read_text(encoding="utf-8"))
            imported_roots = {
                alias.name.partition(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported_roots.update(
                node.module.partition(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            )
            self.assertTrue(imported_roots.isdisjoint(banned_roots), filename)


class TestPresenceService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sent: list[dict[str, object]] = []

        async def send(message) -> None:
            self.sent.append(dict(message))

        self.service = PresenceService(send)

    async def test_listening_activity_is_preferred_and_cached(self) -> None:
        snapshot = agent(
            activities=(
                ActivitySnapshot(kind=ActivityKind.PLAYING, name="Game"),
                ActivitySnapshot(
                    kind=ActivityKind.LISTENING,
                    name="Spotify",
                    title="Track",
                    artist="Artist",
                ),
            )
        )

        await self.service.start(snapshot, -2, enabled=True)
        await self.service.update(snapshot, -2, enabled=True)

        self.assertEqual(self.service.cache[(1, 2)], "Track — Artist")
        self.assertEqual([message["type"] for message in self.sent], ["agentToolStart"])

    async def test_changed_label_clears_before_replacement(self) -> None:
        first = agent(activities=(ActivitySnapshot(kind=ActivityKind.PLAYING, name="One"),))
        second = agent(activities=(ActivitySnapshot(kind=ActivityKind.PLAYING, name="Two"),))

        await self.service.start(first, -2, enabled=True)
        self.sent.clear()
        await self.service.update(second, -2, enabled=True)

        self.assertEqual(
            [message["type"] for message in self.sent],
            ["agentToolsClear", "agentToolStart"],
        )

    def test_message_projection_truncates_at_the_locked_boundary(self) -> None:
        snapshot = MessageSnapshot(AgentKey(1, 2), message_id=99, content="x" * 100)
        projected = self.service.message_start(snapshot, -2)

        self.assertEqual(projected["toolId"], "msg-99")
        self.assertEqual(projected["status"], "x" * 40 + "…")


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

    async def test_clear_message_activity_pings_a_waiting_checkmark(self) -> None:
        """clear_message_activity must send an agentStatus:"waiting" ping — the
        checkmark bubble pixel-agents draws unconditionally on canvas, bypassing
        the alwaysShowLabels/hover/select gate that hides the Message tool label
        itself — so something visibly happens even when labels aren't shown."""
        await self.service.reconcile(agent(), include_bots=True, rich_presence_enabled=True)
        self.sent.clear()

        await self.service.clear_message_activity(AgentKey(1, 2))

        types = [message["type"] for message in self.sent]
        self.assertIn("agentStatus", types)
        status_message = next(m for m in self.sent if m["type"] == "agentStatus")
        self.assertEqual(status_message["status"], "waiting")
        self.assertEqual(status_message["id"], self.service.agent_id(2))
        # The clear must precede the ping so it reads as "a message landed"
        # after the label disappears, not overlapping it.
        self.assertLess(types.index("agentToolsClear"), types.index("agentStatus"))

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

        with self.assertLogs("floorplan.application.office", level=logging.WARNING) as logs:
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


class TestTaskSupervisor(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_and_awaits_every_task(self) -> None:
        supervisor = TaskSupervisor()
        started = asyncio.Event()
        finished = asyncio.Event()

        async def worker() -> None:
            started.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

        supervisor.create(worker(), name="worker")
        await started.wait()

        await supervisor.shutdown()

        self.assertTrue(finished.is_set())
        self.assertEqual(supervisor.tasks, frozenset())

    async def test_failure_is_observed_and_isolated(self) -> None:
        supervisor = TaskSupervisor()

        async def broken() -> None:
            raise RuntimeError("boom")

        with self.assertLogs("floorplan.application.tasks", level=logging.ERROR) as logs:
            supervisor.create(broken(), name="broken")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        self.assertIn("background task broken failed", logs.output[0])
        self.assertEqual(supervisor.tasks, frozenset())

    async def test_tasks_are_refused_after_shutdown(self) -> None:
        supervisor = TaskSupervisor()
        awaited = False

        async def worker() -> None:
            nonlocal awaited
            awaited = True

        await supervisor.shutdown()
        task = supervisor.create(worker(), name="late")

        self.assertIsNone(task)
        self.assertFalse(awaited)


class TestCogTaskLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_unload_cancels_message_clear_without_emitting_after_close(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.user = None
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)
        cog._agents[(1, 2)] = ("online", "Agent")
        await cog.config.guild_from_id(1).enabled.set(True)
        await cog.config.message_tool_clear_delay.set(3600.0)
        socket = _FakeClientWebSocketResponse()
        cog._client_hub.add(socket)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            author=SimpleNamespace(id=2),
            id=99,
            content="hello",
        )

        await cog.on_message(message)
        self.assertEqual(len(cog._task_supervisor.tasks), 1)

        await cog.cog_unload()

        sent_types = [json.loads(payload)["type"] for payload in socket._sent]
        self.assertEqual(sent_types, ["agentToolStart"])
        self.assertTrue(socket.closed)
        self.assertEqual(cog._task_supervisor.tasks, frozenset())

        await cog.on_message(message)
        self.assertEqual(len(socket._sent), 1)
