"""classify()/coerce_scalar()/build_event() -- the generic field
classification and construction logic, tested against both synthetic
FieldSpecs (for the classification rules themselves) and the real
corridor.domain dataclasses (for build_event's end-to-end correctness)."""

from __future__ import annotations

import unittest

from corridor.domain import AgentPresenceChanged, AgentRef, AgentReplied

from ..application import FieldKind, build_event, classify, coerce_scalar, literal_options
from ..domain import EventSpec, FieldSpec


class TestClassify(unittest.TestCase):
    def test_agent_ref_field(self) -> None:
        field = FieldSpec(name="agent", type_str="AgentRef", required=True)
        self.assertIs(classify(field), FieldKind.AGENT_REF)

    def test_literal_field(self) -> None:
        field = FieldSpec(name="status", type_str="Literal['online', 'offline']", required=True)
        self.assertIs(classify(field), FieldKind.LITERAL)

    def test_tuple_field(self) -> None:
        field = FieldSpec(
            name="activities", type_str="tuple[AgentActivity, ...]", required=False, default=[]
        )
        self.assertIs(classify(field), FieldKind.TUPLE)

    def test_str_field(self) -> None:
        field = FieldSpec(name="summary", type_str="str", required=True)
        self.assertIs(classify(field), FieldKind.SCALAR)

    def test_int_field(self) -> None:
        field = FieldSpec(name="count", type_str="int", required=True)
        self.assertIs(classify(field), FieldKind.SCALAR)

    def test_bool_field(self) -> None:
        field = FieldSpec(name="is_bot", type_str="bool", required=True)
        self.assertIs(classify(field), FieldKind.SCALAR)

    def test_optional_scalar_field(self) -> None:
        field = FieldSpec(name="tool_name", type_str="str | None", required=False, default=None)
        self.assertIs(classify(field), FieldKind.SCALAR)

    def test_unknown_value_object_degrades_to_tuple(self) -> None:
        field = FieldSpec(name="future", type_str="SomeFutureType", required=False, default=None)
        self.assertIs(classify(field), FieldKind.TUPLE)


class TestLiteralOptions(unittest.TestCase):
    def test_parses_the_quoted_values(self) -> None:
        options = literal_options("Literal['online', 'idle', 'dnd', 'offline']")
        self.assertEqual(options, ("online", "idle", "dnd", "offline"))


class TestCoerceScalar(unittest.TestCase):
    def test_str_passthrough(self) -> None:
        self.assertEqual(coerce_scalar("str", "hello"), "hello")

    def test_int_conversion(self) -> None:
        self.assertEqual(coerce_scalar("int", "42"), 42)

    def test_bad_int_raises(self) -> None:
        with self.assertRaises(ValueError):
            coerce_scalar("int", "not a number")

    def test_bool_true_values(self) -> None:
        for raw in ("true", "True", "1", "yes"):
            self.assertIs(coerce_scalar("bool", raw), True)

    def test_bool_false_values(self) -> None:
        for raw in ("false", "False", "0", "no"):
            self.assertIs(coerce_scalar("bool", raw), False)

    def test_bad_bool_raises(self) -> None:
        with self.assertRaises(ValueError):
            coerce_scalar("bool", "maybe")

    def test_optional_blank_is_none(self) -> None:
        self.assertIsNone(coerce_scalar("str | None", ""))

    def test_required_blank_raises(self) -> None:
        with self.assertRaises(ValueError):
            coerce_scalar("str", "")

    def test_literal_valid_value(self) -> None:
        self.assertEqual(coerce_scalar("Literal['active', 'waiting']", "active"), "active")

    def test_literal_invalid_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            coerce_scalar("Literal['active', 'waiting']", "bogus")


class TestBuildEvent(unittest.TestCase):
    def test_builds_a_real_agent_replied(self) -> None:
        spec = EventSpec(
            name="AgentReplied",
            fields=(
                FieldSpec(name="agent", type_str="AgentRef", required=True),
                FieldSpec(name="summary", type_str="str", required=True),
            ),
        )

        event = build_event(
            spec,
            agent_selections={"agent": (1, 100, False)},
            literal_selections={},
            scalar_inputs={"summary": "hello world"},
        )

        self.assertEqual(
            event,
            AgentReplied(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=False),
                summary="hello world",
            ),
        )

    def test_builds_a_real_agent_presence_changed_with_defaulted_tuple(self) -> None:
        spec = EventSpec(
            name="AgentPresenceChanged",
            fields=(
                FieldSpec(name="agent", type_str="AgentRef", required=True),
                FieldSpec(name="display_name", type_str="str", required=True),
                FieldSpec(
                    name="status",
                    type_str="Literal['online', 'idle', 'dnd', 'offline']",
                    required=True,
                ),
                FieldSpec(
                    name="activities",
                    type_str="tuple[AgentActivity, ...]",
                    required=False,
                    default=[],
                ),
            ),
        )

        event = build_event(
            spec,
            agent_selections={"agent": (1, 100, True)},
            literal_selections={"status": "dnd"},
            scalar_inputs={"display_name": "Tin"},
        )

        self.assertEqual(
            event,
            AgentPresenceChanged(
                agent=AgentRef(discord_user_id=1, guild_id=100, is_bot=True),
                display_name="Tin",
                status="dnd",
                activities=(),
            ),
        )

    def test_required_tuple_field_with_no_default_raises(self) -> None:
        spec = EventSpec(
            name="Bogus",
            fields=(FieldSpec(name="items", type_str="tuple[str, ...]", required=True),),
        )

        with self.assertRaises(ValueError):
            build_event(spec, agent_selections={}, literal_selections={}, scalar_inputs={})


if __name__ == "__main__":
    unittest.main()
