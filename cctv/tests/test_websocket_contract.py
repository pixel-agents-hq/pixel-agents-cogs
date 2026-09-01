from __future__ import annotations

import unittest

from ..contracts import (
    InvalidClientMessageError,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    parse_client_message,
)


def _layout() -> dict[str, object]:
    return {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}


class TestWebsocketContract(unittest.TestCase):
    def test_parses_layout_and_seat_writes(self) -> None:
        self.assertIsInstance(
            parse_client_message({"type": "saveLayout", "layout": _layout()}),
            SaveLayoutMessage,
        )
        self.assertIsInstance(
            parse_client_message({"type": "saveAgentSeats", "seats": {"-1": {"palette": 2}}}),
            SaveAgentSeatsMessage,
        )

    def test_unknown_messages_are_ignored(self) -> None:
        self.assertIsNone(parse_client_message({"type": "futureMessage"}))

    def test_malformed_known_messages_are_rejected(self) -> None:
        with self.assertRaises(InvalidClientMessageError):
            parse_client_message({"type": "saveLayout", "layout": {"version": 99}})
        with self.assertRaises(InvalidClientMessageError):
            parse_client_message({"type": "saveAgentSeats", "seats": []})


if __name__ == "__main__":
    unittest.main()
