"""Typed builders for the pixel-agents webview's agent-visualization protocol.

Field shapes are verified against the vendored `pixel-agents/core/asyncapi.yaml`
(the generated source of truth for the webview's message contract) rather than
inferred from client code.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class _AgentCreatedRequired(TypedDict):
    type: Literal["agentCreated"]
    id: int
    folderName: str
    palette: int
    hueShift: int


class AgentCreatedMessage(_AgentCreatedRequired, total=False):
    isExternal: bool


class AgentClosedMessage(TypedDict):
    type: Literal["agentClosed"]
    id: int


class _AgentTeamInfoRequired(TypedDict):
    type: Literal["agentTeamInfo"]
    id: int
    agentName: str


class AgentTeamInfoMessage(_AgentTeamInfoRequired, total=False):
    teamName: str
    isTeamLead: bool
    leadAgentId: int
    teamUsesTmux: bool


class _AgentStatusRequired(TypedDict):
    type: Literal["agentStatus"]
    id: int
    status: Literal["active", "waiting"]


class AgentStatusMessage(_AgentStatusRequired, total=False):
    awaitingInput: bool


class AgentToolStartMessage(TypedDict):
    type: Literal["agentToolStart"]
    id: int
    toolId: str
    toolName: str
    status: str


class AgentToolDoneMessage(TypedDict):
    type: Literal["agentToolDone"]
    id: int
    toolId: str


class AgentToolsClearMessage(TypedDict):
    type: Literal["agentToolsClear"]
    id: int


class AgentToolPermissionMessage(TypedDict):
    type: Literal["agentToolPermission"]
    id: int


class AgentToolPermissionClearMessage(TypedDict):
    type: Literal["agentToolPermissionClear"]
    id: int


class SubagentToolStartMessage(TypedDict):
    type: Literal["subagentToolStart"]
    id: int
    parentToolId: str
    toolId: str
    status: str


class SubagentToolDoneMessage(TypedDict):
    type: Literal["subagentToolDone"]
    id: int
    parentToolId: str
    toolId: str


class SubagentClearMessage(TypedDict):
    type: Literal["subagentClear"]
    id: int
    parentToolId: str


class SubagentToolPermissionMessage(TypedDict):
    type: Literal["subagentToolPermission"]
    id: int
    parentToolId: str


class AgentContextUsageMessage(TypedDict):
    type: Literal["agentContextUsage"]
    id: int
    contextTokens: int
    maxContextTokens: int


def agent_created(
    agent_id: int,
    folder_name: str,
    palette: int,
    hue_shift: int,
    *,
    is_external: bool | None = None,
) -> AgentCreatedMessage:
    message: AgentCreatedMessage = {
        "type": "agentCreated",
        "id": agent_id,
        "folderName": folder_name,
        "palette": palette,
        "hueShift": hue_shift,
    }
    if is_external is not None:
        message["isExternal"] = is_external
    return message


def agent_closed(agent_id: int) -> AgentClosedMessage:
    return {"type": "agentClosed", "id": agent_id}


def agent_team_info(
    agent_id: int,
    display_name: str,
    *,
    team_name: str | None = None,
    is_team_lead: bool | None = None,
    lead_agent_id: int | None = None,
    team_uses_tmux: bool | None = None,
) -> AgentTeamInfoMessage:
    message: AgentTeamInfoMessage = {
        "type": "agentTeamInfo",
        "id": agent_id,
        "agentName": display_name,
    }
    if team_name is not None:
        message["teamName"] = team_name
    if is_team_lead is not None:
        message["isTeamLead"] = is_team_lead
    if lead_agent_id is not None:
        message["leadAgentId"] = lead_agent_id
    if team_uses_tmux is not None:
        message["teamUsesTmux"] = team_uses_tmux
    return message


def agent_status(
    agent_id: int,
    status: Literal["active", "waiting"],
    *,
    awaiting_input: bool | None = None,
) -> AgentStatusMessage:
    message: AgentStatusMessage = {"type": "agentStatus", "id": agent_id, "status": status}
    if awaiting_input is not None:
        message["awaitingInput"] = awaiting_input
    return message


def agent_tool_start(
    agent_id: int,
    tool_id: str,
    tool_name: str,
    status: str,
) -> AgentToolStartMessage:
    return {
        "type": "agentToolStart",
        "id": agent_id,
        "toolId": tool_id,
        "toolName": tool_name,
        "status": status,
    }


def agent_tool_done(agent_id: int, tool_id: str) -> AgentToolDoneMessage:
    return {"type": "agentToolDone", "id": agent_id, "toolId": tool_id}


def agent_tools_clear(agent_id: int) -> AgentToolsClearMessage:
    return {"type": "agentToolsClear", "id": agent_id}


def agent_tool_permission(agent_id: int) -> AgentToolPermissionMessage:
    return {"type": "agentToolPermission", "id": agent_id}


def agent_tool_permission_clear(agent_id: int) -> AgentToolPermissionClearMessage:
    return {"type": "agentToolPermissionClear", "id": agent_id}


def subagent_tool_start(
    agent_id: int,
    parent_tool_id: str,
    tool_id: str,
    status: str,
) -> SubagentToolStartMessage:
    return {
        "type": "subagentToolStart",
        "id": agent_id,
        "parentToolId": parent_tool_id,
        "toolId": tool_id,
        "status": status,
    }


def subagent_tool_done(agent_id: int, parent_tool_id: str, tool_id: str) -> SubagentToolDoneMessage:
    return {
        "type": "subagentToolDone",
        "id": agent_id,
        "parentToolId": parent_tool_id,
        "toolId": tool_id,
    }


def subagent_clear(agent_id: int, parent_tool_id: str) -> SubagentClearMessage:
    return {"type": "subagentClear", "id": agent_id, "parentToolId": parent_tool_id}


def subagent_tool_permission(agent_id: int, parent_tool_id: str) -> SubagentToolPermissionMessage:
    return {"type": "subagentToolPermission", "id": agent_id, "parentToolId": parent_tool_id}


def agent_context_usage(
    agent_id: int, context_tokens: int, max_context_tokens: int
) -> AgentContextUsageMessage:
    return {
        "type": "agentContextUsage",
        "id": agent_id,
        "contextTokens": context_tokens,
        "maxContextTokens": max_context_tokens,
    }
