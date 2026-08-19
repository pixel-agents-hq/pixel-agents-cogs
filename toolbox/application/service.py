"""Framework-agnostic application logic.

NodeService depends only on the NodeRepository and NodeInstaller protocols
below, never on Red's Config or the real filesystem/network directly --
swap in fakes in tests, or the real Red-backed repository and the
tarball-downloading installer in production.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from ..domain import NodeInstallation, NodeStatus


class NodeRepository(Protocol):
    """The persistence boundary NodeService depends on."""

    async def get_installed(self) -> NodeInstallation | None: ...

    async def set_installed(self, installation: NodeInstallation | None) -> None: ...


class NodeInstaller(Protocol):
    """The host-effecting boundary NodeService depends on.

    Every method here is synchronous and can block (a network download, tar
    extraction) -- NodeService always runs them through `asyncio.to_thread`,
    the same pattern pixelagents' `webview_build.py` uses for its own
    git/npm/vite subprocess calls.
    """

    def resolve_version(self, requested: str | None) -> str: ...

    def install(self, version: str) -> NodeInstallation: ...

    def uninstall(self, installation: NodeInstallation) -> None: ...

    def activate(self, installation: NodeInstallation) -> None: ...

    def deactivate(self, installation: NodeInstallation) -> None: ...


class NodeService:
    def __init__(self, repository: NodeRepository, installer: NodeInstaller) -> None:
        self._repository = repository
        self._installer = installer

    async def status(self) -> NodeStatus:
        installation = await self._repository.get_installed()
        if installation is None:
            return NodeStatus(installed=False)
        return NodeStatus(
            installed=True, version=installation.version, install_dir=installation.install_dir
        )

    async def install(self, requested_version: str | None = None) -> NodeInstallation:
        """Install `requested_version` (or the installer's default), making
        it the active one. If a different version was previously active,
        it is removed only after the new one installs successfully --
        switching never leaves the bot without a working `node`/`npm`."""

        version = await asyncio.to_thread(self._installer.resolve_version, requested_version)
        previous = await self._repository.get_installed()
        if previous is not None and previous.version == version:
            await asyncio.to_thread(self._installer.activate, previous)
            return previous

        installation = await asyncio.to_thread(self._installer.install, version)
        await asyncio.to_thread(self._installer.activate, installation)
        await self._repository.set_installed(installation)

        if previous is not None:
            await asyncio.to_thread(self._installer.deactivate, previous)
            await asyncio.to_thread(self._installer.uninstall, previous)

        return installation

    async def uninstall(self) -> NodeInstallation | None:
        installation = await self._repository.get_installed()
        if installation is None:
            return None
        await asyncio.to_thread(self._installer.deactivate, installation)
        await asyncio.to_thread(self._installer.uninstall, installation)
        await self._repository.set_installed(None)
        return installation

    async def reactivate(self) -> None:
        """Re-apply PATH activation for a previously-installed Node.js.

        PATH mutation is process-local (`os.environ`): the install on disk
        and the persisted record both survive a bot restart, but the PATH
        change does not. Call this from `cog_load` so a previously
        installed Node.js is back on PATH without the owner having to
        re-run `[p]toolbox node install`.
        """

        installation = await self._repository.get_installed()
        if installation is not None:
            await asyncio.to_thread(self._installer.activate, installation)
