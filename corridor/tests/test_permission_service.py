"""PermissionService is fully testable without Red: plain fakes satisfy the
MemberRef/OwnerRegistry protocols, no unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import PermissionService
from ..domain import PermissionGroup, PermissionSettings


class FakeMember:
    def __init__(self, member_id: int, role_ids: frozenset[int] = frozenset()) -> None:
        self.id = member_id
        self.role_ids = role_ids


class FakeOwnerRegistry:
    def __init__(self, owner_ids: frozenset[int] = frozenset()) -> None:
        self._owner_ids = owner_ids

    async def is_owner(self, user_id: int) -> bool:
        return user_id in self._owner_ids


SETTINGS = PermissionSettings(
    moderator_role_ids=frozenset({100}),
    privileged_role_ids=frozenset({200}),
)


class TestPermissionService(unittest.IsolatedAsyncioTestCase):
    async def test_bot_owner_satisfies_every_group(self) -> None:
        service = PermissionService(FakeOwnerRegistry(owner_ids=frozenset({1})))
        member = FakeMember(1)

        for group in PermissionGroup:
            self.assertTrue(await service.satisfies(member, SETTINGS, group))

    async def test_moderator_role_grants_moderator_only(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(2, role_ids=frozenset({100}))

        self.assertTrue(await service.satisfies(member, SETTINGS, PermissionGroup.MODERATOR))
        self.assertFalse(await service.satisfies(member, SETTINGS, PermissionGroup.PRIVILEGED))

    async def test_privileged_role_grants_privileged_only(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(3, role_ids=frozenset({200}))

        self.assertTrue(await service.satisfies(member, SETTINGS, PermissionGroup.PRIVILEGED))
        self.assertFalse(await service.satisfies(member, SETTINGS, PermissionGroup.MODERATOR))

    async def test_member_with_no_roles_only_satisfies_all(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(4)

        self.assertTrue(await service.satisfies(member, SETTINGS, PermissionGroup.ALL))
        self.assertFalse(await service.satisfies(member, SETTINGS, PermissionGroup.MODERATOR))
        self.assertFalse(await service.satisfies(member, SETTINGS, PermissionGroup.PRIVILEGED))
