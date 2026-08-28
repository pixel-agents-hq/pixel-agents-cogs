"""Shared fakes for the adapter-layer tests. Module stubbing lives in one
place (../conftest.py) and is not duplicated here -- this module only holds
the fake Discord-facing objects tests construct directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.sent: list[dict[str, Any]] = []

    async def send(self, content: str | None = None, **kwargs: object) -> None:
        self.sent.append({"content": content, **kwargs})


class FakeContext:
    def __init__(self, guild_id: int = 12345, author_id: int = 999) -> None:
        self.guild = FakeGuild(guild_id)
        self.author = FakeUser(author_id)
        self.sent: list[str] = []

    async def send(self, content: str = "", **kwargs: object) -> None:
        self.sent.append(content)

    async def send_help(self) -> None:
        self.sent.append("__help__")


@dataclass(frozen=True)
class FakeRegisteredAgent:
    agent_key: str


class FakeReplySender:
    def __init__(self, corridor: FakeCorridor) -> None:
        self._corridor = corridor

    async def send_reply(self, ctx: object, **kwargs: object) -> None:
        await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]

    async def send_channel_reply(self, channel: object, guild_id: int, **kwargs: object) -> None:
        await self._corridor.send_channel_reply(channel, guild_id, **kwargs)  # type: ignore[arg-type]


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`. Tests here verify this cog
    *asks* corridor to reply/register with the right arguments -- what
    corridor actually renders/decides/routes is covered by corridor's own
    test suite, not duplicated here."""

    def __init__(self, agent_keys: tuple[str, ...] = ()) -> None:
        self.replies: list[dict[str, Any]] = []
        self.channel_replies: list[dict[str, Any]] = []
        self.registered_dependents: set[str] = set()
        self.registered_mcp_servers: list[tuple[object, str]] = []
        self.unregistered_mcp_server_urls: list[str] = []
        self.mcp_registration_error: str | None = None
        self._agents = [FakeRegisteredAgent(agent_key=key) for key in agent_keys]

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    async def register_mcp_server(self, server: object, *, owner: str) -> str | None:
        self.registered_mcp_servers.append((server, owner))
        return self.mcp_registration_error

    def unregister_mcp_server(self, base_url: str) -> None:
        self.unregistered_mcp_server_urls.append(base_url)

    def list_agents(self) -> tuple[FakeRegisteredAgent, ...]:
        return tuple(self._agents)

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
    ) -> None:
        self.replies.append({"title": title, "description": description, "content": content})

    async def send_channel_reply(
        self,
        channel: object,
        guild_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        fields: object = (),
    ) -> None:
        self.channel_replies.append(
            {
                "channel": channel,
                "guild_id": guild_id,
                "title": title,
                "description": description,
                "fields": fields,
            }
        )
        await channel.send(embed=None)  # type: ignore[attr-defined]

    def reply_sender(
        self, *, owner: str, avatar_path: Any = None, category: Any = None
    ) -> FakeReplySender:
        """Stands in for corridor.reply_sender -- author identity/category is
        a corridor-side concern, covered by corridor's own test suite; this
        cog's tests only need the same `self.replies`/`self.channel_replies`
        recording already provides."""

        return FakeReplySender(self)


@dataclass(frozen=True)
class FakeModuleSpec:
    name: str


class FakeCogManager:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot
        self.find_cog_calls: list[str] = []

    async def find_cog(self, name: str) -> FakeModuleSpec | None:
        self.find_cog_calls.append(name)
        return FakeModuleSpec(name) if self.bot.corridor_installable else None


class FakeBot:
    """`corridor=None` simulates corridor already being loaded on the bot
    (the common case). Pass `preloaded=False` to simulate it having been
    unloaded, exercising CogBase.cog_load()'s auto-load-via-ensure_loaded
    path instead."""

    def __init__(
        self,
        corridor: FakeCorridor | None = None,
        preloaded: bool = True,
        corridor_installable: bool = True,
    ) -> None:
        self._pending_corridor = corridor or FakeCorridor()
        self.corridor: FakeCorridor | None = self._pending_corridor if preloaded else None
        self.corridor_installable = corridor_installable
        self._cog_mgr = FakeCogManager(self)
        self.load_extension_calls: list[str] = []
        self.loaded_packages: list[str] = []
        self.add_cog_calls: list[Any] = []
        self.channels: dict[int, FakeChannel] = {}
        self.owner_messages: list[str] = []

    def get_cog(self, name: str) -> Any:
        if name == "Corridor":
            return self.corridor
        if name == "Suggestionbox":
            return getattr(self, "suggestionbox_cog", None)
        return None

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(channel_id)

    async def send_to_owners(self, message: str) -> None:
        self.owner_messages.append(message)

    async def load_extension(self, spec: FakeModuleSpec) -> None:
        self.load_extension_calls.append(spec.name)
        if spec.name == "corridor":
            self.corridor = self._pending_corridor

    async def add_loaded_package(self, name: str) -> None:
        self.loaded_packages.append(name)

    async def add_cog(self, cog: Any) -> None:
        self.add_cog_calls.append(cog)
        self.suggestionbox_cog = cog
        await cog.cog_load()
