"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import EventSpec, FieldSpec


def test_field_spec_holds_its_fields() -> None:
    field = FieldSpec(name="summary", type_str="str", required=True, default=None)

    assert field.name == "summary"
    assert field.type_str == "str"
    assert field.required is True
    assert field.default is None


def test_field_spec_is_frozen() -> None:
    field = FieldSpec(name="summary", type_str="str", required=True)

    with pytest.raises(FrozenInstanceError):
        field.required = False  # type: ignore[misc]


def test_event_spec_holds_its_fields() -> None:
    fields = (FieldSpec(name="summary", type_str="str", required=True),)
    spec = EventSpec(name="AgentReplied", fields=fields)

    assert spec.name == "AgentReplied"
    assert spec.fields == fields


def test_event_spec_is_frozen() -> None:
    spec = EventSpec(name="AgentReplied", fields=())

    with pytest.raises(FrozenInstanceError):
        spec.name = "Other"  # type: ignore[misc]
