"""Per-send reply avatars: `reply_sender(agent=...)` asks the registered
avatar resolver (pixelagents: the agent's office character) on every send."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..corridor import Corridor
from ..domain import AgentRef
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember

ARCHITECT = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="architect")


class TestReplyAvatars(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.static = self.tmp / "avatar.png"
        self.static.write_bytes(b"static")
        self.character = self.tmp / "0123456789abcdef.png"
        self.character.write_bytes(b"character")
        self.asked: list[AgentRef] = []

    def _ctx(self) -> FakeContext:
        return FakeContext(author=FakeMember(2, self.guild), guild=self.guild)

    async def _resolve(self, agent: AgentRef) -> Path | None:
        self.asked.append(agent)
        return self.character

    async def test_the_agents_character_is_the_author_icon(self) -> None:
        self.corridor.register_avatar_resolver(self._resolve, owner="pixelagents")
        sender = self.corridor.reply_sender(
            owner="Architect", avatar_path=self.static, agent=ARCHITECT
        )
        ctx = self._ctx()

        await sender.send_reply(ctx, title="Hi")

        self.assertEqual(self.asked, [ARCHITECT])
        ctx.sent[0]["embed"].set_author.assert_called_once_with(
            name="Architect", icon_url="attachment://0123456789abcdef.png"
        )
        self.assertEqual(ctx.sent[0]["files"][0].filename, "0123456789abcdef.png")

    async def test_without_a_resolver_the_static_avatar_stays(self) -> None:
        sender = self.corridor.reply_sender(
            owner="Architect", avatar_path=self.static, agent=ARCHITECT
        )
        ctx = self._ctx()

        await sender.send_reply(ctx, title="Hi")

        ctx.sent[0]["embed"].set_author.assert_called_once_with(
            name="Architect", icon_url="attachment://avatar.png"
        )

    async def test_a_failing_resolver_never_fails_the_reply(self) -> None:
        async def broken(agent: AgentRef) -> Path | None:
            raise RuntimeError("pixatar exploded")

        self.corridor.register_avatar_resolver(broken, owner="pixelagents")
        sender = self.corridor.reply_sender(
            owner="Architect", avatar_path=self.static, agent=ARCHITECT
        )
        ctx = self._ctx()

        with self.assertLogs("red.corridor", level="ERROR"):
            await sender.send_reply(ctx, title="Hi")

        ctx.sent[0]["embed"].set_author.assert_called_once_with(
            name="Architect", icon_url="attachment://avatar.png"
        )

    async def test_a_lazy_agent_is_resolved_at_send_time(self) -> None:
        self.corridor.register_avatar_resolver(self._resolve, owner="pixelagents")
        bot_account: list[AgentRef] = []
        sender = self.corridor.reply_sender(
            owner="Pico", avatar_path=self.static, agent=lambda: next(iter(bot_account), None)
        )

        await sender.send_reply(self._ctx(), title="before login")
        bot_account.append(AgentRef(discord_user_id=99, guild_id=None, is_bot=True))
        await sender.send_reply(self._ctx(), title="after login")

        self.assertEqual([a.discord_user_id for a in self.asked], [99])

    async def test_unregistering_only_drops_the_owners_resolver(self) -> None:
        self.corridor.register_avatar_resolver(self._resolve, owner="pixelagents")

        self.corridor.unregister_avatar_resolver("someone-else")
        self.assertEqual(await self.corridor.avatar_for(ARCHITECT), self.character)

        self.corridor.unregister_avatar_resolver("pixelagents")
        self.assertIsNone(await self.corridor.avatar_for(ARCHITECT))


if __name__ == "__main__":
    unittest.main()
