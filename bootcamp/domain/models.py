"""Pure business models. Zero framework imports -- this module never imports
discord.py or redbot, so it is trivially unit-testable without either
installed."""

from __future__ import annotations

from dataclasses import dataclass

# The reserved, always-unrestricted tier -- corridor's own EMPLOYEE_KEY
# (see corridor/domain/models.py), duplicated here as a plain string
# constant rather than importing corridor.domain at this layer: this
# module stays framework/corridor-agnostic like every other cog's domain
# package, and the value is part of corridor's public permission-group
# contract, not something that can drift independently.
DEFAULT_PERMISSION_GROUP = "employee"

DEFAULT_MAX_TOOL_CALLS = 8


@dataclass(frozen=True, slots=True)
class CustomAgent:
    """One bot-owner-created custom LLM agent: a name, a system prompt, and
    who may use it.

    `agent_key` is both the display name on its `AgentCard` and the mount
    path/`consult_<agent_key>` suffix corridor's `AgentDirectoryService`
    gives it once registered (see docs/agent-directory-design.md) -- there
    is no separate display name, unlike telephonepole's `name`/`base_url`
    split, since a bootcamp agent has no external URL identity to preserve
    across a rename.

    `permission_group` is a corridor permission-group key (see
    docs/corridor.md's Permissions section) gating who may *use* this
    agent -- both directly (`[p]bootcamp <agent_key> <prompt>`, checked via
    `corridor.require_permission`) and indirectly through pico (checked via
    `corridor.capabilities_satisfy` against the same key, stored on the
    `RegisteredAgent.required_permission_group` this agent registers with).
    Defaults to `DEFAULT_PERMISSION_GROUP` ("employee"), corridor's reserved
    always-satisfied tier, so a newly created agent is usable by anyone
    until its creator narrows it. *Creating/removing/editing* a custom
    agent is a separate, bot-owner-only concern (`adapters/commands.py`),
    not gated by this field at all.

    `max_tool_calls`/`debug_logging` are this agent's own per-turn budget
    and debug-event-streaming toggle, the same two settings architect's
    `GlobalSettings` carries -- just per-agent here instead of once
    process-wide, since bootcamp hosts an open-ended number of agents
    rather than being one itself."""

    agent_key: str
    system_prompt: str
    permission_group: str = DEFAULT_PERMISSION_GROUP
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    debug_logging: bool = False


__all__ = ["DEFAULT_MAX_TOOL_CALLS", "DEFAULT_PERMISSION_GROUP", "CustomAgent"]
