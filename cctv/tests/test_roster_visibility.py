"""Who the Discord page shows: offline members, and who is drawn translucent."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from corridor.domain import AgentPresenceChanged, AgentRef, OfficeStateKind
from pixelagents.domain import AgentSnapshot, OfficeVisibility

from ..adapters.cog_base import CctvBase, roster_warnings
from ..adapters.commands import CommandsMixin
from ..domain import GuildSettings


def _settings(**overrides: bool) -> GuildSettings:
    values = {
        "enabled": True,
        "include_bots": True,
        "show_offline": False,
        "ghost_offline": True,
        "ghost_bots": True,
    }
    values.update(overrides)
    return GuildSettings(guild_id=1, **values)


class _Settings:
    def __init__(self, guild: GuildSettings) -> None:
        self.guild = guild
        self.changes: list[tuple[str, bool]] = []

    async def guild_settings(self, guild: object) -> GuildSettings:
        del guild
        return self.guild

    async def guild_enabled(self, guild: object) -> bool:
        del guild
        return self.guild.enabled

    async def global_settings(self) -> Any:
        return SimpleNamespace(broadcast_rich_presence=False)

    async def set_guild_show_offline(self, guild_id: int, value: bool) -> None:
        self.changes.append(("show_offline", value))

    async def set_guild_ghost_offline(self, guild_id: int, value: bool) -> None:
        self.changes.append(("ghost_offline", value))

    async def set_guild_ghost_bots(self, guild_id: int, value: bool) -> None:
        self.changes.append(("ghost_bots", value))


class _Office:
    def __init__(self) -> None:
        self.synced: list[tuple[tuple[AgentSnapshot, ...], OfficeVisibility]] = []

    async def sync_guild(
        self, guild_id: int, snapshots: tuple[AgentSnapshot, ...], **kwargs: Any
    ) -> str:
        self.synced.append((snapshots, kwargs["visibility"]))
        return "Sync complete."


class _Pipeline:
    def __init__(self) -> None:
        self.office = _Office()
        self.reconciled: list[tuple[AgentSnapshot, OfficeVisibility]] = []

    async def reconcile_discord(self, snapshot: AgentSnapshot, **kwargs: Any) -> None:
        self.reconciled.append((snapshot, kwargs["visibility"]))


def _intents(*, members: bool = True, presences: bool = True) -> Any:
    return SimpleNamespace(members=members, presences=presences)


class _Guild:
    def __init__(self, *, chunked: bool = True) -> None:
        self.id = 1
        self.chunked = chunked
        self.chunk_calls = 0
        self.members = [_member(2, "online"), _member(3, "offline")]

    async def chunk(self) -> None:
        self.chunk_calls += 1
        self.chunked = True


def _member(user_id: int, status: str) -> Any:
    return SimpleNamespace(
        id=user_id,
        guild=SimpleNamespace(id=1),
        display_name=f"member-{user_id}",
        status=status,
        bot=False,
        activities=(),
    )


class _Harness(CommandsMixin):
    def __init__(self, guild: GuildSettings, *, intents: Any = None) -> None:
        self._settings = _Settings(guild)  # type: ignore[assignment]
        self._pipelines = {
            OfficeStateKind.DISCORD: _Pipeline(),  # type: ignore[dict-item]
            OfficeStateKind.EDITOR: _Pipeline(),  # type: ignore[dict-item]
        }
        self.bot = SimpleNamespace(  # type: ignore[assignment]
            user=SimpleNamespace(id=99), intents=intents or _intents()
        )
        self.replies: list[str] = []

    async def _reply(self, ctx: object, content: str | None = None, **kwargs: object) -> None:
        del ctx, kwargs
        if content is not None:
            self.replies.append(content)


def _presence(status: str, *, left_guild: bool = False) -> AgentPresenceChanged:
    return AgentPresenceChanged(
        agent=AgentRef(discord_user_id=2, guild_id=1, is_bot=False),
        display_name="Ada",
        status=status,  # type: ignore[arg-type]
        left_guild=left_guild,
    )


class TestPresenceEvents(unittest.IsolatedAsyncioTestCase):
    async def test_an_offline_event_carries_the_guilds_visibility(self) -> None:
        cog = _Harness(_settings(show_offline=True, ghost_offline=False, ghost_bots=False))

        await CctvBase._on_agent_presence_changed(cog, _presence("offline"))

        snapshot, visibility = cog.discord_pipeline.reconciled[-1]  # type: ignore[attr-defined]
        self.assertIsNone(snapshot.status)
        self.assertEqual(
            visibility,
            OfficeVisibility(show_offline=True, ghost_offline=False, ghost_bots=False),
        )

    async def test_a_member_who_left_is_removed_even_when_offline_members_show(self) -> None:
        cog = _Harness(_settings(show_offline=True))

        await CctvBase._on_agent_presence_changed(cog, _presence("offline", left_guild=True))

        snapshot, visibility = cog.discord_pipeline.reconciled[-1]  # type: ignore[attr-defined]
        self.assertIsNone(visibility.directory(snapshot))


class TestFullSync(unittest.IsolatedAsyncioTestCase):
    async def test_show_offline_seeds_offline_members_from_the_member_list(self) -> None:
        cog = _Harness(_settings(show_offline=True))
        guild = _Guild()

        result = await CctvBase._full_sync(cog, guild)  # type: ignore[arg-type]

        snapshots, visibility = cog.discord_pipeline.office.synced[-1]  # type: ignore[attr-defined]
        self.assertEqual([s.key.user_id for s in snapshots], [2, 3])
        self.assertIsNone(snapshots[1].status)
        self.assertTrue(visibility.show_offline)
        self.assertEqual(result, "Sync complete.")

    async def test_an_unchunked_guild_is_chunked_before_showing_offline_members(self) -> None:
        cog = _Harness(_settings(show_offline=True))
        guild = _Guild(chunked=False)

        await CctvBase._full_sync(cog, guild)  # type: ignore[arg-type]

        self.assertEqual(guild.chunk_calls, 1)

    async def test_hidden_offline_members_need_no_chunking(self) -> None:
        cog = _Harness(_settings())
        guild = _Guild(chunked=False)

        result = await CctvBase._full_sync(cog, guild)  # type: ignore[arg-type]

        self.assertEqual(guild.chunk_calls, 0)
        self.assertEqual(result, "Sync complete.")

    async def test_a_missing_members_intent_is_reported(self) -> None:
        cog = _Harness(_settings(show_offline=True), intents=_intents(members=False))
        guild = _Guild(chunked=False)

        result = await CctvBase._full_sync(cog, guild)  # type: ignore[arg-type]

        self.assertEqual(guild.chunk_calls, 0)
        self.assertIn("members intent is disabled", result)


class TestRosterWarnings(unittest.TestCase):
    def test_a_chunked_guild_with_both_intents_has_no_warnings(self) -> None:
        self.assertEqual(roster_warnings(_Guild(), _intents()), ())  # type: ignore[arg-type]

    def test_each_missing_piece_is_named(self) -> None:
        warnings = roster_warnings(
            _Guild(chunked=False),  # type: ignore[arg-type]
            _intents(presences=False),
        )
        self.assertEqual(len(warnings), 2)
        self.assertIn("presences intent", warnings[0])
        self.assertIn("not chunked", warnings[1])


class TestPolicyCommands(unittest.IsolatedAsyncioTestCase):
    async def _run(self, command: Any, settings: GuildSettings) -> _Harness:
        cog = _Harness(settings)
        ctx = SimpleNamespace(guild=_Guild())
        await command.callback(cog, ctx, False)
        return cog

    async def test_each_command_stores_its_setting_and_resyncs(self) -> None:
        for command, name in (
            (CommandsMixin.cmd_show_offline, "show_offline"),
            (CommandsMixin.cmd_ghost_offline, "ghost_offline"),
            (CommandsMixin.cmd_ghost_bots, "ghost_bots"),
        ):
            with self.subTest(name):
                cog = await self._run(command, _settings())
                self.assertEqual(cog._settings.changes, [(name, False)])  # type: ignore[attr-defined]
                self.assertEqual(len(cog.discord_pipeline.office.synced), 1)  # type: ignore[attr-defined]
                self.assertIn("Sync complete.", cog.replies[-1])

    async def test_a_disabled_guild_only_stores_the_setting(self) -> None:
        cog = await self._run(CommandsMixin.cmd_ghost_bots, _settings(enabled=False))

        self.assertEqual(cog.discord_pipeline.office.synced, [])  # type: ignore[attr-defined]
        self.assertEqual(cog.replies, ["Ghost-bots set to `False`."])


if __name__ == "__main__":
    unittest.main()
