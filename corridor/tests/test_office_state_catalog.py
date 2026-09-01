from __future__ import annotations

import unittest

from corridor.office_state_catalog import build_contract


class TestOfficeStateCatalog(unittest.TestCase):
    def test_contract_is_separate_and_complete(self) -> None:
        contract = build_contract()

        self.assertEqual(contract["state_kinds"], ["discord", "editor"])
        self.assertEqual(contract["events"]["OfficeState"]["kind"], "value-object")
        self.assertEqual(contract["events"]["OfficeStateChanged"]["kind"], "event")
        self.assertEqual(
            contract["events"]["OfficeStateChanged"]["subscriber_timeout_seconds"],
            5.0,
        )


if __name__ == "__main__":
    unittest.main()
