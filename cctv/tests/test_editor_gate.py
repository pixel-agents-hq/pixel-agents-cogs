"""The editor office: anyone may watch it, only the bot owner (or members an
owner-chosen corridor permission group lets in) may edit it from its page,
enforced on the socket -- and the agents' own edits never go through it."""

from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from typing import Any

from corridor.domain import OfficeRef, OfficeState, OfficeStateChanged, OfficeStateKind

from ..adapters.commands import CommandsMixin
from ..domain import EditorGate
from ..infrastructure import EDITOR_SESSION_PATH, EDITOR_WS_PATH, CctvServer
from .test_guild_offices import GUILD_A, GUILD_B, OWNER, _Cctv, _PixelAgents, _Settings, _Socket

KEYHOLDER_A = 2  # a keyholder in guild A only
KEYHOLDER_B = 3  # a keyholder in guild B only
MEMBER_A = 1  # a member of guild A holding no group
LAYOUT = {"version": 1, "cols": 1, "rows": 1, "tiles": [7], "furniture": []}


class _GateSettings(_Settings):
    def __init__(self, enabled: set[int]) -> None:
        super().__init__(enabled)
        self.editor_gate: EditorGate | None = None

    async def global_settings(self) -> Any:
        return SimpleNamespace(editor_gate=self.editor_gate, public_base_url=None)

    async def set_editor_gate(self, gate: EditorGate | None) -> None:
        self.editor_gate = gate


class _WritablePixelAgents(_PixelAgents):
    def __init__(self) -> None:
        super().__init__()
        self.layout_writes: list[tuple[OfficeRef, dict[str, Any]]] = []

    async def set_office_layout(self, ref: OfficeRef, layout: Any) -> OfficeState:
        self.layout_writes.append((ref, dict(layout)))
        return OfficeState(ref.kind, dict(layout), {}, 2, ref.guild_id)


class _GuildGroups:
    async def guild_settings(self, guild_id: int) -> Any:
        groups = {"keyholder", "building_manager"}
        return SimpleNamespace(
            permissions=SimpleNamespace(group=lambda key: key if key in groups else None)
        )


class _EditorCctv(CommandsMixin, _Cctv):
    def __init__(self) -> None:
        super().__init__()
        self._settings = _GateSettings({GUILD_A, GUILD_B})  # type: ignore[assignment]
        self._pixelagents = _WritablePixelAgents()
        corridor = self._corridor
        groups = _GuildGroups()
        self._corridor = SimpleNamespace(
            capabilities_satisfy=corridor.capabilities_satisfy,
            guild_settings=groups.guild_settings,
        )
        self._editor = self._create_pipeline(OfficeRef.editor())
        self.replies: list[str] = []

    async def _ensure_editor_page(self) -> str | None:
        return None

    async def _reply(self, ctx: object, content: str | None = None, **kwargs: object) -> None:
        del ctx, kwargs
        if content is not None:
            self.replies.append(content)


def _ctx(guild_id: int = GUILD_A) -> Any:
    return SimpleNamespace(guild=SimpleNamespace(id=guild_id))


def _ticket(response: dict[str, Any]) -> Any:
    raw = response["raw_response"]
    return json.loads(base64.b64decode(raw["body_base64"]))["ticket"]


class TestEditorGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cctv = _EditorCctv()

    async def test_by_default_only_the_bot_owner_may_edit(self) -> None:
        self.assertTrue(await self.cctv._can_edit_editor(OWNER))
        for user_id in (KEYHOLDER_A, KEYHOLDER_B, MEMBER_A, 0, -1):
            with self.subTest(user_id=user_id):
                self.assertFalse(await self.cctv._can_edit_editor(user_id))

    async def test_a_wider_gate_is_one_servers_corridor_group(self) -> None:
        self.cctv._settings.editor_gate = EditorGate(GUILD_A, "keyholder")  # type: ignore[attr-defined]

        self.assertTrue(await self.cctv._can_edit_editor(OWNER))
        self.assertTrue(await self.cctv._can_edit_editor(KEYHOLDER_A))
        # A keyholder of another server, or a member without the group, isn't let in.
        self.assertFalse(await self.cctv._can_edit_editor(KEYHOLDER_B))
        self.assertFalse(await self.cctv._can_edit_editor(MEMBER_A))

    async def test_a_gate_on_a_server_the_bot_left_lets_only_the_owner_in(self) -> None:
        self.cctv._settings.editor_gate = EditorGate(999, "keyholder")  # type: ignore[attr-defined]

        self.assertTrue(await self.cctv._can_edit_editor(OWNER))
        self.assertFalse(await self.cctv._can_edit_editor(KEYHOLDER_A))

    async def test_the_socket_refuses_an_anonymous_or_unauthorised_edit(self) -> None:
        editor = self.cctv.editor_pipeline
        server = CctvServer(lambda guild_id: None, tuple, editor, self.cctv._tickets, dict)
        write = {"type": "saveLayout", "layout": LAYOUT}
        anonymous, stranger, owner = _Socket(), _Socket(), _Socket()
        for socket in (anonymous, stranger, owner):
            editor.clients.add(socket)  # type: ignore[arg-type]

        await server.handle_payload(editor, anonymous, write)  # type: ignore[arg-type]
        await server.handle_payload(
            editor,
            stranger,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": self.cctv._tickets.mint(KEYHOLDER_A, None)},
        )
        await server.handle_payload(editor, stranger, write)  # type: ignore[arg-type]
        self.assertEqual(self.cctv._pixelagents.layout_writes, [])

        await server.handle_payload(
            editor,
            owner,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": self.cctv._tickets.mint(OWNER, None)},
        )
        await server.handle_payload(editor, owner, write)  # type: ignore[arg-type]
        self.assertEqual(
            [ref for ref, _ in self.cctv._pixelagents.layout_writes], [OfficeRef.editor()]
        )

    async def test_an_agent_edit_reaches_anonymous_viewers(self) -> None:
        editor = self.cctv.editor_pipeline
        await editor.seed_state(OfficeState(OfficeStateKind.EDITOR, {"tiles": [1]}, {}, 1, None))
        viewer = _Socket()
        editor.clients.add(viewer)  # type: ignore[arg-type]

        # What architect/painter/animator's facade writes publish: no socket,
        # no edit gate.
        await editor.state_changed(
            OfficeStateChanged(OfficeState(OfficeStateKind.EDITOR, LAYOUT, {}, 2, None))
        )

        self.assertIn(
            {"type": "layoutLoaded", "layout": LAYOUT}, [json.loads(s) for s in viewer.sent]
        )

    async def test_changing_the_gate_reauthorises_open_sockets(self) -> None:
        editor = self.cctv.editor_pipeline
        socket = _Socket()
        editor.clients.add(socket)  # type: ignore[arg-type]

        await CommandsMixin.cmd_editors_set.callback(self.cctv, _ctx(), "keyholder")
        editor.clients.identify(socket, KEYHOLDER_A, is_editor=False)  # type: ignore[arg-type]
        await self.cctv._reauthorize_editor()
        state = editor.clients.get(socket)  # type: ignore[arg-type]
        assert state is not None
        self.assertTrue(state.is_editor)

        await CommandsMixin.cmd_editors_reset.callback(self.cctv, _ctx())
        self.assertFalse(state.is_editor)


class TestEditorsCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cctv = _EditorCctv()

    async def test_shows_owner_only_by_default(self) -> None:
        await CommandsMixin.cmd_editors.callback(self.cctv, _ctx())

        self.assertIn("Only the bot owner", self.cctv.replies[-1])

    async def test_set_opts_in_this_servers_group_and_reset_goes_back(self) -> None:
        await CommandsMixin.cmd_editors_set.callback(self.cctv, _ctx(GUILD_B), "keyholder")

        self.assertEqual(
            self.cctv._settings.editor_gate,  # type: ignore[attr-defined]
            EditorGate(GUILD_B, "keyholder"),
        )
        self.assertIn("Beta", self.cctv.replies[-1])
        self.assertIn("`keyholder`", self.cctv.replies[-1])

        await CommandsMixin.cmd_editors_reset.callback(self.cctv, _ctx())
        self.assertIsNone(self.cctv._settings.editor_gate)  # type: ignore[attr-defined]
        self.assertIn("Only the bot owner", self.cctv.replies[-1])

    async def test_reserved_groups_are_accepted_and_unknown_ones_refused(self) -> None:
        await CommandsMixin.cmd_editors_set.callback(self.cctv, _ctx(), "nobody")
        self.assertIsNone(self.cctv._settings.editor_gate)  # type: ignore[attr-defined]
        self.assertIn("no `nobody` permission group", self.cctv.replies[-1])

        await CommandsMixin.cmd_editors_set.callback(self.cctv, _ctx(), "owner")
        self.assertEqual(
            self.cctv._settings.editor_gate,  # type: ignore[attr-defined]
            EditorGate(GUILD_A, "owner"),
        )

    def test_every_editors_command_is_bot_owner_only(self) -> None:
        for command in (
            CommandsMixin.cmd_editors,
            CommandsMixin.cmd_editors_set,
            CommandsMixin.cmd_editors_reset,
        ):
            with self.subTest(command=command.name):
                self.assertTrue(getattr(command.callback, "__is_owner__", False))


class TestEditorDashboardPages(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cctv = _EditorCctv()

    async def test_the_editor_page_fetches_an_editor_ticket(self) -> None:
        page = await self.cctv.dashboard_editor()

        self.assertEqual(page["ws_path"], EDITOR_WS_PATH)
        self.assertEqual(page["ticket_path"], EDITOR_SESSION_PATH)
        self.assertFalse(page["private"])

    async def test_an_editor_ticket_proves_the_signed_in_user_to_the_editor_only(self) -> None:
        self.assertEqual(_ticket(await self.cctv.dashboard_editor_session(user_id=7)), "ticket")
        self.assertIsNone(self.cctv._tickets.consume("ticket", GUILD_A))

        _ticket(await self.cctv.dashboard_editor_session(user_id=7))
        self.assertEqual(self.cctv._tickets.consume("ticket", None), 7)
        self.assertIsNone(_ticket(await self.cctv.dashboard_editor_session(user_id=0)))


if __name__ == "__main__":
    unittest.main()
