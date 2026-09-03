"""TelephonepoleService is fully testable without Red: plain in-memory fakes
satisfy the ServerRepository/McpRegistrar protocols, no unittest.mock
needed."""

from __future__ import annotations

import unittest

from ..application import AgentAllowedCheck, TelephonepoleService
from ..domain import ThirdPartyMcpServer


class FakeServerRepository:
    def __init__(self) -> None:
        self._servers: dict[str, ThirdPartyMcpServer] = {}
        self._agent_access: dict[str, dict[str, bool]] = {}

    async def list_servers(self) -> tuple[ThirdPartyMcpServer, ...]:
        return tuple(self._servers.values())

    async def get_server(self, name: str) -> ThirdPartyMcpServer | None:
        return self._servers.get(name)

    async def save_server(self, server: ThirdPartyMcpServer) -> None:
        self._servers[server.name] = server

    async def delete_server(self, name: str) -> None:
        self._servers.pop(name, None)
        self._agent_access.pop(name, None)

    async def is_agent_enabled(self, name: str, agent_key: str) -> bool:
        return self._agent_access.get(name, {}).get(agent_key, False)

    async def set_agent_enabled(self, name: str, agent_key: str, value: bool) -> None:
        self._agent_access.setdefault(name, {})[agent_key] = value


class FakeRegistrar:
    def __init__(self, register_error: str | None = None, raise_value_error: bool = False) -> None:
        self.register_error = register_error
        self.raise_value_error = raise_value_error
        self.registered: dict[str, AgentAllowedCheck] = {}
        self.unregistered: list[str] = []
        self.register_calls: list[tuple[str, str]] = []

    async def register(
        self, name: str, base_url: str, agent_allowed: AgentAllowedCheck
    ) -> str | None:
        self.register_calls.append((name, base_url))
        if self.raise_value_error:
            raise ValueError(f"{base_url!r} is already registered by someone else")
        if self.register_error is not None:
            return self.register_error
        self.registered[base_url] = agent_allowed
        return None

    def unregister(self, base_url: str) -> None:
        self.unregistered.append(base_url)
        self.registered.pop(base_url, None)


class TestAddServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeServerRepository()
        self.registrar = FakeRegistrar()
        self.service = TelephonepoleService(self.repository, registrar=self.registrar)

    async def test_registers_with_corridor_and_persists_on_success(self) -> None:
        error = await self.service.add_server("freecad", "http://freecad-mcp:8765/mcp")

        self.assertIsNone(error)
        self.assertEqual(
            self.registrar.register_calls, [("freecad", "http://freecad-mcp:8765/mcp")]
        )
        server = await self.repository.get_server("freecad")
        assert server is not None
        self.assertEqual(server.base_url, "http://freecad-mcp:8765/mcp")

    async def test_rejects_a_name_already_in_use_without_calling_the_registrar_again(self) -> None:
        await self.service.add_server("freecad", "http://freecad-mcp:8765/mcp")

        error = await self.service.add_server("freecad", "http://other-host:9000/mcp")

        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("already registered", error)
        self.assertEqual(len(self.registrar.register_calls), 1)
        server = await self.repository.get_server("freecad")
        assert server is not None
        self.assertEqual(server.base_url, "http://freecad-mcp:8765/mcp")

    async def test_a_registrar_connection_failure_is_not_persisted(self) -> None:
        service = TelephonepoleService(
            self.repository, registrar=FakeRegistrar(register_error="connection refused")
        )

        error = await service.add_server("freecad", "http://freecad-mcp:8765/mcp")

        self.assertEqual(error, "connection refused")
        self.assertIsNone(await self.repository.get_server("freecad"))

    async def test_a_registrar_owner_collision_is_caught_and_returned_as_an_error(self) -> None:
        service = TelephonepoleService(
            self.repository, registrar=FakeRegistrar(raise_value_error=True)
        )

        error = await service.add_server("freecad", "http://freecad-mcp:8765/mcp")

        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("already registered", error)
        self.assertIsNone(await self.repository.get_server("freecad"))


class TestRemoveServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeServerRepository()
        self.registrar = FakeRegistrar()
        self.service = TelephonepoleService(self.repository, registrar=self.registrar)

    async def test_unregisters_with_corridor_and_deletes_on_success(self) -> None:
        await self.service.add_server("freecad", "http://freecad-mcp:8765/mcp")

        error = await self.service.remove_server("freecad")

        self.assertIsNone(error)
        self.assertEqual(self.registrar.unregistered, ["http://freecad-mcp:8765/mcp"])
        self.assertIsNone(await self.repository.get_server("freecad"))

    async def test_removing_an_unknown_name_returns_an_error_without_touching_the_registrar(
        self,
    ) -> None:
        error = await self.service.remove_server("does-not-exist")

        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("no server named", error)
        self.assertEqual(self.registrar.unregistered, [])


class TestRestoreAll(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeServerRepository()

    async def test_re_registers_every_persisted_server(self) -> None:
        registrar = FakeRegistrar()
        service = TelephonepoleService(self.repository, registrar=registrar)
        await service.add_server("freecad", "http://freecad-mcp:8765/mcp")
        await service.add_server("other", "http://other-host:9000/mcp")
        registrar.register_calls.clear()

        errors = await service.restore_all()

        self.assertEqual(errors, {})
        self.assertEqual(
            sorted(registrar.register_calls),
            [("freecad", "http://freecad-mcp:8765/mcp"), ("other", "http://other-host:9000/mcp")],
        )

    async def test_collects_per_server_errors_without_raising(self) -> None:
        await self.repository.save_server(
            ThirdPartyMcpServer(name="broken", base_url="http://unreachable:1/mcp")
        )
        service = TelephonepoleService(
            self.repository, registrar=FakeRegistrar(register_error="connection refused")
        )

        errors = await service.restore_all()

        self.assertEqual(errors, {"broken": "connection refused"})

    async def test_a_failed_restore_keeps_the_persisted_entry(self) -> None:
        await self.repository.save_server(
            ThirdPartyMcpServer(name="broken", base_url="http://unreachable:1/mcp")
        )
        service = TelephonepoleService(
            self.repository, registrar=FakeRegistrar(register_error="connection refused")
        )

        await service.restore_all()

        self.assertIsNotNone(await self.repository.get_server("broken"))


class TestAgentAllowedClosure(unittest.IsolatedAsyncioTestCase):
    async def test_agent_allowed_reads_the_repository_for_the_registering_name(self) -> None:
        repository = FakeServerRepository()
        registrar = FakeRegistrar()
        service = TelephonepoleService(repository, registrar=registrar)
        await service.add_server("freecad", "http://freecad-mcp:8765/mcp")
        agent_allowed = registrar.registered["http://freecad-mcp:8765/mcp"]

        self.assertFalse(await agent_allowed("architect"))

        await repository.set_agent_enabled("freecad", "architect", True)

        self.assertTrue(await agent_allowed("architect"))
        self.assertFalse(await agent_allowed("painter"))
