"""Agents' Discord replies wear their office characters: real corridor and
pixelagents, `cog_load()`-ed together, with the real pixatar CLI pixelagents
builds.

pixelagents registers its avatar resolver with corridor at `cog_load`; a
`reply_sender(agent=...)` reply then attaches the agent's pixatar avatar as
its author icon. No Discord gateway: the reply goes to a recording context.

Gated like the rest of e2e (`PIXELAGENTS_REAL_WEBVIEW_BUILD=1`); also needs
pixatar to build (git, Node 22+), and skips if it didn't.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from corridor.domain import AgentRef

from .fixtures import FakeBot, construct_core_cogs, real_webview_build_enabled

_AGENT = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="e2e-avatar-agent")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class _Context:
    def __init__(self, guild: Any) -> None:
        self.guild = guild
        self.clean_prefix = "!"
        self.interaction = None
        self.sent: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return SimpleNamespace(id=1)


@unittest.skipUnless(
    real_webview_build_enabled(),
    "needs a real network clone+build of the vendored webview and pixatar; "
    "set PIXELAGENTS_REAL_WEBVIEW_BUILD=1 to run (see e2e/README.md)",
)
class TestAgentRepliesWearTheirCharacter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        # The guild footer's default icon is the bot's own avatar.
        self.bot.user.display_avatar = SimpleNamespace(url="https://e2e.invalid/bot.png")  # type: ignore[attr-defined]
        cogs = await construct_core_cogs(
            self.bot, add_cleanup=self.addCleanup, add_async_cleanup=self.addAsyncCleanup
        )
        self.corridor = cogs.corridor
        self.pixelagents = cogs.pixelagents
        if self.pixelagents._pixatar is None:  # noqa: SLF001
            self.skipTest(f"pixatar did not build: {self.pixelagents.pixatar_status()}")

    async def test_the_reply_author_icon_is_the_agents_pixatar_avatar(self) -> None:
        avatar = await self.corridor.avatar_for(_AGENT)
        assert avatar is not None, "corridor resolved no avatar for a genuine agent"
        self.assertTrue(avatar.read_bytes().startswith(_PNG_SIGNATURE))

        sender = self.corridor.reply_sender(owner="e2e-avatar-agent", agent=_AGENT)
        ctx = _Context(SimpleNamespace(id=4242))
        with mock.patch("discord.Embed") as embed:
            await sender.send_reply(ctx, description="hello")

        embed.return_value.set_author.assert_called_once_with(
            name="e2e-avatar-agent", icon_url=f"attachment://{avatar.name}"
        )
        self.assertEqual([f.filename for f in ctx.sent[0]["files"]], [avatar.name])


if __name__ == "__main__":
    unittest.main()
