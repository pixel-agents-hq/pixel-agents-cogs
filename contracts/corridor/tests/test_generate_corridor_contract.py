"""Unit tests for contracts.corridor.generate_corridor_contract -- the thin
CLI wrapper around corridor.event_catalog.build_contract(). The
introspection itself is tested in corridor/tests/test_event_catalog.py
now (see that module's docstring for why it moved)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.corridor import generate_corridor_contract as gcc


class TestMain(unittest.TestCase):
    # Path instances don't support attribute patching -- point CONTRACT_PATH
    # at a real temp file per test instead of mocking its methods.

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._path = Path(self._tmpdir.name) / "corridor.yaml"
        self._patcher = patch.object(gcc, "CONTRACT_PATH", self._path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_check_mode_fails_when_committed_file_is_stale(self) -> None:
        self._path.write_text("different content\n", encoding="utf-8")

        with patch.object(gcc, "render", return_value="fresh content\n"):
            self.assertEqual(gcc.main(check=True), 1)

    def test_check_mode_passes_when_committed_file_matches(self) -> None:
        self._path.write_text("matching content\n", encoding="utf-8")

        with patch.object(gcc, "render", return_value="matching content\n"):
            self.assertEqual(gcc.main(check=True), 0)

    def test_check_mode_fails_when_committed_file_is_missing(self) -> None:
        with patch.object(gcc, "render", return_value="some content\n"):
            self.assertEqual(gcc.main(check=True), 1)


class TestRealCommittedContract(unittest.TestCase):
    def test_matches_a_fresh_regeneration(self) -> None:
        # Integration check against the actual committed file (no
        # CONTRACT_PATH patching here, deliberately) -- catches "someone
        # edited corridor/domain/models.py and forgot to regenerate" the
        # same way CI's --check step does.
        self.assertEqual(gcc.main(check=True), 0)


if __name__ == "__main__":
    unittest.main()
