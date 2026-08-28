"""Shared fakes for the adapter-layer tests. Module stubbing lives in one
place (../conftest.py) and is not duplicated here -- this module only holds
the fake Discord-facing objects tests construct directly."""

from __future__ import annotations

import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class FakeContext:
    def __init__(self, guild_id: int = 12345) -> None:
        self.guild = FakeGuild(guild_id)
        self.invoked_subcommand: object | None = None
        self.sent: list[str] = []

    async def send(self, content: str = "") -> None:
        self.sent.append(content)

    async def send_help(self) -> None:
        self.sent.append("__help__")


@dataclass(frozen=True)
class FakeLLMSettings:
    """Stands in for `corridor.domain.LLMSettings`."""

    llm_base_url: str = "https://example.test/"
    llm_api_key: str | None = "sk-test"
    llm_model: str | None = "test-model"

    @property
    def ready(self) -> bool:
        return self.llm_api_key is not None and self.llm_model is not None


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`. Tests here verify this cog
    *asks* corridor to reply with the right arguments -- what corridor
    actually renders/decides is covered by corridor's own test suite."""

    def __init__(self) -> None:
        self.replies: list[dict[str, Any]] = []
        self.registered_dependents: set[str] = set()
        self._llm_settings = FakeLLMSettings()
        self.published: list[Any] = []
        self._subscribers: dict[type, list[tuple[str, Any]]] = {}
        self._registered_agents: dict[str, Any] = {}

    async def llm_settings(self) -> FakeLLMSettings:
        return self._llm_settings

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    async def register_agent(self, agent: Any, *, owner: str) -> None:
        """Stands in for corridor.register_agent -- records the agent
        without corridor's real URL-rewriting (that behavior belongs to
        corridor's own test suite, not architect's)."""

        self._registered_agents[agent.agent_key] = (owner, agent)

    def unregister_agent_owner(self, owner: str) -> None:
        for key in [k for k, (o, _) in self._registered_agents.items() if o == owner]:
            del self._registered_agents[key]

    def list_agents(self) -> tuple[Any, ...]:
        return tuple(agent for _, agent in self._registered_agents.values())

    async def publish_event(self, event: object) -> None:
        """Mirrors corridor's real EventBusService.publish -- records every
        published event, then dispatches to any registered subscriber. Kept
        in sync with floorplan/tests/conftest.py's own FakeCorridor."""

        self.published.append(event)
        for _owner, handler in list(self._subscribers.get(type(event), ())):
            await handler(event)

    def subscribe_event(self, event_type: type, handler: Any, *, owner: str) -> None:
        self._subscribers.setdefault(event_type, []).append((owner, handler))

    def unsubscribe_owner(self, owner: str) -> None:
        for handlers in self._subscribers.values():
            handlers[:] = [(o, h) for o, h in handlers if o != owner]

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: object = (),
        **_: object,
    ) -> None:
        self.replies.append(
            {
                "title": title,
                "description": description,
                "content": content,
                "fields": list(fields),  # type: ignore[call-overload]
            }
        )

    def reply_sender(
        self, *, owner: str, avatar_path: Any = None, category: Any = None
    ) -> FakeReplySender:
        """Stands in for corridor.reply_sender -- records reply/author
        identity is a corridor-side concern, covered by corridor's own
        test suite; architect's tests only need the same `self.replies`
        recording `send_reply` already provides."""

        return FakeReplySender(self)


class FakeReplySender:
    def __init__(self, corridor: FakeCorridor) -> None:
        self._corridor = corridor

    async def send_reply(self, ctx: object, **kwargs: object) -> None:
        await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]

    async def render_reply(self, ctx: object, **kwargs: object) -> None:
        await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]

    async def publish_event(self, event: object) -> None:
        await self._corridor.publish_event(event)


class FakePixelAgents:
    """Test double for the cross-cog `bot.get_cog("PixelAgents")` reference.

    Mirrors `pixelagents.adapters.cog_base.WebviewBundleStatus` --
    architect only ever reads this, never triggers a (re)build (see
    `adapters/cog_base.py::_sync_webview_assets`). Copied from
    `floorplan/tests/conftest.py`'s own `FakePixelAgents`."""

    def __init__(
        self,
        *,
        ready: bool = True,
        dist_path: Path | None = None,
        detail: str = "✅ loaded",
        built_commit: str = "a" * 40,
        built_base_path: str = "./",
        furniture_styles: dict[str, Any] | None = None,
    ) -> None:
        self.dist_path = dist_path or Path(tempfile.mkdtemp(prefix="fake-pixelagents-dist-"))
        self.ready = ready
        self.detail = detail
        self.built_commit = built_commit if ready else None
        self.built_base_path = built_base_path if ready else None
        self._furniture_styles = furniture_styles

    def webview_bundle_status(self) -> Any:
        return types.SimpleNamespace(
            dist_path=self.dist_path,
            ready=self.ready,
            detail=self.detail,
            built_commit=self.built_commit,
            built_base_path=self.built_base_path,
        )

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return self._furniture_styles


@dataclass(frozen=True)
class FakeModuleSpec:
    name: str


class FakeCogManager:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot
        self.find_cog_calls: list[str] = []

    async def find_cog(self, name: str) -> FakeModuleSpec | None:
        self.find_cog_calls.append(name)
        installable = {
            "corridor": self.bot.corridor_installable,
            "pixelagents": self.bot.pixelagents_installable,
        }.get(name, True)
        return FakeModuleSpec(name) if installable else None


class FakeBot:
    """`preloaded=True` (the default) simulates corridor and pixelagents
    already being loaded on the bot. Pass `preloaded=False` to simulate
    both having been unloaded, exercising CogBase.cog_load()'s
    auto-load-via-ensure_loaded path instead."""

    def __init__(
        self,
        corridor: FakeCorridor | None = None,
        pixelagents: FakePixelAgents | None = None,
        preloaded: bool = True,
        corridor_installable: bool = True,
        pixelagents_installable: bool = True,
    ) -> None:
        self._pending_corridor = corridor or FakeCorridor()
        self.corridor: FakeCorridor | None = self._pending_corridor if preloaded else None
        self.corridor_installable = corridor_installable
        self._pending_pixelagents = pixelagents or FakePixelAgents()
        self.pixelagents: FakePixelAgents | None = self._pending_pixelagents if preloaded else None
        self.pixelagents_installable = pixelagents_installable
        self._cog_mgr = FakeCogManager(self)
        self.load_extension_calls: list[str] = []
        self.loaded_packages: list[str] = []
        self.add_cog_calls: list[Any] = []
        self.owner_notifications: list[str] = []

    def get_cog(self, name: str) -> Any:
        if name == "Corridor":
            return self.corridor
        if name == "PixelAgents":
            return self.pixelagents
        return None

    async def send_to_owners(self, message: str) -> None:
        self.owner_notifications.append(message)

    async def load_extension(self, spec: FakeModuleSpec) -> None:
        self.load_extension_calls.append(spec.name)
        if spec.name == "corridor":
            self.corridor = self._pending_corridor
        elif spec.name == "pixelagents":
            self.pixelagents = self._pending_pixelagents

    async def add_loaded_package(self, name: str) -> None:
        self.loaded_packages.append(name)

    async def add_cog(self, cog: Any) -> None:
        self.add_cog_calls.append(cog)
        await cog.cog_load()


@dataclass
class FakeEventQueue:
    """A bare capture of enqueued A2A events -- real `TaskStatusUpdateEvent`/
    `Message` protobuf objects, not further faked, since architect's own
    code never inspects the queue itself, only enqueues into it."""

    events: list[Any] = field(default_factory=list)

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)


class FakeRequestContext:
    """The narrow slice of `a2a.server.agent_execution.context.RequestContext`
    `ArchitectAgentExecutor` actually reads."""

    def __init__(
        self, user_input: str, *, task_id: str = "task-1", context_id: str = "ctx-1"
    ) -> None:
        self._user_input = user_input
        self.task_id = task_id
        self.context_id = context_id

    def get_user_input(self, delimiter: str = "\n") -> str:
        return self._user_input
