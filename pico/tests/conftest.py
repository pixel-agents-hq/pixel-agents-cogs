"""Shared fakes for the adapter-layer tests. Module stubbing lives in one
place (../conftest.py) and is not duplicated here -- this module only holds
the fake Discord-facing objects tests construct directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class FakeMember:
    def __init__(self, member_id: int = 999, *, bot: bool = False) -> None:
        self.id = member_id
        self.bot = bot


class FakeMessage:
    def __init__(self) -> None:
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeContext:
    def __init__(self, guild_id: int | None = 12345, author_id: int = 999) -> None:
        self.guild = FakeGuild(guild_id) if guild_id is not None else None
        self.author = FakeMember(author_id)
        self.message = FakeMessage()
        self.invoked_subcommand: object | None = None
        self.sent: list[str] = []

    async def send(self, content: str = "") -> None:
        self.sent.append(content)

    async def send_help(self) -> None:
        self.sent.append("__help__")


@dataclass(frozen=True)
class FakeLLMSettings:
    """Stands in for `corridor.domain.LLMSettings` -- pico only reads this
    through `self._corridor.llm_settings()`, never constructs the real
    corridor type directly."""

    llm_base_url: str = "https://example.test/"
    llm_api_key: str | None = "sk-test"
    llm_model: str | None = "test-model"

    @property
    def ready(self) -> bool:
        return self.llm_api_key is not None and self.llm_model is not None


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`. Tests here verify this cog
    *asks* corridor to reply with the right arguments -- what corridor
    actually renders/decides is covered by corridor's own test suite, not
    duplicated here."""

    def __init__(self) -> None:
        self.replies: list[dict[str, Any]] = []
        self.registered_dependents: set[str] = set()
        self._next_message_id = 1
        self.tools_for_member: list[Any] = []
        self.list_tools_for_calls: list[Any] = []
        self.agents: list[Any] = []
        self._llm_settings = FakeLLMSettings()
        self.allow_capabilities = True
        self.capabilities_checks: list[tuple[Any, str]] = []

    async def llm_settings(self) -> FakeLLMSettings:
        return self._llm_settings

    async def capabilities_satisfy(self, member: Any, group_key: str) -> bool:
        """Stands in for corridor.capabilities_satisfy -- used by
        `_agent_tools` to gate a `RegisteredAgent.required_permission_group`.
        See docs/bootcamp-design.md."""

        self.capabilities_checks.append((member, group_key))
        return self.allow_capabilities

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    async def list_tools_for(self, ctx: object) -> list[Any]:
        self.list_tools_for_calls.append(ctx)
        return self.tools_for_member

    def list_agents(self) -> list[Any]:
        """Stands in for corridor.AgentDirectoryService.list_agents --
        see docs/agent-directory-design.md."""

        return self.agents

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: object = (),
        footer_override: object = None,
    ) -> FakeSentMessage:
        self.replies.append(
            {
                "title": title,
                "description": description,
                "content": content,
                "fields": list(fields),  # type: ignore[call-overload]
                "footer_override": footer_override,
            }
        )
        message = FakeSentMessage(self._next_message_id)
        self._next_message_id += 1
        return message

    def reply_sender(
        self, *, owner: str, avatar_path: Any = None, category: Any = None
    ) -> FakeReplySender:
        """Stands in for corridor.reply_sender -- author identity is a
        corridor-side concern, covered by corridor's own test suite;
        pico's tests only need the same `self.replies` recording
        `send_reply` already provides."""

        return FakeReplySender(self)


class FakeReplySender:
    def __init__(self, corridor: FakeCorridor) -> None:
        self._corridor = corridor

    async def send_reply(self, ctx: object, **kwargs: object) -> FakeSentMessage:
        return await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]

    async def render_reply(self, ctx: object, **kwargs: object) -> FakeSentMessage:
        return await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]

    async def publish_event(self, event: object) -> None:
        pass


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


class FakeUser:
    def __init__(self, user_id: int = 1) -> None:
        self.id = user_id


class FakeBot:
    """`preloaded=True` (the default) simulates corridor already being
    loaded on the bot. Pass `preloaded=False` to simulate it having been
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
        self.user = FakeUser()

    def get_cog(self, name: str) -> Any:
        if name == "Corridor":
            return self.corridor
        return None

    async def load_extension(self, spec: FakeModuleSpec) -> None:
        self.load_extension_calls.append(spec.name)
        if spec.name == "corridor":
            self.corridor = self._pending_corridor

    async def add_loaded_package(self, name: str) -> None:
        self.loaded_packages.append(name)

    async def add_cog(self, cog: Any) -> None:
        self.add_cog_calls.append(cog)
        await cog.cog_load()
