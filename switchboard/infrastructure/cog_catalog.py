"""Find every loadable cog package on Red's cog paths, and where it came from."""

from __future__ import annotations

import json
import keyword
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from ..domain import CogPackage, CogSource, classify_root

log = logging.getLogger("red.office-cogs.switchboard.catalog")


class CogManager(Protocol):
    """The subset of `redbot.core._cog_manager.CogManager` used here."""

    CORE_PATH: Path

    async def paths(self) -> list[Path]: ...

    async def install_path(self) -> Path: ...

    async def user_defined_paths(self) -> list[Path]: ...


#: module name -> Downloader repo name, for every Downloader-installed cog.
DownloaderIndex = Callable[[], Awaitable[Mapping[str, str]]]


class CogCatalog:
    def __init__(self, cog_manager: CogManager, *, downloader_index: DownloaderIndex) -> None:
        self._cog_manager = cog_manager
        self._downloader_index = downloader_index

    async def scan(self) -> dict[str, list[CogPackage]]:
        """name -> every copy on disk, in Red's own resolution priority.

        The first entry is the one `[p]load <name>` resolves to; the rest are
        shadowed. `SHARED_LIBRARY` packages are skipped (Red's Downloader skips
        them too).
        """

        install_path = (await self._cog_manager.install_path()).resolve()
        user_paths = [path.resolve() for path in await self._cog_manager.user_defined_paths()]
        core_path = self._cog_manager.CORE_PATH.resolve()
        tracked = await self._downloader_index()

        packages: dict[str, list[CogPackage]] = {}
        for root in await self._cog_manager.paths():
            root = root.resolve()
            source = classify_root(
                root, install_path=install_path, user_paths=user_paths, core_path=core_path
            )
            for package in _scan_root(root, source, tracked):
                packages.setdefault(package.name, []).append(package)
        return packages


def _scan_root(root: Path, source: CogSource, tracked: Mapping[str, str]) -> list[CogPackage]:
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []

    found: list[CogPackage] = []
    for directory in children:
        name = directory.name
        if not name.isidentifier() or keyword.iskeyword(name):
            continue
        if not (directory / "__init__.py").is_file():
            continue
        info = _read_info(directory / "info.json")
        if info.get("type") == "SHARED_LIBRARY":
            continue

        package_source = source
        repo_name: str | None = None
        if source is CogSource.DOWNLOADER:
            repo_name = tracked.get(name)
            if repo_name is None:
                package_source = CogSource.DOWNLOADER_UNTRACKED

        found.append(
            CogPackage(
                name=name,
                directory=directory,
                root=root,
                source=package_source,
                requirements=_string_tuple(info.get("requirements")),
                required_cogs=_string_tuple(info.get("required_cogs")),
                hidden=bool(info.get("hidden", False)),
                disabled=bool(info.get("disabled", False)),
                repo_name=repo_name,
            )
        )
    return found


def _read_info(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Ignoring unreadable %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def _string_tuple(value: object) -> tuple[str, ...]:
    # requirements is a list; required_cogs is a {name: repo_url} dict.
    if isinstance(value, dict):
        value = list(value)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)
