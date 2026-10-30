from __future__ import annotations

import unittest
from pathlib import Path

from ..application import SwitchboardService
from ..domain import CogSource, LoadOutcome, UnloadOutcome
from .conftest import (
    FakeCatalog,
    FakeDistributions,
    FakeExtensions,
    FakeInstaller,
    FakeSettings,
    package,
)

CORRIDOR = package("corridor", requirements=("a2a-sdk", "uvicorn"))
PIXELAGENTS = package("pixelagents", requires=("corridor",), requirements=("pyyaml>=6",))
CCTV = package("cctv", requires=("corridor", "pixelagents"))
DESKUTILS = package("deskutils", requires=("corridor",))
SWITCHBOARD = package("switchboard")


def build(
    *packages,  # type: ignore[no-untyped-def]
    dists: FakeDistributions | None = None,
    extensions: FakeExtensions | None = None,
    settings: FakeSettings | None = None,
    install_ok: bool = True,
):
    dists = dists or FakeDistributions()
    installer = FakeInstaller(dists, ok=install_ok)
    extensions = extensions or FakeExtensions(loaded=["switchboard"])
    settings = settings or FakeSettings()
    service = SwitchboardService(
        catalog=FakeCatalog(*(packages or (CORRIDOR, PIXELAGENTS, CCTV, DESKUTILS, SWITCHBOARD))),
        installer=installer,
        lookup_factory=lambda: dists.lookup,
        extensions=extensions,
        settings=settings,
    )
    return service, installer, extensions, settings


class TestLoad(unittest.IsolatedAsyncioTestCase):
    async def test_loads_dependencies_first_after_installing_requirements(self) -> None:
        service, installer, extensions, settings = build()

        report = await service.load(["cctv"])

        self.assertTrue(report.ok)
        self.assertEqual(report.order, ("corridor", "pixelagents", "cctv"))
        self.assertEqual(extensions.load_calls, ["corridor", "pixelagents", "cctv"])
        # One pip run with every requirement of the closure.
        self.assertEqual(installer.calls, [("a2a-sdk", "uvicorn", "pyyaml>=6")])
        self.assertEqual(report.still_missing, {})
        self.assertEqual(await settings.managed(), ["cctv", "corridor", "pixelagents"])

    async def test_does_not_run_pip_when_everything_is_satisfied(self) -> None:
        dists = FakeDistributions(**{"a2a-sdk": "1", "uvicorn": "1"})
        service, installer, _, _ = build(dists=dists)

        report = await service.load(["corridor"])

        self.assertIsNone(report.install)
        self.assertEqual(installer.calls, [])

    async def test_pip_run_includes_requirements_of_already_loaded_cogs(self) -> None:
        # pixelagents is loaded and satisfied, but its pins must still reach
        # the resolver when deskutils' closure triggers an install.
        dists = FakeDistributions(pyyaml="6.0")
        extensions = FakeExtensions(loaded=["switchboard", "pixelagents"])
        service, installer, _, _ = build(dists=dists, extensions=extensions)

        await service.load(["deskutils"])

        self.assertEqual(installer.calls, [("a2a-sdk", "uvicorn", "pyyaml>=6")])

    async def test_already_loaded_cogs_are_not_reloaded(self) -> None:
        extensions = FakeExtensions(loaded=["switchboard", "corridor"])
        dists = FakeDistributions(pyyaml="6.1")
        service, _, _, _ = build(dists=dists, extensions=extensions)

        report = await service.load(["pixelagents"])

        self.assertEqual(extensions.load_calls, ["pixelagents"])
        self.assertEqual(
            [(r.name, r.outcome) for r in report.results],
            [("corridor", LoadOutcome.ALREADY_LOADED), ("pixelagents", LoadOutcome.LOADED)],
        )

    async def test_a_dependency_pulled_in_by_side_effect_is_already_loaded(self) -> None:
        # Not reachable via switchboard's own order, but a cog's setup() can
        # load something switchboard was about to load next.
        a = package("a")
        b = package("b")
        extensions = FakeExtensions(loaded=["switchboard"], side_effects={"a": ["b"]})
        service, _, _, _ = build(a, b, extensions=extensions)

        report = await service.load(["a", "b"])

        self.assertEqual(extensions.load_calls, ["a"])
        self.assertEqual(report.results[1].outcome, LoadOutcome.ALREADY_LOADED)

    async def test_failure_skips_dependents_but_not_unrelated_cogs(self) -> None:
        extensions = FakeExtensions(
            loaded=["switchboard"], failing={"pixelagents": "ModuleNotFoundError: yaml"}
        )
        other = package("other")
        service, _, _, settings = build(CORRIDOR, PIXELAGENTS, CCTV, other, extensions=extensions)

        report = await service.load(["cctv", "other"])

        outcomes = {r.name: (r.outcome, r.detail) for r in report.results}
        self.assertEqual(outcomes["corridor"][0], LoadOutcome.LOADED)
        self.assertEqual(outcomes["pixelagents"], (LoadOutcome.FAILED, "ModuleNotFoundError: yaml"))
        self.assertEqual(outcomes["cctv"][0], LoadOutcome.SKIPPED)
        self.assertIn("pixelagents", outcomes["cctv"][1])
        self.assertEqual(outcomes["other"][0], LoadOutcome.LOADED)
        self.assertFalse(report.ok)
        # Only what actually ended up loaded becomes managed.
        self.assertEqual(await settings.managed(), ["corridor", "other"])

    async def test_failed_install_is_reported_with_what_is_still_missing(self) -> None:
        service, _, _, _ = build(install_ok=False)

        report = await service.load(["corridor"])

        assert report.install is not None
        self.assertFalse(report.install.ok)
        self.assertEqual(report.still_missing, {"corridor": ("a2a-sdk", "uvicorn")})

    async def test_unknown_names_are_reported(self) -> None:
        service, _, extensions, _ = build()

        report = await service.load(["nope"])

        self.assertEqual(report.not_found, ("nope",))
        self.assertEqual(extensions.load_calls, [])

    async def test_cycle_is_reported_and_nothing_loads(self) -> None:
        a = package("a", requires=("b",))
        b = package("b", requires=("a",))
        service, _, extensions, _ = build(a, b)

        report = await service.load(["a"])

        self.assertIn("cycle", report.error)
        self.assertEqual(extensions.load_calls, [])

    async def test_switchboard_never_manages_itself(self) -> None:
        service, _, _, settings = build()

        await service.load(["switchboard", "corridor"])

        self.assertEqual(await settings.managed(), ["corridor"])


class TestTokens(unittest.IsolatedAsyncioTestCase):
    async def test_all_expands_to_local_cogs_except_switchboard_and_disabled(self) -> None:
        downloaded = package("downloaded", source=CogSource.DOWNLOADER, root=Path("/data/cogs"))
        cli = package("cli", source=CogSource.CLI_PATH, root=Path("/ci/fixtures"))
        disabled = package("off", disabled=True)
        service, _, extensions, _ = build(
            CORRIDOR,
            DESKUTILS,
            SWITCHBOARD,
            downloaded,
            cli,
            disabled,
            dists=FakeDistributions(**{"a2a-sdk": "1", "uvicorn": "1"}),
        )

        report = await service.load(["all"])

        self.assertEqual(report.order, ("cli", "corridor", "deskutils"))
        self.assertEqual(extensions.load_calls, ["cli", "corridor", "deskutils"])

    async def test_directory_name_expands_to_that_directory_only(self) -> None:
        cli = package("cli", source=CogSource.CLI_PATH, root=Path("/ci/fixtures"))
        service, _, _, _ = build(
            CORRIDOR, cli, dists=FakeDistributions(**{"a2a-sdk": "1", "uvicorn": "1"})
        )

        report = await service.load(["fixtures"])

        self.assertEqual(report.order, ("cli",))


class TestUnload(unittest.IsolatedAsyncioTestCase):
    async def test_unloads_loaded_dependents_first_and_forgets_them(self) -> None:
        extensions = FakeExtensions(loaded=["switchboard", "corridor", "pixelagents", "cctv"])
        settings = FakeSettings(managed=["corridor", "pixelagents", "cctv", "deskutils"])
        service, _, _, _ = build(extensions=extensions, settings=settings)

        report = await service.unload(["corridor"])

        self.assertEqual(report.order, ("cctv", "pixelagents", "corridor"))
        self.assertEqual(extensions.unload_calls, ["cctv", "pixelagents", "corridor"])
        self.assertEqual(extensions.loaded(), {"switchboard"})
        # deskutils was not loaded, so it is neither unloaded nor forgotten.
        self.assertEqual(await settings.managed(), ["deskutils"])

    async def test_a_cog_already_removed_by_a_cascade_is_not_unloaded_again(self) -> None:
        # alpha declares no dependency, so it is unloaded after corridor --
        # but corridor's own cog_unload cascade already took it down.
        alpha = package("alpha")
        extensions = FakeExtensions(
            loaded=["switchboard", "corridor", "alpha"], cascades={"corridor": ["alpha"]}
        )
        service, _, _, _ = build(CORRIDOR, alpha, extensions=extensions)

        report = await service.unload(["corridor", "alpha"])

        self.assertEqual(
            [(r.name, r.outcome) for r in report.results],
            [("corridor", UnloadOutcome.UNLOADED), ("alpha", UnloadOutcome.NOT_LOADED)],
        )
        self.assertEqual(extensions.unload_calls, ["corridor"])

    async def test_not_loaded_target_is_reported(self) -> None:
        service, _, _, _ = build()

        report = await service.unload(["cctv"])

        self.assertEqual(
            [(r.name, r.outcome) for r in report.results], [("cctv", UnloadOutcome.NOT_LOADED)]
        )

    async def test_switchboard_never_unloads_itself(self) -> None:
        service, _, extensions, _ = build()

        report = await service.unload(["switchboard"])

        self.assertEqual(report.results[0].outcome, UnloadOutcome.SKIPPED)
        self.assertEqual(extensions.unload_calls, [])

    async def test_reload_reloads_the_dependents_it_unloaded(self) -> None:
        extensions = FakeExtensions(loaded=["switchboard", "corridor", "deskutils"])
        dists = FakeDistributions(**{"a2a-sdk": "1", "uvicorn": "1"})
        service, _, _, _ = build(dists=dists, extensions=extensions)

        unloaded, loaded = await service.reload(["corridor"])

        self.assertEqual(unloaded.order, ("deskutils", "corridor"))
        self.assertEqual(loaded.order, ("corridor", "deskutils"))
        self.assertEqual(extensions.loaded(), {"switchboard", "corridor", "deskutils"})


class TestInstall(unittest.IsolatedAsyncioTestCase):
    async def test_installs_the_closure_even_when_satisfied(self) -> None:
        dists = FakeDistributions(pyyaml="6", **{"a2a-sdk": "1", "uvicorn": "1"})
        service, installer, _, _ = build(dists=dists)

        report = await service.install(["pixelagents"])

        self.assertEqual(report.names, ("corridor", "pixelagents"))
        self.assertEqual(installer.calls, [("a2a-sdk", "uvicorn", "pyyaml>=6")])

    async def test_nothing_to_install(self) -> None:
        service, installer, _, _ = build()

        report = await service.install(["switchboard"])

        self.assertFalse(report.install.ran)
        self.assertEqual(installer.calls, [])


class TestRepair(unittest.IsolatedAsyncioTestCase):
    async def test_loads_managed_cogs_without_changing_the_managed_set(self) -> None:
        settings = FakeSettings(managed=["cctv"])
        service, _, extensions, _ = build(settings=settings)

        report = await service.repair()

        assert report is not None
        self.assertEqual(extensions.load_calls, ["corridor", "pixelagents", "cctv"])
        self.assertEqual(await settings.managed(), ["cctv"])

    async def test_off_returns_none(self) -> None:
        service, _, extensions, _ = build(settings=FakeSettings(managed=["cctv"], autorepair=False))

        self.assertIsNone(await service.repair())
        self.assertEqual(extensions.load_calls, [])


class TestStatus(unittest.IsolatedAsyncioTestCase):
    async def test_default_lists_non_core_cogs_and_managed_names(self) -> None:
        core = package("admin", source=CogSource.CORE, root=Path("/redbot/cogs"))
        settings = FakeSettings(managed=["gone"])
        service, _, _, _ = build(CORRIDOR, SWITCHBOARD, core, settings=settings)

        statuses = await service.status()

        self.assertEqual([s.name for s in statuses], ["corridor", "gone", "switchboard"])
        by_name = {s.name: s for s in statuses}
        self.assertEqual(by_name["corridor"].missing_requirements, ("a2a-sdk", "uvicorn"))
        self.assertIsNone(by_name["gone"].package)
        self.assertTrue(by_name["switchboard"].loaded)

    async def test_downloader_copy_shadows_addpath_copy(self) -> None:
        installed = package(
            "corridor", source=CogSource.DOWNLOADER, root=Path("/data/cogs/CogManager/cogs")
        )
        service, _, _, _ = build(installed, CORRIDOR)

        (status,) = await service.status(["corridor"])

        assert status.package is not None
        self.assertEqual(status.package.source, CogSource.DOWNLOADER)
        self.assertEqual([p.source for p in status.shadowed], [CogSource.ADDPATH])

    async def test_loaded_cog_reports_the_copy_it_runs_from(self) -> None:
        installed = package(
            "corridor", source=CogSource.DOWNLOADER, root=Path("/data/cogs/CogManager/cogs")
        )
        extensions = FakeExtensions(loaded=["switchboard", "corridor"])
        # Loaded from the addpath copy before the Downloader copy existed.
        extensions.origins["corridor"] = CORRIDOR.directory
        service, _, _, _ = build(installed, CORRIDOR, extensions=extensions)

        (status,) = await service.status(["corridor"])

        assert status.package is not None
        self.assertEqual(status.package.source, CogSource.ADDPATH)
        self.assertEqual([p.source for p in status.shadowed], [CogSource.DOWNLOADER])
