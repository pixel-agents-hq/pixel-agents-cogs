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
from unittest.mock import AsyncMock

from corridor.domain import RegisteredTool

from ..adapters.listener import ListenerMixin, _agent_tools, _cross_cog_tools, _message_text
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
    required_permission_group: str | None = None,
) -> SimpleNamespace:
    card = SimpleNamespace(
        description=f"{agent_key} agent.",
        supported_interfaces=[SimpleNamespace(url=url)],
        icon_url=icon_url,
    )
    return SimpleNamespace(
        agent_key=agent_key,
        card=card,
        required_permission_group=required_permission_group,
    )


class TestAgentTools(unittest.IsolatedAsyncioTestCase):
    async def test_returns_one_consult_tool_per_registered_agent(self) -> None:
        corridor = FakeCorridor()
        corridor.agents = [_agent("architect"), _agent("agent-n")]

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual({tool.name for tool in tools}, {"consult_architect", "consult_agent-n"})
        self.assertTrue(all(isinstance(tool, ConsultAgentTool) for tool in tools))

    async def test_the_consult_footer_wears_the_agents_office_character(self) -> None:
        corridor = FakeCorridor()
        corridor.agents = [
            _agent("architect"),
            _agent("painter"),
        ]
        corridor.avatars = {"architect": Path("/pixelagents/avatars/0123.png")}

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        footers = {tool.name: tool._footer_icon_path for tool in tools}  # type: ignore[attr-defined]
        self.assertEqual(
            footers,
            {
                "consult_architect": Path("/pixelagents/avatars/0123.png"),
                # No character avatar (pixatar unavailable): no footer icon.
                "consult_painter": None,
            },
        )

    async def test_zero_registered_agents_yields_zero_tools(self) -> None:
        corridor = FakeCorridor()

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual(tools, [])

    async def test_a_malformed_agent_is_skipped_without_dropping_others(self) -> None:
        corridor = FakeCorridor()
        broken = SimpleNamespace(
            agent_key="broken", card=None, required_permission_group=None
        )  # .description access will raise
        corridor.agents = [broken, _agent("architect")]

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual([tool.name for tool in tools], ["consult_architect"])

    async def test_an_agent_with_no_required_group_is_never_permission_checked(self) -> None:
        corridor = FakeCorridor()
        corridor.agents = [_agent("architect")]

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual([tool.name for tool in tools], ["consult_architect"])
        self.assertEqual(corridor.capabilities_checks, [])

    async def test_an_agent_is_offered_when_the_member_satisfies_its_required_group(self) -> None:
        corridor = FakeCorridor()
        corridor.agents = [_agent("recruiter", required_permission_group="keyholder")]
        corridor.allow_capabilities = True
        ctx = _ctx()

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            ctx,
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual([tool.name for tool in tools], ["consult_recruiter"])
        self.assertEqual(corridor.capabilities_checks, [(ctx.author, "keyholder")])

    async def test_an_agent_is_omitted_when_the_member_does_not_satisfy_its_required_group(
        self,
    ) -> None:
        corridor = FakeCorridor()
        corridor.agents = [_agent("recruiter", required_permission_group="keyholder")]
        corridor.allow_capabilities = False

        tools = await _agent_tools(
            corridor,
            corridor.reply_sender(owner="Pico"),
            ArchitectClient(),
            _ctx(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual(tools, [])

    async def test_a_permission_check_failure_omits_that_agent_without_dropping_others(
        self,
    ) -> None:
        corridor = FakeCorridor()
        corridor.agents = [
            _agent("recruiter", required_permission_group="keyholder"),
            _agent("architect"),
        ]

        async def _raising_check(member: object, group_key: str) -> bool:
            raise RuntimeError("guild settings unavailable")

        corridor.capabilities_satisfy = _raising_check  # type: ignore[method-assign]

        tools = await _agent_tools(
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


class _Listener(ListenerMixin):
    """Just enough of the Pico cog to reach on_message's early exits."""

    def __init__(
        self,
        *,
        disabled: bool = False,
        enabled: bool = False,
        allowed: bool = True,
        not_ignored: bool = True,
    ) -> None:
        self.bot = SimpleNamespace(
            cog_disabled_in_guild=AsyncMock(return_value=disabled),
            allowed_by_whitelist_blacklist=AsyncMock(return_value=allowed),
            ignored_channel_or_guild=AsyncMock(return_value=not_ignored),
        )
        # Pico switched off in the guild (the default): on_message stops
        # right after. Switched on, the LLM settings read is the next step.
        self._repository = SimpleNamespace(guild_enabled=AsyncMock(return_value=enabled))
        self._corridor = SimpleNamespace(
            llm_settings=AsyncMock(return_value=SimpleNamespace(ready=False))
        )


def _guild_message(*, bot: bool = False, guild: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(bot=bot), guild=SimpleNamespace(id=10) if guild else None
    )


class TestOnMessageEarlyExits(unittest.IsolatedAsyncioTestCase):
    async def test_bot_authors_and_dms_stop_before_any_lookup(self) -> None:
        for message in (_guild_message(bot=True), _guild_message(guild=False)):
            listener = _Listener()
            await listener.on_message(message)  # type: ignore[arg-type]
            listener.bot.cog_disabled_in_guild.assert_not_awaited()
            listener._repository.guild_enabled.assert_not_awaited()

    async def test_disabled_in_guild_stops_before_config(self) -> None:
        listener = _Listener(disabled=True)
        message = _guild_message()
        await listener.on_message(message)  # type: ignore[arg-type]
        listener.bot.cog_disabled_in_guild.assert_awaited_once_with(listener, message.guild)
        listener._repository.guild_enabled.assert_not_awaited()

    async def test_enabled_cog_goes_on_to_the_guild_setting(self) -> None:
        listener = _Listener()
        await listener.on_message(_guild_message())  # type: ignore[arg-type]
        listener._repository.guild_enabled.assert_awaited_once_with(10)

    async def test_blocklisted_members_and_ignored_channels_never_start_a_turn(self) -> None:
        for listener in (_Listener(enabled=True, allowed=False), _Listener(enabled=True, not_ignored=False)):
            message = _guild_message()
            await listener.on_message(message)  # type: ignore[arg-type]
            listener._corridor.llm_settings.assert_not_awaited()

    async def test_an_allowed_member_goes_on_to_the_llm_settings(self) -> None:
        listener = _Listener(enabled=True)
        message = _guild_message()
        await listener.on_message(message)  # type: ignore[arg-type]
        listener.bot.allowed_by_whitelist_blacklist.assert_awaited_once_with(message.author)
        listener.bot.ignored_channel_or_guild.assert_awaited_once_with(message)
        listener._corridor.llm_settings.assert_awaited_once_with()
