"""Agent directory domain model -- corridor's one deliberate exception to
this package's "zero framework types" convention (see `RegisteredTool`'s
own docstring in `models.py`). Isolated in its own module, rather than
folded into `models.py`, so that module's purity stays intact and this
exception stays visibly scoped to exactly the types that need it. See
docs/agent-directory-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.types import AgentCard
from a2a.utils import TransportProtocol


@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    """One agent's A2A surface, registered into `AgentDirectoryService` by
    the agent's own cog at its `cog_load` -- the in-process registration
    shape `ToolRegistryService`/`EventBusService` already use, generalized
    to a network-reachable agent instead of an in-process callable or
    subscriber.

    `card` is the real a2a-sdk `AgentCard` the registering agent built
    (name, description, skills, ...); corridor overwrites its one
    `supported_interfaces[0].url` (and `icon_url`, when `avatar_path` is
    set) via `card_with_url` below before storing it, since the
    registering agent has no way to know what host/port/mount-path it
    will ultimately be reachable at -- that's corridor's shared
    listener's own configuration, not the agent's.

    `executor` is the a2a-sdk `AgentExecutor` extension point the
    registering agent built to run its own tool-calling loop against one
    inbound A2A message. Corridor never inspects it -- it's only wired
    into a `DefaultRequestHandler` mounted under this agent's own path
    (`/<agent_key>/`) on corridor's one shared listener
    (`corridor/infrastructure/a2a_server.py`).

    `avatar_path`, when set, is a bundled image file on the registering
    agent's own disk (same "conventional path, existence checked fresh
    on every request" convention `ReplySender` uses for a cog's own
    author icon, see docs/reply-identity-design.md) -- corridor serves it
    at `/<agent_key>/avatar.png` on its shared A2A listener and sets the
    card's `icon_url` to that address, so a consulting agent (pico) can
    show it as a `FooterOverride` distinct from its own author identity."""

    agent_key: str
    card: AgentCard
    executor: AgentExecutor
    avatar_path: Path | None = None


def card_with_url(card: AgentCard, url: str, *, icon_url: str | None = None) -> AgentCard:
    """Return a copy of `card` with its one supported interface's URL
    replaced by `url`, and `icon_url` set when given. `AgentCard` is a
    protobuf message (see `docs/architect-design.md` §9 -- `a2a-sdk`'s
    wire types are generated from `a2a_pb2`, not plain pydantic models),
    so this is `CopyFrom` + clear-and-rebuild the repeated field, not a
    dataclasses.replace-style call."""

    rewritten = AgentCard()
    rewritten.CopyFrom(card)
    del rewritten.supported_interfaces[:]
    rewritten.supported_interfaces.add(url=url, protocol_binding=TransportProtocol.JSONRPC.value)
    if icon_url is not None:
        rewritten.icon_url = icon_url
    return rewritten


__all__ = ["RegisteredAgent", "card_with_url"]
