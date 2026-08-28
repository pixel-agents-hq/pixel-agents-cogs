"""AgentToolServerRegistry against a plain fake `McpTools` client pool --
same "no real network needed for registration/gating logic" bar
ToolRegistryService's own test suite sets; McpClientPool's own real-server
coverage lives in test_mcp_client.py instead."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from mcp import types as mcp_types

from ..application.agent_tool_server_registry import AgentToolServerRegistry
from ..domain.agent_tool_server import RegisteredMcpServer
from ..infrastructure.mcp_client import McpRequestError


def _tool(name: str) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name, description=f"{name} tool.", inputSchema={"type": "object", "properties": {}}
    )


class _FakeClientPool:
    def __init__(self, tools_by_url: dict[str, tuple[mcp_types.Tool, ...]]) -> None:
        self._tools_by_url = tools_by_url
        self.calls: list[tuple[str, str, Mapping[str, Any]]] = []

    async def list_tools(self, base_url: str) -> tuple[mcp_types.Tool, ...]:
        if base_url not in self._tools_by_url:
            raise McpRequestError(f"no such server: {base_url}")
        return self._tools_by_url[base_url]

    async def call_tool(
        self, base_url: str, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.calls.append((base_url, name, arguments))
        return {"status": "ok"}


async def _allow_all(_agent_key: str) -> bool:
    return True


async def _deny_all(_agent_key: str) -> bool:
    return False


class TestAgentToolServerRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_list_tools_for_with_nothing_registered_is_empty(self) -> None:
        registry = AgentToolServerRegistry(_FakeClientPool({}))

        self.assertEqual(await registry.list_tools_for("architect"), ())

    async def test_registered_servers_tools_are_listed_for_an_allowed_agent(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        error = await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        self.assertIsNone(error)
        tools = await registry.list_tools_for("architect")
        self.assertEqual([t.name for t in tools], ["report_error"])

    async def test_registration_failure_returns_error_and_registers_nothing(self) -> None:
        registry = AgentToolServerRegistry(_FakeClientPool({}))

        error = await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://unreachable/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        self.assertIsNotNone(error)
        self.assertEqual(await registry.list_tools_for("architect"), ())

    async def test_agent_allowed_false_omits_that_servers_tools(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_deny_all
            ),
            owner="SuggestionBox",
        )

        self.assertEqual(await registry.list_tools_for("architect"), ())

    async def test_agent_allowed_raising_omits_that_servers_tools(self) -> None:
        async def _raises(_agent_key: str) -> bool:
            raise RuntimeError("boom")

        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_raises
            ),
            owner="SuggestionBox",
        )

        self.assertEqual(await registry.list_tools_for("architect"), ())

    async def test_same_owner_reregistration_overwrites(self) -> None:
        pool = _FakeClientPool(
            {"http://s/mcp": (_tool("report_error"), _tool("suggest_improvement"))}
        )
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        tools = await registry.list_tools_for("architect")
        self.assertEqual(sorted(t.name for t in tools), ["report_error", "suggest_improvement"])

    async def test_different_owner_url_collision_raises(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        with self.assertRaises(ValueError):
            await registry.register(
                RegisteredMcpServer(
                    owner="Other", base_url="http://s/mcp", agent_allowed=_allow_all
                ),
                owner="Other",
            )

    async def test_unregister_owner_drops_only_that_owners_servers(self) -> None:
        pool = _FakeClientPool(
            {"http://a/mcp": (_tool("a_tool"),), "http://b/mcp": (_tool("b_tool"),)}
        )
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(owner="A", base_url="http://a/mcp", agent_allowed=_allow_all),
            owner="A",
        )
        await registry.register(
            RegisteredMcpServer(owner="B", base_url="http://b/mcp", agent_allowed=_allow_all),
            owner="B",
        )

        registry.unregister_owner("A")

        tools = await registry.list_tools_for("architect")
        self.assertEqual([t.name for t in tools], ["b_tool"])

    async def test_unregister_owner_for_unknown_owner_is_a_noop(self) -> None:
        registry = AgentToolServerRegistry(_FakeClientPool({}))
        registry.unregister_owner("nobody")  # must not raise

    async def test_unregister_removes_one_server_by_url(self) -> None:
        pool = _FakeClientPool(
            {"http://a/mcp": (_tool("a_tool"),), "http://b/mcp": (_tool("b_tool"),)}
        )
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(owner="A", base_url="http://a/mcp", agent_allowed=_allow_all),
            owner="A",
        )
        await registry.register(
            RegisteredMcpServer(owner="A", base_url="http://b/mcp", agent_allowed=_allow_all),
            owner="A",
        )

        registry.unregister("http://a/mcp")

        tools = await registry.list_tools_for("architect")
        self.assertEqual([t.name for t in tools], ["b_tool"])

    async def test_wrapped_tool_handler_calls_through_to_the_client_pool(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        (tool,) = await registry.list_tools_for("architect")
        result = await tool.handler(None, {"what_happened": "x"})

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(pool.calls, [("http://s/mcp", "report_error", {"what_happened": "x"})])

    async def test_wrapped_tool_handler_reports_call_failure_as_status_error(self) -> None:
        class _FailingPool(_FakeClientPool):
            async def call_tool(
                self, base_url: str, name: str, arguments: Mapping[str, Any]
            ) -> Mapping[str, Any]:
                raise McpRequestError("unreachable")

        pool = _FailingPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        (tool,) = await registry.list_tools_for("architect")
        result = await tool.handler(None, {})

        self.assertEqual(result, {"status": "error", "error": "unreachable"})


if __name__ == "__main__":
    unittest.main()
