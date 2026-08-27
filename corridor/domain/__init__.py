from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_directory import RegisteredAgent, card_with_url

from .llm_tools import LLMToolSpec, ToolDescription, infer_parameters, llm_tool, llm_tool_spec
from .models import (
    EMPLOYEE_KEY,
    OWNER_KEY,
    RESERVED_GROUP_KEYS,
    A2ASettings,
    AgentActivity,
    AgentActivityEvent,
    AgentHighlighted,
    AgentPresenceChanged,
    AgentRef,
    AgentReplied,
    AgentStatusChanged,
    AgentToolStarted,
    AgentUnhighlighted,
    FooterOverride,
    GuildSettings,
    IconPreference,
    IconSource,
    LLMSettings,
    MemberCapabilities,
    PermissionGroupDef,
    PermissionSettings,
    RegisteredTool,
    RenderedReply,
    ReplyField,
    ReplyIdentity,
    ReplyMode,
    ReplyPreferences,
    ToolAvailabilityCheck,
    ToolHandler,
    ToolVisibilityFilter,
)

__all__ = [
    "EMPLOYEE_KEY",
    "LLMToolSpec",
    "OWNER_KEY",
    "RESERVED_GROUP_KEYS",
    "A2ASettings",
    "AgentActivity",
    "AgentActivityEvent",
    "AgentHighlighted",
    "AgentPresenceChanged",
    "AgentRef",
    "AgentReplied",
    "AgentStatusChanged",
    "AgentToolStarted",
    "AgentUnhighlighted",
    "FooterOverride",
    "GuildSettings",
    "IconPreference",
    "IconSource",
    "LLMSettings",
    "MemberCapabilities",
    "PermissionGroupDef",
    "PermissionSettings",
    "RegisteredAgent",
    "RegisteredTool",
    "RenderedReply",
    "ReplyField",
    "ReplyIdentity",
    "ReplyMode",
    "ReplyPreferences",
    "ToolHandler",
    "ToolAvailabilityCheck",
    "ToolVisibilityFilter",
    "ToolDescription",
    "card_with_url",
    "infer_parameters",
    "llm_tool",
    "llm_tool_spec",
]

_AGENT_DIRECTORY_NAMES = {"RegisteredAgent", "card_with_url"}


def __getattr__(name: str) -> object:
    # a2a-sdk is a heavy, optional dependency for consumers of this package
    # that only need the plain-dataclass models above (e.g. pixelagents'
    # test conftest, which stubs redbot/discord.py but never installs
    # a2a-sdk) -- deferred here so `from corridor.domain import ReplyMode`
    # doesn't drag in agent_directory's a2a imports for them.
    if name in _AGENT_DIRECTORY_NAMES:
        from . import agent_directory

        return getattr(agent_directory, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
