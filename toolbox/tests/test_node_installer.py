"""infrastructure.node_installer has no Red/discord dependency -- every test
here exercises it directly, with the network (`opener`) and the host
(`platform`/`os.environ`) faked out at their existing seams."""

from __future__ import annotations

import io
import json
import logging
import os
import tarfile
from pathlib import Path

import pytest

from ..domain import NodeInstallation
from ..infrastructure import node_installer as ni

_LOGGER = logging.getLogger("test.toolbox")


def _index_response(*, versions: list[tuple[str, bool]]) -> io.BytesIO:
    payload = [{"version": version, "lts": lts} for version, lts in versions]
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class _RaisingOpener:
    def __call__(self, url: str) -> io.BytesIO:
        raise OSError("network unreachable")


def test_resolve_version_returns_explicit_version_unchanged() -> None:
    assert ni.resolve_version("20.17.0") == "20.17.0"


def test_resolve_version_strips_leading_v() -> None:
    assert ni.resolve_version("v20.17.0") == "20.17.0"


def test_resolve_version_picks_latest_lts_release_in_default_major() -> None:
    releases = [
        ("v23.1.0", False),  # newer major, not the default -- skipped
        ("v22.18.0", True),  # first (newest) v22.x LTS entry -- picked
        ("v22.17.0", True),
        ("v22.5.0", False),  # not LTS -- skipped
    ]

    def opener(url: str) -> io.BytesIO:
        assert url == ni.DIST_INDEX_URL
        return _index_response(versions=releases)

    assert ni.resolve_version(None, opener=opener) == "22.18.0"


def test_resolve_version_raises_a_clear_error_when_the_index_is_unreachable() -> None:
    with pytest.raises(ni.NodeInstallError, match="Could not resolve"):
        ni.resolve_version(None, opener=_RaisingOpener())


def test_resolve_version_raises_when_no_lts_release_matches() -> None:
    def opener(url: str) -> io.BytesIO:
        return _index_response(versions=[("v22.5.0", False)])

    with pytest.raises(ni.NodeInstallError, match="no LTS release"):
        ni.resolve_version(None, opener=opener)


def test_platform_target_maps_linux_x86_64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")

    assert ni.platform_target() == ("linux-x64", "tar.gz")


def test_platform_target_maps_darwin_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ni.platform, "machine", lambda: "arm64")

    assert ni.platform_target() == ("darwin-arm64", "tar.gz")


def test_platform_target_maps_windows_x64_to_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ni.platform, "machine", lambda: "AMD64")

    assert ni.platform_target() == ("win-x64", "zip")


def test_platform_target_rejects_unsupported_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "riscv64")

    with pytest.raises(ni.NodeInstallError, match="Unsupported CPU architecture"):
        ni.platform_target()


def test_platform_target_rejects_unsupported_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "PlayStation")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")

    with pytest.raises(ni.NodeInstallError, match="Unsupported OS"):
        ni.platform_target()


def test_activate_prepends_bin_dir_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
    installation = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")

    ni.activate(installation)
    ni.activate(installation)

    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == "/data/node/22.18.0/bin"
    assert parts.count("/data/node/22.18.0/bin") == 1


def test_deactivate_removes_bin_dir_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("PATH", os.pathsep.join(["/data/node/22.18.0/bin", "/usr/bin", "/bin"]))
    installation = NodeInstallation(version="22.18.0", install_dir="/data/node/22.18.0")

    ni.deactivate(installation)

    assert "/data/node/22.18.0/bin" not in os.environ["PATH"].split(os.pathsep)


def _make_node_tar_gz(*, dirname: str, node_bin: bool = True) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if node_bin:
            data = b"#!/bin/sh\necho fake node\n"
            info = tarfile.TarInfo(name=f"{dirname}/bin/node")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        npm_data = b"#!/bin/sh\necho fake npm\n"
        npm_info = tarfile.TarInfo(name=f"{dirname}/bin/npm")
        npm_info.size = len(npm_data)
        tar.addfile(npm_info, io.BytesIO(npm_data))
    return buffer.getvalue()


def test_download_and_install_extracts_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")
    archive_bytes = _make_node_tar_gz(dirname="node-v22.18.0-linux-x64")

    def opener(url: str) -> io.BytesIO:
        assert url == "https://nodejs.org/dist/v22.18.0/node-v22.18.0-linux-x64.tar.gz"
        return io.BytesIO(archive_bytes)

    version_dir = tmp_path / "node" / "22.18.0"
    installation = ni.download_and_install("22.18.0", version_dir, logger=_LOGGER, opener=opener)

    assert installation == NodeInstallation(version="22.18.0", install_dir=str(version_dir))
    assert (version_dir / "bin" / "node").is_file()
    assert (version_dir / "bin" / "npm").is_file()


def test_download_and_install_raises_when_the_archive_has_no_node_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")
    archive_bytes = _make_node_tar_gz(dirname="node-v22.18.0-linux-x64", node_bin=False)

    def opener(url: str) -> io.BytesIO:
        return io.BytesIO(archive_bytes)

    with pytest.raises(ni.NodeInstallError, match="no node binary"):
        ni.download_and_install(
            "22.18.0", tmp_path / "node" / "22.18.0", logger=_LOGGER, opener=opener
        )


def test_download_and_install_raises_a_clear_error_on_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")

    with pytest.raises(ni.NodeInstallError, match="Could not download"):
        ni.download_and_install(
            "22.18.0", tmp_path / "node" / "22.18.0", logger=_LOGGER, opener=_RaisingOpener()
        )


def test_download_and_install_rejects_path_traversal_in_archive_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ni.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ni.platform, "machine", lambda: "x86_64")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = b"malicious"
        info = tarfile.TarInfo(name="../../etc/evil")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    archive_bytes = buffer.getvalue()

    def opener(url: str) -> io.BytesIO:
        return io.BytesIO(archive_bytes)

    with pytest.raises(ni.NodeInstallError, match="outside destination"):
        ni.download_and_install(
            "22.18.0", tmp_path / "node" / "22.18.0", logger=_LOGGER, opener=opener
        )


def test_remove_install_deletes_the_version_directory(tmp_path: Path) -> None:
    install_dir = tmp_path / "node" / "22.18.0"
    (install_dir / "bin").mkdir(parents=True)
    (install_dir / "bin" / "node").write_text("fake")
    installation = NodeInstallation(version="22.18.0", install_dir=str(install_dir))

    ni.remove_install(installation, logger=_LOGGER)

    assert not install_dir.exists()


def test_remove_install_is_a_noop_when_the_directory_is_already_gone(tmp_path: Path) -> None:
    install_dir = tmp_path / "node" / "22.18.0"
    installation = NodeInstallation(version="22.18.0", install_dir=str(install_dir))

    ni.remove_install(installation, logger=_LOGGER)  # must not raise
