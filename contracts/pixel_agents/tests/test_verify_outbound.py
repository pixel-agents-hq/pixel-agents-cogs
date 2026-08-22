"""Unit tests for contracts.pixel_agents.verify_outbound -- fully offline.

The real vendor schema is exercised by hand (not as part of the test suite)
via `contracts.pixel_agents.verify_outbound.run` pointed at a real checkout;
these tests instead fake the schema index so they run without network/git.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import RefResolver

from contracts.pixel_agents import verify_outbound
from contracts.pixel_agents.schema import MessageSchemas


def _permissive_schemas_for(messages: list[dict]) -> MessageSchemas:
    """A schema index that accepts exactly the `type`s seen in `messages`,
    with no constraint beyond that -- used where the test only cares about
    the outbound_messages check's matching/lookup behavior, not real shapes."""

    by_type = {
        message["type"]: {"type": "object", "properties": {"type": {"const": message["type"]}}}
        for message in messages
    }
    return MessageSchemas(by_type=by_type, resolver=RefResolver.from_schema({}))


class TestCheckOutboundMessages(unittest.TestCase):
    def test_every_real_captured_message_finds_and_matches_a_schema(self) -> None:
        messages = asyncio.run(verify_outbound._capture_messages())
        schemas = _permissive_schemas_for(messages)

        with patch.object(verify_outbound, "load_message_schemas", return_value=schemas):
            ok, detail = verify_outbound._check_outbound_messages(Path("/unused"))

        self.assertTrue(ok, detail)
        self.assertIn(f"{len(messages)}", detail)

    def test_a_captured_type_with_no_matching_schema_fails_with_its_name(self) -> None:
        schemas = MessageSchemas(by_type={}, resolver=RefResolver.from_schema({}))

        async def fake_capture() -> list[dict]:
            return [{"type": "notARealMessageType"}]

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_capture_messages", fake_capture),
        ):
            ok, detail = verify_outbound._check_outbound_messages(Path("/unused"))

        self.assertFalse(ok)
        self.assertIn("notARealMessageType", detail)

    def test_a_message_violating_its_schema_fails_with_the_validation_error(self) -> None:
        schemas = MessageSchemas(
            by_type={
                "agentClosed": {
                    "type": "object",
                    "required": ["type", "id"],
                    "properties": {"type": {"const": "agentClosed"}, "id": {"type": "integer"}},
                }
            },
            resolver=RefResolver.from_schema({}),
        )

        async def fake_capture() -> list[dict]:
            return [{"type": "agentClosed", "id": "not-an-int"}]

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_capture_messages", fake_capture),
        ):
            ok, detail = verify_outbound._check_outbound_messages(Path("/unused"))

        self.assertFalse(ok)
        self.assertIn("agentClosed", detail)


class TestCheckHelperSmoke(unittest.TestCase):
    def test_passes_against_realistic_and_edge_case_input(self) -> None:
        ok, detail = verify_outbound._check_helper_smoke()

        self.assertTrue(ok, detail)


class TestRun(unittest.TestCase):
    def test_returns_one_entry_per_check_in_the_verify_checks_shape(self) -> None:
        messages = asyncio.run(verify_outbound._capture_messages())
        schemas = _permissive_schemas_for(messages)

        with patch.object(verify_outbound, "load_message_schemas", return_value=schemas):
            checks = verify_outbound.run(Path("/unused"))

        names = {check["name"] for check in checks}
        self.assertEqual(names, {"outbound_messages", "helper_smoke"})
        for check in checks:
            self.assertEqual(check["status"], "pass", check["detail"])


if __name__ == "__main__":
    unittest.main()
