"""Unit tests for corridor.office_event_catalog -- fully offline, mirrors
test_event_catalog.py's shape for the Agent* catalog. Confirms the two
catalogs stay genuinely partitioned: office-state names never leak into
the Agent* one and vice versa."""

from __future__ import annotations

import unittest

from corridor import event_catalog, office_event_catalog


class TestBuildOfficeContract(unittest.TestCase):
    def test_office_state_is_a_value_object(self) -> None:
        contract = office_event_catalog.build_office_contract()
        entry = contract["events"]["OfficeState"]

        self.assertEqual(entry["kind"], "value-object")
        self.assertEqual(
            entry["fields"],
            {
                "kind": {"type": "Literal['discord', 'editor']"},
                "layout": {"type": "dict[str, object]"},
                "seats": {"type": "dict[str, dict[str, object]]"},
                "revision": {"type": "int"},
            },
        )

    def test_office_state_changed_is_an_event_referencing_office_state(self) -> None:
        contract = office_event_catalog.build_office_contract()
        entry = contract["events"]["OfficeStateChanged"]

        self.assertEqual(entry["kind"], "event")
        self.assertEqual(entry["fields"], {"state": {"type": "OfficeState"}})

    def test_only_office_prefixed_names_are_included(self) -> None:
        contract = office_event_catalog.build_office_contract()

        self.assertTrue(all(name.startswith("Office") for name in contract["events"]))
        self.assertNotIn("AgentReplied", contract["events"])

    def test_source_doc_points_at_the_cctv_design_doc(self) -> None:
        contract = office_event_catalog.build_office_contract()

        self.assertEqual(contract["source_doc"], "docs/cctv-design.md")


class TestCatalogsStayPartitioned(unittest.TestCase):
    def test_agent_catalog_never_includes_office_names(self) -> None:
        contract = event_catalog.build_contract()

        self.assertNotIn("OfficeState", contract["events"])
        self.assertNotIn("OfficeStateChanged", contract["events"])

    def test_office_catalog_never_includes_agent_names(self) -> None:
        contract = office_event_catalog.build_office_contract()

        self.assertNotIn("AgentPresenceChanged", contract["events"])


if __name__ == "__main__":
    unittest.main()
