"""Typed builders for stable, frequently emitted server messages."""

from __future__ import annotations

from typing import Literal, TypedDict

from .layout import RawOfficeLayout


class LayoutLoadedMessage(TypedDict):
    type: Literal["layoutLoaded"]
    layout: RawOfficeLayout | None


class AgentCreatedMessage(TypedDict):
    type: Literal["agentCreated"]
    id: int
    folderName: str
    palette: int
    hueShift: int


class AgentClosedMessage(TypedDict):
    type: Literal["agentClosed"]
    id: int


class AgentTeamInfoMessage(TypedDict):
    type: Literal["agentTeamInfo"]
    id: int
    agentName: str


class AgentStatusMessage(TypedDict):
    type: Literal["agentStatus"]
    id: int
    status: Literal["active", "waiting"]


class AgentToolStartMessage(TypedDict):
    type: Literal["agentToolStart"]
    id: int
    toolId: str
    toolName: str
    status: str


class AgentToolsClearMessage(TypedDict):
    type: Literal["agentToolsClear"]
    id: int


def layout_loaded(layout: RawOfficeLayout | None) -> LayoutLoadedMessage:
    return {"type": "layoutLoaded", "layout": layout}


def agent_created(
    agent_id: int,
    folder_name: str,
    palette: int,
    hue_shift: int,
) -> AgentCreatedMessage:
    return {
        "type": "agentCreated",
        "id": agent_id,
        "folderName": folder_name,
        "palette": palette,
        "hueShift": hue_shift,
    }


def agent_closed(agent_id: int) -> AgentClosedMessage:
    return {"type": "agentClosed", "id": agent_id}


def agent_team_info(agent_id: int, display_name: str) -> AgentTeamInfoMessage:
    return {"type": "agentTeamInfo", "id": agent_id, "agentName": display_name}


def agent_status(agent_id: int, status: Literal["active", "waiting"]) -> AgentStatusMessage:
    return {"type": "agentStatus", "id": agent_id, "status": status}


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


def agent_tools_clear(agent_id: int) -> AgentToolsClearMessage:
    return {"type": "agentToolsClear", "id": agent_id}
