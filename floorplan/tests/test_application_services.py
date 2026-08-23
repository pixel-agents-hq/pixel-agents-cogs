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

from corridor.domain import AgentPresenceChanged, AgentReplied
from floorplan.application import TaskSupervisor
from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.infrastructure.discord import member_snapshot, message_snapshot
from floorplan.tests.conftest import FakeCorridor, _FakeClientWebSocketResponse
from pixelagents.domain import ActivityKind, PresenceStatus


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

    def test_message_adapter_resolves_mentions_to_display_names(self) -> None:
        message = SimpleNamespace(
            guild=SimpleNamespace(id=20),
            author=SimpleNamespace(id=10),
            id=30,
            content="hey <@10>, check this out",
            clean_content="hey @Someone, check this out",
        )

        snapshot = message_snapshot(message)

        assert snapshot is not None
        self.assertEqual(snapshot.content, "hey @Someone, check this out")


class TestApplicationBoundaries(unittest.TestCase):
    def test_office_policy_modules_do_not_import_frameworks(self) -> None:
        application_root = Path(__file__).parents[1] / "application"
        banned_roots = {"aiohttp", "discord", "redbot"}

        for filename in ("catalogue.py", "tasks.py"):
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
        cog._corridor = FakeCorridor()
        cog._corridor.subscribe_event(
            AgentPresenceChanged, cog._on_agent_presence_changed, owner="Floorplan"
        )
        cog._corridor.subscribe_event(AgentReplied, cog._on_agent_replied, owner="Floorplan")
        cog._agents[(1, 2)] = ("online", "Agent")
        await cog.config.guild_from_id(1).enabled.set(True)
        await cog.config.message_tool_clear_delay.set(3600.0)
        socket = _FakeClientWebSocketResponse()
        cog._client_hub.add(socket)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            author=SimpleNamespace(id=2, bot=False),
            id=99,
            content="hello",
            clean_content="hello",
        )

        await cog.on_message(message)
        self.assertEqual(len(cog._task_supervisor.tasks), 1)

        await cog.cog_unload()

        sent_types = [json.loads(payload)["type"] for payload in socket._sent]
        self.assertEqual(sent_types, ["agentToolStart", "agentSelected"])
        self.assertTrue(socket.closed)
        self.assertEqual(cog._task_supervisor.tasks, frozenset())

        await cog.on_message(message)
        self.assertEqual(len(socket._sent), 2)
