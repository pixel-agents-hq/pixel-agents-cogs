"""Pure business models. Zero framework imports -- this module never imports
discord.py or redbot, so it is trivially unit-testable without either
installed."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class CogSource(StrEnum):
    """Where Red resolves a cog package from.

    Red searches its cog paths in a fixed priority order (Downloader's
    install path, then `--cog-path` CLI paths, then `[p]addpath` paths, then
    Red's bundled core cogs), so the directory a package sits in tells us
    exactly how it got there.
    """

    # Copied into Red's install path by `[p]cog install`, and recorded in
    # Downloader's own bookkeeping.
    DOWNLOADER = "downloader"
    # Sits in Red's install path, but Downloader has no record of it (a hand
    # copy, or an install made outside the Downloader cog).
    DOWNLOADER_UNTRACKED = "downloader_untracked"
    # A directory registered with `[p]addpath`.
    ADDPATH = "addpath"
    # A non-persistent `--cog-path` given on Red's command line.
    CLI_PATH = "cli_path"
    # One of Red's bundled cogs.
    CORE = "core"


#: Sources whose packages Downloader never manages -- the ones switchboard
#: exists for, and the ones the `all` keyword expands to.
LOCAL_SOURCES = frozenset({CogSource.ADDPATH, CogSource.CLI_PATH})


@dataclass(frozen=True, slots=True)
class CogPackage:
    """One loadable package directory found on one of Red's cog paths."""

    name: str
    directory: Path
    root: Path
    source: CogSource
    requirements: tuple[str, ...] = ()
    required_cogs: tuple[str, ...] = ()
    hidden: bool = False
    disabled: bool = False
    # Downloader repo name, only for CogSource.DOWNLOADER.
    repo_name: str | None = None


@dataclass(frozen=True, slots=True)
class CogStatus:
    """What switchboard knows about one cog name right now."""

    name: str
    # The copy Red loads (or has loaded); None when no cog path has it.
    package: CogPackage | None
    loaded: bool
    missing_requirements: tuple[str, ...] = ()
    # Lower-priority copies of the same name that Red will never pick while
    # `package` exists -- e.g. an addpath copy hidden by a Downloader install.
    shadowed: tuple[CogPackage, ...] = ()


class LoadOutcome(StrEnum):
    LOADED = "loaded"
    ALREADY_LOADED = "already_loaded"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    # Not attempted because a cog it requires did not load.
    SKIPPED = "skipped"


class UnloadOutcome(StrEnum):
    UNLOADED = "unloaded"
    NOT_LOADED = "not_loaded"
    # Not attempted (switchboard never unloads itself).
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class LoadResult:
    name: str
    outcome: LoadOutcome
    detail: str = ""


@dataclass(frozen=True, slots=True)
class UnloadResult:
    name: str
    outcome: UnloadOutcome
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InstallResult:
    """One pip run (or the decision not to run one)."""

    ok: bool
    requirements: tuple[str, ...] = ()
    output: str = ""
    returncode: int = 0
    # False when nothing needed installing and pip was never invoked.
    ran: bool = True


@dataclass(frozen=True, slots=True)
class InstallReport:
    names: tuple[str, ...]
    not_found: tuple[str, ...]
    install: InstallResult
    # Requirements still unsatisfied after the install, per cog.
    still_missing: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # required_cogs entries no cog path provides, per dependent.
    unknown_dependencies: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadReport:
    order: tuple[str, ...]
    results: tuple[LoadResult, ...]
    not_found: tuple[str, ...] = ()
    install: InstallResult | None = None
    still_missing: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unknown_dependencies: dict[str, tuple[str, ...]] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(
            result.outcome in (LoadOutcome.LOADED, LoadOutcome.ALREADY_LOADED)
            for result in self.results
        )


@dataclass(frozen=True, slots=True)
class UnloadReport:
    order: tuple[str, ...]
    results: tuple[UnloadResult, ...]
    not_found: tuple[str, ...] = ()
    error: str = ""


def classify_root(
    root: Path,
    *,
    install_path: Path,
    user_paths: Iterable[Path],
    core_path: Path,
) -> CogSource:
    """Classify one of Red's cog search paths.

    Downloader tracking is a separate question (it needs Downloader's own
    bookkeeping), so anything in `install_path` comes back as
    `CogSource.DOWNLOADER` here; the catalog downgrades untracked packages.
    """

    if root == install_path:
        return CogSource.DOWNLOADER
    if root == core_path:
        return CogSource.CORE
    if root in set(user_paths):
        return CogSource.ADDPATH
    return CogSource.CLI_PATH
