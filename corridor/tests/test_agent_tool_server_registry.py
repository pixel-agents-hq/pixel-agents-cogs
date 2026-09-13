"""AgentToolServerRegistry against a plain fake `McpTools` client pool --
same "no real network needed for registration/gating logic" bar
ToolRegistryService's own test suite sets; McpClientPool's own real-server
coverage lives in test_mcp_client.py instead."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from mcp import types as mcp_types

from ..application.agent_tool_server_registry import FRESH_TTL_SECONDS, AgentToolServerRegistry
from ..domain.agent_tool_server import McpCallOptions, RegisteredMcpServer
from ..infrastructure.mcp_client import McpRequestError


def _tool(name: str) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name, description=f"{name} tool.", inputSchema={"type": "object", "properties": {}}
    )


class FakeClock:
    """Same controllable-clock shape `test_model_catalog_service.py` uses
    for `ModelCatalogService`."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeClientPool:
    def __init__(self, tools_by_url: dict[str, tuple[mcp_types.Tool, ...] | Exception]) -> None:
        self._tools_by_url = tools_by_url
        self.calls: list[tuple[str, str, Mapping[str, Any], float | None]] = []
        self.list_tools_calls: list[str] = []

    async def list_tools(self, base_url: str) -> tuple[mcp_types.Tool, ...]:
        self.list_tools_calls.append(base_url)
        if base_url not in self._tools_by_url:
            raise McpRequestError(f"no such server: {base_url}")
        result = self._tools_by_url[base_url]
        if isinstance(result, Exception):
            raise result
        return result

    async def call_tool(
        self,
        base_url: str,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append((base_url, name, arguments, timeout_seconds))
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
        self.assertEqual(
            pool.calls, [("http://s/mcp", "report_error", {"what_happened": "x"}, None)]
        )

    async def test_wrapped_tool_handler_forwards_mcp_call_options_timeout(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("get_job"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="Animator", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="Animator",
        )

        (tool,) = await registry.list_tools_for("animator")
        result = await tool.handler(McpCallOptions(timeout_seconds=90.0), {"job_id": "j1"})

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(pool.calls, [("http://s/mcp", "get_job", {"job_id": "j1"}, 90.0)])

    async def test_wrapped_tool_handler_ignores_a_non_mcp_call_options_ctx(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        registry = AgentToolServerRegistry(pool)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        (tool,) = await registry.list_tools_for("architect")
        await tool.handler(object(), {})  # e.g. a real Discord commands.Context

        self.assertEqual(pool.calls, [("http://s/mcp", "report_error", {}, None)])

    async def test_wrapped_tool_handler_reports_call_failure_as_status_error(self) -> None:
        class _FailingPool(_FakeClientPool):
            async def call_tool(
                self,
                base_url: str,
                name: str,
                arguments: Mapping[str, Any],
                *,
                timeout_seconds: float | None = None,
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


class TestAgentToolServerRegistryTtlCache(unittest.IsolatedAsyncioTestCase):
    async def test_second_call_within_ttl_serves_the_cache_without_refetching(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        clock = FakeClock()
        registry = AgentToolServerRegistry(pool, clock=clock)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        clock.advance(FRESH_TTL_SECONDS - 1)
        tools = await registry.list_tools_for("architect")

        self.assertEqual([t.name for t in tools], ["report_error"])
        self.assertEqual(pool.list_tools_calls, ["http://s/mcp"])

    async def test_call_past_the_ttl_refetches_and_picks_up_a_schema_change(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        clock = FakeClock()
        registry = AgentToolServerRegistry(pool, clock=clock)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        pool._tools_by_url["http://s/mcp"] = (_tool("report_error"), _tool("suggest_improvement"))
        clock.advance(FRESH_TTL_SECONDS + 1)
        tools = await registry.list_tools_for("architect")

        self.assertEqual(sorted(t.name for t in tools), ["report_error", "suggest_improvement"])
        self.assertEqual(pool.list_tools_calls, ["http://s/mcp", "http://s/mcp"])

    async def test_a_refreshed_entry_stays_fresh_until_the_ttl_elapses_again(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        clock = FakeClock()
        registry = AgentToolServerRegistry(pool, clock=clock)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        clock.advance(FRESH_TTL_SECONDS + 1)
        await registry.list_tools_for("architect")
        clock.advance(FRESH_TTL_SECONDS - 1)
        await registry.list_tools_for("architect")

        self.assertEqual(pool.list_tools_calls, ["http://s/mcp", "http://s/mcp"])

    async def test_failed_refetch_past_the_ttl_serves_the_stale_cache(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        clock = FakeClock()
        registry = AgentToolServerRegistry(pool, clock=clock)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        pool._tools_by_url["http://s/mcp"] = McpRequestError("upstream unreachable")
        clock.advance(FRESH_TTL_SECONDS + 1)
        tools = await registry.list_tools_for("architect")

        self.assertEqual([t.name for t in tools], ["report_error"])

    async def test_a_stale_failed_refetch_is_retried_on_the_next_call(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": McpRequestError("upstream unreachable")})
        clock = FakeClock()
        registry = AgentToolServerRegistry(pool, clock=clock)
        # Seed a cache entry directly through a successful registration,
        # then only start failing afterwards.
        pool._tools_by_url["http://s/mcp"] = (_tool("report_error"),)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )
        pool._tools_by_url["http://s/mcp"] = McpRequestError("still unreachable")

        clock.advance(FRESH_TTL_SECONDS + 1)
        await registry.list_tools_for("architect")
        clock.advance(1)
        await registry.list_tools_for("architect")

        # Both post-TTL calls attempted a refetch (neither one cached a
        # failure), on top of the initial successful registration fetch.
        self.assertEqual(pool.list_tools_calls, ["http://s/mcp"] * 3)

    async def test_register_forces_an_immediate_refresh_even_within_the_ttl(self) -> None:
        pool = _FakeClientPool({"http://s/mcp": (_tool("report_error"),)})
        clock = FakeClock()
        registry = AgentToolServerRegistry(pool, clock=clock)
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )

        pool._tools_by_url["http://s/mcp"] = (_tool("report_error"), _tool("suggest_improvement"))
        clock.advance(1)  # well within FRESH_TTL_SECONDS
        await registry.register(
            RegisteredMcpServer(
                owner="SuggestionBox", base_url="http://s/mcp", agent_allowed=_allow_all
            ),
            owner="SuggestionBox",
        )
        tools = await registry.list_tools_for("architect")

        self.assertEqual(sorted(t.name for t in tools), ["report_error", "suggest_improvement"])
        self.assertEqual(pool.list_tools_calls, ["http://s/mcp", "http://s/mcp"])


if __name__ == "__main__":
    unittest.main()
