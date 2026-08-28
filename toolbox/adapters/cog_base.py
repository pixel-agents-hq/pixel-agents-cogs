"""Dependency composition and lifecycle for the Toolbox Cog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from redbot.core import commands, data_manager
from redbot.core.bot import Red

from corridor.domain import RegisteredTool, ReplyCategory

from ..application import NodeService, ToolSelectionService, ToolVisibilityService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import (
    NodeInstaller,
    RedNodeRepository,
    RedToolSelectionRepository,
    RedToolVisibilityRepository,
)
from .tool_wrapping import collect_wrappable_tools

log = logging.getLogger("red.toolbox")

# Conventional path for toolbox's own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedNodeRepository.create(self)
        self.config = self._repository.config
        # Not the installed toolbox/ tree: Downloader manages that and never
        # writes anything into it, so installed runtimes live in Red's
        # per-cog data directory instead -- writable, and persists across
        # cog updates/reloads.
        install_root = data_manager.cog_data_path(self) / "node"
        self._installer = NodeInstaller(install_root=install_root)
        self._service = NodeService(self._repository, self._installer)
        self._tool_selection_repository = RedToolSelectionRepository.create(self)
        self._tool_selection_service = ToolSelectionService(self._tool_selection_repository)
        self._tool_visibility_repository = RedToolVisibilityRepository.create(self)
        self._tool_visibility_service = ToolVisibilityService(self._tool_visibility_repository)
        self._corridor: Any = None
        self._reply: Any = None

    async def cog_load(self) -> None:
        """Extension point for start-up work (background tasks, sessions, ...).

        required_cogs in info.json is only a Downloader install hint -- Red
        does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded() pulls corridor back in
        if it was unloaded independently.
        """

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("toolbox")
        self._reply = self._corridor.reply_sender(
            owner="Toolbox", avatar_path=AVATAR_PATH, category=ReplyCategory.FURNITURE
        )
        self._corridor.register_tool_visibility_filter(self._is_tool_visible, owner="Toolbox")
        await self._service.reactivate()
        # Every cog already loaded by the time toolbox itself finishes
        # loading never fires its own on_cog_add (Red dispatches that event
        # only for cogs added *after* a listener exists to hear it) -- catch
        # up on all of them once here.
        await self._resync_all_cogs()

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_visibility_filter_owner("Toolbox")
            self._corridor.unregister_dependent("toolbox")

    async def _is_tool_visible(self, ctx: commands.Context, tool: RegisteredTool) -> bool:
        """The predicate installed as corridor's one visibility filter --
        see docs/toolbox-command-tool-toggle-design.md. Applies to every
        registered tool, not just ones toolbox itself wrapped: a
        `@llm_tool`-decorated command the owner wants hidden goes through
        this exact same check."""

        guild_id = ctx.guild.id if ctx.guild is not None else None
        return await self._tool_visibility_service.is_enabled(tool.name, guild_id)

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog) -> None:
        """Red-specific event (redbot/core/bot.py, dispatched from
        `Red.add_cog` after every `bot.add_cog`), not stock discord.py --
        see docs/toolbox-command-tool-toggle-design.md. Re-wraps every
        selected-but-undecorated command the newly (re)loaded cog provides.
        Cleanup on the opposite direction needs no handler here at all:
        corridor's own defensive `on_cog_remove` already unregisters every
        tool registered under a removed cog's `qualified_name`, and toolbox
        registers wrapped tools under exactly that owner (see
        `_resync_tool_registrations` below), not under `"Toolbox"`."""

        if cog is self:
            return
        await self._resync_tool_registrations(cog)

    async def _resync_all_cogs(self) -> None:
        for cog in list(self.bot.cogs.values()):
            if cog is not self:
                await self._resync_tool_registrations(cog)

    async def _resync_tool_registrations(self, cog: commands.Cog) -> None:
        if self._corridor is None:
            return
        selected = await self._tool_selection_service.list_selected()
        if not selected:
            return
        for tool in collect_wrappable_tools(cog, selected):
            try:
                self._corridor.register_tool(tool, owner=cog.qualified_name)
            except ValueError:
                log.warning(
                    "toolbox: %r is already registered as an LLM tool by another cog; "
                    "skipping the wrapped version of %r",
                    tool.name,
                    getattr(cog, "qualified_name", cog),
                )

    async def select_tool(self, qualified_name: str) -> None:
        """Opt `qualified_name` into tool-wrapping and make it usable
        immediately -- re-syncs every loaded cog rather than tracking which
        one owns this particular command, reusing `_resync_all_cogs` (cheap:
        `collect_wrappable_tools` is a plain in-memory scan).

        Raises `ValueError` -- without selecting anything -- if this
        command's tool name is already registered by something else, so
        the panel can surface that as a UI error instead of a silent no-op
        (`_resync_tool_registrations`'s own collision handling stays
        tolerant, for the bulk on_cog_add path; this is the single,
        explicit user action, so it fails loudly instead)."""

        tool_name = "_".join(qualified_name.split())
        already_selected = qualified_name in await self._tool_selection_service.list_selected()
        if self._corridor is not None and not already_selected:
            existing_names = {tool.name for tool in self._corridor.list_tools()}
            if tool_name in existing_names:
                raise ValueError(
                    f"Cannot select {qualified_name!r}: a tool named {tool_name!r} is already "
                    "registered."
                )
        await self._tool_selection_service.select(qualified_name)
        await self._resync_all_cogs()

    async def deselect_tool(self, qualified_name: str) -> None:
        """Opt `qualified_name` back out. Unlike selection, this can't wait
        for the next resync: the tool must disappear now, and
        `unregister_owner` would be the wrong shape here anyway (it would
        also drop any of the same cog's other selected tools) -- see
        `CogBase.unregister_tool` / `docs/toolbox-command-tool-toggle-design.md`."""

        await self._tool_selection_service.deselect(qualified_name)
        if self._corridor is not None:
            tool_name = "_".join(qualified_name.split())
            self._corridor.unregister_tool(tool_name)
