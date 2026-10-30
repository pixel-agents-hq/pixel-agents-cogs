"""Framework-agnostic application logic.

SwitchboardService depends only on the Protocols below, never on Red
directly -- the adapters wire in the real Red-backed implementations, tests
wire in plain in-memory fakes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Protocol

from ..domain import (
    LOCAL_SOURCES,
    CogPackage,
    CogSource,
    CogStatus,
    DependencyCycleError,
    DistributionLookup,
    InstallReport,
    InstallResult,
    LoadOutcome,
    LoadReport,
    LoadResult,
    UnloadOutcome,
    UnloadReport,
    UnloadResult,
    closure,
    load_order,
    unload_order,
    unsatisfied,
)

log = logging.getLogger("red.office-cogs.switchboard")

#: Expands to every non-disabled cog on an addpath/--cog-path directory.
ALL_KEYWORD = "all"


class Catalog(Protocol):
    async def scan(self) -> dict[str, list[CogPackage]]: ...


class Installer(Protocol):
    async def install(self, requirements: Sequence[str]) -> InstallResult: ...


class Extensions(Protocol):
    """The bot's loaded-extension state, and Red's own load/unload logic."""

    def loaded(self) -> set[str]: ...

    def origin(self, name: str) -> Path | None:
        """Package directory of a loaded extension, if it has one."""
        ...

    async def load(self, name: str) -> LoadResult: ...

    async def unload(self, name: str) -> UnloadResult: ...


class SwitchboardSettings(Protocol):
    async def managed(self) -> list[str]: ...

    async def set_managed(self, names: list[str]) -> None: ...

    async def autorepair(self) -> bool: ...

    async def set_autorepair(self, enabled: bool) -> None: ...


class SwitchboardService:
    def __init__(
        self,
        *,
        catalog: Catalog,
        installer: Installer,
        lookup_factory: Callable[[], DistributionLookup],
        extensions: Extensions,
        settings: SwitchboardSettings,
        self_name: str = "switchboard",
    ) -> None:
        self._catalog = catalog
        self._installer = installer
        self._lookup_factory = lookup_factory
        self._extensions = extensions
        self._settings = settings
        self._self_name = self_name

    # -- queries -------------------------------------------------------

    async def status(self, tokens: Sequence[str] = ()) -> list[CogStatus]:
        """Status of the named cogs; with no names, of every non-core cog
        on disk plus every managed or loaded name."""

        packages = await self._catalog.scan()
        loaded = self._extensions.loaded()
        if tokens:
            names, not_found = self._resolve(tokens, packages)
            names = [*names, *not_found]
        else:
            names = sorted(
                {
                    name
                    for name, copies in packages.items()
                    if copies[0].source is not CogSource.CORE
                }
                | set(await self._settings.managed())
                | {name for name in loaded if name in packages}
            )

        lookup = self._lookup_factory()
        statuses: list[CogStatus] = []
        for name in names:
            copies = packages.get(name, [])
            effective, shadowed = self._effective(name, copies)
            missing = unsatisfied(effective.requirements, lookup) if effective is not None else ()
            statuses.append(
                CogStatus(
                    name=name,
                    package=effective,
                    loaded=name in loaded,
                    missing_requirements=missing,
                    shadowed=shadowed,
                )
            )
        return statuses

    async def managed(self) -> list[str]:
        return await self._settings.managed()

    async def autorepair(self) -> bool:
        return await self._settings.autorepair()

    async def set_autorepair(self, enabled: bool) -> None:
        await self._settings.set_autorepair(enabled)

    # -- commands ------------------------------------------------------

    async def install(self, tokens: Sequence[str]) -> InstallReport:
        """Install every requirement of the named cogs and everything they
        require, whether or not it already looks satisfied."""

        packages = await self._catalog.scan()
        targets, not_found = self._resolve(tokens, packages)
        deps = closure(targets, self._requires(packages))
        names = tuple(sorted(deps.names))
        install = await self._installer.install(await self._requirement_set(names, packages))
        return InstallReport(
            names=names,
            not_found=tuple(not_found),
            install=install,
            still_missing=self._missing_by_cog(names, packages),
            unknown_dependencies=deps.unknown,
        )

    async def load(self, tokens: Sequence[str], *, remember: bool = True) -> LoadReport:
        """Load the named cogs and everything they require, dependencies first,
        installing missing requirements first."""

        packages = await self._catalog.scan()
        targets, not_found = self._resolve(tokens, packages)
        requires = self._requires(packages)
        deps = closure(targets, requires)
        try:
            order = tuple(load_order(deps.names, requires))
        except DependencyCycleError as exc:
            return LoadReport(order=(), results=(), not_found=tuple(not_found), error=str(exc))

        loaded = self._extensions.loaded()
        pending = [name for name in order if name not in loaded]

        install: InstallResult | None = None
        if self._missing_by_cog(pending, packages):
            install = await self._installer.install(await self._requirement_set(order, packages))
        still_missing = self._missing_by_cog(pending, packages)

        results: list[LoadResult] = []
        not_loaded: set[str] = set()
        for name in order:
            if name in loaded:
                results.append(LoadResult(name, LoadOutcome.ALREADY_LOADED))
                continue
            blocked = sorted(dep for dep in packages[name][0].required_cogs if dep in not_loaded)
            if blocked:
                results.append(
                    LoadResult(
                        name,
                        LoadOutcome.SKIPPED,
                        "requires " + ", ".join(blocked) + ", which did not load",
                    )
                )
                not_loaded.add(name)
                continue
            # A dependent's own setup() may already have pulled this in.
            if name in self._extensions.loaded():
                results.append(LoadResult(name, LoadOutcome.ALREADY_LOADED))
                continue
            result = await self._extensions.load(name)
            results.append(result)
            if result.outcome not in (LoadOutcome.LOADED, LoadOutcome.ALREADY_LOADED):
                not_loaded.add(name)

        if remember:
            now_loaded = self._extensions.loaded()
            managed = set(await self._settings.managed())
            managed |= {name for name in order if name in now_loaded}
            managed.discard(self._self_name)
            await self._settings.set_managed(sorted(managed))

        report = LoadReport(
            order=order,
            results=tuple(results),
            not_found=tuple(not_found),
            install=install,
            still_missing=still_missing,
            unknown_dependencies=deps.unknown,
        )
        for result in report.results:
            log.info("load %s: %s %s", result.name, result.outcome.value, result.detail)
        return report

    async def unload(self, tokens: Sequence[str]) -> UnloadReport:
        """Unload the named cogs and every loaded cog that requires them,
        dependents first, and stop managing them."""

        packages = await self._catalog.scan()
        targets, not_found = self._resolve(tokens, packages)
        requires = self._requires(packages)
        loaded = self._extensions.loaded()
        try:
            order = tuple(unload_order(targets, loaded, requires))
        except DependencyCycleError as exc:
            return UnloadReport(order=(), results=(), not_found=tuple(not_found), error=str(exc))

        results: list[UnloadResult] = []
        for name in order:
            if name == self._self_name:
                results.append(
                    UnloadResult(name, UnloadOutcome.SKIPPED, "switchboard never unloads itself")
                )
                continue
            # Unloading a cog can cascade (corridor unloads its dependents).
            if name not in self._extensions.loaded():
                results.append(UnloadResult(name, UnloadOutcome.NOT_LOADED))
                continue
            results.append(await self._extensions.unload(name))
        for name in sorted(set(targets) - set(order)):
            results.append(UnloadResult(name, UnloadOutcome.NOT_LOADED))

        managed = set(await self._settings.managed())
        managed -= set(targets) | set(order)
        await self._settings.set_managed(sorted(managed))
        return UnloadReport(order=order, results=tuple(results), not_found=tuple(not_found))

    async def reload(self, tokens: Sequence[str]) -> tuple[UnloadReport, LoadReport]:
        """Unload, then load again, the named cogs and their loaded dependents."""

        unloaded = await self.unload(tokens)
        again = [
            result.name for result in unloaded.results if result.outcome is UnloadOutcome.UNLOADED
        ]
        return unloaded, await self.load([*tokens, *again])

    async def repair(self) -> LoadReport | None:
        """Load every managed cog that is not loaded; None when autorepair is off."""

        if not await self._settings.autorepair():
            return None
        managed = await self._settings.managed()
        if not managed:
            return LoadReport(order=(), results=())
        return await self.load(managed, remember=False)

    # -- helpers -------------------------------------------------------

    def _resolve(
        self, tokens: Iterable[str], packages: dict[str, list[CogPackage]]
    ) -> tuple[list[str], list[str]]:
        """Expand `all` / directory-name tokens; split into known and unknown names."""

        local = {
            name: copies[0]
            for name, copies in packages.items()
            if copies[0].source in LOCAL_SOURCES
            and not copies[0].disabled
            and name != self._self_name
        }
        names: list[str] = []
        not_found: list[str] = []
        for token in tokens:
            if token == ALL_KEYWORD:
                expanded = sorted(local)
            elif token in packages:
                expanded = [token]
            else:
                expanded = sorted(
                    name for name, package in local.items() if package.root.name == token
                )
                if not expanded:
                    not_found.append(token)
            for name in expanded:
                if name not in names:
                    names.append(name)
        return names, not_found

    def _effective(
        self, name: str, copies: list[CogPackage]
    ) -> tuple[CogPackage | None, tuple[CogPackage, ...]]:
        """The copy Red is running (if loaded) or would load, plus the rest."""

        if not copies:
            return None, ()
        origin = self._extensions.origin(name)
        effective = copies[0]
        if origin is not None:
            for package in copies:
                if package.directory.resolve() == origin.resolve():
                    effective = package
                    break
        return effective, tuple(package for package in copies if package is not effective)

    @staticmethod
    def _requires(packages: dict[str, list[CogPackage]]) -> dict[str, tuple[str, ...]]:
        return {name: copies[0].required_cogs for name, copies in packages.items()}

    def _missing_by_cog(
        self, names: Iterable[str], packages: dict[str, list[CogPackage]]
    ) -> dict[str, tuple[str, ...]]:
        lookup = self._lookup_factory()
        missing: dict[str, tuple[str, ...]] = {}
        for name in names:
            copies = packages.get(name)
            if not copies:
                continue
            gaps = unsatisfied(copies[0].requirements, lookup)
            if gaps:
                missing[name] = gaps
        return missing

    async def _requirement_set(
        self, names: Iterable[str], packages: dict[str, list[CogPackage]]
    ) -> list[str]:
        """Requirements of `names` plus every loaded or managed cog.

        Everything goes to pip in one run, so its resolver sees every pin
        together. Installing one cog's requirements alone lets pip upgrade a
        shared package past a pin another cog relies on (the pydantic/anyio
        incident documented in .github/workflows/cogs-quality.yml).
        """

        everything = [
            *names,
            *sorted(self._extensions.loaded()),
            *await self._settings.managed(),
        ]
        requirements: list[str] = []
        for name in dict.fromkeys(everything):
            copies = packages.get(name)
            if copies:
                requirements.extend(copies[0].requirements)
        return list(dict.fromkeys(requirements))
