"""In-process registry of cross-cog, LLM-callable tools.

Depends on nothing but stdlib -- no discord.py, no redbot, no pydantic --
the same pattern event_bus_service.py/permission_service.py/reply_service.py
already follow. See docs/corridor-tool-registry-design.md for the full
design rationale.
"""

from __future__ import annotations

from ..domain.models import RegisteredTool


class ToolRegistryService:
    """One registry per bot process, not per guild -- same scoping as
    EventBusService; any guild-specific behavior a tool needs is entirely
    the registering cog's own handler's concern, not this registry's."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[str, RegisteredTool]] = {}

    def register(self, tool: RegisteredTool, *, owner: str) -> None:
        """Register `tool` under `owner` (the registering cog's class name,
        matching subscribe_event's convention). Re-registering the same
        name under the same `owner` overwrites -- idempotent across repeat
        `cog_load` calls. A name collision from a *different* owner is a
        real authoring conflict between two cogs, so it raises instead of
        silently letting one shadow the other."""

        existing = self._tools.get(tool.name)
        if existing is not None and existing[0] != owner:
            raise ValueError(
                f"tool {tool.name!r} is already registered by {existing[0]!r}, "
                f"cannot re-register it for {owner!r}"
            )
        self._tools[tool.name] = (owner, tool)

    def unregister_owner(self, owner: str) -> None:
        """The registering cog's own responsibility, called from its own
        cog_unload -- the reverse direction of
        register_dependent/unregister_dependent (corridor does not
        track/cascade a registrant's lifecycle the way it does the opposite
        direction for a dependent cog)."""

        for name in [n for n, (o, _) in self._tools.items() if o == owner]:
            del self._tools[name]

    def list_tools(self) -> tuple[RegisteredTool, ...]:
        return tuple(tool for _, tool in self._tools.values())
