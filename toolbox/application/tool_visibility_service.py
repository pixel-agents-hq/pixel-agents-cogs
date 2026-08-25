"""Framework-agnostic use case: is a registered LLM tool currently
visible, given an optional guild.

Depends only on the ToolVisibilityRepository protocol below, never on
Red's Config directly -- same pattern as NodeService/ToolSelectionService.
"""

from __future__ import annotations

from typing import Protocol


class ToolVisibilityRepository(Protocol):
    """The persistence boundary ToolVisibilityService depends on."""

    async def get_default(self, tool_name: str) -> bool | None: ...

    async def set_default(self, tool_name: str, enabled: bool) -> None: ...

    async def all_defaults(self) -> dict[str, bool]: ...

    async def all_overrides(self, guild_id: int) -> dict[str, bool]: ...

    async def get_override(self, guild_id: int, tool_name: str) -> bool | None: ...

    async def set_override(self, guild_id: int, tool_name: str, enabled: bool) -> None: ...

    async def clear_override(self, guild_id: int, tool_name: str) -> None: ...


class ToolVisibilityService:
    def __init__(self, repository: ToolVisibilityRepository) -> None:
        self._repository = repository

    async def is_enabled(self, tool_name: str, guild_id: int | None) -> bool:
        """A guild override, when present, always wins over the global
        default for that guild. No override and no explicit default means
        visible -- a freshly selected or freshly decorated tool starts
        enabled; the owner opts it *out*, not in."""

        if guild_id is not None:
            override = await self._repository.get_override(guild_id, tool_name)
            if override is not None:
                return override
        default = await self._repository.get_default(tool_name)
        return True if default is None else default

    async def set_default(self, tool_name: str, enabled: bool) -> None:
        await self._repository.set_default(tool_name, enabled)

    async def all_defaults(self) -> dict[str, bool]:
        """Every tool name with an explicit global default set -- absent
        means "no explicit default", not "disabled" (see `is_enabled`).
        Lets a UI render every visible row's current state with one Config
        read instead of one per row."""

        return await self._repository.all_defaults()

    async def all_overrides(self, guild_id: int) -> dict[str, bool]:
        """Every tool name with an explicit override for `guild_id` --
        same bulk-read rationale as `all_defaults`."""

        return await self._repository.all_overrides(guild_id)

    async def get_override(self, guild_id: int, tool_name: str) -> bool | None:
        """`None` means this guild has no explicit override -- it follows
        the global default. Exists for the UI to render an override's
        presence distinctly from its value; `is_enabled` alone can't tell
        those apart."""

        return await self._repository.get_override(guild_id, tool_name)

    async def set_override(self, guild_id: int, tool_name: str, enabled: bool) -> None:
        await self._repository.set_override(guild_id, tool_name, enabled)

    async def clear_override(self, guild_id: int, tool_name: str) -> None:
        await self._repository.clear_override(guild_id, tool_name)
