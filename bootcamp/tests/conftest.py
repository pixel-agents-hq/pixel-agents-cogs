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
    def __init__(self, member_id: int = 1) -> None:
        self.id = member_id


class FakeContext:
    def __init__(self, guild_id: int = 12345, author_id: int = 1) -> None:
        self.guild = FakeGuild(guild_id)
        self.author = FakeMember(author_id)
        self.sent: list[dict[str, Any]] = []

    async def send(self, content: str = "", *, view: Any = None) -> None:
        self.sent.append({"content": content, "view": view})

    async def send_help(self) -> None:
        self.sent.append({"content": "__help__", "view": None})


class FakeLLMSettings:
    def __init__(self, *, ready: bool = True) -> None:
        self.llm_base_url = "http://llm.example/"
        self.llm_api_key = "key" if ready else None
        self.llm_model = "gpt-fake" if ready else None
        self.ready = ready


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`. Tests here verify this cog
    *asks* corridor to register/reply/check permissions with the right
    arguments -- what corridor actually does with an agent registration or
    a permission check is covered by corridor's own test suite, not
    duplicated here."""

    def __init__(self, allow_permission: bool = True, llm_ready: bool = True) -> None:
        self.allow_permission = allow_permission
        self.replies: list[dict[str, Any]] = []
        self.permission_checks: list[object] = []
        self.registered_dependents: set[str] = set()
        self.registered_agents: dict[str, Any] = {}
        self.unregistered_agent_owners: list[str] = []
        self.unregistered_agents: list[str] = []
        self._llm_settings = FakeLLMSettings(ready=llm_ready)
        self.agent_tools: dict[str, tuple[Any, ...]] = {}
        self.published_events: list[object] = []

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    async def register_agent(self, agent: Any, *, owner: str) -> None:
        existing_owner = self.registered_agents.get(agent.agent_key)
        if existing_owner is not None and existing_owner[0] != owner:
            raise ValueError(
                f"agent {agent.agent_key!r} is already registered by {existing_owner[0]!r}"
            )
        self.registered_agents[agent.agent_key] = (owner, agent)

    async def unregister_agent(self, agent_key: str) -> None:
        self.unregistered_agents.append(agent_key)
        self.registered_agents.pop(agent_key, None)

    async def unregister_agent_owner(self, owner: str) -> None:
        self.unregistered_agent_owners.append(owner)
        for key in [k for k, (o, _) in self.registered_agents.items() if o == owner]:
            del self.registered_agents[key]

    def list_agents(self) -> tuple[Any, ...]:
        return tuple(agent for _, agent in self.registered_agents.values())

    async def llm_settings(self) -> FakeLLMSettings:
        return self._llm_settings

    def llm_client(self) -> Any:
        raise NotImplementedError("tests exercise the tool loop through fakes, not a real LLM")

    async def list_agent_tools_for(self, agent_key: str) -> tuple[Any, ...]:
        return self.agent_tools.get(agent_key, ())

    async def publish_event(self, event: object) -> None:
        self.published_events.append(event)

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
    ) -> None:
        self.replies.append({"title": title, "description": description, "content": content})

    async def require_permission(self, ctx: object, group: object) -> bool:
        self.permission_checks.append(group)
        return self.allow_permission

    def reply_sender(
        self, *, owner: str, avatar_path: Any = None, category: Any = None
    ) -> FakeReplySender:
        """Stands in for corridor.reply_sender -- author identity/category is
        a corridor-side concern, covered by corridor's own test suite; this
        cog's tests only need the same `self.replies` recording `send_reply`
        already provides."""

        return FakeReplySender(self)


class FakeReplySender:
    def __init__(self, corridor: FakeCorridor) -> None:
        self._corridor = corridor

    async def send_reply(self, ctx: object, **kwargs: object) -> None:
        await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]


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
        self.owner_messages: list[str] = []

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

    async def send_to_owners(self, message: str) -> None:
        self.owner_messages.append(message)
