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


def _permissive_contract_for(messages: list[dict]) -> dict:
    """A consumer contract with an entry per `type` seen in `messages`, each
    declaring zero fields -- so _check_consumer_contract_drift has nothing to
    compare against a real (or fixture) vendor schema and trivially passes.
    Used alongside _permissive_schemas_for where a test only cares about
    another check's behavior, not the drift check's."""

    return {"messages": {message["type"]: {"properties": {}} for message in messages}}


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
                "agentSelected": {
                    "type": "object",
                    "required": ["type", "id"],
                    "properties": {"type": {"const": "agentSelected"}, "id": {"type": "integer"}},
                }
            },
            resolver=RefResolver.from_schema({}),
        )

        async def fake_capture() -> list[dict]:
            return [{"type": "agentSelected", "id": "not-an-int"}]

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_capture_messages", fake_capture),
        ):
            ok, detail = verify_outbound._check_outbound_messages(Path("/unused"))

        self.assertFalse(ok)
        self.assertIn("agentSelected", detail)


class TestCheckHelperSmoke(unittest.TestCase):
    def test_passes_against_realistic_and_edge_case_input(self) -> None:
        ok, detail = verify_outbound._check_helper_smoke()

        self.assertTrue(ok, detail)


class TestCheckConsumerContractDrift(unittest.TestCase):
    def test_passes_when_every_declared_field_is_still_in_the_vendor_schema(self) -> None:
        contract = {
            "messages": {
                "agentCreated": {"properties": {"id": {"type": "integer"}}},
            }
        }
        schemas = MessageSchemas(
            by_type={
                "agentCreated": {
                    "properties": {"id": {"type": "integer"}, "folderName": {"type": "string"}},
                    "required": ["id"],
                }
            },
            resolver=RefResolver.from_schema({}),
        )

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_load_consumer_contract", return_value=contract),
        ):
            ok, detail = verify_outbound._check_consumer_contract_drift(Path("/unused"))

        self.assertTrue(ok, detail)

    def test_fails_when_a_declared_field_no_longer_exists_upstream(self) -> None:
        contract = {"messages": {"agentCreated": {"properties": {"gone": {"type": "string"}}}}}
        schemas = MessageSchemas(
            by_type={"agentCreated": {"properties": {}, "required": []}},
            resolver=RefResolver.from_schema({}),
        )

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_load_consumer_contract", return_value=contract),
        ):
            ok, detail = verify_outbound._check_consumer_contract_drift(Path("/unused"))

        self.assertFalse(ok)
        self.assertIn("agentCreated.gone", detail)
        self.assertIn("no longer in vendor schema", detail)

    def test_fails_when_upstream_now_requires_a_field_we_dont_declare(self) -> None:
        contract = {"messages": {"agentCreated": {"properties": {}}}}
        schemas = MessageSchemas(
            by_type={
                "agentCreated": {
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                }
            },
            resolver=RefResolver.from_schema({}),
        )

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_load_consumer_contract", return_value=contract),
        ):
            ok, detail = verify_outbound._check_consumer_contract_drift(Path("/unused"))

        self.assertFalse(ok)
        self.assertIn("vendor now requires", detail)

    def test_fails_when_the_message_type_is_removed_upstream(self) -> None:
        contract = {"messages": {"agentCreated": {"properties": {}}}}
        schemas = MessageSchemas(by_type={}, resolver=RefResolver.from_schema({}))

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_load_consumer_contract", return_value=contract),
        ):
            ok, detail = verify_outbound._check_consumer_contract_drift(Path("/unused"))

        self.assertFalse(ok)
        self.assertIn("no vendor schema", detail)

    def test_follows_a_ref_to_compare_an_enum_field(self) -> None:
        contract = {"messages": {"agentStatus": {"properties": {"status": {"enum": ["active"]}}}}}
        schemas = MessageSchemas(
            by_type={
                "agentStatus": {
                    "properties": {"status": {"$ref": "#/components/schemas/AgentActivityStatus"}},
                    "required": [],
                }
            },
            resolver=RefResolver.from_schema(
                {
                    "components": {
                        "schemas": {
                            "AgentActivityStatus": {"type": "string", "enum": ["active", "waiting"]}
                        }
                    }
                }
            ),
        )

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_load_consumer_contract", return_value=contract),
        ):
            ok, detail = verify_outbound._check_consumer_contract_drift(Path("/unused"))

        self.assertTrue(ok, detail)


class TestRun(unittest.TestCase):
    def test_returns_one_entry_per_check_in_the_verify_checks_shape(self) -> None:
        messages = asyncio.run(verify_outbound._capture_messages())
        schemas = _permissive_schemas_for(messages)
        contract = _permissive_contract_for(messages)

        with (
            patch.object(verify_outbound, "load_message_schemas", return_value=schemas),
            patch.object(verify_outbound, "_load_consumer_contract", return_value=contract),
        ):
            checks = verify_outbound.run(Path("/unused"))

        names = {check["name"] for check in checks}
        self.assertEqual(names, {"outbound_messages", "helper_smoke", "consumer_contract_drift"})
        for check in checks:
            self.assertEqual(check["status"], "pass", check["detail"])


if __name__ == "__main__":
    unittest.main()
