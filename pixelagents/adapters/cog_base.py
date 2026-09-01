"""Dependency composition for the bundle and validated office-state facade."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from redbot.core import commands, data_manager
from redbot.core.bot import Red

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind, SeatRecords

from ..application.office_state import OfficeStateFacade, OfficeStateSeatRepository
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure.furniture_styles import FurnitureStyleLoader
from ..infrastructure.settings import RedSettingsRepository
from ..infrastructure.webview_build import (
    BuildOutcome,
    build_webview,
    built_base_path,
    built_commit,
    load_bundled_default_layout,
    owner_notification_for,
)

log = logging.getLogger("red.d_cogs.pixelagents")
MutationResult = TypeVar("MutationResult")


@dataclass(frozen=True)
class WebviewBundleStatus:
    """Read-only cross-cog view of the built webview bundle.

    `built_commit` lets a consumer tell a rebuild-to-a-different-commit
    apart from "nothing changed" without re-deriving that from `detail`'s
    free text. `built_base_path` lets a consumer confirm the on-disk build
    actually uses the current, consumer-agnostic build convention
    (`infrastructure.webview_build.RELATIVE_BASE_PATH`) rather than a stale
    build from before it existed -- see that module's docstring.
    """

    dist_path: Path
    ready: bool
    detail: str
    built_commit: str | None
    built_base_path: str | None


class PixelAgentsBase:
    """Own the bundle lifecycle and compose Corridor's schema-aware facade."""

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
        self._style_loader = FurnitureStyleLoader(self)
        self._office_state: OfficeStateFacade | None = None

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
        outcome = await asyncio.to_thread(
            build_webview, self._cog_data_dir, logger=log, force=force, commit=commit
        )
        self._webview_build_outcome = outcome
        return outcome.status_line

    def webview_bundle_status(self) -> WebviewBundleStatus:
        """Public, read-only cross-cog surface consumed by CCTV and agents.

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

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        """Public, read-only cross-cog surface: the generated
        `{"styles": [...]}` manifest (`infrastructure/furniture_style_builder.py`,
        docs/architect-semantic-ir-design.md section 6.4), or `None` if the
        webview hasn't been built yet. Mirrors `webview_bundle_status()`'s
        shape -- same "read whatever's on disk, no rebuild trigger here"
        contract.
        """

        path = self._webview_dist_path() / "assets" / "furniture-styles.json"
        try:
            return cast("dict[str, Any]", json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            return None

    def bundled_default_layout(self) -> dict[str, Any] | None:
        """Load the default selected by the generated asset index."""

        try:
            return cast("dict[str, Any]", load_bundled_default_layout(self._webview_dist_path()))
        except (OSError, TypeError, ValueError) as exc:
            log.warning("pixelagents: bundled default layout unavailable: %s", exc)
            return None

    def _states(self) -> OfficeStateFacade:
        if self._office_state is None:
            raise RuntimeError("pixelagents office-state facade is not loaded")
        return self._office_state

    async def office_state(self, kind: OfficeStateKind) -> OfficeState:
        return await self._states().state(kind)

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: Mapping[str, object]
    ) -> OfficeState:
        return await self._states().set_layout(kind, layout)

    async def set_office_seats(self, kind: OfficeStateKind, seats: object) -> OfficeState:
        return await self._states().set_seats(kind, seats)

    async def mutate_office_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        return await self._states().mutate_seats(kind, mutation)

    async def merge_office_seat_patch(
        self,
        kind: OfficeStateKind,
        agent_id: str,
        patch: Mapping[str, object],
        *,
        palette_count: int,
    ) -> OfficeState:
        return await self._states().merge_seat_patch(
            kind, agent_id, patch, palette_count=palette_count
        )

    async def watch_office_state(
        self,
        kind: OfficeStateKind,
        handler: Callable[[OfficeStateChanged], Awaitable[None]],
        *,
        owner: str,
    ) -> OfficeState:
        return await self._states().watch(kind, handler, owner=owner)

    def office_seat_repository(self, kind: OfficeStateKind) -> OfficeStateSeatRepository:
        return OfficeStateSeatRepository(self._states(), kind)

    async def _notify_owners_webview_build_failed(self) -> None:
        outcome = self._webview_build_outcome
        if outcome is None or outcome.ok:
            return
        try:
            message = await self._corridor.substitute_default_prefix(
                owner_notification_for(outcome)
            )
            await self.bot.send_to_owners(message)
        except Exception:  # best-effort notification only, must never raise
            log.exception("pixelagents: could not notify owners about the webview build failure")

    async def cog_load(self) -> None:
        self._corridor = await ensure_corridor_loaded(self.bot)
        self._corridor.register_dependent("pixelagents")
        log.info("pixelagents: %s", await self._rebuild_webview(force=False))
        self._office_state = OfficeStateFacade(
            self._corridor,
            self.bundled_default_layout,
            self._style_loader.styles,
        )
        if self._webview_build_outcome is not None and not self._webview_build_outcome.ok:
            await self._notify_owners_webview_build_failed()

    async def cog_unload(self) -> None:
        self._office_state = None
        if self._corridor is not None:
            self._corridor.unregister_dependent("pixelagents")

    # Cross-adapter hook resolved by the composed Cog's MRO.
    async def _reply(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError
