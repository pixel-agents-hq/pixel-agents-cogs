"""list_publishable_events()/value_object_fields() against the real
corridor.event_catalog.build_contract() -- not a hand-maintained fixture,
so this breaks, correctly, if corridor's domain model changes in a way
this cog's classifier can't handle yet."""

from __future__ import annotations

import unittest

from ..application import list_publishable_events, value_object_fields
from ..domain import FieldSpec

_REAL_EVENT_NAMES = {
    "AgentHighlighted",
    "AgentPresenceChanged",
    "AgentReplied",
    "AgentStatusChanged",
    "AgentToolStarted",
    "AgentUnhighlighted",
}


class TestListPublishableEvents(unittest.TestCase):
    def test_returns_exactly_the_real_event_names(self) -> None:
        specs = list_publishable_events()

        self.assertEqual({spec.name for spec in specs}, _REAL_EVENT_NAMES)

    def test_value_objects_are_excluded(self) -> None:
        specs = list_publishable_events()

        names = {spec.name for spec in specs}
        self.assertNotIn("AgentRef", names)
        self.assertNotIn("AgentActivity", names)

    def test_sorted_by_name(self) -> None:
        specs = list_publishable_events()

        self.assertEqual([spec.name for spec in specs], sorted(_REAL_EVENT_NAMES))

    def test_agent_replied_has_the_expected_fields(self) -> None:
        specs = list_publishable_events()

        agent_replied = next(spec for spec in specs if spec.name == "AgentReplied")
        self.assertEqual(
            agent_replied.fields,
            (
                FieldSpec(name="agent", type_str="AgentRef", required=True),
                FieldSpec(name="summary", type_str="str", required=True),
            ),
        )

    def test_optional_field_reports_required_false_with_its_default(self) -> None:
        specs = list_publishable_events()

        tool_started = next(spec for spec in specs if spec.name == "AgentToolStarted")
        tool_name = next(field for field in tool_started.fields if field.name == "tool_name")
        self.assertFalse(tool_name.required)
        self.assertIsNone(tool_name.default)


class TestValueObjectFields(unittest.TestCase):
    def test_agent_ref_fields(self) -> None:
        fields = value_object_fields("AgentRef")

        self.assertEqual(
            fields,
            (
                FieldSpec(name="discord_user_id", type_str="int | None", required=True),
                FieldSpec(name="guild_id", type_str="int | None", required=True),
                FieldSpec(name="is_bot", type_str="bool", required=True),
                FieldSpec(name="agent_key", type_str="str | None", required=False, default=None),
            ),
        )

    def test_unknown_name_returns_none(self) -> None:
        self.assertIsNone(value_object_fields("NotARealType"))

    def test_an_event_name_is_not_a_value_object(self) -> None:
        self.assertIsNone(value_object_fields("AgentReplied"))


if __name__ == "__main__":
    unittest.main()
