"""Shared in-memory fakes for switchboard's tests.

Switchboard's tests run against the real `red-discordbot` package (it is the
one cog here that cannot share corridor's sys.modules stubs, since it must
not depend on corridor), so nothing here stubs redbot/discord -- these fakes
only stand in for the service's own Protocol boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..domain import (
    CogPackage,
    CogSource,
    InstalledDistribution,
    InstallResult,
    LoadOutcome,
    LoadResult,
    UnloadOutcome,
    UnloadResult,
)

ROOT = Path("/cogs/office-cogs")


def package(
    name: str,
    *,
    requires: Sequence[str] = (),
    requirements: Sequence[str] = (),
    source: CogSource = CogSource.ADDPATH,
    root: Path = ROOT,
    disabled: bool = False,
) -> CogPackage:
    return CogPackage(
        name=name,
        directory=root / name,
        root=root,
        source=source,
        requirements=tuple(requirements),
        required_cogs=tuple(requires),
        disabled=disabled,
    )


class FakeCatalog:
    def __init__(self, *packages: CogPackage) -> None:
        self.packages: dict[str, list[CogPackage]] = {}
        for item in packages:
            self.packages.setdefault(item.name, []).append(item)

    async def scan(self) -> dict[str, list[CogPackage]]:
        return {name: list(copies) for name, copies in self.packages.items()}


class FakeDistributions:
    """A mutable distribution index; FakeInstaller adds to it."""

    def __init__(self, **versions: str) -> None:
        self.dists = {name: InstalledDistribution(version) for name, version in versions.items()}

    def lookup(self, name: str) -> InstalledDistribution | None:
        return self.dists.get(name)


class FakeInstaller:
    def __init__(self, dists: FakeDistributions, *, ok: bool = True) -> None:
        self.dists = dists
        self.ok = ok
        self.calls: list[tuple[str, ...]] = []

    async def install(self, requirements: Sequence[str]) -> InstallResult:
        requirements = tuple(requirements)
        if not requirements:
            return InstallResult(ok=True, ran=False)
        self.calls.append(requirements)
        if self.ok:
            for raw in requirements:
                name = raw.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()
                self.dists.dists[name] = InstalledDistribution("99.0")
        return InstallResult(ok=self.ok, requirements=requirements, returncode=0 if self.ok else 1)


class FakeExtensions:
    def __init__(
        self,
        *,
        loaded: Sequence[str] = (),
        failing: dict[str, str] | None = None,
        side_effects: dict[str, Sequence[str]] | None = None,
        cascades: dict[str, Sequence[str]] | None = None,
    ) -> None:
        self._loaded = set(loaded)
        self.failing = failing or {}
        # Loading a key also loads these (a dependent's setup() pulling in corridor).
        self.side_effects = side_effects or {}
        # Unloading a key also unloads these (corridor's cascade).
        self.cascades = cascades or {}
        self.load_calls: list[str] = []
        self.unload_calls: list[str] = []
        self.origins: dict[str, Path] = {}

    def loaded(self) -> set[str]:
        return set(self._loaded)

    def origin(self, name: str) -> Path | None:
        return self.origins.get(name) if name in self._loaded else None

    async def load(self, name: str) -> LoadResult:
        self.load_calls.append(name)
        if name in self.failing:
            return LoadResult(name, LoadOutcome.FAILED, self.failing[name])
        self._loaded.add(name)
        self._loaded.update(self.side_effects.get(name, ()))
        return LoadResult(name, LoadOutcome.LOADED)

    async def unload(self, name: str) -> UnloadResult:
        self.unload_calls.append(name)
        if name not in self._loaded:
            return UnloadResult(name, UnloadOutcome.NOT_LOADED)
        self._loaded.discard(name)
        for other in self.cascades.get(name, ()):
            self._loaded.discard(other)
        return UnloadResult(name, UnloadOutcome.UNLOADED)


class FakeSettings:
    def __init__(self, managed: Sequence[str] = (), autorepair: bool = True) -> None:
        self._managed = list(managed)
        self._autorepair = autorepair

    async def managed(self) -> list[str]:
        return list(self._managed)

    async def set_managed(self, names: list[str]) -> None:
        self._managed = sorted(set(names))

    async def autorepair(self) -> bool:
        return self._autorepair

    async def set_autorepair(self, enabled: bool) -> None:
        self._autorepair = enabled


class FakeContext:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.help_sent = 0

    async def send(self, content: str = "") -> None:
        self.sent.append(content)

    async def send_help(self) -> None:
        self.help_sent += 1

    def typing(self) -> FakeTyping:
        return FakeTyping()


class FakeTyping:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None
