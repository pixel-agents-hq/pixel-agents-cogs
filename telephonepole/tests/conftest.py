"""Shared fakes for the adapter-layer tests. Module stubbing lives in one
place (../conftest.py) and is not duplicated here -- this module only holds
the fake Discord-facing objects tests construct directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeContext:
    def __init__(self, author_id: int = 999) -> None:
        self.author = FakeUser(author_id)
        self.sent: list[dict[str, Any]] = []

    async def send(self, content: str = "", view: Any = None) -> None:
        self.sent.append({"content": content, "view": view})

    async def send_help(self) -> None:
        self.sent.append({"content": "__help__", "view": None})


class FakeAgent:
    def __init__(self, agent_key: str) -> None:
        self.agent_key = agent_key


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`. Tests here verify this cog
    *asks* corridor to register/unregister/reply with the right arguments --
    what corridor actually does with a registered server is covered by
    corridor's own test suite, not duplicated here."""

    def __init__(
        self,
        register_error: str | None = None,
        agent_keys: tuple[str, ...] = (),
    ) -> None:
        self.replies: list[dict[str, Any]] = []
        self.registered_dependents: set[str] = set()
        # base_url -> (owner, agent_allowed)
        self.registered_servers: dict[str, tuple[str, Any]] = {}
        self.unregistered_urls: list[str] = []
        self.unregistered_owners: list[str] = []
        self.register_error = register_error
        self._agents = tuple(FakeAgent(key) for key in agent_keys)

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
    ) -> None:
        self.replies.append({"title": title, "description": description, "content": content})

    def reply_sender(
        self, *, owner: str, avatar_path: Any = None, category: Any = None
    ) -> FakeReplySender:
        return FakeReplySender(self)

    async def register_mcp_server(self, server: Any, *, owner: str) -> str | None:
        if self.register_error is not None:
            return self.register_error
        self.registered_servers[server.base_url] = (owner, server.agent_allowed)
        return None

    def unregister_mcp_server(self, base_url: str) -> None:
        self.unregistered_urls.append(base_url)
        self.registered_servers.pop(base_url, None)

    def unregister_mcp_server_owner(self, owner: str) -> None:
        self.unregistered_owners.append(owner)
        for url in [u for u, (o, _) in self.registered_servers.items() if o == owner]:
            del self.registered_servers[url]

    def list_agents(self) -> tuple[FakeAgent, ...]:
        return self._agents


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
        self.owner_dms: list[str] = []

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
        self.owner_dms.append(message)
