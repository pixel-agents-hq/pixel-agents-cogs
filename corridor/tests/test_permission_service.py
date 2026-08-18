"""PermissionService is fully testable without Red: plain fakes satisfy the
MemberRef/OwnerRegistry protocols, no unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import PermissionService
from ..domain import EMPLOYEE_KEY, OWNER_KEY, PermissionGroupDef, PermissionSettings


class FakeMember:
    def __init__(
        self,
        member_id: int,
        role_ids: frozenset[int] = frozenset(),
        is_administrator: bool = False,
    ) -> None:
        self.id = member_id
        self.role_ids = role_ids
        self.is_administrator = is_administrator


class FakeOwnerRegistry:
    def __init__(self, owner_ids: frozenset[int] = frozenset()) -> None:
        self._owner_ids = owner_ids

    async def is_owner(self, user_id: int) -> bool:
        return user_id in self._owner_ids


SETTINGS = PermissionSettings(
    groups=(
        PermissionGroupDef(
            key="building_manager", label="Building Manager", role_ids=frozenset({100})
        ),
        PermissionGroupDef(key="keyholder", label="Keyholder", role_ids=frozenset({200})),
    ),
)


class TestPermissionService(unittest.IsolatedAsyncioTestCase):
    async def test_bot_owner_satisfies_every_group(self) -> None:
        service = PermissionService(FakeOwnerRegistry(owner_ids=frozenset({1})))
        member = FakeMember(1)

        for key in (EMPLOYEE_KEY, OWNER_KEY, "building_manager", "keyholder"):
            self.assertTrue(await service.satisfies(member, SETTINGS, key))

    async def test_guild_administrator_satisfies_every_group_without_bot_ownership(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(5, is_administrator=True)

        for key in (EMPLOYEE_KEY, OWNER_KEY, "building_manager", "keyholder"):
            self.assertTrue(await service.satisfies(member, SETTINGS, key))

    async def test_building_manager_role_grants_building_manager_only(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(2, role_ids=frozenset({100}))

        self.assertTrue(await service.satisfies(member, SETTINGS, "building_manager"))
        self.assertFalse(await service.satisfies(member, SETTINGS, "keyholder"))

    async def test_keyholder_role_grants_keyholder_only(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(3, role_ids=frozenset({200}))

        self.assertTrue(await service.satisfies(member, SETTINGS, "keyholder"))
        self.assertFalse(await service.satisfies(member, SETTINGS, "building_manager"))

    async def test_member_with_no_roles_only_satisfies_employee(self) -> None:
        service = PermissionService(FakeOwnerRegistry())
        member = FakeMember(4)

        self.assertTrue(await service.satisfies(member, SETTINGS, EMPLOYEE_KEY))
        self.assertFalse(await service.satisfies(member, SETTINGS, "building_manager"))
        self.assertFalse(await service.satisfies(member, SETTINGS, "keyholder"))
        self.assertFalse(await service.satisfies(member, SETTINGS, OWNER_KEY))
