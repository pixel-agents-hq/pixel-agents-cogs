"""Unit tests for contracts.corridor.lint_corridor_contract -- fully offline."""

from __future__ import annotations

import unittest

from contracts.corridor import lint_corridor_contract as lcc


class TestCheck(unittest.TestCase):
    def test_the_real_files_pass_with_zero_problems(self) -> None:
        contract = lcc.load_contract()
        doc_text = lcc.DESIGN_DOC_PATH.read_text(encoding="utf-8")

        problems = lcc.check(contract, doc_text)

        self.assertEqual(problems, [])

    def test_an_event_name_absent_from_the_doc_text_is_reported(self) -> None:
        contract = {"events": {"AgentReplied": {}, "AgentTotallyMadeUp": {}}}
        doc_text = "mentions AgentReplied but nothing else"

        problems = lcc.check(contract, doc_text)

        self.assertEqual(len(problems), 1)
        self.assertIn("AgentTotallyMadeUp", problems[0])
        self.assertIn("not mentioned", problems[0])

    def test_every_name_present_is_clean(self) -> None:
        contract = {"events": {"AgentReplied": {}, "AgentRef": {}}}
        doc_text = "mentions both AgentReplied and AgentRef"

        problems = lcc.check(contract, doc_text)

        self.assertEqual(problems, [])

    def test_missing_events_mapping_is_reported(self) -> None:
        problems = lcc.check({}, doc_text="")

        self.assertEqual(problems, ["corridor.yaml has no top-level 'events' mapping"])


if __name__ == "__main__":
    unittest.main()
