"""Unit tests for contracts.corridor.generate_corridor_contract -- fully
offline (introspects corridor.domain directly, no clone/network needed)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.corridor import generate_corridor_contract as gcc


class TestBuildContract(unittest.TestCase):
    def test_agent_ref_is_a_value_object(self) -> None:
        contract = gcc.build_contract()
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
        contract = gcc.build_contract()
        entry = contract["events"]["AgentPresenceChanged"]

        self.assertEqual(entry["kind"], "event")
        self.assertEqual(
            entry["fields"]["status"]["type"], "Literal['online', 'idle', 'dnd', 'offline']"
        )

    def test_activities_default_to_an_empty_list(self) -> None:
        contract = gcc.build_contract()
        entry = contract["events"]["AgentPresenceChanged"]

        self.assertEqual(entry["fields"]["activities"]["type"], "tuple[AgentActivity, ...]")
        self.assertEqual(entry["fields"]["activities"]["default"], [])

    def test_optional_field_renders_as_a_union_with_none(self) -> None:
        contract = gcc.build_contract()
        entry = contract["events"]["AgentToolStarted"]

        self.assertEqual(entry["fields"]["tool_name"]["type"], "str | None")
        self.assertIsNone(entry["fields"]["tool_name"]["default"])

    def test_agent_activity_event_union_alias_is_excluded_not_a_dataclass(self) -> None:
        contract = gcc.build_contract()

        self.assertNotIn("AgentActivityEvent", contract["events"])

    def test_only_agent_prefixed_names_are_included(self) -> None:
        # corridor.domain also exports non-pubsub types (GuildSettings,
        # PermissionGroupDef, ReplyField, ...) -- a different contract's
        # concern entirely.
        contract = gcc.build_contract()

        self.assertTrue(all(name.startswith("Agent") for name in contract["events"]))
        self.assertNotIn("GuildSettings", contract["events"])


class TestMain(unittest.TestCase):
    # Path instances don't support attribute patching -- point CONTRACT_PATH
    # at a real temp file per test instead of mocking its methods.

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._path = Path(self._tmpdir.name) / "corridor.yaml"
        self._patcher = patch.object(gcc, "CONTRACT_PATH", self._path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_check_mode_fails_when_committed_file_is_stale(self) -> None:
        self._path.write_text("different content\n", encoding="utf-8")

        with patch.object(gcc, "render", return_value="fresh content\n"):
            self.assertEqual(gcc.main(check=True), 1)

    def test_check_mode_passes_when_committed_file_matches(self) -> None:
        self._path.write_text("matching content\n", encoding="utf-8")

        with patch.object(gcc, "render", return_value="matching content\n"):
            self.assertEqual(gcc.main(check=True), 0)

    def test_check_mode_fails_when_committed_file_is_missing(self) -> None:
        with patch.object(gcc, "render", return_value="some content\n"):
            self.assertEqual(gcc.main(check=True), 1)


class TestRealCommittedContract(unittest.TestCase):
    def test_matches_a_fresh_regeneration(self) -> None:
        # Integration check against the actual committed file (no
        # CONTRACT_PATH patching here, deliberately) -- catches "someone
        # edited corridor/domain/models.py and forgot to regenerate" the
        # same way CI's --check step does.
        self.assertEqual(gcc.main(check=True), 0)


if __name__ == "__main__":
    unittest.main()
