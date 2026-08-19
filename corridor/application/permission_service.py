"""Framework-agnostic permission resolution. Depends only on the two
protocols below, never on discord.Member or redbot -- swap in fakes for
tests, real discord.py objects (via a thin adapter) in production."""

from __future__ import annotations

from typing import Protocol

from ..domain import MemberCapabilities, PermissionSettings


class MemberRef(Protocol):
    """Minimal framework-neutral shape a permission check needs."""

    id: int
    role_ids: frozenset[int]
    permission_names: frozenset[str]
    is_administrator: bool


class OwnerRegistry(Protocol):
    """The bot-owner boundary -- part of who counts as the OWNER tier
    (the other part, guild Administrator permission, comes from
    MemberRef.is_administrator)."""

    async def is_owner(self, user_id: int) -> bool: ...


class PermissionService:
    def __init__(self, owners: OwnerRegistry) -> None:
        self._owners = owners

    async def capabilities_for(
        self, member: MemberRef, settings: PermissionSettings
    ) -> MemberCapabilities:
        is_owner = await self._owners.is_owner(member.id) or member.is_administrator
        satisfied_keys = frozenset(
            group.key
            for group in settings.groups
            if (member.role_ids & group.role_ids)
            or (member.permission_names & group.permission_names)
        )
        return MemberCapabilities(is_owner=is_owner, satisfied_keys=satisfied_keys)

    async def satisfies(
        self,
        member: MemberRef,
        settings: PermissionSettings,
        key: str,
    ) -> bool:
        capabilities = await self.capabilities_for(member, settings)
        return capabilities.satisfies(key)
