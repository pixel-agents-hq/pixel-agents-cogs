"""Dependency composition and lifecycle for the Pixel Agents Cog.

pixelagents owns exactly one thing now: vendoring and building the Pixel
Agents webview (see Architecture.md). floorplan consumes the result through
`webview_bundle_status()` below instead of resolving its own
`cog_data_path` or running its own build.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redbot.core import commands, data_manager
from redbot.core.bot import Red

from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure.settings import RedSettingsRepository
from ..infrastructure.webview_build import (
    BuildOutcome,
    build_webview,
    built_base_path,
    built_commit,
    owner_notification_for,
)

log = logging.getLogger("red.d_cogs.pixelagents")


@dataclass(frozen=True)
class WebviewBundleStatus:
    """Read-only cross-cog view of the built webview bundle.

    `built_commit` lets a consumer tell a rebuild-to-a-different-commit
    apart from "nothing changed" without re-deriving that from `detail`'s
    free text. `built_base_path` lets a consumer confirm the on-disk build
    actually matches the route it's serving assets at -- the exact mismatch
    (bundle built for `/third-party/pixelagents/...`, served from
    `/third-party/floorplan/...`) that first exposed the need for this
    field: a stale build whose pinned commit hadn't changed, so it was never
    invalidated by commit alone.
    """

    dist_path: Path
    ready: bool
    detail: str
    built_commit: str | None
    built_base_path: str | None


class PixelAgentsBase:
    """Wire the one remaining repository and own the build's lifecycle."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._corridor: Any = None
        self._settings_repository = RedSettingsRepository.create(self)
        self.config = self._settings_repository.config
        # Not the installed pixelagents/ tree: Downloader never copies a
        # frontend build for us (docs/red-downloader-submodules.md), so the
        # webview is cloned and built into Red's per-cog data directory --
        # writable, and persists across cog updates/reloads -- the first
        # time cog_load runs. See infrastructure/webview_build.py.
        self._cog_data_dir: Path = data_manager.cog_data_path(self)
        self._webview_build_outcome: BuildOutcome | None = None

    def _webview_dist_path(self) -> Path:
        return self._cog_data_dir / "webview_dist"

    async def _rebuild_webview(self, *, force: bool) -> str:
        """Build the webview off the event loop; return a status line.

        The formatting and never-raises guarantee live in
        `infrastructure.webview_build.build_webview` -- this just runs it on
        a worker thread and records the outcome for `webview_bundle_status`
        and the owner-DM path.
        """

        commit = await self._settings_repository.webview_commit_override()
        base_path = await self._settings_repository.webview_base_path()
        outcome = await asyncio.to_thread(
            build_webview,
            self._cog_data_dir,
            logger=log,
            force=force,
            commit=commit,
            base_path=base_path,
        )
        self._webview_build_outcome = outcome
        return outcome.status_line

    def webview_bundle_status(self) -> WebviewBundleStatus:
        """Public, read-only cross-cog surface consumed by floorplan.

        No rebuild trigger here -- rebuilding stays
        `[p]pixelagents webview rebuild`-only.
        """

        dist_path = self._webview_dist_path()
        ready = (dist_path / "index.html").is_file()
        outcome = self._webview_build_outcome
        if ready:
            detail = "✅ loaded"
        elif outcome is not None and outcome.missing_tools:
            detail = f"⚠️ missing tool(s): {', '.join(outcome.missing_tools)}"
        elif outcome is not None and not outcome.ok:
            detail = "⚠️ build failed — see `[p]pixelagents webview rebuild`"
        else:
            detail = "⚠️ missing"
        return WebviewBundleStatus(
            dist_path=dist_path,
            ready=ready,
            detail=detail,
            built_commit=built_commit(dist_path) if ready else None,
            built_base_path=built_base_path(dist_path) if ready else None,
        )

    async def _notify_owners_webview_build_failed(self) -> None:
        outcome = self._webview_build_outcome
        if outcome is None or outcome.ok:
            return
        try:
            await self.bot.send_to_owners(owner_notification_for(outcome))
        except Exception:  # best-effort notification only, must never raise
            log.exception("pixelagents: could not notify owners about the webview build failure")

    async def cog_load(self) -> None:
        self._corridor = await ensure_corridor_loaded(self.bot)
        self._corridor.register_dependent("pixelagents")
        log.info("pixelagents: %s", await self._rebuild_webview(force=False))
        if self._webview_build_outcome is not None and not self._webview_build_outcome.ok:
            await self._notify_owners_webview_build_failed()

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unregister_dependent("pixelagents")

    # Cross-adapter hook resolved by the composed Cog's MRO.
    async def _reply(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError
