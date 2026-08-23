"""Unit tests for contracts.pixel_agents.lint_outbound_contract -- fully
offline."""

from __future__ import annotations

import asyncio
import unittest

from contracts.pixel_agents import lint_outbound_contract as loc
from contracts.pixel_agents.verify_outbound import _capture_messages


class TestCheck(unittest.TestCase):
    def test_the_real_committed_contract_matches_real_captured_messages(self) -> None:
        # Integration check, fully offline: no network, no vendor clone --
        # _capture_messages() drives OfficeService in-memory.
        contract = loc._load_contract()
        in_scope = loc._outbound_message_types()
        messages = [m for m in asyncio.run(_capture_messages()) if m.get("type") in in_scope]

        ok, problems = loc.check(contract, messages)

        self.assertTrue(ok, problems)
        self.assertGreater(len(messages), 0)

    def test_a_message_violating_a_field_type_fails_with_its_type_name(self) -> None:
        contract = loc._load_contract()
        bad_message = {
            "type": "agentCreated",
            "id": "not-an-int",
            "folderName": "x",
            "palette": 0,
            "hueShift": 0,
        }

        ok, problems = loc.check(contract, [bad_message])

        self.assertFalse(ok)
        self.assertIn("agentCreated", problems[0])

    def test_a_message_type_with_no_contract_entry_fails(self) -> None:
        contract = loc._load_contract()

        ok, problems = loc.check(contract, [{"type": "notARealMessageType"}])

        self.assertFalse(ok)
        self.assertIn("no contract entry", problems[0])


if __name__ == "__main__":
    unittest.main()
