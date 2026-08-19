"""NodeService is fully testable without Red or the real network: plain
in-memory fakes satisfy the NodeRepository/NodeInstaller protocols, no
unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import NodeService
from ..domain import NodeInstallation


class FakeNodeRepository:
    def __init__(self, installed: NodeInstallation | None = None) -> None:
        self._installed = installed

    async def get_installed(self) -> NodeInstallation | None:
        return self._installed

    async def set_installed(self, installation: NodeInstallation | None) -> None:
        self._installed = installation


class FakeNodeInstaller:
    def __init__(self, default_version: str = "22.18.0") -> None:
        self.default_version = default_version
        self.install_calls: list[str] = []
        self.uninstall_calls: list[NodeInstallation] = []
        self.activate_calls: list[NodeInstallation] = []
        self.deactivate_calls: list[NodeInstallation] = []

    def resolve_version(self, requested: str | None) -> str:
        return requested.lstrip("v") if requested else self.default_version

    def install(self, version: str) -> NodeInstallation:
        self.install_calls.append(version)
        return NodeInstallation(version=version, install_dir=f"/data/node/{version}")

    def uninstall(self, installation: NodeInstallation) -> None:
        self.uninstall_calls.append(installation)

    def activate(self, installation: NodeInstallation) -> None:
        self.activate_calls.append(installation)

    def deactivate(self, installation: NodeInstallation) -> None:
        self.deactivate_calls.append(installation)


class TestNodeServiceStatus(unittest.IsolatedAsyncioTestCase):
    async def test_status_reports_not_installed_initially(self) -> None:
        service = NodeService(FakeNodeRepository(), FakeNodeInstaller())

        status = await service.status()

        self.assertFalse(status.installed)
        self.assertIsNone(status.version)

    async def test_status_reports_persisted_installation(self) -> None:
        installed = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")
        service = NodeService(FakeNodeRepository(installed), FakeNodeInstaller())

        status = await service.status()

        self.assertTrue(status.installed)
        self.assertEqual(status.version, "22.18.0")
        self.assertEqual(status.install_dir, "/data/node/22.18.0")


class TestNodeServiceInstall(unittest.IsolatedAsyncioTestCase):
    async def test_install_with_no_version_uses_installer_default(self) -> None:
        installer = FakeNodeInstaller(default_version="22.18.0")
        repository = FakeNodeRepository()
        service = NodeService(repository, installer)

        installation = await service.install()

        self.assertEqual(installation.version, "22.18.0")
        self.assertEqual(installer.install_calls, ["22.18.0"])
        self.assertEqual(installer.activate_calls, [installation])
        self.assertEqual(await repository.get_installed(), installation)

    async def test_install_with_explicit_version_uses_it(self) -> None:
        installer = FakeNodeInstaller(default_version="22.18.0")
        service = NodeService(FakeNodeRepository(), installer)

        installation = await service.install("20.17.0")

        self.assertEqual(installation.version, "20.17.0")
        self.assertEqual(installer.install_calls, ["20.17.0"])

    async def test_install_same_version_again_only_reactivates(self) -> None:
        installer = FakeNodeInstaller()
        existing = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")
        service = NodeService(FakeNodeRepository(existing), installer)

        installation = await service.install("22.18.0")

        self.assertEqual(installation, existing)
        self.assertEqual(installer.install_calls, [])
        self.assertEqual(installer.activate_calls, [existing])

    async def test_install_switching_versions_removes_previous_after_new_one_succeeds(
        self,
    ) -> None:
        installer = FakeNodeInstaller()
        previous = NodeInstallation(version="20.17.0", install_dir="/data/node/20.17.0")
        repository = FakeNodeRepository(previous)
        service = NodeService(repository, installer)

        installation = await service.install("22.18.0")

        self.assertEqual(installation.version, "22.18.0")
        self.assertEqual(await repository.get_installed(), installation)
        self.assertEqual(installer.deactivate_calls, [previous])
        self.assertEqual(installer.uninstall_calls, [previous])


class TestNodeServiceUninstall(unittest.IsolatedAsyncioTestCase):
    async def test_uninstall_removes_and_clears_persisted_state(self) -> None:
        installer = FakeNodeInstaller()
        installed = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")
        repository = FakeNodeRepository(installed)
        service = NodeService(repository, installer)

        result = await service.uninstall()

        self.assertEqual(result, installed)
        self.assertEqual(installer.deactivate_calls, [installed])
        self.assertEqual(installer.uninstall_calls, [installed])
        self.assertIsNone(await repository.get_installed())

    async def test_uninstall_when_nothing_installed_is_a_noop(self) -> None:
        installer = FakeNodeInstaller()
        service = NodeService(FakeNodeRepository(), installer)

        result = await service.uninstall()

        self.assertIsNone(result)
        self.assertEqual(installer.uninstall_calls, [])


class TestNodeServiceReactivate(unittest.IsolatedAsyncioTestCase):
    async def test_reactivate_reapplies_path_for_a_persisted_install(self) -> None:
        installer = FakeNodeInstaller()
        installed = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")
        service = NodeService(FakeNodeRepository(installed), installer)

        await service.reactivate()

        self.assertEqual(installer.activate_calls, [installed])

    async def test_reactivate_is_a_noop_when_nothing_installed(self) -> None:
        installer = FakeNodeInstaller()
        service = NodeService(FakeNodeRepository(), installer)

        await service.reactivate()

        self.assertEqual(installer.activate_calls, [])
