"""Domain models need no mocking, no stubs -- pure values."""

from __future__ import annotations

from ..domain import (
    EMPLOYEE_KEY,
    OWNER_KEY,
    IconPreference,
    IconSource,
    MemberCapabilities,
)


def test_owner_satisfies_every_group() -> None:
    capabilities = MemberCapabilities(is_owner=True, satisfied_keys=frozenset())

    assert capabilities.satisfies(EMPLOYEE_KEY)
    assert capabilities.satisfies(OWNER_KEY)
    assert capabilities.satisfies("building_manager")
    assert capabilities.satisfies("keyholder")
    assert capabilities.satisfies("anything_undefined")


def test_role_backed_groups_are_independent_tiers() -> None:
    building_manager_only = MemberCapabilities(
        is_owner=False, satisfied_keys=frozenset({"building_manager"})
    )

    assert building_manager_only.satisfies("building_manager")
    assert not building_manager_only.satisfies("keyholder")
    assert not building_manager_only.satisfies(OWNER_KEY)


def test_employee_never_restricts() -> None:
    nobody = MemberCapabilities(is_owner=False, satisfied_keys=frozenset())

    assert nobody.satisfies(EMPLOYEE_KEY)
    assert not nobody.satisfies("building_manager")
    assert not nobody.satisfies(OWNER_KEY)


def test_unknown_key_denies() -> None:
    nobody = MemberCapabilities(is_owner=False, satisfied_keys=frozenset({"keyholder"}))

    assert not nobody.satisfies("some_deleted_group")


def test_icon_preference_holds_custom_url() -> None:
    icon = IconPreference(source=IconSource.CUSTOM, custom_url="https://example.com/icon.png")

    assert icon.source is IconSource.CUSTOM
    assert icon.custom_url == "https://example.com/icon.png"
