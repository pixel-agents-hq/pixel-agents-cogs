#!/usr/bin/env python3
"""Install and validate Red-DiscordBot cogs through the downloader cog."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

import aiohttp
import shutil
from jsonschema import Draft7Validator, ValidationError

try:
    from jsonschema import RefResolver
except ImportError:  # pragma: no cover - fallback for newer jsonschema versions
    RefResolver = None  # type: ignore[assignment, misc]

from redbot.cogs.downloader import errors as downloader_errors
from redbot.cogs.downloader.repo_manager import Repo, RepoManager
from redbot.core import data_manager
from redbot.core._cog_manager import CogManager

JSONRPC_VERSION = "2.0"
RPC_URL_TEMPLATE = "ws://127.0.0.1:{port}/"
CORE_LOAD_METHOD = "CORE__LOAD"
CORE_UNLOAD_METHOD = "CORE__UNLOAD"
RPC_WAIT_TIMEOUT = 30
RPC_WAIT_INTERVAL = 0.5

# Cache for downloaded schemas to avoid rate limits
_SCHEMA_CACHE: dict[str, dict] = {}


try:
    from aiohttp import ClientWSTimeout
except ImportError:  # pragma: no cover - fallback for older aiohttp
    ClientWSTimeout = None  # type: ignore[assignment]


def _ws_timeout(seconds: float):
    if ClientWSTimeout is None:
        return seconds
    return ClientWSTimeout(ws_close=seconds)


WS_CONNECT_TIMEOUT = _ws_timeout(10)
WS_WAIT_TIMEOUT = _ws_timeout(3)

DEFAULT_INSTANCE_NAME = "tinkerer"
TEMPORARY_INSTANCE_NAME = "temporary_red"

# Loaded once, upfront, and left loaded for the whole run -- satisfies
# floorplan's dashboard_cog_loaded() check so its cog_load() doesn't DM the
# real bot owner about a "missing" dashboard on every CI run. Never part of
# `expected_names`/`installed` (it lives outside the repo under test), so it
# never goes through install_cogs_from_repo, exercise_cogs, or
# _transitive_dependencies -- it's just a fixed fact of the RPC instance's
# state by the time the real cogs are exercised.
DASHBOARD_STUB_NAME = "dashboard"
DASHBOARD_STUB_FIXTURE = Path(__file__).parent / "fixtures" / DASHBOARD_STUB_NAME


class RPCError(RuntimeError):
    """Raised when the RPC server reports an error."""


class RPCProtocolError(RuntimeError):
    """Raised when the RPC response payload is malformed."""


class JsonRpcClient:
    """Simple JSON-RPC client that reuses a single websocket connection."""

    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self._url = url
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 1

    async def __aenter__(self) -> "JsonRpcClient":
        self._ws = await self._session.ws_connect(self._url, timeout=WS_CONNECT_TIMEOUT)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def request(self, method: str, params: list | None = None, *, timeout: int = 30):
        if self._ws is None:
            raise RPCProtocolError("WebSocket connection has not been established yet")

        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        await self._ws.send_json(payload)
        try:
            message = await asyncio.wait_for(self._ws.receive(), timeout=timeout)
        except asyncio.TimeoutError as exc:  # pragma: no cover - runtime guard
            raise RPCError(f"Timed out waiting for RPC response to {method}") from exc

        if message.type == aiohttp.WSMsgType.TEXT:
            try:
                response = json.loads(message.data)
            except json.JSONDecodeError as exc:  # pragma: no cover - runtime guard
                raise RPCProtocolError(
                    f"Invalid JSON payload from RPC server: {message.data}"
                ) from exc
        elif message.type == aiohttp.WSMsgType.ERROR:
            raise RPCError(f"WebSocket error while calling {method}: {self._ws.exception()}")
        else:  # pragma: no cover - runtime guard
            raise RPCProtocolError(f"Unexpected WebSocket message type: {message.type}")

        if response.get("id") != request_id:
            raise RPCProtocolError(
                f"Mismatched RPC response id. Expected {request_id}, got {response.get('id')}"
            )

        if "error" in response:
            raise RPCError(f"RPC call {method} failed: {response['error']}")
        return response.get("result")


def parse_env() -> tuple[List[Path], int, str, Path | None, str, str | None]:
    raw_paths = os.environ.get("COG_PATHS", "")
    port_raw = os.environ.get("RPC_PORT", "6133")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"RPC_PORT must be an integer (received {port_raw!r})") from exc

    repo_name_raw = os.environ.get("REPO_NAME", "test-repo").strip()
    repo_path_raw = os.environ.get("REPO_PATH", "").strip()
    repo_url_raw = os.environ.get("REPO_URL", "").strip()
    repo_path: Path | None = None
    if repo_url_raw:
        if repo_path_raw:
            repo_path = Path(repo_path_raw).resolve()
    else:
        if not repo_path_raw:
            raise RuntimeError("REPO_PATH environment variable must be provided")
        repo_path = Path(repo_path_raw).resolve()
    repo_branch_raw = os.environ.get("REPO_BRANCH", "").strip()
    repo_branch = repo_branch_raw or None

    cog_paths: List[Path] = []
    if raw_paths:
        for chunk in raw_paths.split(","):
            candidate = chunk.strip()
            if not candidate:
                continue
            candidate_path = Path(candidate).expanduser()
            if not candidate_path.exists():
                raise RuntimeError(f"Cog path does not exist: {candidate}")
            cog_paths.append(candidate_path.resolve())
    elif not repo_url_raw:
        workspace_raw = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
        workspace = Path(workspace_raw).expanduser()
        cog_paths = discover_local_cog_paths(workspace)
        if not cog_paths:
            raise RuntimeError(f"No cog directories with info.json were found in {workspace}")

    return cog_paths, port, repo_name_raw, repo_path, repo_url_raw, repo_branch


async def wait_for_rpc(session: aiohttp.ClientSession, url: str) -> None:
    """Poll the RPC endpoint until a websocket handshake succeeds or timeout occurs."""

    start = time.monotonic()
    while True:
        try:
            async with session.ws_connect(url, timeout=WS_WAIT_TIMEOUT):
                print(f"🟢 RPC endpoint {url} is ready")
                return
        except (aiohttp.ClientError, OSError):
            if time.monotonic() - start >= RPC_WAIT_TIMEOUT:
                raise RuntimeError(f"Timed out waiting for RPC endpoint at {url}")
            await asyncio.sleep(RPC_WAIT_INTERVAL)


UNLOAD_SCOPE_COG = "cog"
UNLOAD_SCOPE_COG_AND_DEPENDENCIES = "cog-and-dependencies"
_VALID_UNLOAD_SCOPES = {UNLOAD_SCOPE_COG, UNLOAD_SCOPE_COG_AND_DEPENDENCIES}


def resolved_unload_scope() -> str:
    """Which state a cog's dependencies should be forced into right before
    its own load/unload cycle -- see the module docstring-level rationale in
    exercise_cogs() for why this needs to be an explicit, driven scenario
    rather than incidental test order."""

    raw = os.environ.get("UNLOAD_SCOPE", UNLOAD_SCOPE_COG).strip()
    if raw not in _VALID_UNLOAD_SCOPES:
        raise RuntimeError(
            f"UNLOAD_SCOPE must be one of {sorted(_VALID_UNLOAD_SCOPES)} (received {raw!r})"
        )
    return raw


def cog_name_from_path(path: Path) -> str:
    cog_name = path.name
    if not cog_name:
        raise RuntimeError(f"Could not derive cog name from {path}")
    return cog_name


async def load_cog(client: JsonRpcClient, cog_name: str) -> None:
    print(f"📥 Loading cog {cog_name}")
    result = await client.request(CORE_LOAD_METHOD, [[cog_name]])
    loaded = (result or {}).get("loaded_packages", [])
    failed = (result or {}).get("failed_packages", [])
    if cog_name not in loaded:
        raise RPCError(f"RPC did not report {cog_name} in loaded_packages: {result}")
    if failed:
        raise RPCError(f"RPC reported failed packages while loading {cog_name}: {failed}")
    print(f"✅ Cog {cog_name} loaded successfully")


async def unload_cog(client: JsonRpcClient, cog_name: str) -> None:
    print(f"📤 Unloading cog {cog_name}")
    result = await client.request(CORE_UNLOAD_METHOD, [[cog_name]])
    unloaded = (result or {}).get("unloaded_packages", [])
    if cog_name not in unloaded:
        raise RPCError(f"RPC did not report {cog_name} in unloaded_packages: {result}")
    print(f"♻️ Cog {cog_name} unloaded successfully")


async def unload_quietly(client: JsonRpcClient, cog_name: str) -> None:
    """Force `cog_name` into an unloaded state before testing it, whether or
    not it happens to already be loaded (e.g. a real cross-cog dependency an
    earlier-tested cog pulled in as a side effect). Red's own CORE__UNLOAD
    reports a name that was never loaded under `notloaded_packages`, not as
    an error (see redbot.core.core_commands.CoreLogic._unload) -- that is a
    valid, expected outcome here, not something to raise on."""

    result = await client.request(CORE_UNLOAD_METHOD, [[cog_name]])
    unloaded = (result or {}).get("unloaded_packages", [])
    not_loaded = (result or {}).get("notloaded_packages", [])
    if cog_name in unloaded:
        print(f"♻️ Pre-unloaded already-loaded cog {cog_name}")
    elif cog_name not in not_loaded:
        raise RPCError(f"RPC gave an unexpected response unloading {cog_name}: {result}")


def normalize_repo_name(name: str) -> str:
    sanitized = name.strip().replace("-", "_")
    try:
        return RepoManager.validate_and_normalize_repo_name(sanitized)
    except downloader_errors.InvalidRepoName as exc:
        raise RuntimeError(f"Invalid downloader repo name: {name}") from exc


def resolved_instance_name() -> str:
    candidate = os.environ.get("RED_INSTANCE_NAME", "").strip()
    return candidate or DEFAULT_INSTANCE_NAME


def ensure_basic_configuration(instance_hint: str) -> str:
    config_path = Path(data_manager.config_file)
    config_data: dict[str, object] = {}
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as fp:
                raw = json.load(fp)
                if isinstance(raw, dict):
                    config_data = raw
        except json.JSONDecodeError:
            config_data = {}
    if not config_data:
        print("ℹ️ No Red instance configuration detected; creating temporary instance for downloader tests")
        data_manager.create_temp_config()
        target = TEMPORARY_INSTANCE_NAME
    else:
        target = instance_hint if instance_hint in config_data else next(iter(config_data))
        if target != instance_hint:
            print(f"ℹ️ Using available instance '{target}' instead of '{instance_hint}'")
    data_manager.load_basic_configuration(target)
    return target


async def setup_repo_manager() -> RepoManager:
    instance = ensure_basic_configuration(resolved_instance_name())
    os.environ.setdefault("RED_INSTANCE_NAME", instance)
    manager = RepoManager()
    await manager.initialize()
    return manager


async def add_test_repo(
    manager: RepoManager, name: str, url: str, branch: str | None
) -> Repo:
    normalized = normalize_repo_name(name)
    if manager.does_repo_exist(normalized):
        print(f"♻️ Removing pre-existing repo {normalized}")
        await manager.delete_repo(normalized)
    print(f"➕ Adding downloader repo {normalized} from {url}")
    try:
        repo = await manager.add_repo(url=url, name=normalized, branch=branch)
    except downloader_errors.DownloaderException as exc:
        raise RuntimeError(f"Failed to add downloader repo {normalized}: {exc}") from exc
    return repo


async def get_cog_install_path() -> Path:
    manager = CogManager()
    path = await manager.install_path()
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_dashboard_stub(install_path: Path) -> None:
    """Copy the fixture dashboard cog directly into Red's cog search path.

    Bypasses Downloader entirely (no Repo/install_cog involved) -- Red's
    CogManager searches `install_path` for loadable packages regardless of
    whether Downloader's own bookkeeping considers something "installed"
    there, so a plain copy is sufficient.
    """

    target = install_path / DASHBOARD_STUB_NAME
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    shutil.copytree(DASHBOARD_STUB_FIXTURE, target)


def ensure_lib_paths() -> Path:
    base = data_manager.cog_data_path(raw_name="Downloader") / "lib"
    shared = base / "cog_shared"
    shared.mkdir(parents=True, exist_ok=True)
    init_path = shared / "__init__.py"
    if not init_path.exists():
        init_path.write_text("", encoding="utf-8")
    return base

def validate_info_json(info_json_path: Path) -> None:
    """Validate info.json against its declared schema.
    
    Args:
        info_json_path: Path to the info.json file to validate
        
    Raises:
        RuntimeError: If validation fails or schema cannot be loaded
    """
    print(f"📋 Validating schema for {info_json_path}")
    
    try:
        with info_json_path.open(encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to read {info_json_path}: {exc}") from exc
    
    schema_url = data.get("$schema")
    if not schema_url:
        print(f"⚠️  No $schema defined in {info_json_path.name}, skipping validation")
        return
    
    # Check cache first to avoid re-downloading the same schema
    if schema_url in _SCHEMA_CACHE:
        print(f"📦 Using cached schema for {schema_url}")
        schema = _SCHEMA_CACHE[schema_url]
    else:
        print(f"🔗 Fetching schema from {schema_url}")
        try:
            with urllib.request.urlopen(schema_url) as response:
                schema = json.loads(response.read().decode())
                _SCHEMA_CACHE[schema_url] = schema
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch schema from {schema_url}: {exc}") from exc
    
    # Create a resolver so $ref inside the schema works (if available)
    if RefResolver is not None:
        resolver = RefResolver(base_uri=schema_url, referrer=schema)
        validator = Draft7Validator(schema, resolver=resolver)
    else:
        validator = Draft7Validator(schema)
    
    try:
        validator.validate(data)
        print(f"✅ Schema validation passed for {info_json_path.name}")
    except ValidationError as e:
        error_path = ".".join(str(p) for p in e.path) if e.path else "root"
        raise RuntimeError(
            f"Schema validation failed for {info_json_path.name}\n"
            f"  Error: {e.message}\n"
            f"  At path: {error_path}"
        ) from e


async def install_cogs_from_repo(
    repo: Repo,
    install_path: Path,
    requirements_path: Path,
    expected_names: Sequence[str],
) -> tuple[List[str], Dict[str, Dict[str, str]]]:
    available = {cog.name: cog for cog in repo.available_cogs}
    missing = [name for name in expected_names if name not in available]
    if missing:
        raise RuntimeError(f"Repository did not contain expected cogs: {', '.join(missing)}")

    installed: List[str] = []
    required_cogs_by_name: Dict[str, Dict[str, str]] = {}
    for name in expected_names:
        cog = available[name]
        
        # Validate info.json schema before installation
        info_json_path = repo.folder_path / name / "info.json"
        if info_json_path.exists():
            validate_info_json(info_json_path)
        else:
            print(f"⚠️  No info.json found at {info_json_path}")
        
        print(f"🧩 Installing cog {name} via downloader")
        target = install_path / name
        if target.exists():
            print(f"♻️ Removing existing cog directory at {target}")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        try:
            await repo.install_cog(cog, target_dir=install_path)
        except downloader_errors.DownloaderException as exc:
            raise RuntimeError(f"Failed to install cog {name} from {repo.name}: {exc}") from exc
        if cog.requirements:
            ok = await repo.install_requirements(cog=cog, target_dir=requirements_path)
            if not ok:
                raise RuntimeError(f"Failed to install requirements for {name}")
        installed.append(name)
        required_cogs_by_name[name] = dict(cog.required_cogs)
    return installed, required_cogs_by_name


def _transitive_dependencies(
    name: str, required_cogs_by_name: Mapping[str, Mapping[str, str]]
) -> List[str]:
    """Every cog `name` (transitively) requires, restricted to cogs actually
    installed in this run -- a `required_cogs` entry pointing at a cog this
    run didn't install has nothing here to unload. Order doesn't matter:
    each dependency is unloaded independently and idempotently."""

    seen: set[str] = set()
    to_visit = list(required_cogs_by_name.get(name, {}).keys())
    while to_visit:
        dep = to_visit.pop()
        if dep in seen or dep not in required_cogs_by_name:
            continue
        seen.add(dep)
        to_visit.extend(required_cogs_by_name.get(dep, {}).keys())
    return sorted(seen)


async def exercise_cogs(
    client: JsonRpcClient,
    names: Iterable[str],
    required_cogs_by_name: Mapping[str, Mapping[str, str]],
    unload_scope: str,
) -> None:
    """Load then unload each cog, having first forced it (and, in the
    `cog-and-dependencies` scope, its dependencies) into an unloaded state --
    see resolved_unload_scope()'s docstring for why this needs to be a
    deliberate, driven scenario. This also incidentally fixes the case a
    dependent cog (e.g. floorplan) already fully loaded a dependency (e.g.
    pixelagents) as a real, synchronous side effect of its own load: by the
    time that dependency's own turn comes, it's forced unloaded first, so it
    always gets a genuine, fresh load/unload test regardless of what an
    earlier cog's turn left loaded."""

    for name in names:
        if unload_scope == UNLOAD_SCOPE_COG_AND_DEPENDENCIES:
            for dep in _transitive_dependencies(name, required_cogs_by_name):
                await unload_quietly(client, dep)
        await unload_quietly(client, name)
        await load_cog(client, name)
        await unload_cog(client, name)


def cleanup_installed_cogs(install_path: Path, names: Iterable[str]) -> None:
    for name in names:
        target = install_path / name
        if target.exists():
            print(f"🧽 Removing installed cog {name} from {target}")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()


async def main_async() -> None:
    cog_paths, port, repo_name, repo_path, repo_url, repo_branch = parse_env()
    if repo_url:
        repo_target = repo_url
    elif repo_path is not None:
        repo_target = str(repo_path)
    else:  # pragma: no cover - defensive guard
        raise RuntimeError("Neither repo_url nor repo_path was provided")
    if cog_paths:
        expected_names: List[str] | None = [cog_name_from_path(path) for path in cog_paths]
    else:
        expected_names = None

    repo_manager = await setup_repo_manager()
    install_path = await get_cog_install_path()
    requirements_path = ensure_lib_paths()
    install_dashboard_stub(install_path)

    unload_scope = resolved_unload_scope()
    print(f"🧪 Unload scope: {unload_scope}")

    repo: Repo | None = None
    installed: List[str] = []
    try:
        repo = await add_test_repo(repo_manager, repo_name, repo_target, repo_branch)
        if expected_names is None:
            expected_names = sorted(cog.name for cog in repo.available_cogs)
            if not expected_names:
                raise RuntimeError(f"Repository {repo.name} does not contain any cogs to test")
            print(
                f"🧭 No COG_PATHS specified; exercising all {len(expected_names)} cogs from {repo.name}"
            )
        assert expected_names is not None
        installed, required_cogs_by_name = await install_cogs_from_repo(
            repo, install_path, requirements_path, expected_names
        )

        rpc_url = RPC_URL_TEMPLATE.format(port=port)
        print(f"🔌 Validating {len(installed)} cog(s) at {rpc_url}")
        async with aiohttp.ClientSession() as session:
            await wait_for_rpc(session, rpc_url)
            async with JsonRpcClient(session, rpc_url) as client:
                await load_cog(client, DASHBOARD_STUB_NAME)
                await exercise_cogs(client, installed, required_cogs_by_name, unload_scope)
                await unload_quietly(client, DASHBOARD_STUB_NAME)

        print("🎯 Downloader installation tests passed")
    finally:
        cleanup_installed_cogs(install_path, [*installed, DASHBOARD_STUB_NAME])
        if repo is not None:
            try:
                await repo_manager.delete_repo(repo.name)
            except downloader_errors.DownloaderException as exc:
                repo_path = repo_manager.repos_folder / repo.name
                print(f"⚠️ Failed to remove downloader repo {repo.name} at {repo_path}: {exc}")
                try:
                    if repo_path.exists():
                        shutil.rmtree(repo_path)
                        print(f"🧽 Removed repo directory {repo_path} via fallback cleanup")
                except Exception as cleanup_exc:  # pragma: no cover - best effort cleanup
                    print(f"⚠️ Failed to remove repo directory {repo_path}: {cleanup_exc}")


def main() -> None:
    try:
        asyncio.run(main_async())
    except Exception as exc:
        print(f"❌ {exc}")
        sys.exit(1)


def discover_local_cog_paths(base: Path) -> List[Path]:
    base = base.expanduser().resolve()
    if not base.exists():
        raise RuntimeError(f"Workspace path does not exist: {base}")
    if not base.is_dir():
        raise RuntimeError(f"Workspace path is not a directory: {base}")
    paths: List[Path] = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "info.json").is_file():
            paths.append(child.resolve())
    return paths


def run_discovery_cli(argv: Sequence[str]) -> None:
    if len(argv) < 3:
        print("Usage: test_downloader_cogs.py --discover-local <workspace>", file=sys.stderr)
        sys.exit(2)
    base = Path(argv[2])
    try:
        cogs = discover_local_cog_paths(base)
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    if not cogs:
        print(f"❌ No cog directories with info.json were found in {base}", file=sys.stderr)
        sys.exit(1)
    root = base.expanduser().resolve()
    rels = [cog.relative_to(root).as_posix() for cog in cogs]
    print(",".join(rels))
    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--discover-local":
        run_discovery_cli(sys.argv)
    main()
