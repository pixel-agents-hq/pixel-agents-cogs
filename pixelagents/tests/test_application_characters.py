"""CharacterService and the character roster OfficeService draws from it."""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

from pixelagents.application.characters import (
    LAST_RESORT_PROFILE,
    CharacterService,
    parse_character_record,
)
from pixelagents.application.office import OfficeService
from pixelagents.domain import AgentKey, AgentSnapshot, GenuineAgentKey, PresenceStatus
from pixelagents.domain.characters import (
    CharacterSource,
    PixatarCharacter,
    PixatarFailure,
    character_key,
    deal_text,
    profile_id,
)

MutationResult = TypeVar("MutationResult")


class _Corridor:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.writes = 0

    async def office_characters(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self.records)

    async def mutate_office_characters(
        self, mutation: Callable[[dict[str, dict[str, Any]]], MutationResult]
    ) -> tuple[dict[str, dict[str, Any]], MutationResult]:
        records = deepcopy(self.records)
        result = mutation(records)
        self.records = records
        self.writes += 1
        return deepcopy(records), result


class _Pixatar:
    """Deals `px.text-<text>`; knows the codes in `codes`."""

    def __init__(self) -> None:
        self.codes = {"px.designed": {"body": "flat", "hair": "long_bangs"}}
        self.dealt: list[str] = []

    async def from_text(self, text: str) -> PixatarCharacter:
        self.dealt.append(text)
        return PixatarCharacter(f"px.text-{text}", {"body": "flat", "hair": f"hair-{text}"})

    async def from_seed(self, seed: str) -> PixatarCharacter:
        if seed not in self.codes:
            raise PixatarFailure("invalid-seed", "This seed code is not one pixatar knows.")
        return PixatarCharacter(seed, dict(self.codes[seed]))

    async def avatar_png(self, seed: str, *, scale: int) -> bytes:
        return f"{seed}@{scale}".encode()


DEFAULTS = [{"id": f"char-{i}", "name": "Suit", "body": "flat", "hair": f"d{i}"} for i in range(6)]
MEMBER = AgentKey(1, 42)


class TestCharacterService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.corridor = _Corridor()
        self.pixatar: _Pixatar | None = _Pixatar()
        self.service = CharacterService(self.corridor, lambda: self.pixatar, lambda: DEFAULTS)

    async def test_a_member_without_a_character_is_dealt_one_from_their_user_id(self) -> None:
        record = await self.service.character(MEMBER)

        assert record is not None
        self.assertEqual(record.seed, "px.text-42")
        self.assertIs(record.source, CharacterSource.DERIVED)
        self.assertEqual(
            self.corridor.records["user:42"],
            {"seed": "px.text-42", "source": "derived", "profile": record.profile},
        )

    async def test_a_genuine_agent_is_dealt_one_from_its_agent_key(self) -> None:
        profile = await self.service.profile(GenuineAgentKey("architect"))

        self.assertEqual(profile, {"body": "flat", "hair": "hair-architect"})
        self.assertIn("agent:architect", self.corridor.records)

    async def test_a_stored_character_is_never_dealt_again(self) -> None:
        await self.service.character(MEMBER)
        assert self.pixatar is not None
        self.pixatar.dealt.clear()

        # The same account in another guild wears the same character.
        await self.service.character(AgentKey(2, 42))

        self.assertEqual(self.pixatar.dealt, [])
        self.assertEqual(self.corridor.writes, 1)

    async def test_setting_a_seed_code_replaces_the_dealt_character(self) -> None:
        await self.service.character(MEMBER)

        record = await self.service.set_seed(MEMBER, "  px.designed ")

        self.assertEqual(record.seed, "px.designed")
        self.assertIs(record.source, CharacterSource.CUSTOM)
        self.assertEqual(await self.service.profile(MEMBER), {"body": "flat", "hair": "long_bangs"})

    async def test_anything_but_a_readable_seed_code_is_refused(self) -> None:
        for seed in ("octocat", "px.nope"):
            with self.subTest(seed=seed), self.assertRaises(PixatarFailure) as caught:
                await self.service.set_seed(MEMBER, seed)
            self.assertEqual(caught.exception.code, "invalid-seed")
        self.assertEqual(self.corridor.records, {})

    async def test_reset_deals_from_the_user_id_again(self) -> None:
        await self.service.set_seed(MEMBER, "px.designed")

        record = await self.service.reset(MEMBER)

        self.assertEqual(record.seed, "px.text-42")
        self.assertEqual(self.corridor.records["user:42"]["source"], "derived")

    async def test_without_pixatar_a_default_cast_member_stands_in_and_nothing_is_stored(
        self,
    ) -> None:
        self.pixatar = None

        first = await self.service.profile(MEMBER)
        second = await self.service.profile(MEMBER)

        self.assertEqual(first, second)
        self.assertNotIn("id", first)
        self.assertIn(first, [{k: v for k, v in d.items() if k != "id"} for d in DEFAULTS])
        self.assertEqual(self.corridor.records, {})
        self.assertIsNone(await self.service.character(MEMBER))
        with self.assertRaises(PixatarFailure):
            await self.service.set_seed(MEMBER, "px.designed")

    async def test_without_pixatar_or_a_built_cast_the_last_resort_profile_stands_in(
        self,
    ) -> None:
        service = CharacterService(self.corridor, lambda: None, lambda: [])

        self.assertEqual(await service.profile(MEMBER), LAST_RESORT_PROFILE)

    async def test_an_unreadable_record_is_dealt_afresh(self) -> None:
        self.corridor.records["user:42"] = {"seed": "octocat", "source": "derived", "profile": {}}

        record = await self.service.character(MEMBER)

        assert record is not None
        self.assertEqual(record.seed, "px.text-42")

    def test_keys_from_a_re_executed_module_are_read_the_same(self) -> None:
        # Red re-executes pixelagents' modules on load, so a consuming cog's
        # AgentKey/GenuineAgentKey can be another execution's class.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class AgentKey:  # noqa: N801 -- stands in for an older execution's class
            guild_id: int
            user_id: int

        @dataclass(frozen=True)
        class GenuineAgentKey:  # noqa: N801
            agent_key: str

        self.assertEqual(profile_id(AgentKey(1, 42)), "u42")  # type: ignore[arg-type]
        self.assertEqual(character_key(AgentKey(1, 42)), "user:42")  # type: ignore[arg-type]
        self.assertEqual(deal_text(AgentKey(1, 42)), "42")  # type: ignore[arg-type]
        self.assertEqual(
            profile_id(GenuineAgentKey("bootcamp.helper")),  # type: ignore[arg-type]
            "a-bootcamp-helper",
        )
        self.assertEqual(
            character_key(GenuineAgentKey("architect")),  # type: ignore[arg-type]
            "agent:architect",
        )

    def test_parse_rejects_malformed_records(self) -> None:
        good = {"seed": "px.a", "source": "custom", "profile": {"body": "flat"}}
        self.assertIsNotNone(parse_character_record(good))
        for bad in (
            None,
            {**good, "seed": 1},
            {**good, "source": "stolen"},
            {**good, "profile": {"hair": "x"}},
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_character_record(bad))


class _Looks:
    def __init__(self) -> None:
        self.hair: dict[str, str] = {}

    async def profile(self, identity: Any) -> dict[str, Any]:
        key = profile_id(identity)
        return {"body": "flat", "hair": self.hair.get(key, key)}


class _Seats:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def seats(self) -> dict[str, dict[str, object]]:
        return deepcopy(self.values)

    async def mutate_seats(self, mutation: Any) -> Any:
        return mutation(self.values)


def _member(user_id: int, name: str, *, guild_id: int = 1) -> AgentSnapshot:
    return AgentSnapshot(
        key=AgentKey(guild_id, user_id),
        display_name=name,
        status=PresenceStatus.ONLINE,
        is_bot=False,
    )


class TestCharacterRoster(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sent: list[dict[str, Any]] = []

        async def send(message: Any) -> None:
            self.sent.append(dict(message))

        self.looks = _Looks()
        self.seats = _Seats()
        self.office = OfficeService(self.seats, send, characters=self.looks)

    def _of_type(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["type"] == kind]

    async def test_each_shown_character_wears_its_own_profile_named_after_it(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)
        await self.office.spawn(_member(20, "Grace"), rich_presence_enabled=False)

        created = self._of_type("agentCreated")
        self.assertEqual(
            [(c["id"], c["palette"], c["repeat"]) for c in created], [(-10, 0, 0), (-20, 1, 0)]
        )
        self.assertEqual(
            self.office.roster_profiles(),
            [
                {"body": "flat", "hair": "u10", "id": "u10", "name": "Ada"},
                {"body": "flat", "hair": "u20", "id": "u20", "name": "Grace"},
            ],
        )
        # Nothing about the look is written into the seats.
        self.assertEqual(self.seats.values, {})

    async def test_the_cast_is_sent_before_the_agent_that_wears_it(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)

        types = [m["type"] for m in self.sent]
        self.assertLess(types.index("characterProfilesLoaded"), types.index("agentCreated"))

    async def test_a_leaving_character_leaves_the_cast_and_the_rest_move_up(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)
        await self.office.spawn(_member(20, "Grace"), rich_presence_enabled=False)
        self.sent.clear()

        await self.office.close(AgentKey(1, 10))

        loaded = self._of_type("characterProfilesLoaded")[-1]
        self.assertEqual([p["name"] for p in loaded["profiles"]], ["Grace"])
        self.assertEqual(loaded["agentLooks"], [{"id": -20, "palette": 0, "repeat": 0}])
        existing = self._of_type("existingAgents")[-1]
        self.assertEqual(existing["agentMeta"]["-20"]["palette"], 0)

    async def test_a_rename_renames_the_cast_entry(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)
        self.sent.clear()

        await self.office.reconcile(
            _member(10, "Ada L."), include_bots=False, rich_presence_enabled=False
        )

        loaded = self._of_type("characterProfilesLoaded")
        self.assertEqual(loaded[-1]["profiles"][0]["name"], "Ada L.")

    async def test_a_member_in_two_guilds_is_one_cast_entry(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)
        await self.office.spawn(_member(10, "Ada", guild_id=2), rich_presence_enabled=False)

        self.assertEqual(len(self.office.roster_profiles()), 1)

    async def test_genuine_agents_are_in_the_cast_too(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)
        await self.office.reconcile_genuine_agent(
            GenuineAgentKey("architect"), "Architect", "online"
        )

        self.assertEqual([p["id"] for p in self.office.roster_profiles()], ["u10", "a-architect"])
        await self.office.close_genuine_agent(GenuineAgentKey("architect"))
        self.assertEqual([p["id"] for p in self.office.roster_profiles()], ["u10"])

    async def test_refresh_redresses_a_shown_character_only(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)
        self.sent.clear()

        self.looks.hair["u10"] = "long_bangs"
        await self.office.refresh_character(AgentKey(1, 10))
        await self.office.refresh_character(AgentKey(1, 99))

        loaded = self._of_type("characterProfilesLoaded")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["profiles"][0]["hair"], "long_bangs")

    async def test_bootstrap_sends_the_cast_in_place_of_the_defaults(self) -> None:
        await self.office.spawn(_member(10, "Ada"), rich_presence_enabled=False)

        messages = self.office.bootstrap_messages(
            assets={"characterProfiles": DEFAULTS}, seats={}, layout={"tiles": []}
        )

        loaded = next(m for m in messages if m["type"] == "characterProfilesLoaded")
        self.assertEqual([p["id"] for p in loaded["profiles"]], ["u10"])

    async def test_an_empty_office_bootstraps_with_the_defaults(self) -> None:
        messages = self.office.bootstrap_messages(
            assets={"characterProfiles": DEFAULTS}, seats={}, layout={"tiles": []}
        )

        loaded = next(m for m in messages if m["type"] == "characterProfilesLoaded")
        self.assertEqual(loaded["profiles"], DEFAULTS)


@unittest.skipUnless(
    os.environ.get("PIXELAGENTS_REAL_PIXATAR") == "1",
    "set PIXELAGENTS_REAL_PIXATAR=1 (and PIXATAR_CLI, or a built CLI) to deal from the real CLI",
)
class TestRealPixatarDeal(unittest.IsolatedAsyncioTestCase):
    async def test_a_user_id_deals_the_character_pixel_index_deals(self) -> None:
        from pixelagents.infrastructure.pixatar_client import PixatarClient

        client = PixatarClient()
        try:
            dealt = await client.from_text("123456789012345678")
            again = await client.from_seed(dealt.seed)
        finally:
            client.close()
        # Pinned by pixel-index's own `pixatar info 123456789012345678`.
        self.assertEqual(
            dealt.seed, "px.ASMBNkHtAcOaAj2AbyDFgBthZAUGfQQ53_gJfRRQ_-kKfYgBHdT6AEIAZQh9VUX8AQ"
        )
        self.assertEqual(again.profile, dealt.profile)


if __name__ == "__main__":
    unittest.main()
