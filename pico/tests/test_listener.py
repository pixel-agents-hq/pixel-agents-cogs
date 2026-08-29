"""`_message_text`: plain content plus a text rendering of embeds, so
embed-only messages (corridor's own EMBED reply mode, or other bots'
embeds) don't show up as empty history entries.

Uses bare `SimpleNamespace` stand-ins for `discord.Message`/`discord.Embed`
rather than the real types -- `_message_text` only reads `.content` and
`.embeds` (and each embed's `.title`/`.description`/`.fields`), and the
project-wide test stub (`corridor.testing.install_stubs`) replaces
`discord.Embed` with a bare `MagicMock` class anyway, whose instances don't
carry constructor kwargs as attributes."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from corridor.domain import RegisteredTool

from ..adapters.listener import _agent_tools, _cross_cog_tools, _message_text
from ..infrastructure.architect_client import ArchitectClient
from ..tools.consult_agent_tool import ConsultAgentTool
from ..tools.cross_cog import CrossCogTool
from .conftest import FakeCorridor


def _field(name: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, value=value)


def _embed(
    title: str | None = None,
    description: str | None = None,
    fields: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(title=title, description=description, fields=fields or [])


def _message(content: str = "", embeds: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, embeds=embeds or [])


class TestMessageText(unittest.TestCase):
    def test_plain_content_passes_through(self) -> None:
        self.assertEqual(_message_text(_message("hello there")), "hello there")

    def test_embed_only_message_is_not_empty(self) -> None:
        embed = _embed(title="Title", description="Description")
        message = _message(content="", embeds=[embed])

        self.assertEqual(_message_text(message), "Title\nDescription")

    def test_embed_fields_are_rendered(self) -> None:
        embed = _embed(title="Status", fields=[_field("Enabled", "true")])
        message = _message(embeds=[embed])

        self.assertEqual(_message_text(message), "Status\n**Enabled:** true")

    def test_content_and_embed_are_both_included(self) -> None:
        embed = _embed(description="extra detail")
        message = _message(content="here's a reply", embeds=[embed])

        self.assertEqual(_message_text(message), "here's a reply\nextra detail")

    def test_no_content_and_no_embeds_is_empty(self) -> None:
        self.assertEqual(_message_text(_message()), "")

    def test_embed_with_no_title_or_description_falls_back_to_fields_only(self) -> None:
        embed = _embed(fields=[_field("Key", "Value")])
        message = _message(embeds=[embed])

        self.assertEqual(_message_text(message), "**Key:** Value")

    def test_multiple_embeds_are_all_included(self) -> None:
        message = _message(embeds=[_embed(title="First"), _embed(title="Second")])

        self.assertEqual(_message_text(message), "First\nSecond")


async def _handler(ctx: object, raw_input: dict) -> dict:
    return {}


def _registered_tool(name: str = "a") -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="A tool.",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
    )


def _ctx(author: object = None) -> SimpleNamespace:
    return SimpleNamespace(author=author if author is not None else SimpleNamespace())


def _agent(
    agent_key: str,
    *,
    url: str = "http://localhost:8931/architect/",
    icon_url: str = "",
    avatar_path: Path | None = None,
) -> SimpleNamespace:
    card = SimpleNamespace(
        description=f"{agent_key} agent.",
        supported_interfaces=[SimpleNamespace(url=url)],
        icon_url=icon_url,
    )
    return SimpleNamespace(agent_key=agent_key, card=card, avatar_path=avatar_path)


class TestAgentTools(unittest.TestCase):
    def test_returns_one_consult_tool_per_registered_agent(self) -> None:
        corridor = FakeCorridor()
        corridor.agents = [_agent("architect"), _agent("agent-n")]

        tools = _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual({tool.name for tool in tools}, {"consult_architect", "consult_agent-n"})
        self.assertTrue(all(isinstance(tool, ConsultAgentTool) for tool in tools))

    def test_zero_registered_agents_yields_zero_tools(self) -> None:
        corridor = FakeCorridor()

        tools = _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual(tools, [])

    def test_a_malformed_agent_is_skipped_without_dropping_others(self) -> None:
        corridor = FakeCorridor()
        broken = SimpleNamespace(agent_key="broken", card=None)  # .description access will raise
        corridor.agents = [broken, _agent("architect")]

        tools = _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual([tool.name for tool in tools], ["consult_architect"])


class TestCrossCogTools(unittest.IsolatedAsyncioTestCase):
    async def test_returns_one_adapted_tool_per_registered_entry(self) -> None:
        corridor = FakeCorridor()
        corridor.tools_for_member = [_registered_tool("a"), _registered_tool("b")]

        tools = await _cross_cog_tools(corridor, _ctx(), guild_id=1, bot_user_id=999)

        self.assertEqual({tool.name for tool in tools}, {"a", "b"})
        self.assertTrue(all(isinstance(tool, CrossCogTool) for tool in tools))

    async def test_filters_through_corridors_permission_check(self) -> None:
        corridor = FakeCorridor()
        member = SimpleNamespace(id=1)
        ctx = _ctx(member)

        await _cross_cog_tools(corridor, ctx, guild_id=1, bot_user_id=999)

        self.assertEqual(corridor.list_tools_for_calls, [ctx])

    async def test_a_tool_that_fails_to_adapt_is_skipped_without_dropping_others(self) -> None:
        corridor = FakeCorridor()
        broken = _registered_tool("broken")
        object.__setattr__(broken, "parameters", None)  # malformed: not mapping-shaped
        corridor.tools_for_member = [broken, _registered_tool("healthy")]

        tools = await _cross_cog_tools(corridor, _ctx(), guild_id=1, bot_user_id=999)

        self.assertEqual([tool.name for tool in tools], ["healthy"])
