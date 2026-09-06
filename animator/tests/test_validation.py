"""parse_request_timeout: pure parsing/validation, no stubs needed."""

from __future__ import annotations

import unittest

from ..adapters.validation import parse_request_timeout


class TestParseRequestTimeout(unittest.TestCase):
    def test_parses_a_positive_number(self) -> None:
        value, error = parse_request_timeout("120")

        self.assertEqual(value, 120.0)
        self.assertIsNone(error)

    def test_parses_a_float(self) -> None:
        value, error = parse_request_timeout("45.5")

        self.assertEqual(value, 45.5)
        self.assertIsNone(error)

    def test_default_literal_resets_to_none(self) -> None:
        value, error = parse_request_timeout("default")

        self.assertIsNone(value)
        self.assertIsNone(error)

    def test_none_literal_resets_to_none(self) -> None:
        value, error = parse_request_timeout("none")

        self.assertIsNone(value)
        self.assertIsNone(error)

    def test_literal_matching_is_case_insensitive(self) -> None:
        value, error = parse_request_timeout("DEFAULT")

        self.assertIsNone(value)
        self.assertIsNone(error)

    def test_rejects_a_non_numeric_string(self) -> None:
        value, error = parse_request_timeout("soon")

        self.assertIsNone(value)
        assert error is not None
        self.assertIn("not a valid request timeout", error)

    def test_rejects_zero(self) -> None:
        value, error = parse_request_timeout("0")

        self.assertIsNone(value)
        assert error is not None
        self.assertIn("positive", error)

    def test_rejects_a_negative_number(self) -> None:
        value, error = parse_request_timeout("-5")

        self.assertIsNone(value)
        assert error is not None
        self.assertIn("positive", error)


if __name__ == "__main__":
    unittest.main()
