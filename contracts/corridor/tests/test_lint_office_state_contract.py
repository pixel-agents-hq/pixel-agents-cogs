"""Unit tests for contracts.corridor.lint_office_state_contract -- fully
offline. Mirrors test_lint_corridor_contract.py's shape for the parallel
office-state contract."""

from __future__ import annotations

import unittest

from contracts.corridor import lint_office_state_contract as losc


class TestCheck(unittest.TestCase):
    def test_the_real_files_pass_with_zero_problems(self) -> None:
        contract = losc.load_contract()
        doc_text = losc.DESIGN_DOC_PATH.read_text(encoding="utf-8")

        problems = losc.check(contract, doc_text)

        self.assertEqual(problems, [])

    def test_an_event_name_absent_from_the_doc_text_is_reported(self) -> None:
        contract = {"events": {"OfficeState": {}, "OfficeTotallyMadeUp": {}}}
        doc_text = "mentions OfficeState but nothing else"

        problems = losc.check(contract, doc_text)

        self.assertEqual(len(problems), 1)
        self.assertIn("OfficeTotallyMadeUp", problems[0])
        self.assertIn("not mentioned", problems[0])

    def test_every_name_present_is_clean(self) -> None:
        contract = {"events": {"OfficeState": {}, "OfficeStateChanged": {}}}
        doc_text = "mentions both OfficeState and OfficeStateChanged"

        problems = losc.check(contract, doc_text)

        self.assertEqual(problems, [])

    def test_missing_events_mapping_is_reported(self) -> None:
        problems = losc.check({}, doc_text="")

        self.assertEqual(problems, ["office_state.yaml has no top-level 'events' mapping"])


if __name__ == "__main__":
    unittest.main()
