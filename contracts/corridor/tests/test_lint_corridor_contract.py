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

    def test_a_missing_event_is_reported(self) -> None:
        contract = {
            "events": {
                name: {"fields": {}}
                for name in lcc._EXPECTED_EVENTS
                if name != "AgentUnhighlighted"
            }
        }

        problems = lcc.check(
            contract,
            doc_text="AgentRef AgentReplied AgentToolStarted "
            "AgentStatusChanged AgentHighlighted AgentUnhighlighted",
        )

        self.assertTrue(any("missing event(s)" in p for p in problems))

    def test_an_undocumented_extra_event_is_reported(self) -> None:
        contract = {
            "events": {
                **{name: {"fields": {}} for name in lcc._EXPECTED_EVENTS},
                "AgentSomethingElse": {"fields": {}},
            }
        }

        problems = lcc.check(contract, doc_text="mentions every real name plus AgentSomethingElse")

        self.assertTrue(any("undocumented event(s)" in p for p in problems))

    def test_an_event_name_absent_from_the_doc_text_is_reported(self) -> None:
        contract = {"events": {name: {"fields": {}} for name in lcc._EXPECTED_EVENTS}}
        doc_text_missing_one = (
            "AgentRef AgentReplied AgentToolStarted AgentStatusChanged AgentHighlighted"
        )

        problems = lcc.check(contract, doc_text_missing_one)

        self.assertTrue(any("AgentUnhighlighted" in p and "not mentioned" in p for p in problems))

    def test_a_field_with_no_type_is_reported(self) -> None:
        contract = {
            "events": {
                **{name: {"fields": {}} for name in lcc._EXPECTED_EVENTS if name != "AgentRef"},
                "AgentRef": {"fields": {"discord_user_id": {}}},
            }
        }
        doc_text = " ".join(lcc._EXPECTED_EVENTS)

        problems = lcc.check(contract, doc_text)

        self.assertTrue(any("discord_user_id has no declared type" in p for p in problems))

    def test_missing_events_mapping_is_reported(self) -> None:
        problems = lcc.check({}, doc_text="")

        self.assertEqual(problems, ["corridor.yaml has no top-level 'events' mapping"])


if __name__ == "__main__":
    unittest.main()
