"""Unit tests for the outbound agent-visualization websocket contract.

Expected wire shapes are verified against the vendored
`pixel-agents/core/asyncapi.yaml` field definitions.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pixelagents.contracts.outbound import (
    agent_closed,
    agent_context_usage,
    agent_created,
    agent_selected,
    agent_status,
    agent_team_info,
    agent_tool_done,
    agent_tool_permission,
    agent_tool_permission_clear,
    agent_tool_start,
    agent_tools_clear,
    subagent_clear,
    subagent_tool_done,
    subagent_tool_permission,
    subagent_tool_start,
)


class TestOutboundBuilders:
    @pytest.mark.parametrize(
        ("builder", "expected"),
        [
            (
                lambda: agent_created(-1, "online", 2, 45),
                {
                    "type": "agentCreated",
                    "id": -1,
                    "folderName": "online",
                    "palette": 2,
                    "hueShift": 45,
                },
            ),
            (
                lambda: agent_created(-1, "online", 2, 45, is_external=True),
                {
                    "type": "agentCreated",
                    "id": -1,
                    "folderName": "online",
                    "palette": 2,
                    "hueShift": 45,
                    "isExternal": True,
                },
            ),
            (lambda: agent_closed(-1), {"type": "agentClosed", "id": -1}),
            (lambda: agent_selected(-1), {"type": "agentSelected", "id": -1}),
            (
                lambda: agent_team_info(-1, "Tin"),
                {"type": "agentTeamInfo", "id": -1, "agentName": "Tin"},
            ),
            (
                lambda: agent_team_info(
                    -1,
                    "Tin",
                    team_name="Alpha",
                    is_team_lead=True,
                    lead_agent_id=-1,
                    team_uses_tmux=False,
                ),
                {
                    "type": "agentTeamInfo",
                    "id": -1,
                    "agentName": "Tin",
                    "teamName": "Alpha",
                    "isTeamLead": True,
                    "leadAgentId": -1,
                    "teamUsesTmux": False,
                },
            ),
            (
                lambda: agent_status(-1, "waiting"),
                {"type": "agentStatus", "id": -1, "status": "waiting"},
            ),
            (
                lambda: agent_status(-1, "waiting", awaiting_input=True),
                {"type": "agentStatus", "id": -1, "status": "waiting", "awaitingInput": True},
            ),
            (
                lambda: agent_tool_start(-1, "rp-1", "Activity", "Listening"),
                {
                    "type": "agentToolStart",
                    "id": -1,
                    "toolId": "rp-1",
                    "toolName": "Activity",
                    "status": "Listening",
                },
            ),
            (
                lambda: agent_tool_done(-1, "rp-1"),
                {"type": "agentToolDone", "id": -1, "toolId": "rp-1"},
            ),
            (lambda: agent_tools_clear(-1), {"type": "agentToolsClear", "id": -1}),
            (
                lambda: agent_tool_permission(-1),
                {"type": "agentToolPermission", "id": -1},
            ),
            (
                lambda: agent_tool_permission_clear(-1),
                {"type": "agentToolPermissionClear", "id": -1},
            ),
            (
                lambda: subagent_tool_start(-1, "toolu_1", "sub-1", "Reading foo.ts"),
                {
                    "type": "subagentToolStart",
                    "id": -1,
                    "parentToolId": "toolu_1",
                    "toolId": "sub-1",
                    "status": "Reading foo.ts",
                },
            ),
            (
                lambda: subagent_tool_done(-1, "toolu_1", "sub-1"),
                {
                    "type": "subagentToolDone",
                    "id": -1,
                    "parentToolId": "toolu_1",
                    "toolId": "sub-1",
                },
            ),
            (
                lambda: subagent_clear(-1, "toolu_1"),
                {"type": "subagentClear", "id": -1, "parentToolId": "toolu_1"},
            ),
            (
                lambda: subagent_tool_permission(-1, "toolu_1"),
                {"type": "subagentToolPermission", "id": -1, "parentToolId": "toolu_1"},
            ),
            (
                lambda: agent_context_usage(-1, 12000, 200000),
                {
                    "type": "agentContextUsage",
                    "id": -1,
                    "contextTokens": 12000,
                    "maxContextTokens": 200000,
                },
            ),
        ],
    )
    def test_builder_matches_wire_shape(
        self,
        builder: Callable[[], object],
        expected: dict[str, object],
    ) -> None:
        assert builder() == expected
