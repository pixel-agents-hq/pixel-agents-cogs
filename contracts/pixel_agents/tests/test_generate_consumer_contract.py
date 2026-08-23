"""Unit tests for contracts.pixel_agents.generate_consumer_contract -- fully
offline, no vendor clone needed (introspects pixelagents.contracts.outbound
directly)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.pixel_agents import generate_consumer_contract as gcc


class TestBuildContract(unittest.TestCase):
    def test_agent_created_required_optional_split(self) -> None:
        contract = gcc.build_contract()
        entry = contract["messages"]["agentCreated"]

        self.assertEqual(
            entry["required"], sorted(["type", "id", "folderName", "palette", "hueShift"])
        )
        self.assertEqual(entry["properties"]["isExternal"], {"type": "boolean"})
        self.assertEqual(entry["properties"]["id"], {"type": "integer"})
        self.assertEqual(entry["properties"]["type"], {"const": "agentCreated"})

    def test_multi_value_literal_becomes_an_enum_not_a_const(self) -> None:
        # agentStatus.status: Literal["active", "waiting"] -- the exact shape
        # an earlier draft of the generator crashed on (tried to unpack two
        # values into one).
        contract = gcc.build_contract()
        entry = contract["messages"]["agentStatus"]

        self.assertEqual(entry["properties"]["status"], {"enum": ["active", "waiting"]})
        self.assertEqual(entry["properties"]["type"], {"const": "agentStatus"})

    def test_existing_agents_maps_and_list_fields(self) -> None:
        contract = gcc.build_contract()
        entry = contract["messages"]["existingAgents"]

        self.assertEqual(
            entry["properties"]["agents"], {"type": "array", "items": {"type": "integer"}}
        )
        self.assertEqual(entry["properties"]["agentMeta"], {"type": "object"})
        self.assertEqual(
            entry["required"],
            sorted(["type", "agents", "agentMeta", "folderNames", "externalAgents"]),
        )

    def test_private_required_base_classes_are_excluded(self) -> None:
        contract = gcc.build_contract()

        self.assertNotIn(
            "_AgentCreatedRequired", {e["typeddict"] for e in contract["messages"].values()}
        )

    def test_output_has_no_repeated_object_identity_for_yaml_to_alias(self) -> None:
        # Regression check for the anchor/alias bug caught while building this
        # generator: _field_schema must return a fresh dict per call, not a
        # shared _PRIMITIVES reference, or rendered YAML gets &id001/*id001
        # noise that defeats committing this file for human review.
        rendered = gcc.render()

        self.assertNotIn("&id", rendered)
        self.assertNotIn("*id", rendered)


class TestMain(unittest.TestCase):
    # Path instances don't support attribute patching (no __dict__ on the
    # C-level slots-based class) -- point CONTRACT_PATH at a real temp file
    # per test instead of mocking its methods.

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._path = Path(self._tmpdir.name) / "pixel-agents-consumer-contract.yaml"
        self._patcher = patch.object(gcc, "CONTRACT_PATH", self._path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_check_mode_fails_when_committed_file_is_stale(self) -> None:
        self._path.write_text("different content\n", encoding="utf-8")

        with patch.object(gcc, "render", return_value="stale content\n"):
            self.assertEqual(gcc.main(check=True), 1)

    def test_check_mode_passes_when_committed_file_matches(self) -> None:
        self._path.write_text("matching content\n", encoding="utf-8")

        with patch.object(gcc, "render", return_value="matching content\n"):
            self.assertEqual(gcc.main(check=True), 0)

    def test_check_mode_fails_when_committed_file_is_missing(self) -> None:
        with patch.object(gcc, "render", return_value="some content\n"):
            self.assertEqual(gcc.main(check=True), 1)


if __name__ == "__main__":
    unittest.main()
