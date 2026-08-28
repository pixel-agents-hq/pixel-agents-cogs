"""Shared fakes for the adapter-layer tests. Module stubbing lives in one
place (../conftest.py) and is not duplicated here -- this module only holds
the fake Discord-facing objects tests construct directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class FakeContext:
    def __init__(self, guild_id: int | None = 12345) -> None:
        self.guild = FakeGuild(guild_id) if guild_id is not None else None
        self.sent: list[str] = []

    async def send(self, content: str = "") -> None:
        self.sent.append(content)

    async def send_help(self) -> None:
        self.sent.append("__help__")


class FakeCorridor:
    """Stands in for `bot.get_cog("Corridor")`. Tests here verify this cog
    *asks* corridor to reply with the right arguments -- what corridor
    actually renders is covered by corridor's own test suite, not
    duplicated here."""

    def __init__(self) -> None:
        self.replies: list[dict[str, Any]] = []
        self.registered_dependents: set[str] = set()
        self._tools: dict[str, tuple[str, Any]] = {}
        self.visibility_filters: dict[str, Any] = {}

    def register_dependent(self, extension_name: str) -> None:
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self.registered_dependents.discard(extension_name)

    def register_tool(self, tool: Any, *, owner: str) -> None:
        """Mirrors corridor's real ToolRegistryService.register just
        enough for toolbox's own tests: idempotent re-registration under
        the same owner, ValueError on a cross-owner name collision (the
        one behavior toolbox's resync path reacts to)."""

        existing = self._tools.get(tool.name)
        if existing is not None and existing[0] != owner:
            raise ValueError(
                f"tool {tool.name!r} is already registered by {existing[0]!r}, "
                f"cannot re-register it for {owner!r}"
            )
        self._tools[tool.name] = (owner, tool)

    def unregister_tool(self, name: str) -> None:
        self._tools.pop(name, None)

    def list_tools(self) -> tuple[Any, ...]:
        return tuple(tool for _, tool in self._tools.values())

    def register_tool_visibility_filter(self, predicate: Any, *, owner: str) -> None:
        self.visibility_filters[owner] = predicate

    def unregister_visibility_filter_owner(self, owner: str) -> None:
        self.visibility_filters.pop(owner, None)

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
        """Stands in for corridor.reply_sender -- author identity is a
        corridor-side concern, covered by corridor's own test suite; this
        cog's tests only need the same `self.replies` recording
        `send_reply` already provides."""

        return FakeReplySender(self)


class FakeReplySender:
    def __init__(self, corridor: FakeCorridor) -> None:
        self._corridor = corridor

    async def send_reply(self, ctx: object, **kwargs: object) -> None:
        await self._corridor.send_reply(ctx, **kwargs)  # type: ignore[arg-type]

    async def render_reply(self, ctx: object, **kwargs: object) -> None:
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
        cogs: dict[str, Any] | None = None,
    ) -> None:
        self._pending_corridor = corridor or FakeCorridor()
        self.corridor: FakeCorridor | None = self._pending_corridor if preloaded else None
        self.corridor_installable = corridor_installable
        self._cog_mgr = FakeCogManager(self)
        self.load_extension_calls: list[str] = []
        self.loaded_packages: list[str] = []
        self.add_cog_calls: list[Any] = []
        # Mirrors discord.py's own Bot.cogs -- already-loaded cogs at the
        # moment toolbox's own cog_load runs, which its startup catch-up
        # loop iterates (real on_cog_add never fires retroactively for
        # these). Empty by default so existing tests, which never populate
        # it, see no behavior change.
        self.cogs: dict[str, Any] = cogs or {}
        # Mirrors discord.py's own Bot.walk_commands() -- a flat stand-in
        # since nothing here needs the real recursive subcommand walk, just
        # something toolbox's tool_panel._refresh() can iterate over.
        self.walk_commands_result: list[Any] = []

    def walk_commands(self) -> Any:
        yield from self.walk_commands_result

    def get_cog(self, name: str) -> Any:
        if name == "Corridor":
            return self.corridor
        if name == "Toolbox":
            return self.cogs.get("Toolbox")
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
