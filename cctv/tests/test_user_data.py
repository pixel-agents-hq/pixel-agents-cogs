"""Red's data deletion API: CCTV revokes a user's unspent tickets and
forgets which Activity sign-in tokens are theirs."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from .. import __red_end_user_data_statement__
from ..cctv import CCTV
from ..infrastructure import DiscordOAuth, TicketStore

USER = 42
OTHER = 43
GUILD = 7
REQUESTERS = ("discord_deleted_user", "owner", "user", "user_strict")


class TestTicketStoreForgetUser(unittest.TestCase):
    def test_only_the_users_tickets_are_revoked(self) -> None:
        tokens = iter(("mine-1", "mine-2", "theirs"))
        store = TicketStore(token_factory=lambda: next(tokens))
        mine = [store.mint(USER, GUILD), store.mint(USER, GUILD + 1)]
        theirs = store.mint(OTHER, GUILD)

        self.assertEqual(store.forget_user(USER), 2)

        self.assertIsNone(store.consume(mine[0], GUILD))
        self.assertIsNone(store.consume(mine[1], GUILD + 1))
        self.assertEqual(store.consume(theirs, GUILD), OTHER)


class TestRedDeleteDataForUser(unittest.IsolatedAsyncioTestCase):
    async def test_every_requester_type_revokes_the_users_tickets(self) -> None:
        for requester in REQUESTERS:
            with self.subTest(requester=requester):
                cog = MagicMock()
                cog._tickets = TicketStore()
                cog._oauth = DiscordOAuth()
                token = cog._tickets.mint(USER, GUILD)
                cog._oauth._remember("mine", USER, 3600.0)
                cog._oauth._remember("theirs", OTHER, 3600.0)

                await CCTV.red_delete_data_for_user(cog, requester=requester, user_id=USER)

                self.assertIsNone(cog._tickets.consume(token, GUILD))
                self.assertIsNone(cog._oauth.signed_in_user("mine"))
                self.assertEqual(cog._oauth.signed_in_user("theirs"), OTHER)


class TestEndUserDataStatement(unittest.TestCase):
    def test_statement_is_the_info_json_one(self) -> None:
        info = json.loads((Path(__file__).parents[1] / "info.json").read_text("utf-8"))
        self.assertEqual(__red_end_user_data_statement__, info["end_user_data_statement"])
