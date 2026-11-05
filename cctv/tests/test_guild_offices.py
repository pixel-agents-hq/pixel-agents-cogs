"""One isolated office per enabled guild: state, presence, write
authorization, health and the Dashboard pages never cross guilds."""

from __future__ import annotations

import unittest
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from corridor.domain import (
    AgentPresenceChanged,
    AgentRef,
    OfficeRef,
    OfficeState,
    OfficeStateChanged,
    OfficeStateKind,
)

from ..adapters.dashboard import DashboardMixin, parse_guild_id
from ..application import GuildOffices

GUILD_A = 111
GUILD_B = 222


def _layout() -> dict[str, Any]:
    return {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}


class _Seats:
    async def seats(self) -> dict[str, Any]:
        return {}

    async def mutate_seats(self, mutation: Any) -> Any:
        return mutation({})


class _PixelAgents:
    def __init__(self) -> None:
        self.reads: list[OfficeRef] = []

    async def office_state(self, ref: OfficeRef) -> OfficeState:
        self.reads.append(ref)
        return OfficeState(ref.kind, _layout(), {}, 1, ref.guild_id)

    def office_seat_repository(self, ref: OfficeRef) -> _Seats:
        return _Seats()

    async def office_character_profile(self, identity: object) -> dict[str, Any]:
        return {"body": "flat"}


class _Settings:
    def __init__(self, enabled: set[int]) -> None:
        self.enabled = enabled

    async def guild_enabled(self, guild: object) -> bool:
        return guild in self.enabled

    async def guild_settings(self, guild: object) -> Any:
        return SimpleNamespace(
            enabled=guild in self.enabled,
            include_bots=True,
            show_offline=False,
            ghost_offline=True,
            ghost_bots=True,
            broadcast_rich_presence=False,
            broadcast_messages=True,
        )


class _Member:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Guild:
    def __init__(self, guild_id: int, name: str, members: set[int]) -> None:
        self.id = guild_id
        self.name = name
        self.icon = None
        self._members = members

    def get_member(self, user_id: int) -> _Member | None:
        return _Member(user_id) if user_id in self._members else None


class _Corridor:
    def __init__(self, keyholders: set[int]) -> None:
        self.keyholders = keyholders

    async def capabilities_satisfy(self, member: _Member, group: str) -> bool:
        return group == "keyholder" and member.id in self.keyholders


class _Socket:
    closed = False

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


class _Assets:
    assets: dict[str, object] = {}
    ready = True

    def not_found_response(self) -> dict[str, object]:
        return {"status": 1, "error_code": 404}

    def redirect_response(self, path: str) -> dict[str, object]:
        return {"status": 0, "redirect_url": path}

    def page_response(self, ws_path: str, *, identify: bool) -> dict[str, object]:
        return {"status": 0, "ws_path": ws_path, "identify": identify}


class _Cctv(DashboardMixin):
    def __init__(self) -> None:
        guilds = {
            GUILD_A: _Guild(GUILD_A, "Alpha", {1, 2}),
            GUILD_B: _Guild(GUILD_B, "Beta", {1, 3}),
        }
        self._settings = _Settings({GUILD_A, GUILD_B})  # type: ignore[assignment]
        self._pixelagents = _PixelAgents()
        self._corridor = _Corridor(keyholders={2, 3})
        self._assets = _Assets()  # type: ignore[assignment]
        self._offices = GuildOffices()
        self._editor = None
        self._genuine_agents = {"architect": ("Architect", "online")}
        self._server = None
        self.bot = SimpleNamespace(  # type: ignore[assignment]
            user=SimpleNamespace(id=99),
            get_guild=guilds.get,
            is_owner=self._is_owner,
        )

    async def _is_owner(self, user: object) -> bool:
        return False

    async def _ensure_office_page(self, office: Any) -> str | None:
        return None


class TestGuildOffices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cctv = _Cctv()
        self.a = await self.cctv._open_office(GUILD_A)
        self.b = await self.cctv._open_office(GUILD_B)

    async def test_each_office_reads_its_own_aggregate_and_seats_the_agents(self) -> None:
        self.assertEqual(
            self.cctv._pixelagents.reads,
            [OfficeRef.discord(GUILD_A), OfficeRef.discord(GUILD_B)],
        )
        self.assertEqual((self.a.revision, self.b.revision), (1, 1))
        self.assertTrue(self.a.office._genuine_agents)
        self.assertIs(await self.cctv._open_office(GUILD_A), self.a)

    async def test_a_state_change_reaches_only_its_guilds_office(self) -> None:
        socket_a, socket_b = _Socket(), _Socket()
        self.a.clients.add(socket_a)  # type: ignore[arg-type]
        self.b.clients.add(socket_b)  # type: ignore[arg-type]
        changed = OfficeState(OfficeStateKind.DISCORD, {**_layout(), "cols": 1}, {}, 5, GUILD_A)

        await self.cctv._on_office_state_changed(OfficeStateChanged(deepcopy(changed)))

        self.assertEqual((self.a.revision, self.b.revision), (5, 1))
        self.assertTrue(socket_a.sent)
        self.assertEqual(socket_b.sent, [])

    async def test_a_member_appears_only_in_the_office_of_the_guild_they_act_in(self) -> None:
        await self.cctv._on_agent_presence_changed(
            AgentPresenceChanged(
                agent=AgentRef(discord_user_id=1, guild_id=GUILD_A, is_bot=False),
                display_name="Ada",
                status="online",
            )
        )

        self.assertEqual(self.a.office.tracked_user_ids(), [1])
        self.assertEqual(self.b.office.tracked_user_ids(), [])

    async def test_a_disabled_guilds_events_open_nothing(self) -> None:
        await self.cctv._on_agent_presence_changed(
            AgentPresenceChanged(
                agent=AgentRef(discord_user_id=1, guild_id=333, is_bot=False),
                display_name="Ada",
                status="online",
            )
        )

        self.assertEqual(self.cctv._offices.guild_ids(), (GUILD_A, GUILD_B))

    async def test_closing_an_office_disconnects_its_viewers_only(self) -> None:
        socket_a, socket_b = _Socket(), _Socket()
        self.a.clients.add(socket_a)  # type: ignore[arg-type]
        self.b.clients.add(socket_b)  # type: ignore[arg-type]

        await self.cctv._close_office(GUILD_A)

        self.assertIsNone(self.cctv.office(GUILD_A))
        self.assertTrue(socket_a.closed)
        self.assertFalse(socket_b.closed)

    async def test_a_keyholder_edits_only_their_own_guilds_office(self) -> None:
        self.assertTrue(await self.cctv._can_edit_guild(GUILD_A, 2))
        self.assertFalse(await self.cctv._can_edit_guild(GUILD_B, 2))
        self.assertTrue(await self.cctv._can_edit_guild(GUILD_B, 3))
        self.assertFalse(await self.cctv._can_edit_guild(GUILD_A, 1))
        self.assertFalse(await self.cctv._can_edit_guild(GUILD_A, 0))
        self.cctv._settings.enabled.discard(GUILD_A)  # type: ignore[attr-defined]
        self.assertFalse(await self.cctv._can_edit_guild(GUILD_A, 2))

    async def test_health_names_no_guild(self) -> None:
        self.cctv._degraded_reasons = lambda: ()  # type: ignore[method-assign]
        health = self.cctv._health_snapshot()

        self.assertEqual(health["offices"]["open"], 2)  # type: ignore[index]
        self.assertNotIn(str(GUILD_A), repr(health))
        self.assertNotIn("Alpha", repr(health))


class TestDashboardPages(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cctv = _Cctv()
        await self.cctv._open_office(GUILD_A)

    async def test_an_open_office_serves_the_webview_on_its_own_socket(self) -> None:
        response = await self.cctv.dashboard_office(guild=str(GUILD_A))

        self.assertEqual(response["ws_path"], f"/cctv/office/{GUILD_A}/ws")

    async def test_every_unavailable_office_answers_the_same(self) -> None:
        for raw in (str(GUILD_B), "999", "", "abc", "-1", "1" * 30):
            with self.subTest(raw=raw):
                self.assertEqual(
                    await self.cctv.dashboard_office(guild=raw),
                    {"status": 1, "error_code": 404},
                )

    async def test_the_old_discord_page_redirects_to_the_picker(self) -> None:
        self.assertEqual(
            await self.cctv.dashboard_discord(), {"status": 0, "redirect_url": "/third-party/cctv"}
        )

    async def test_the_picker_lists_open_offices(self) -> None:
        response = await self.cctv.dashboard_picker()
        guilds = response["web_content"]["cctv_guilds"]  # type: ignore[index]

        self.assertEqual(
            guilds,
            [
                {
                    "href": f"/third-party/cctv/office?guild={GUILD_A}",
                    "name": "Alpha",
                    "icon_url": None,
                    "headcount": 1,
                }
            ],
        )

    def test_parse_guild_id(self) -> None:
        self.assertEqual(parse_guild_id("123"), 123)
        self.assertEqual(parse_guild_id(" 123 "), 123)
        for raw in ("", "0", "-5", "1e3", "abc", "1" * 21, None, True, 1.5):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_guild_id(raw))
