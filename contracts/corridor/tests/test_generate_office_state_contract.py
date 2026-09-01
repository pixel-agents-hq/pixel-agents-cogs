from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.corridor import generate_office_state_contract as generator


class TestOfficeStateContractGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._path = Path(self._tmpdir.name) / "office_state.yaml"
        self._patcher = patch.object(generator, "CONTRACT_PATH", self._path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_check_fails_for_missing_or_stale_contract(self) -> None:
        with patch.object(generator, "render", return_value="fresh\n"):
            self.assertEqual(generator.main(check=True), 1)
            self._path.write_text("stale\n", encoding="utf-8")
            self.assertEqual(generator.main(check=True), 1)

    def test_check_passes_for_matching_contract(self) -> None:
        self._path.write_text("fresh\n", encoding="utf-8")
        with patch.object(generator, "render", return_value="fresh\n"):
            self.assertEqual(generator.main(check=True), 0)


class TestCommittedOfficeStateContract(unittest.TestCase):
    def test_matches_generator(self) -> None:
        self.assertEqual(generator.main(check=True), 0)


if __name__ == "__main__":
    unittest.main()
