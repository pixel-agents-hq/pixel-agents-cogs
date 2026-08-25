"""Framework-agnostic use case: which command qualified names has the bot
owner opted into LLM-tool wrapping.

Depends only on the ToolSelectionRepository protocol below, never on Red's
Config directly -- same pattern as NodeService/NodeRepository in service.py.
"""

from __future__ import annotations

from typing import Protocol


class ToolSelectionRepository(Protocol):
    """The persistence boundary ToolSelectionService depends on."""

    async def list_selected(self) -> frozenset[str]: ...

    async def add_selected(self, qualified_name: str) -> None: ...

    async def remove_selected(self, qualified_name: str) -> None: ...


class ToolSelectionService:
    def __init__(self, repository: ToolSelectionRepository) -> None:
        self._repository = repository

    async def list_selected(self) -> frozenset[str]:
        return await self._repository.list_selected()

    async def select(self, qualified_name: str) -> None:
        await self._repository.add_selected(qualified_name)

    async def deselect(self, qualified_name: str) -> None:
        await self._repository.remove_selected(qualified_name)
