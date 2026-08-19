"""Unit tests for framework-free administrator-setting validation."""

from __future__ import annotations

import unittest

from pixelagents.domain import parse_commit_ref


class TestParseCommitRef(unittest.TestCase):
    def test_accepts_a_full_hash(self) -> None:
        commit = "3537e140c2094761beae748592aeb92ece8edfdd"
        self.assertEqual(parse_commit_ref(commit), commit)

    def test_accepts_a_short_hash(self) -> None:
        self.assertEqual(parse_commit_ref("3537e14"), "3537e14")

    def test_lowercases_a_mixed_case_hash(self) -> None:
        self.assertEqual(parse_commit_ref("3537E14"), "3537e14")

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(parse_commit_ref("  3537e14  "), "3537e14")

    def test_accepts_a_tree_link(self) -> None:
        commit = "3537e140c2094761beae748592aeb92ece8edfdd"
        self.assertEqual(
            parse_commit_ref(f"https://github.com/pixel-agents-hq/pixel-agents/tree/{commit}"),
            commit,
        )

    def test_accepts_a_commit_link(self) -> None:
        commit = "3537e140c2094761beae748592aeb92ece8edfdd"
        self.assertEqual(
            parse_commit_ref(f"https://github.com/pixel-agents-hq/pixel-agents/commit/{commit}"),
            commit,
        )

    def test_accepts_a_tree_link_with_a_trailing_path(self) -> None:
        commit = "3537e140c2094761beae748592aeb92ece8edfdd"
        self.assertEqual(
            parse_commit_ref(
                f"https://github.com/pixel-agents-hq/pixel-agents/tree/{commit}/webview-ui"
            ),
            commit,
        )

    def test_rejects_a_hash_that_is_too_short(self) -> None:
        with self.assertRaises(ValueError):
            parse_commit_ref("abc123")

    def test_rejects_non_hex_input(self) -> None:
        with self.assertRaises(ValueError):
            parse_commit_ref("not-a-commit")

    def test_rejects_a_link_to_a_different_repository(self) -> None:
        with self.assertRaises(ValueError):
            parse_commit_ref("https://github.com/pixel-agents-hq/pixel-index/tree/" + "a" * 40)


if __name__ == "__main__":
    unittest.main()
