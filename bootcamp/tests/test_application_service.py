"""BootcampService is fully testable without corridor/Red: plain in-memory
fakes satisfy the AgentRepository/AgentRegistrar protocols, no
unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application.service import RESERVED_AGENT_KEYS, BootcampService
from ..domain import CustomAgent


class FakeAgentRepository:
    def __init__(self) -> None:
        self._agents: dict[str, CustomAgent] = {}

    async def list_agents(self) -> tuple[CustomAgent, ...]:
        return tuple(self._agents.values())

    async def get_agent(self, agent_key: str) -> CustomAgent | None:
        return self._agents.get(agent_key)

    async def save_agent(self, agent: CustomAgent) -> None:
        self._agents[agent.agent_key] = agent

    async def delete_agent(self, agent_key: str) -> None:
        self._agents.pop(agent_key, None)


class FakeAgentRegistrar:
    """`fail_with` simulates a cross-owner `agent_key` collision -- the one
    real failure mode corridor's own `AgentDirectoryService.register`
    raises `ValueError` for (see `corridor/application/
    agent_directory_service.py`)."""

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.fail_with = fail_with
        self.registered: list[CustomAgent] = []
        self.unregistered: list[str] = []

    async def register(self, agent: CustomAgent) -> str | None:
        if self.fail_with is not None:
            raise ValueError(self.fail_with)
        self.registered.append(agent)
        return None

    async def unregister(self, agent_key: str) -> None:
        self.unregistered.append(agent_key)


class TestCreateAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeAgentRepository()
        self.registrar = FakeAgentRegistrar()
        self.service = BootcampService(self.repository, registrar=self.registrar)

    async def test_creates_and_registers_and_persists(self) -> None:
        error = await self.service.create_agent("recruiter", "You screen job applicants.")

        self.assertIsNone(error)
        self.assertEqual([a.agent_key for a in self.registrar.registered], ["recruiter"])
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertEqual(stored.system_prompt, "You screen job applicants.")
        self.assertEqual(stored.permission_group, "employee")

    async def test_accepts_custom_permission_group_and_budget(self) -> None:
        error = await self.service.create_agent(
            "recruiter",
            "You screen job applicants.",
            permission_group="keyholder",
            max_tool_calls=3,
            debug_logging=True,
            request_timeout_seconds=45.0,
        )

        self.assertIsNone(error)
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertEqual(stored.permission_group, "keyholder")
        self.assertEqual(stored.max_tool_calls, 3)
        self.assertTrue(stored.debug_logging)
        self.assertEqual(stored.request_timeout_seconds, 45.0)

    async def test_defaults_request_timeout_seconds_to_none(self) -> None:
        error = await self.service.create_agent("recruiter", "prompt")

        self.assertIsNone(error)
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertIsNone(stored.request_timeout_seconds)

    async def test_rejects_an_invalid_agent_key(self) -> None:
        for bad_key in ["Recruiter", "1recruiter", "recruiter!", "", "re cruiter"]:
            with self.subTest(bad_key=bad_key):
                error = await self.service.create_agent(bad_key, "prompt")
                self.assertIsNotNone(error)
                self.assertEqual(self.registrar.registered, [])

    async def test_rejects_a_reserved_subcommand_name(self) -> None:
        for reserved in RESERVED_AGENT_KEYS:
            with self.subTest(reserved=reserved):
                error = await self.service.create_agent(reserved, "prompt")
                self.assertIsNotNone(error)
                self.assertIn("reserved", error)

    async def test_rejects_an_empty_system_prompt(self) -> None:
        error = await self.service.create_agent("recruiter", "   ")

        self.assertIsNotNone(error)
        self.assertEqual(self.registrar.registered, [])

    async def test_rejects_a_non_positive_max_tool_calls(self) -> None:
        error = await self.service.create_agent("recruiter", "prompt", max_tool_calls=0)

        self.assertIsNotNone(error)
        self.assertEqual(self.registrar.registered, [])

    async def test_rejects_a_bool_max_tool_calls(self) -> None:
        error = await self.service.create_agent("recruiter", "prompt", max_tool_calls=True)

        self.assertIsNotNone(error)

    async def test_rejects_a_non_positive_request_timeout_seconds(self) -> None:
        error = await self.service.create_agent(
            "recruiter", "prompt", request_timeout_seconds=0
        )

        self.assertIsNotNone(error)
        self.assertEqual(self.registrar.registered, [])

    async def test_rejects_a_bool_request_timeout_seconds(self) -> None:
        error = await self.service.create_agent(
            "recruiter", "prompt", request_timeout_seconds=True
        )

        self.assertIsNotNone(error)

    async def test_rejects_a_duplicate_agent_key_without_re_registering(self) -> None:
        await self.service.create_agent("recruiter", "first prompt")

        error = await self.service.create_agent("recruiter", "second prompt")

        self.assertIsNotNone(error)
        self.assertIn("already exists", error)
        self.assertEqual(len(self.registrar.registered), 1)
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertEqual(stored.system_prompt, "first prompt")

    async def test_surfaces_a_cross_owner_collision_without_persisting(self) -> None:
        self.registrar.fail_with = "agent_key 'recruiter' is already registered by 'Architect'"
        error = await self.service.create_agent("recruiter", "prompt")

        self.assertEqual(error, self.registrar.fail_with)
        self.assertIsNone(await self.repository.get_agent("recruiter"))


class TestRemoveAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeAgentRepository()
        self.registrar = FakeAgentRegistrar()
        self.service = BootcampService(self.repository, registrar=self.registrar)

    async def test_unregisters_and_deletes(self) -> None:
        await self.service.create_agent("recruiter", "prompt")

        error = await self.service.remove_agent("recruiter")

        self.assertIsNone(error)
        self.assertEqual(self.registrar.unregistered, ["recruiter"])
        self.assertIsNone(await self.repository.get_agent("recruiter"))

    async def test_returns_an_error_for_an_unknown_agent(self) -> None:
        error = await self.service.remove_agent("ghost")

        self.assertIsNotNone(error)
        self.assertEqual(self.registrar.unregistered, [])


class TestEditAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeAgentRepository()
        self.registrar = FakeAgentRegistrar()
        self.service = BootcampService(self.repository, registrar=self.registrar)

    async def asyncSetUp(self) -> None:
        await self.service.create_agent("recruiter", "prompt")
        self.registrar.registered.clear()

    async def test_set_permission_group_re_registers_with_the_new_value(self) -> None:
        error = await self.service.set_permission_group("recruiter", "keyholder")

        self.assertIsNone(error)
        self.assertEqual([a.permission_group for a in self.registrar.registered], ["keyholder"])
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertEqual(stored.permission_group, "keyholder")

    async def test_set_permission_group_on_an_unknown_agent_is_an_error(self) -> None:
        error = await self.service.set_permission_group("ghost", "keyholder")

        self.assertIsNotNone(error)
        self.assertEqual(self.registrar.registered, [])

    async def test_set_max_tool_calls_updates_config_without_re_registering(self) -> None:
        error = await self.service.set_max_tool_calls("recruiter", 5)

        self.assertIsNone(error)
        self.assertEqual(self.registrar.registered, [])
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertEqual(stored.max_tool_calls, 5)

    async def test_set_max_tool_calls_rejects_non_positive_values(self) -> None:
        error = await self.service.set_max_tool_calls("recruiter", 0)

        self.assertIsNotNone(error)

    async def test_set_request_timeout_updates_config_without_re_registering(self) -> None:
        error = await self.service.set_request_timeout("recruiter", 45.0)

        self.assertIsNone(error)
        self.assertEqual(self.registrar.registered, [])
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertEqual(stored.request_timeout_seconds, 45.0)

    async def test_set_request_timeout_none_resets_to_the_default(self) -> None:
        await self.service.set_request_timeout("recruiter", 45.0)

        error = await self.service.set_request_timeout("recruiter", None)

        self.assertIsNone(error)
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertIsNone(stored.request_timeout_seconds)

    async def test_set_request_timeout_rejects_non_positive_values(self) -> None:
        error = await self.service.set_request_timeout("recruiter", 0)

        self.assertIsNotNone(error)

    async def test_set_request_timeout_on_an_unknown_agent_is_an_error(self) -> None:
        error = await self.service.set_request_timeout("ghost", 45.0)

        self.assertIsNotNone(error)

    async def test_set_debug_logging_updates_config_without_re_registering(self) -> None:
        error = await self.service.set_debug_logging("recruiter", True)

        self.assertIsNone(error)
        self.assertEqual(self.registrar.registered, [])
        stored = await self.repository.get_agent("recruiter")
        assert stored is not None
        self.assertTrue(stored.debug_logging)


class TestRestoreAll(unittest.IsolatedAsyncioTestCase):
    async def test_re_registers_every_persisted_agent(self) -> None:
        repository = FakeAgentRepository()
        await repository.save_agent(CustomAgent(agent_key="recruiter", system_prompt="p1"))
        await repository.save_agent(CustomAgent(agent_key="onboarder", system_prompt="p2"))
        registrar = FakeAgentRegistrar()
        service = BootcampService(repository, registrar=registrar)

        errors = await service.restore_all()

        self.assertEqual(errors, {})
        self.assertEqual(
            sorted(a.agent_key for a in registrar.registered), ["onboarder", "recruiter"]
        )

    async def test_collects_a_per_agent_error_without_raising(self) -> None:
        repository = FakeAgentRepository()
        await repository.save_agent(CustomAgent(agent_key="recruiter", system_prompt="p1"))
        registrar = FakeAgentRegistrar(fail_with="collision")
        service = BootcampService(repository, registrar=registrar)

        errors = await service.restore_all()

        self.assertEqual(errors, {"recruiter": "collision"})
        # The persisted entry is left in place either way -- the bot owner
        # can retry once the issue is fixed, same convention telephonepole's
        # own restore_all documents.
        self.assertIsNotNone(await repository.get_agent("recruiter"))


class TestListAndGet(unittest.IsolatedAsyncioTestCase):
    async def test_list_and_get_pass_through_to_the_repository(self) -> None:
        repository = FakeAgentRepository()
        registrar = FakeAgentRegistrar()
        service = BootcampService(repository, registrar=registrar)
        await service.create_agent("recruiter", "prompt")

        self.assertEqual([a.agent_key for a in await service.list_agents()], ["recruiter"])
        agent = await service.get_agent("recruiter")
        assert agent is not None
        self.assertEqual(agent.agent_key, "recruiter")
        self.assertIsNone(await service.get_agent("ghost"))
