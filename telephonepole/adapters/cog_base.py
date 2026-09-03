"""Dependency composition and lifecycle for the Telephonepole Cog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from corridor.domain import RegisteredMcpServer

from ..application import AgentAllowedCheck, TelephonepoleService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import RedTelephonepoleRepository

log = logging.getLogger("red.telephonepole")

# Conventional path for this cog's own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class CorridorMcpRegistrar:
    """The only place in this cog that imports `corridor.domain` or calls
    `corridor.register_mcp_server`/`unregister_mcp_server` -- same
    "adapter is the only corridor-aware layer" split
    `suggestionbox/adapters/cog_base.py`'s `_post_feedback` already
    documents for its own corridor integration. Every server this cog
    registers shares `owner="Telephonepole"`, so `cog_unload` can drop
    them all at once via `unregister_mcp_server_owner`."""

    def __init__(self, corridor: Any) -> None:
        self._corridor = corridor

    async def register(
        self, name: str, base_url: str, agent_allowed: AgentAllowedCheck
    ) -> str | None:
        return await self._corridor.register_mcp_server(
            RegisteredMcpServer(
                owner="Telephonepole", base_url=base_url, agent_allowed=agent_allowed
            ),
            owner="Telephonepole",
        )

    def unregister(self, base_url: str) -> None:
        self._corridor.unregister_mcp_server(base_url)


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedTelephonepoleRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._reply: Any = None
        self._service: TelephonepoleService | None = None

    async def cog_load(self) -> None:
        """`required_cogs` in `info.json` is only a Downloader install
        hint -- Red does not auto-load a dependency at runtime just
        because it's declared there, so `ensure_corridor_loaded()` pulls
        corridor back in if it was unloaded independently. Also
        re-registers every persisted third-party server with corridor,
        since corridor's in-memory `AgentToolServerRegistry` does not
        survive a bot restart even though this cog's own Config does."""

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("telephonepole")
        # Bound once, reused at every reply call site (self._reply.
        # send_reply(...)) instead of repeating this cog's owner name as an
        # argument everywhere -- see docs/reply-identity-design.md.
        self._reply = self._corridor.reply_sender(owner="Telephonepole", avatar_path=AVATAR_PATH)
        self._service = TelephonepoleService(
            self._repository, registrar=CorridorMcpRegistrar(self._corridor)
        )
        errors = await self._service.restore_all()
        if errors:
            await self._notify_owners_restore_failed(errors)

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unregister_mcp_server_owner("Telephonepole")
            self._corridor.unregister_dependent("telephonepole")

    async def _notify_owners_restore_failed(self, errors: dict[str, str]) -> None:
        """Best-effort DM -- must never raise: a missing/unreachable owner
        DM is not a reason to fail this cog's own load."""

        detail = "; ".join(f"{name}: {error}" for name, error in errors.items())
        message = (
            f"⚠️ telephonepole could not re-register {len(errors)} MCP server(s) on load "
            f"({detail}). telephonepole is still loaded and its Discord commands work, but "
            "no registered A2A agent can use those servers' tools until this is fixed -- try "
            "[p]telephonepole add again once the issue is resolved."
        )
        try:
            await self.bot.send_to_owners(message)
        except Exception:
            log.exception("telephonepole: could not notify owners about a failed server restore")

    def list_agents(self) -> tuple[Any, ...]:
        """Every agent currently registered in corridor's
        `AgentDirectoryService` -- the candidate rows for the Components V2
        access panel (`adapters/agent_access_panel.py`). Empty (not an
        error) if corridor has no registered agents, or this cog hasn't
        finished `cog_load` yet."""

        if self._corridor is None:
            return ()
        return tuple(self._corridor.list_agents())


__all__ = ["AVATAR_PATH", "CogBase", "CorridorMcpRegistrar"]
