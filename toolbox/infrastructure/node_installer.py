"""Download-and-extract Node.js prebuilt releases from nodejs.org onto the
bot host.

Node's own tarball/zip already bundles npm (`bin/npm`, `bin/npx` next to
`bin/node`), so installing it is the whole job -- no package manager, no
root/sudo, no second `npm install -g npm` step. Everything lives under a
`toolbox`-owned directory (Red's per-cog data path) rather than a system
location, so this works the same on bare metal, in a container, or on
managed hosting where the bot process has no shell access at all.

Every function that touches the network or filesystem is synchronous and
can block (a download, tar/zip extraction) -- callers must run it through
`asyncio.to_thread`, the same pattern pixelagents' `webview_build.py` uses
for its own git/npm/vite subprocess calls. This module itself has no
Red/discord dependency so it is exercisable directly in tests.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import IO

from ..domain import NodeInstallation

DIST_INDEX_URL = "https://nodejs.org/dist/index.json"
DIST_BASE_URL = "https://nodejs.org/dist"

# `pixel-index`'s package.json (`engines.node: >=22`) and `pixel-agents`'
# own `.nvmrc` both pin Node 22 -- already the version assumed everywhere
# else in this ecosystem.
DEFAULT_MAJOR = "22"

# Used only if resolving the latest DEFAULT_MAJOR release from nodejs.org
# fails (offline host, upstream outage, ...) -- a known-good Node 22 LTS
# release so `[p]toolbox node install` still has a usable default instead
# of a bare error.
FALLBACK_VERSION = "22.18.0"

_Opener = Callable[[str], IO[bytes]]


class NodeInstallError(RuntimeError):
    """Node.js could not be installed/uninstalled on this host."""


def resolve_version(requested: str | None, *, opener: _Opener = urllib.request.urlopen) -> str:
    """`requested` (leading "v" stripped if present), or the latest LTS
    release in `DEFAULT_MAJOR` from nodejs.org's release index."""

    if requested:
        return requested.lstrip("vV")

    try:
        with opener(DIST_INDEX_URL) as response:
            releases = json.loads(response.read())
    except (OSError, ValueError) as exc:
        raise NodeInstallError(
            f"Could not resolve the latest Node.js {DEFAULT_MAJOR}.x LTS release from "
            f"nodejs.org ({exc}). Specify a version explicitly, e.g. "
            f"`[p]toolbox node install {FALLBACK_VERSION}`."
        ) from exc

    for release in releases:
        version = str(release.get("version", ""))
        if version.startswith(f"v{DEFAULT_MAJOR}.") and release.get("lts"):
            return version.lstrip("vV")

    raise NodeInstallError(f"nodejs.org has no LTS release for Node.js {DEFAULT_MAJOR}.x.")


def platform_target() -> tuple[str, str]:
    """(nodejs.org os/arch tag, archive extension) for this host, e.g.
    `("linux-x64", "tar.gz")`."""

    system = platform.system()
    machine = platform.machine().lower()

    arch_map = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine)
    if arch is None:
        raise NodeInstallError(f"Unsupported CPU architecture for Node.js install: {machine}")

    if system == "Linux":
        return f"linux-{arch}", "tar.gz"
    if system == "Darwin":
        return f"darwin-{arch}", "tar.gz"
    if system == "Windows":
        if arch != "x64":
            raise NodeInstallError(
                f"Unsupported Windows architecture for Node.js install: {machine}"
            )
        return "win-x64", "zip"
    raise NodeInstallError(f"Unsupported OS for Node.js install: {system}")


def bin_dir_for(installation: NodeInstallation) -> Path:
    """The directory containing `node`/`npm` for an installation."""

    _target, ext = platform_target()
    install_dir = Path(installation.install_dir)
    return install_dir if ext == "zip" else install_dir / "bin"


def _member_dest(name: str, dest_root: Path) -> Path:
    """Resolve an archive member's path under `dest_root`, rejecting any
    entry (via `..` or an absolute path) that would land outside it --
    nodejs.org's own releases would never do this, but a compromised
    mirror or a corrupted download could."""

    resolved = (dest_root / name).resolve()
    if resolved != dest_root and dest_root not in resolved.parents:
        raise NodeInstallError(f"refusing to extract archive member outside destination: {name}")
    return resolved


def _extract_tar_gz(archive_path: Path, dest_root: Path) -> None:
    with tarfile.open(archive_path) as tar:
        for member in tar.getmembers():
            _member_dest(member.name, dest_root)
        tar.extractall(dest_root)  # membership already validated above


def _extract_zip(archive_path: Path, dest_root: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            _member_dest(name, dest_root)
        archive.extractall(dest_root)


def download_and_install(
    version: str,
    version_dir: Path,
    *,
    logger: logging.Logger,
    opener: _Opener = urllib.request.urlopen,
) -> NodeInstallation:
    """Download and extract Node `version` into `version_dir` (replaced if
    it already exists -- e.g. a previous install of the same version left
    in a bad state)."""

    target, ext = platform_target()
    archive_name = f"node-v{version}-{target}.{ext}"
    url = f"{DIST_BASE_URL}/v{version}/{archive_name}"

    version_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=version_dir.parent) as tmp_name:
        tmp = Path(tmp_name)
        archive_path = tmp / archive_name
        try:
            with opener(url) as response, archive_path.open("wb") as archive_file:
                shutil.copyfileobj(response, archive_file)
        except OSError as exc:
            raise NodeInstallError(
                f"Could not download Node.js {version} from {url}: {exc}"
            ) from exc

        extract_root = tmp / "extracted"
        extract_root.mkdir()
        try:
            if ext == "tar.gz":
                _extract_tar_gz(archive_path, extract_root)
            else:
                _extract_zip(archive_path, extract_root)
        except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
            raise NodeInstallError(f"Could not extract downloaded Node.js archive: {exc}") from exc

        entries = list(extract_root.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise NodeInstallError(f"Unexpected layout in downloaded Node.js archive: {url}")

        if version_dir.exists():
            shutil.rmtree(version_dir)
        shutil.move(str(entries[0]), str(version_dir))

    installation = NodeInstallation(version=version, install_dir=str(version_dir))
    node_bin = bin_dir_for(installation) / ("node.exe" if ext == "zip" else "node")
    if not node_bin.exists():
        raise NodeInstallError(f"Downloaded Node.js archive had no {node_bin.name} binary.")

    logger.info("toolbox: installed Node.js %s to %s", version, version_dir)
    return installation


def remove_install(installation: NodeInstallation, *, logger: logging.Logger) -> None:
    install_dir = Path(installation.install_dir)
    if install_dir.exists():
        shutil.rmtree(install_dir)
    logger.info("toolbox: removed Node.js %s from %s", installation.version, install_dir)


def activate(installation: NodeInstallation) -> None:
    """Prepend this installation's bin dir to the current process's PATH so
    `shutil.which("node")` resolves it immediately. Idempotent."""

    bin_dir = str(bin_dir_for(installation))
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if bin_dir not in parts:
        os.environ["PATH"] = os.pathsep.join([bin_dir, *parts])


def deactivate(installation: NodeInstallation) -> None:
    """Undo `activate`: remove this installation's bin dir from PATH."""

    bin_dir = str(bin_dir_for(installation))
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part != bin_dir]
    os.environ["PATH"] = os.pathsep.join(parts)


class NodeInstaller:
    """The real, host-effecting `application.service.NodeInstaller` --
    bound to one `install_root` directory (versions live at
    `install_root/<version>/`)."""

    def __init__(
        self,
        install_root: Path,
        *,
        logger: logging.Logger | None = None,
        opener: _Opener = urllib.request.urlopen,
    ) -> None:
        self._install_root = install_root
        self._logger = logger or logging.getLogger("red.toolbox")
        self._opener = opener

    def resolve_version(self, requested: str | None) -> str:
        return resolve_version(requested, opener=self._opener)

    def install(self, version: str) -> NodeInstallation:
        version_dir = self._install_root / version
        return download_and_install(version, version_dir, logger=self._logger, opener=self._opener)

    def uninstall(self, installation: NodeInstallation) -> None:
        remove_install(installation, logger=self._logger)

    def activate(self, installation: NodeInstallation) -> None:
        activate(installation)

    def deactivate(self, installation: NodeInstallation) -> None:
        deactivate(installation)
