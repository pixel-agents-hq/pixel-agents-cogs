"""Unit tests for corridor.event_catalog -- fully offline (introspects
corridor.domain directly, no clone/network needed).

Moved here from contracts/corridor/tests/test_generate_corridor_contract.py
once the introspection itself moved into corridor/event_catalog.py (see
that module's docstring) -- this is corridor's own module now, it gets
corridor's own test suite. contracts/corridor/tests/test_generate_corridor_contract.py
keeps only the CLI/--check/committed-file behavior."""

from __future__ import annotations

import unittest

from corridor import event_catalog


class TestBuildContract(unittest.TestCase):
    def test_agent_ref_is_a_value_object(self) -> None:
        contract = event_catalog.build_contract()
        entry = contract["events"]["AgentRef"]

        self.assertEqual(entry["kind"], "value-object")
        self.assertEqual(
            entry["fields"],
            {
                "discord_user_id": {"type": "int"},
                "guild_id": {"type": "int"},
                "is_bot": {"type": "bool"},
            },
        )

    def test_agent_presence_changed_is_an_event_with_a_literal_status(self) -> None:
        contract = event_catalog.build_contract()
        entry = contract["events"]["AgentPresenceChanged"]

        self.assertEqual(entry["kind"], "event")
        self.assertEqual(
            entry["fields"]["status"]["type"], "Literal['online', 'idle', 'dnd', 'offline']"
        )

    def test_activities_default_to_an_empty_list(self) -> None:
        contract = event_catalog.build_contract()
        entry = contract["events"]["AgentPresenceChanged"]

        self.assertEqual(entry["fields"]["activities"]["type"], "tuple[AgentActivity, ...]")
        self.assertEqual(entry["fields"]["activities"]["default"], [])

    def test_optional_field_renders_as_a_union_with_none(self) -> None:
        contract = event_catalog.build_contract()
        entry = contract["events"]["AgentToolStarted"]

        self.assertEqual(entry["fields"]["tool_name"]["type"], "str | None")
        self.assertIsNone(entry["fields"]["tool_name"]["default"])

    def test_agent_activity_event_union_alias_is_excluded_not_a_dataclass(self) -> None:
        contract = event_catalog.build_contract()

        self.assertNotIn("AgentActivityEvent", contract["events"])

    def test_only_agent_prefixed_names_are_included(self) -> None:
        # corridor.domain also exports non-pubsub types (GuildSettings,
        # PermissionGroupDef, ReplyField, ...) -- a different contract's
        # concern entirely.
        contract = event_catalog.build_contract()

        self.assertTrue(all(name.startswith("Agent") for name in contract["events"]))
        self.assertNotIn("GuildSettings", contract["events"])


if __name__ == "__main__":
    unittest.main()
