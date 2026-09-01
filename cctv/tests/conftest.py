"""Shared fakes for cctv's adapter-layer tests. Module stubbing lives in
one place (../conftest.py) and is not duplicated here.

`FakeCorridor`'s pub/sub and reply-render pieces mirror floorplan's/
architect's own now-familiar fakes (kept in sync with corridor's real
`EventBusService`/`ReplyService` contracts, per those modules' own
docstrings). Its new office-state surface is NOT hand-rolled again here
-- it composes the REAL `corridor.application.OfficeStateService` +
`corridor.infrastructure.RedOfficeStateRepository` internally, the same
classes corridor's own real `CogBase` uses, so this fake can't drift out
of sync with the real atomic-watch/locking semantics those tests already
cover in `corridor/tests/test_office_state_service.py`.
"""

from __future__ import annotations

import tempfile
import types
from pathlib import Path
from typing import Any

from corridor.application import OfficeStateService
from corridor.domain import ReplyMode
from corridor.infrastructure import RedOfficeStateRepository
from pixelagents.application.office_state import OfficeStateFacade
from pixelagents.infrastructure.webview_build import bundled_default_layout


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeGuild:
    def __init__(self, guild_id: int, *, members: dict[int, FakeMember] | None = None) -> None:
        self.id = guild_id
        self._members = members or {}

    def get_member(self, user_id: int) -> FakeMember | None:
        return self._members.get(user_id)

    @property
    def members(self) -> list[FakeMember]:
        return list(self._members.values())

    def add_member(self, member: FakeMember) -> None:
        self._members[member.id] = member
        member.guild = self


class FakeMember:
    def __init__(
        self,
        member_id: int,
        *,
        guild: FakeGuild | None = None,
        is_bot: bool = False,
        display_name: str | None = None,
        status: str = "online",
    ) -> None:
        self.id = member_id
        self.guild = guild
        self.bot = is_bot
        self.display_name = display_name or f"user-{member_id}"
        self.status = status
        self.activities: tuple[Any, ...] = ()


class FakeUser:
    def __init__(self, user_id: int = 999, name: str = "cctv-bot") -> None:
        self.id = user_id
        self.name = name


class FakeContext:
    def __init__(self, author: FakeMember | None = None, guild: FakeGuild | None = None) -> None:
        self.guild = guild or FakeGuild(12345)
        self.author = author or FakeMember(1, guild=self.guild)
        self.clean_prefix = ";"
        self.sent: list[dict[str, Any]] = []

    async def send(self, content: str | None = None, **kwargs: object) -> None:
        self.sent.append({"content": content, **kwargs})

    async def send_help(self) -> None:
        self.sent.append({"content": "__help__"})


class _FakeRenderedReply:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`."""

    def __init__(
        self, *, keyholders: frozenset[int] = frozenset(), owners: frozenset[int] = frozenset()
    ) -> None:
        self._keyholders = keyholders
        self._owners = owners
        self.registered_dependents: set[str] = set()
        self.replies: list[dict[str, Any]] = []
        self.published: list[object] = []
        self._subscribers: dict[type, list[tuple[str, Any]]] = {}
        self._registered_agents: dict[str, Any] = {}
        self._office_state_service = OfficeStateService(
            RedOfficeStateRepository.create(cog=object())
        )

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    async def capabilities_satisfy(self, member: Any, group_key: str) -> bool:
        member_id = getattr(member, "id", None)
        if member_id in self._owners:
            return True
        if group_key == "keyholder":
            return member_id in self._keyholders
        return group_key == "employee"

    async def require_permission(self, ctx: Any, group_key: str) -> bool:
        return await self.capabilities_satisfy(ctx.author, group_key)

    async def publish_event(self, event: object) -> None:
        self.published.append(event)
        for _owner, handler in list(self._subscribers.get(type(event), ())):
            await handler(event)

    def subscribe_event(self, event_type: type, handler: Any, *, owner: str) -> None:
        self._subscribers.setdefault(event_type, []).append((owner, handler))

    def unsubscribe_owner(self, owner: str) -> None:
        for handlers in self._subscribers.values():
            handlers[:] = [(o, h) for o, h in handlers if o != owner]

    async def register_agent(self, agent: Any, *, owner: str) -> None:
        self._registered_agents[agent.agent_key] = (owner, agent)

    async def unregister_agent_owner(self, owner: str) -> None:
        for key in [k for k, (o, _) in self._registered_agents.items() if o == owner]:
            del self._registered_agents[key]

    def list_agents(self) -> tuple[Any, ...]:
        return tuple(agent for _, agent in self._registered_agents.values())

    def watch_agents(self, handlers: dict, *, owner: str) -> tuple[Any, ...]:
        for event_type, handler in handlers.items():
            self.subscribe_event(event_type, handler, owner=owner)
        return self.list_agents()

    # --- office state: real corridor application/infrastructure classes ---

    async def read_office_state(self, kind: str) -> Any:
        return await self._office_state_service.read(kind)

    async def set_office_layout(self, kind: str, layout: dict) -> Any:
        return await self._office_state_service.set_layout(kind, layout)

    async def set_office_layout_if_empty(self, kind: str, layout: dict) -> Any:
        return await self._office_state_service.set_layout_if_empty(kind, layout)

    async def mutate_office_seats(self, kind: str, mutation: Any) -> Any:
        return await self._office_state_service.mutate_seats(kind, mutation)

    async def watch_office_state(self, kind: str, handler: Any, *, owner: str) -> Any:
        return await self._office_state_service.watch(kind, handler, owner=owner)

    def unwatch_office_state_owner(self, owner: str) -> None:
        self._office_state_service.unwatch_owner(owner)

    # --- reply rendering: mirrors corridor's real ReplyService.render ---

    async def render_reply(
        self,
        ctx: Any,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Any = (),
        code: Any = (),
        identity: Any = None,
        footer_override: Any = None,
        category: Any = None,
    ) -> _FakeRenderedReply:
        self.replies.append(
            {
                "title": title,
                "description": description,
                "content": content,
                "fields": tuple(fields),
            }
        )
        return _FakeRenderedReply(mode=ReplyMode.EMBED, embed_title=title, fields=tuple(fields))

    async def send_reply(self, ctx: Any, **kwargs: object) -> None:
        rendered = await self.render_reply(ctx, **kwargs)  # type: ignore[arg-type]
        del rendered

    def reply_sender(
        self, *, owner: str, avatar_path: Any = None, category: Any = None
    ) -> FakeReplySender:
        return FakeReplySender(self)


class FakeReplySender:
    def __init__(self, corridor: FakeCorridor) -> None:
        self._corridor = corridor

    async def send_reply(self, ctx: Any, **kwargs: object) -> None:
        await self._corridor.send_reply(ctx, **kwargs)

    async def render_reply(self, ctx: Any, **kwargs: object) -> Any:
        return await self._corridor.render_reply(ctx, **kwargs)  # type: ignore[arg-type]


_AUTO = object()


class FakePixelAgents:
    """Test double for the cross-cog `bot.get_cog("PixelAgents")` reference.
    `office_state()` returns a REAL `OfficeStateFacade` wired to the given
    `FakeCorridor` as its backend -- the facade's own logic (lazy seeding,
    wire-schema validation) is exercised for real, not re-faked.

    `default_layout` defaults to actually reading `dist_path`'s own
    `assets/asset-index.json`/default-layout file via the real
    `bundled_default_layout` (same function `PixelAgentsBase.cog_load`
    wires in production) -- pass an explicit dict (or `None`) to override
    without needing a real bundle on disk."""

    def __init__(
        self,
        *,
        corridor: FakeCorridor,
        ready: bool = True,
        dist_path: Path | None = None,
        detail: str = "✅ loaded",
        built_commit: str = "a" * 40,
        built_base_path: str = "./",
        default_layout: Any = _AUTO,
    ) -> None:
        self.dist_path = dist_path or Path(tempfile.mkdtemp(prefix="fake-cctv-dist-"))
        self.ready = ready
        self.detail = detail
        self.built_commit = built_commit if ready else None
        self.built_base_path = built_base_path if ready else None
        if default_layout is _AUTO:
            self._facade = OfficeStateFacade(
                corridor, default_layout=lambda: bundled_default_layout(self.dist_path)
            )
        else:
            self._facade = OfficeStateFacade(corridor, default_layout=lambda: default_layout)

    def webview_bundle_status(self) -> Any:
        return types.SimpleNamespace(
            dist_path=self.dist_path,
            ready=self.ready,
            detail=self.detail,
            built_commit=self.built_commit,
            built_base_path=self.built_base_path,
        )

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return None

    def office_state(self) -> OfficeStateFacade:
        return self._facade


class FakeModuleSpec:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeCogManager:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot

    async def find_cog(self, name: str) -> FakeModuleSpec | None:
        return FakeModuleSpec(name) if self.bot.installable else None


class FakeBot:
    def __init__(
        self,
        *,
        corridor: FakeCorridor | None = None,
        pixelagents: FakePixelAgents | None = None,
        guilds: list[FakeGuild] | None = None,
        owner_ids: frozenset[int] = frozenset(),
        installable: bool = True,
    ) -> None:
        self._corridor = corridor or FakeCorridor()
        self._pixelagents = pixelagents
        self.guilds = guilds or []
        self.owner_ids = owner_ids
        self.installable = installable
        self._cog_mgr = FakeCogManager(self)
        self.user = FakeUser()
        self.owner_notifications: list[str] = []
        self._cogs: dict[str, Any] = {"Corridor": self._corridor}
        if pixelagents is not None:
            self._cogs["PixelAgents"] = pixelagents

    def get_cog(self, name: str) -> Any:
        return self._cogs.get(name)

    async def is_owner(self, user: Any) -> bool:
        return getattr(user, "id", None) in self.owner_ids

    async def wait_until_red_ready(self) -> None:
        return None

    async def send_to_owners(self, message: str) -> None:
        self.owner_notifications.append(message)

    async def load_extension(self, spec: FakeModuleSpec) -> None:
        if spec.name == "pixelagents" and self._pixelagents is not None:
            self._cogs["PixelAgents"] = self._pixelagents

    async def add_loaded_package(self, name: str) -> None:
        pass

    async def add_cog(self, cog: Any) -> None:
        await cog.cog_load()


__all__ = [
    "FakeBot",
    "FakeCogManager",
    "FakeContext",
    "FakeCorridor",
    "FakeGuild",
    "FakeMember",
    "FakeModuleSpec",
    "FakePixelAgents",
    "FakeReplySender",
    "FakeRole",
    "FakeUser",
]
