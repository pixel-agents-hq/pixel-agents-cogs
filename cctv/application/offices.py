"""Every guild's live Discord office: one isolated pipeline per enabled guild."""

from __future__ import annotations

from collections.abc import Iterator

from .pipeline import CctvPipeline


class GuildOffices:
    """The open guild offices, keyed by guild id. A guild's pipeline exists
    exactly while CCTV is enabled for it; each owns its own clients, roster
    and revision, so nothing one guild's office shows can reach another's."""

    def __init__(self) -> None:
        self._offices: dict[int, CctvPipeline] = {}

    def get(self, guild_id: int) -> CctvPipeline | None:
        return self._offices.get(guild_id)

    def add(self, guild_id: int, pipeline: CctvPipeline) -> None:
        if guild_id in self._offices:
            raise ValueError(f"guild {guild_id} already has an open office")
        self._offices[guild_id] = pipeline

    def pop(self, guild_id: int) -> CctvPipeline | None:
        return self._offices.pop(guild_id, None)

    def clear(self) -> None:
        self._offices.clear()

    def guild_ids(self) -> tuple[int, ...]:
        return tuple(self._offices)

    def __iter__(self) -> Iterator[CctvPipeline]:
        return iter(tuple(self._offices.values()))

    def __len__(self) -> int:
        return len(self._offices)

    def __contains__(self, guild_id: object) -> bool:
        return guild_id in self._offices


__all__ = ["GuildOffices"]
