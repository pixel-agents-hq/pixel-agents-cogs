"""Domain models need no mocking, no stubs -- pure values."""

from __future__ import annotations

from ..domain import (
    IconPreference,
    IconSource,
    MemberCapabilities,
    PermissionGroup,
)


def test_owner_satisfies_every_group() -> None:
    capabilities = MemberCapabilities(is_owner=True, is_moderator=False, is_privileged=False)

    assert capabilities.satisfies(PermissionGroup.ALL)
    assert capabilities.satisfies(PermissionGroup.MODERATOR)
    assert capabilities.satisfies(PermissionGroup.PRIVILEGED)
    assert capabilities.satisfies(PermissionGroup.OWNER)


def test_moderator_and_privileged_are_independent_tiers() -> None:
    moderator_only = MemberCapabilities(is_owner=False, is_moderator=True, is_privileged=False)

    assert moderator_only.satisfies(PermissionGroup.MODERATOR)
    assert not moderator_only.satisfies(PermissionGroup.PRIVILEGED)
    assert not moderator_only.satisfies(PermissionGroup.OWNER)


def test_all_group_never_restricts() -> None:
    nobody = MemberCapabilities(is_owner=False, is_moderator=False, is_privileged=False)

    assert nobody.satisfies(PermissionGroup.ALL)
    assert not nobody.satisfies(PermissionGroup.MODERATOR)


def test_icon_preference_holds_custom_url() -> None:
    icon = IconPreference(source=IconSource.CUSTOM, custom_url="https://example.com/icon.png")

    assert icon.source is IconSource.CUSTOM
    assert icon.custom_url == "https://example.com/icon.png"
