"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import NodeInstallation, NodeStatus


def test_node_installation_holds_its_fields() -> None:
    installation = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")

    assert installation.version == "22.18.0"
    assert installation.install_dir == "/data/node/22.18.0"


def test_node_installation_is_frozen() -> None:
    installation = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")

    with pytest.raises(FrozenInstanceError):
        installation.version = "20.17.0"  # type: ignore[misc]


def test_node_status_defaults_to_not_installed() -> None:
    status = NodeStatus(installed=False)

    assert status.installed is False
    assert status.version is None
    assert status.install_dir is None


def test_node_status_can_report_an_installed_version() -> None:
    status = NodeStatus(installed=True, version="22.18.0", install_dir="/data/node/22.18.0")

    assert status.installed is True
    assert status.version == "22.18.0"
    assert status.install_dir == "/data/node/22.18.0"
