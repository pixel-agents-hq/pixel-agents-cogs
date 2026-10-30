#!/usr/bin/env python3
"""End-to-end checks for the switchboard cog against a real Red instance.

Driven by run.sh, which owns the bot process. Subcommands:

  prepare    Offline, before the first start: register the workspace as an
             `[p]addpath` path in CogManager's Config and, for
             `--source downloader`, install switchboard through Downloader's
             RepoManager and record it in Downloader's own bookkeeping --
             exactly what `[p]repo add` + `[p]cog install` leave behind.
  fresh      Against the running bot: switchboard reports its own source,
             corridor cannot load through Red alone (no requirements yet),
             `SWITCHBOARD__LOAD all` installs requirements and loads every
             cog dependencies-first, `SWITCHBOARD__UNLOAD corridor` takes
             every dependent down first, then everything is loaded again.
  restarted  After run.sh wiped Downloader's lib dir and restarted the bot:
             wait for autorepair to bring every managed cog back.

Every RPC call goes through Red's own JSON-RPC websocket (`--rpc`), the same
transport .github/actions/test-red-discordbot-downloader-local uses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import aiohttp

RPC_URL = "ws://127.0.0.1:{port}/"
SELF = "switchboard"
DOWNLOADER_REPO = "switchboard_ci"
# Generous: SWITCHBOARD__LOAD runs pip for every office cog in one go, and
# pixelagents builds its webview inside cog_load.
LOAD_TIMEOUT = 1800


# -- RPC ------------------------------------------------------------------


class Rpc:
    def __init__(self, session: aiohttp.ClientSession, port: int) -> None:
        self._session = session
        self._url = RPC_URL.format(port=port)
        self._next_id = 1

    async def wait_ready(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                # Red pins aiohttp 3.9, which takes a plain float here.
                async with self._session.ws_connect(self._url, timeout=3):
                    return
            except (aiohttp.ClientError, OSError):
                if time.monotonic() > deadline:
                    raise RuntimeError(f"RPC endpoint {self._url} never came up") from None
                await asyncio.sleep(1)

    async def call(
        self, method: str, params: list[Any] | None = None, *, timeout: float = 60
    ) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        async with self._session.ws_connect(self._url) as ws:
            await ws.send_json(payload)
            message = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if message.type != aiohttp.WSMsgType.TEXT:
            raise RuntimeError(f"{method}: unexpected websocket message {message.type}")
        response = json.loads(message.data)
        if "error" in response:
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response.get("result")

    async def wait_for_method(self, method: str, timeout: float = 120) -> None:
        """Wait until a cog's RPC handler is registered (it loads after RPC starts)."""

        deadline = time.monotonic() + timeout
        while True:
            try:
                await self.call(method, [[SELF]])
                return
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(2)


# -- what the workspace contains -----------------------------------------


def local_cogs(workspace: Path) -> dict[str, list[str]]:
    """name -> required_cogs, for every loadable, enabled cog except switchboard.

    Read straight from info.json, independently of switchboard's own catalog.
    """

    cogs: dict[str, list[str]] = {}
    for info_path in sorted(workspace.glob("*/info.json")):
        directory = info_path.parent
        if directory.name == SELF or not (directory / "__init__.py").is_file():
            continue
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if info.get("type") == "SHARED_LIBRARY" or info.get("disabled"):
            continue
        cogs[directory.name] = list(info.get("required_cogs") or {})
    return cogs


def statuses(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cog["name"]: cog for cog in result["cogs"]}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ok: {message}")


# -- prepare (offline) ---------------------------------------------------


async def prepare(args: argparse.Namespace) -> None:
    from redbot.core import Config, _drivers, data_manager
    from redbot.core._cog_manager import CogManager

    data_manager.load_basic_configuration(args.instance)
    driver = _drivers.get_driver_class()
    await driver.initialize(**data_manager.storage_details())
    try:
        workspace = Path(args.workspace).resolve()
        cog_manager = CogManager()
        await cog_manager.add_path(workspace)
        print(f"addpath: {workspace}")

        if args.source == "downloader":
            await _install_through_downloader(workspace, cog_manager, Config)
    finally:
        await driver.teardown()


async def _install_through_downloader(workspace: Path, cog_manager: Any, config_cls: Any) -> None:
    from redbot.cogs.downloader.repo_manager import RepoManager

    # A throwaway one-cog git repo, so the repo Downloader clones holds
    # exactly the switchboard under test.
    repo_dir = Path(tempfile.mkdtemp(prefix="switchboard-repo-"))
    shutil.copytree(workspace / SELF, repo_dir / SELF, ignore=shutil.ignore_patterns("__pycache__"))
    (repo_dir / "info.json").write_text(json.dumps({"name": DOWNLOADER_REPO, "short": "ci"}))
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "-c", "user.name=ci", "-c", "user.email=ci@invalid", "add", "."],
        ["git", "-c", "user.name=ci", "-c", "user.email=ci@invalid", "commit", "-qm", "ci"],
    ):
        subprocess.run(command, cwd=repo_dir, check=True)

    manager = RepoManager()
    await manager.initialize()
    repo = await manager.add_repo(url=str(repo_dir), name=DOWNLOADER_REPO, branch="main")
    (cog,) = [c for c in repo.available_cogs if c.name == SELF]
    module = await repo.install_cog(cog, await cog_manager.install_path())

    # What Downloader._save_to_installed() records after `[p]cog install`.
    downloader = config_cls.get_conf(None, identifier=998240343, cog_name="Downloader")
    await downloader.installed_cogs.set({DOWNLOADER_REPO: {SELF: module.to_json()}})
    print(f"downloader: installed {SELF} from {DOWNLOADER_REPO} at {module.commit}")


# -- fresh ----------------------------------------------------------------


async def fresh(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    expected = local_cogs(workspace)
    async with aiohttp.ClientSession() as session:
        rpc = Rpc(session, args.port)
        await rpc.wait_ready()
        # Red ignores --load-cogs on an instance's very first start (it only
        # records the Python version then -- see Red._pre_connect), so load
        # switchboard and the dashboard stub explicitly. The `restarted`
        # phase is a second start and relies on --load-cogs for real.
        first = await rpc.call("CORE__LOAD", [[SELF, "dashboard"]])
        check(
            {SELF, "dashboard"} <= set(first["loaded_packages"] + first["alreadyloaded_packages"]),
            f"switchboard and the dashboard stub load ({first})",
        )
        await rpc.wait_for_method("SWITCHBOARD__STATUS")

        print("switchboard knows where it came from")
        own = statuses(await rpc.call("SWITCHBOARD__STATUS", [[SELF, "corridor", "dashboard"]]))
        check(own[SELF]["loaded"], "switchboard is loaded")
        check(
            own[SELF]["source"] == args.source,
            f"switchboard source is {args.source!r} (got {own[SELF]['source']!r})",
        )
        if args.source == "downloader":
            check(
                own[SELF]["package"]["repo_name"] == DOWNLOADER_REPO,
                f"switchboard is tracked under Downloader repo {DOWNLOADER_REPO}",
            )
            check(
                [p["source"] for p in own[SELF]["shadowed"]] == ["addpath"],
                "the addpath copy of switchboard is reported as shadowed",
            )
        check(own["corridor"]["source"] == "addpath", "corridor comes from addpath")
        check(bool(own["corridor"]["missing_requirements"]), "corridor's requirements are missing")
        check(own["dashboard"]["source"] == "cli_path", "the dashboard stub comes from --cog-path")

        print("Red alone cannot load corridor (the original bug)")
        red_only = await rpc.call("CORE__LOAD", [["corridor"]])
        check(
            "corridor" not in red_only["loaded_packages"], f"CORE__LOAD corridor failed: {red_only}"
        )

        print("SWITCHBOARD__LOAD all")
        report = await rpc.call("SWITCHBOARD__LOAD", [["all"]], timeout=LOAD_TIMEOUT)
        _check_load_report(report, expected)

        print("SWITCHBOARD__UNLOAD corridor")
        report = await rpc.call("SWITCHBOARD__UNLOAD", [["corridor"]], timeout=300)
        order = report["order"]
        print(f"  order: {' -> '.join(order)}")
        check(order[-1] == "corridor", "corridor unloads last")
        dependents = {name for name, deps in expected.items() if "corridor" in deps}
        check(dependents <= set(order), "every cog requiring corridor was unloaded")
        _check_dependents_first(order, expected)
        after = statuses(await rpc.call("SWITCHBOARD__STATUS", [list(expected)]))
        still = sorted(name for name in dependents | {"corridor"} if after[name]["loaded"])
        check(not still, f"nothing that needs corridor is still loaded ({still})")

        print("SWITCHBOARD__LOAD all (again, to leave a managed set for the restart)")
        report = await rpc.call("SWITCHBOARD__LOAD", [["all"]], timeout=LOAD_TIMEOUT)
        _check_load_report(report, expected)


def _check_load_report(report: dict[str, Any], expected: dict[str, list[str]]) -> None:
    order = report["order"]
    print(f"  order: {' -> '.join(order)}")
    for result in report["results"]:
        print(f"  {result['name']}: {result['outcome']} {result['detail']}")
    install = report["install"]
    if install and not install["ok"]:
        print(install["output"])
    check(not report["error"], f"no error ({report['error']!r})")
    check(
        report["still_missing"] == {}, f"no requirement still missing ({report['still_missing']})"
    )
    check(
        set(expected) <= set(order),
        f"every local cog is in the load order ({sorted(set(expected) - set(order))})",
    )
    position = {name: index for index, name in enumerate(order)}
    for name, deps in expected.items():
        for dep in deps:
            if dep in position:
                check(position[dep] < position[name], f"{dep} loads before {name}")
    check(report["ok"], "every cog loaded")


def _check_dependents_first(order: list[str], expected: dict[str, list[str]]) -> None:
    position = {name: index for index, name in enumerate(order)}
    for name, deps in expected.items():
        for dep in deps:
            if name in position and dep in position:
                check(position[name] < position[dep], f"{name} unloads before {dep}")


# -- restarted ------------------------------------------------------------


async def restarted(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    expected = sorted(local_cogs(workspace))
    async with aiohttp.ClientSession() as session:
        rpc = Rpc(session, args.port)
        await rpc.wait_ready()
        await rpc.wait_for_method("SWITCHBOARD__STATUS")

        print("waiting for autorepair to load every managed cog")
        deadline = time.monotonic() + LOAD_TIMEOUT
        while True:
            current = statuses(await rpc.call("SWITCHBOARD__STATUS", [expected]))
            missing = [name for name in expected if not current[name]["loaded"]]
            if not missing:
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"autorepair never loaded: {missing}")
            await asyncio.sleep(10)
        check(True, f"autorepair loaded all {len(expected)} cogs")
        unmet = {
            n: s["missing_requirements"] for n, s in current.items() if s["missing_requirements"]
        }
        check(not unmet, f"no requirement is missing after autorepair ({unmet})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--instance", required=True)
    p.add_argument("--source", choices=["addpath", "downloader"], required=True)
    p.add_argument("--workspace", required=True)

    for name in ("fresh", "restarted"):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, required=True)
        p.add_argument("--workspace", required=True)
        if name == "fresh":
            p.add_argument("--source", choices=["addpath", "downloader"], required=True)

    args = parser.parse_args()
    handler = {"prepare": prepare, "fresh": fresh, "restarted": restarted}[args.command]
    try:
        asyncio.run(handler(args))
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
