"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py /
test_node_installer.py).

Commands are exercised against a fake NodeService (built from the same
fakes application-layer tests use), never the real NodeInstaller -- an
adapter test triggering a real network download would be both slow and
unable to run offline."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..application import NodeService
from ..domain import NodeInstallation
from ..infrastructure import NodeInstallError
from ..toolbox import Toolbox
from .conftest import FakeBot, FakeContext, FakeCorridor
from .test_application_service import FakeNodeInstaller, FakeNodeRepository


def _descriptions(corridor: FakeCorridor) -> list[str | None]:
    return [reply["description"] for reply in corridor.replies]


class TestNodeCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Toolbox(bot=self.bot)
        self.repository = FakeNodeRepository()
        self.installer = FakeNodeInstaller()
        self.cog._service = NodeService(self.repository, self.installer)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_install_with_no_version_installs_the_default(self) -> None:
        await self.cog.node_install.callback(self.cog, self.ctx, None)

        self.assertEqual(self.installer.install_calls, ["22.18.0"])
        self.assertEqual(
            _descriptions(self.bot.corridor),
            ["Installing Node.js the default LTS version…", "✅ Installed Node.js 22.18.0."],
        )

    async def test_install_with_an_explicit_version(self) -> None:
        await self.cog.node_install.callback(self.cog, self.ctx, "20.17.0")

        self.assertEqual(self.installer.install_calls, ["20.17.0"])
        self.assertEqual(_descriptions(self.bot.corridor)[-1], "✅ Installed Node.js 20.17.0.")

    async def test_install_reports_installer_failures_without_raising(self) -> None:
        def failing_install(version: str) -> NodeInstallation:
            raise NodeInstallError("could not reach nodejs.org")

        self.installer.install = failing_install  # type: ignore[method-assign]

        await self.cog.node_install.callback(self.cog, self.ctx, "20.17.0")

        self.assertEqual(_descriptions(self.bot.corridor)[-1], "⚠️ could not reach nodejs.org")

    async def test_uninstall_when_a_version_is_installed(self) -> None:
        await self.cog.node_install.callback(self.cog, self.ctx, "20.17.0")

        await self.cog.node_uninstall.callback(self.cog, self.ctx)

        self.assertEqual(_descriptions(self.bot.corridor)[-1], "✅ Uninstalled Node.js 20.17.0.")
        self.assertIsNone(await self.repository.get_installed())

    async def test_uninstall_when_nothing_is_installed(self) -> None:
        await self.cog.node_uninstall.callback(self.cog, self.ctx)

        self.assertEqual(_descriptions(self.bot.corridor)[-1], "Node.js is not installed.")

    async def test_version_when_nothing_is_installed(self) -> None:
        await self.cog.node_version.callback(self.cog, self.ctx)

        self.assertEqual(_descriptions(self.bot.corridor)[-1], "Node.js is not installed.")

    async def test_version_when_a_version_is_installed(self) -> None:
        await self.cog.node_install.callback(self.cog, self.ctx, "20.17.0")

        await self.cog.node_version.callback(self.cog, self.ctx)

        self.assertEqual(
            _descriptions(self.bot.corridor)[-1], "Node.js 20.17.0 (`/data/node/20.17.0`)"
        )


class TestCogLoadReactivatesInstalledNode(unittest.IsolatedAsyncioTestCase):
    """Regression test for: PATH activation is process-local (os.environ),
    so a bot restart loses it even though the persisted record and the
    on-disk install both survive. cog_load must re-activate whatever was
    already installed."""

    async def test_cog_load_reactivates_a_previously_installed_node(self) -> None:
        bot = FakeBot()
        cog = Toolbox(bot=bot)
        installed = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")
        installer = FakeNodeInstaller()
        cog._service = NodeService(FakeNodeRepository(installed), installer)

        await cog.cog_load()

        self.assertEqual(installer.activate_calls, [installed])

    async def test_cog_load_does_nothing_when_nothing_was_installed(self) -> None:
        bot = FakeBot()
        cog = Toolbox(bot=bot)
        installer = FakeNodeInstaller()
        cog._service = NodeService(FakeNodeRepository(), installer)

        await cog.cog_load()

        self.assertEqual(installer.activate_calls, [])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Toolbox(bot=bot)
        self.assertIsNone(bot.get_cog("Corridor"))

        await cog.cog_load()

        self.assertEqual(bot._cog_mgr.find_cog_calls, ["corridor"])
        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertIsNotNone(cog._corridor)

    async def test_package_setup_loads_corridor_before_adding_the_cog(self) -> None:
        bot = FakeBot(preloaded=False)

        await setup(bot)

        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertEqual(len(bot.add_cog_calls), 1)
        self.assertIs(bot.add_cog_calls[0]._corridor, bot.corridor)

    async def test_missing_corridor_reports_a_user_facing_load_error(self) -> None:
        bot = FakeBot(preloaded=False, corridor_installable=False)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await Toolbox(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Toolbox(bot=bot)

        await cog.cog_load()

        self.assertIn("toolbox", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Toolbox(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("toolbox", bot.corridor.registered_dependents)
