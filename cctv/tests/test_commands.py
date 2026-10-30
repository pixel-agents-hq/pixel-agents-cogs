from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from pixelagents.domain import AgentKey, GenuineAgentKey
from pixelagents.domain.characters import CharacterRecord, CharacterSource, PixatarFailure

from ..adapters.commands import CommandsMixin


class _Settings:
    def __init__(self) -> None:
        self.rich_presence: list[bool] = []

    async def global_settings(self) -> Any:
        return SimpleNamespace(designer_url="https://designer.example/assets/design")

    async def set_broadcast_rich_presence(self, value: bool) -> None:
        self.rich_presence.append(value)


class _Office:
    def __init__(self) -> None:
        self.clear_calls = 0

    async def clear_presence(self) -> None:
        self.clear_calls += 1


class _Harness(CommandsMixin):
    def __init__(self) -> None:
        self._settings = _Settings()
        self._pipelines = {"discord": type("Pipeline", (), {"office": _Office()})()}
        self.sync_calls = 0
        self.replies: list[str] = []

    @property
    def discord_pipeline(self) -> Any:
        return self._pipelines["discord"]

    async def _sync_all_guilds(self) -> None:
        self.sync_calls += 1

    async def _reply(self, ctx: object, content: str | None = None, **kwargs: object) -> None:
        del ctx, kwargs
        if content is not None:
            self.replies.append(content)


class TestCctvDisplayCommands(unittest.IsolatedAsyncioTestCase):
    async def test_enabling_rich_presence_reconciles_enabled_guilds(self) -> None:
        cog = _Harness()

        await CommandsMixin.cmd_rich_presence.callback(cog, object(), True)

        self.assertEqual(cog._settings.rich_presence, [True])
        self.assertEqual(cog.sync_calls, 1)
        self.assertEqual(cog.discord_pipeline.office.clear_calls, 0)

    async def test_disabling_rich_presence_clears_existing_activity(self) -> None:
        cog = _Harness()

        await CommandsMixin.cmd_rich_presence.callback(cog, object(), False)

        self.assertEqual(cog._settings.rich_presence, [False])
        self.assertEqual(cog.sync_calls, 0)
        self.assertEqual(cog.discord_pipeline.office.clear_calls, 1)


_DEALT = CharacterRecord("px.dealt", CharacterSource.DERIVED, {"body": "flat"})


class _Characters:
    def __init__(self) -> None:
        self.records: dict[AgentKey, CharacterRecord] = {}
        self.available = True

    def pixatar_status(self) -> str:
        return "⚠️ pixatar's Python client is not installed (reinstall pixelagents)."

    async def office_character(self, identity: AgentKey) -> CharacterRecord | None:
        if not self.available:
            return None
        return self.records.get(identity, _DEALT)

    async def set_office_character_seed(self, identity: AgentKey, seed: str) -> CharacterRecord:
        if not self.available:
            raise PixatarFailure("cli-not-found", "pixatar is not available on this bot")
        if seed != "px.good":
            raise PixatarFailure("invalid-seed", "This seed code is not one pixatar knows.")
        self.records[identity] = CharacterRecord(seed, CharacterSource.CUSTOM, {"body": "flat"})
        return self.records[identity]

    async def reset_office_character(self, identity: AgentKey) -> CharacterRecord:
        self.records.pop(identity, None)
        return _DEALT

    async def office_character_avatar(self, record: CharacterRecord, *, scale: int = 8) -> bytes:
        return b"png:" + record.seed.encode()


class _CharacterHarness(CommandsMixin):
    def __init__(self) -> None:
        self._settings = _Settings()
        self._pixelagents = _Characters()
        self.bot = SimpleNamespace(user=SimpleNamespace(id=7))
        self.allowed = True
        self.permission_checks: list[str] = []
        self._corridor = SimpleNamespace(
            list_agents=lambda: [
                SimpleNamespace(agent_key="painter"),
                SimpleNamespace(agent_key="architect"),
            ],
            require_permission=self._require_permission,
        )
        self.refreshed: list[AgentKey] = []
        self.replies: list[dict[str, Any]] = []

    async def _require_permission(self, ctx: object, group_key: str) -> bool:
        del ctx
        self.permission_checks.append(group_key)
        return self.allowed

    async def _refresh_character(self, identity: Any) -> None:
        self.refreshed.append(identity)

    async def _reply(self, ctx: object, content: str | None = None, **kwargs: Any) -> None:
        del ctx
        self.replies.append({"content": content, **kwargs})


def _ctx() -> Any:
    return SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=42))


def _member(member_id: int, name: str, *, bot: bool = False) -> Any:
    return SimpleNamespace(id=member_id, display_name=name, bot=bot)


class TestCctvCharacterCommands(unittest.IsolatedAsyncioTestCase):
    async def test_shows_the_dealt_character_with_a_designer_link(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character.callback(cog, _ctx())

        reply = cog.replies[0]
        self.assertIn("dealt from your Discord user id", reply["content"])
        self.assertIn("https://designer.example/assets/design?seed=px.dealt", reply["content"])
        self.assertEqual(reply["fields"][0].value, "px.dealt")
        self.assertEqual(reply["files"][0].filename, "character.png")

    async def test_setting_a_seed_stores_it_and_redresses_the_member(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_set.callback(cog, _ctx(), "px.good")

        self.assertEqual(cog._pixelagents.records[AgentKey(1, 42)].seed, "px.good")
        self.assertEqual(cog.refreshed, [AgentKey(1, 42)])
        self.assertIn("a seed code you set", cog.replies[0]["content"])

    async def test_a_bad_seed_is_refused_and_nothing_changes(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_set.callback(cog, _ctx(), "px.bad")

        self.assertEqual(cog._pixelagents.records, {})
        self.assertEqual(cog.refreshed, [])
        self.assertIn("not one pixatar knows", cog.replies[0]["content"])

    async def test_an_unavailable_pixatar_says_why(self) -> None:
        cog = _CharacterHarness()
        cog._pixelagents.available = False

        await CommandsMixin.cmd_character.callback(cog, _ctx())
        await CommandsMixin.cmd_character_set.callback(cog, _ctx(), "px.good")

        for reply in cog.replies:
            self.assertIn("Python client is not installed", reply["content"])

    async def test_an_agent_can_be_dressed_by_seed(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_agent_set.callback(cog, _ctx(), "architect", "px.good")

        architect = GenuineAgentKey("architect")
        self.assertEqual(cog._pixelagents.records[architect].seed, "px.good")  # type: ignore[index]
        self.assertEqual(cog.refreshed, [architect])
        self.assertIn("a seed code set for it", cog.replies[0]["content"])
        self.assertEqual(cog.replies[0]["code"], ["[p]cctv character agentset architect <seed>"])

    async def test_pico_is_not_an_agent_key(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_agent_set.callback(cog, _ctx(), "pico", "px.good")

        self.assertEqual(cog._pixelagents.records, {})
        self.assertEqual(cog.refreshed, [])
        self.assertIn("No agent `pico`", cog.replies[0]["content"])

    async def test_an_unknown_agent_lists_the_known_ones(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_agent.callback(cog, _ctx(), "nobody")

        self.assertEqual(cog.refreshed, [])
        self.assertIn("Known agents: `architect`, `painter`.", cog.replies[0]["content"])

    async def test_a_member_can_be_dressed_by_seed(self) -> None:
        cog = _CharacterHarness()
        target = _member(99, "Robin")

        await CommandsMixin.cmd_character_member_set.callback(cog, _ctx(), target, "px.good")

        self.assertEqual(cog.permission_checks, ["building_manager"])
        self.assertEqual(cog._pixelagents.records[AgentKey(1, 99)].seed, "px.good")
        self.assertEqual(cog.refreshed, [AgentKey(1, 99)])
        reply = cog.replies[0]
        self.assertEqual(reply["title"], "Robin's character updated")
        self.assertIn("Robin's character is a seed code set for them", reply["content"])
        self.assertEqual(reply["code"], ["[p]cctv character memberset 99 <seed>"])

    async def test_a_member_set_seed_is_what_they_see_themselves(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_member_set.callback(
            cog, _ctx(), _member(42, "Me"), "px.good"
        )
        await CommandsMixin.cmd_character.callback(cog, _ctx())

        self.assertEqual(cog.replies[-1]["fields"][0].value, "px.good")

    async def test_the_bots_own_account_is_the_identity_its_replies_wear(self) -> None:
        cog = _CharacterHarness()
        bot = _member(7, "Pico", bot=True)

        await CommandsMixin.cmd_character_member_set.callback(cog, _ctx(), bot, "px.good")
        await CommandsMixin.cmd_character_member.callback(cog, _ctx(), bot)

        # AgentKey(guild, bot user id) is stored as "user:<bot id>", the key
        # pico's reply author icon and both CCTV pages read.
        self.assertEqual(cog._pixelagents.records[AgentKey(1, 7)].seed, "px.good")
        self.assertEqual(cog.refreshed, [AgentKey(1, 7)])
        self.assertEqual(cog.replies[-1]["fields"][0].value, "px.good")

    async def test_other_bots_are_members_too(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_member_reset.callback(
            cog, _ctx(), _member(55, "OtherBot", bot=True)
        )

        self.assertEqual(cog.refreshed, [AgentKey(1, 55)])
        self.assertIn("dealt from their Discord user id", cog.replies[0]["content"])

    async def test_member_commands_need_the_building_manager_group(self) -> None:
        cog = _CharacterHarness()
        cog.allowed = False
        target = _member(99, "Robin")

        await CommandsMixin.cmd_character_member.callback(cog, _ctx(), target)
        await CommandsMixin.cmd_character_member_set.callback(cog, _ctx(), target, "px.good")
        await CommandsMixin.cmd_character_member_reset.callback(cog, _ctx(), target)

        self.assertEqual(cog.permission_checks, ["building_manager"] * 3)
        self.assertEqual(cog._pixelagents.records, {})
        self.assertEqual(cog.refreshed, [])
        self.assertEqual(cog.replies, [])

    async def test_a_bad_member_seed_is_refused(self) -> None:
        cog = _CharacterHarness()

        await CommandsMixin.cmd_character_member_set.callback(
            cog, _ctx(), _member(99, "Robin"), "px.bad"
        )

        self.assertEqual(cog._pixelagents.records, {})
        self.assertEqual(cog.refreshed, [])
        self.assertIn("not one pixatar knows", cog.replies[0]["content"])

    async def test_reset_goes_back_to_the_dealt_character(self) -> None:
        cog = _CharacterHarness()
        await CommandsMixin.cmd_character_set.callback(cog, _ctx(), "px.good")

        await CommandsMixin.cmd_character_reset.callback(cog, _ctx())

        self.assertEqual(cog._pixelagents.records, {})
        self.assertEqual(cog.refreshed, [AgentKey(1, 42), AgentKey(1, 42)])
        self.assertIn("dealt from your Discord user id", cog.replies[-1]["content"])


if __name__ == "__main__":
    unittest.main()
