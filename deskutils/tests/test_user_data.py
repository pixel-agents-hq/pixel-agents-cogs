"""Red's data deletion API: deskutils stores nothing about a user, so every
requester type is a no-op that touches no state."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from .. import __red_end_user_data_statement__
from ..deskutils import Deskutils

REQUESTERS = ("discord_deleted_user", "owner", "user", "user_strict")


class TestRedDeleteDataForUser(unittest.IsolatedAsyncioTestCase):
    async def test_every_requester_type_is_a_no_op(self) -> None:
        for requester in REQUESTERS:
            with self.subTest(requester=requester):
                cog = MagicMock()

                await Deskutils.red_delete_data_for_user(cog, requester=requester, user_id=42)

                self.assertEqual(cog.mock_calls, [])


class TestEndUserDataStatement(unittest.TestCase):
    def test_statement_is_the_info_json_one(self) -> None:
        info = json.loads((Path(__file__).parents[1] / "info.json").read_text("utf-8"))
        self.assertEqual(__red_end_user_data_statement__, info["end_user_data_statement"])
