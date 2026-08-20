"""Runtime clone-and-build of the Pixel Agents webview from a pinned commit.

Red's Downloader copies only the `pixelagents/` directory on install and has
no hook to run a frontend build (see docs/red-downloader-submodules.md), so
unlike Pixel Index's `vendor/pixel-agents.commit` cannot be a build-time-only
submodule pin: the pin ships inside `pixelagents/` (`webview_vendor.commit`,
next to this module) and the corresponding source is cloned and built from
*inside* the installed cog, at `cog_load()` time, into Red's per-cog data
directory (`redbot.core.data_manager.cog_data_path`) rather than anywhere
under the installed package tree.

Every function here is synchronous and can be slow (git clone, `npm ci`,
a Vite build) -- callers must always run `ensure_webview_built` through
`asyncio.to_thread`, the same pattern `PixelAgentsBase._load_assets` uses.
This module itself has no Red/discord dependency so it can be exercised
directly in tests and in CI without either installed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/pixel-agents-hq/pixel-agents.git"


def _commit_url(commit: str) -> str:
    # /tree/<sha> rather than /commit/<sha>: lets a reader browse the
    # checkout at that pin, not just view the (irrelevant, single-commit)
    # diff.
    return f"{REPO_URL.removesuffix('.git')}/tree/{commit}"


REQUIRED_TOOLS = ("git", "node", "npm")

_PIN_FILE = Path(__file__).with_name("webview_vendor.commit")
_EMIT_SCRIPT = Path(__file__).parent / "webview_build_scripts" / "emit_decoded_assets.ts"
_BUILT_COMMIT_MARKER = ".built_commit"
_BUILT_BASE_PATH_MARKER = ".built_base_path"

# pixelagents builds ONE bundle that any number of cogs can serve -- it owns
# the distribution, not any particular route it's served from. A Discord
# Dashboard third-party route is derived from the SERVING cog's own name
# (`/third-party/<cog>/static/...`), which pixelagents has no business
# knowing about at build time; baking one specific cog's absolute route
# into the bundle (an earlier version of this file did, hardcoding
# floorplan's) would mean only that one cog could ever serve it correctly,
# defeating the entire point of a shared, centrally-built distribution.
# Building relative instead lets any consuming cog serve the exact same
# bundle under its own route -- each just injects a `<base href>` for its
# own `/third-party/<cog>/static/` at serve time (see
# floorplan/infrastructure/webview.py::WebviewAssetProvider.base_href).
# Bare "./", not "./static/": the "static" path segment is a routing
# artifact of Red Dashboard's third-party static route
# (`/third-party/<cog>/static/<asset_path>`), which the consuming cog's
# injected `<base href>` already ends with -- the physical files this
# module writes into `webview_dist/` (assets/, fonts/, index.html) have no
# "static/" subfolder of their own, so doubling it here would break every
# asset URL instead of fixing them.
RELATIVE_BASE_PATH = "./"

# Vite's `public/` passthrough also copies upstream's raw per-tile PNGs and a
# handful of unrelated promo images into the build output. Nothing in the
# served bundle or in WebviewAssetProvider reads them -- the production
# bundle receives sprites as pre-decoded pixel arrays instead -- so only what
# is actually served gets synced: the entry HTML, the hashed JS/CSS bundle,
# the font it references, and the JSON WebviewAssetProvider reads.


class WebviewBuildError(RuntimeError):
    """The webview could not be built.

    `missing_tools` is non-empty exactly when the failure is a missing
    `git`/`node`/`npm` on this host -- callers use that to decide between a
    generic build-failure message and a specific "install X" one.
    """

    def __init__(self, message: str, *, missing_tools: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing_tools = missing_tools


@dataclass(frozen=True)
class BuildResult:
    rebuilt: bool
    commit: str
    base_path: str


@dataclass(frozen=True)
class BuildOutcome:
    """Human-facing summary of one `build_webview` attempt.

    Bundles what `PixelAgentsBase` needs for the three surfaces a build
    failure has to reach: the `[p]pixelagents status` embed, the owner DM,
    and the rebuild command's own reply -- so none of that formatting has to
    live in the adapter layer.
    """

    ok: bool
    status_line: str
    error: str | None = None
    missing_tools: tuple[str, ...] = ()


def pinned_commit() -> str:
    """The upstream commit `webview_dist` should be built from."""

    return _PIN_FILE.read_text(encoding="utf-8").strip()


def missing_tools() -> tuple[str, ...]:
    """Required tools not found on PATH, checked before touching the network."""

    return tuple(tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None)


def is_up_to_date(webview_dist: Path, commit: str, base_path: str) -> bool:
    """Whether `webview_dist` already matches both `commit` and `base_path`.

    Both matter: a rebuild-worthy change isn't only a new commit -- Vite
    bakes `base_path` into every asset URL at build time too, so a change to
    the build convention itself (e.g. this codebase's original hardcoded
    `/third-party/pixelagents/`, later `/third-party/floorplan/`, now
    `RELATIVE_BASE_PATH`) has to invalidate the cache exactly the same way a
    commit change does -- a host whose pinned commit hadn't also changed
    would otherwise keep serving a build with the old convention baked in
    indefinitely, 404ing against whatever route actually tried to serve it.
    """

    if not (webview_dist / "index.html").is_file():
        return False
    return built_commit(webview_dist) == commit and built_base_path(webview_dist) == base_path


def built_commit(webview_dist: Path) -> str | None:
    """The commit `webview_dist` was actually built from, if known.

    Used by `PixelAgentsBase.webview_bundle_status()` so floorplan can tell
    a rebuild-to-a-different-commit apart from "still the same bundle" --
    same marker file `is_up_to_date` checks, just exposed for a reader
    outside this module.
    """

    return _read_marker(webview_dist / _BUILT_COMMIT_MARKER)


def built_base_path(webview_dist: Path) -> str | None:
    """The `--base` `webview_dist` was actually built with, if known.

    Lets a consumer (or an operator debugging a 404 on stale asset URLs)
    confirm the on-disk build actually matches the path it's being served
    at -- see `is_up_to_date`.
    """

    return _read_marker(webview_dist / _BUILT_BASE_PATH_MARKER)


def _read_marker(marker: Path) -> str | None:
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def ensure_webview_built(
    cog_data_dir: Path,
    *,
    commit: str | None = None,
    logger: logging.Logger | None = None,
    force: bool = False,
) -> BuildResult:
    """Build `<cog_data_dir>/webview_dist` if it is missing or stale.

    Builds from `commit` when given (an admin-set override -- see
    `[p]pixelagents webview setcommit`), otherwise from the source-pinned
    `pinned_commit()`. Always builds relative (`RELATIVE_BASE_PATH`) -- see
    its own docstring for why that isn't a caller-supplied option.

    Raises `WebviewBuildError` if the commit cannot be built -- including
    when a required tool is missing -- and never partially overwrites a
    previously working `webview_dist`.
    """

    log = logger or logging.getLogger(__name__)
    commit = commit or pinned_commit()
    webview_dist = cog_data_dir / "webview_dist"

    if not force and is_up_to_date(webview_dist, commit, RELATIVE_BASE_PATH):
        return BuildResult(rebuilt=False, commit=commit, base_path=RELATIVE_BASE_PATH)

    missing = missing_tools()
    if missing:
        raise WebviewBuildError(
            f"missing required tool(s) to build the webview: {', '.join(missing)}",
            missing_tools=missing,
        )

    vendor_dir = cog_data_dir / "vendor" / "pixel-agents"
    _checkout(vendor_dir, commit, log)
    _install_dependencies(vendor_dir, log)
    build_out_dir = _build_bundle(vendor_dir, log)
    _emit_decoded_assets(vendor_dir, build_out_dir, log)
    _sync_dist(build_out_dir, webview_dist, commit, RELATIVE_BASE_PATH, log)
    return BuildResult(rebuilt=True, commit=commit, base_path=RELATIVE_BASE_PATH)


def build_webview(
    cog_data_dir: Path,
    *,
    logger: logging.Logger,
    force: bool = False,
    commit: str | None = None,
) -> BuildOutcome:
    """`ensure_webview_built`, but reports rather than raises.

    The one entry point PixelAgentsBase calls, from both `cog_load` and
    `[p]pixelagents webview rebuild` -- never raises, so neither caller needs
    its own try/except to stay safe to await unconditionally.
    """

    try:
        result = ensure_webview_built(cog_data_dir, commit=commit, logger=logger, force=force)
    except WebviewBuildError as exc:
        logger.error("pixelagents: could not build webview assets: %s", exc)
        return BuildOutcome(
            ok=False,
            status_line=f"⚠️ Could not build the webview: {exc}",
            error=str(exc),
            missing_tools=exc.missing_tools,
        )
    except Exception as exc:  # must never break cog_load or the rebuild command
        logger.exception("pixelagents: unexpected error building webview assets")
        return BuildOutcome(
            ok=False, status_line=f"⚠️ Could not build the webview: {exc}", error=str(exc)
        )

    verb = "built" if result.rebuilt else "already up to date"
    short = result.commit[:7]
    link = f"[pixel-agents-hq/pixel-agents@{short}]({_commit_url(result.commit)})"
    return BuildOutcome(ok=True, status_line=f"✅ Webview {verb} ({link}).")


def owner_notification_for(outcome: BuildOutcome) -> str:
    """DM text for `Red.send_to_owners` when `outcome.ok` is False."""

    if outcome.missing_tools:
        return (
            "⚠️ Pixel Agents could not build the office webview: missing "
            f"{', '.join(outcome.missing_tools)} on this bot's host. Install the missing "
            "tool(s), then run `[p]pixelagents webview rebuild`."
        )
    return (
        f"⚠️ Pixel Agents could not build the office webview: {outcome.error}\n"
        "Run `[p]pixelagents webview rebuild` after investigating, or check the logs."
    )


def _run(args: list[str], *, cwd: Path | None, timeout: float, log: logging.Logger) -> None:
    log.info("pixelagents: webview build: %s", " ".join(args))
    try:
        subprocess.run(
            args,
            cwd=cwd,
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        # Defense in depth: ensure_webview_built already checked PATH before
        # calling here, but a tool can vanish between that check and this
        # call (a mid-build `npm uninstall -g`, a broken PATH entry, ...).
        raise WebviewBuildError(
            f"required tool not found: {args[0]}", missing_tools=(args[0],)
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise WebviewBuildError(
            f"`{' '.join(args)}` failed (exit {exc.returncode}): {stderr[-2000:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise WebviewBuildError(f"`{' '.join(args)}` timed out after {timeout}s") from exc


def _checkout(vendor_dir: Path, commit: str, log: logging.Logger) -> None:
    if not (vendor_dir / ".git").exists():
        vendor_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", REPO_URL, str(vendor_dir)], cwd=None, timeout=300, log=log)
    else:
        _run(["git", "-C", str(vendor_dir), "fetch", "origin"], cwd=None, timeout=300, log=log)
    _run(
        ["git", "-C", str(vendor_dir), "checkout", "--detach", commit],
        cwd=None,
        timeout=60,
        log=log,
    )


def _install_dependencies(vendor_dir: Path, log: logging.Logger) -> None:
    # Scoped to the webview-ui workspace, and --ignore-scripts: the root
    # package.json's devDependencies also cover the VS Code extension, its
    # e2e suite, and its docs tooling (Playwright, @vscode/test-electron,
    # allure, ...), none of which the webview build needs, and lifecycle
    # scripts (husky's `prepare`) have no business running unattended on an
    # end user's bot host.
    _run(
        [
            "npm",
            "ci",
            "--prefix",
            str(vendor_dir),
            "--workspace=webview-ui",
            "--ignore-scripts",
        ],
        cwd=None,
        timeout=600,
        log=log,
    )


def _build_bundle(vendor_dir: Path, log: logging.Logger) -> Path:
    # Relative (`RELATIVE_BASE_PATH`), not rooted at any one cog's Dashboard
    # route -- pixelagents owns the distribution, not where it's served, so
    # the bundle must stay servable by whichever cog(s) end up consuming it.
    # This is a subpath-relative build (`--base`), supported upstream and
    # covered by its own build-subpath test -- everything else (outDir,
    # emptyOutDir) still comes from webview-ui/vite.config.ts.
    vite_bin = vendor_dir / "node_modules" / ".bin" / "vite"
    _run(
        [str(vite_bin), "build", "--base", RELATIVE_BASE_PATH],
        cwd=vendor_dir / "webview-ui",
        timeout=180,
        log=log,
    )
    return vendor_dir / "dist" / "webview"


def _emit_decoded_assets(vendor_dir: Path, build_out_dir: Path, log: logging.Logger) -> None:
    tsx_bin = vendor_dir / "node_modules" / ".bin" / "tsx"
    assets_dir = vendor_dir / "webview-ui" / "public" / "assets"
    out_dir = build_out_dir / "assets" / "decoded"
    _run(
        [str(tsx_bin), str(_EMIT_SCRIPT), str(vendor_dir), str(assets_dir), str(out_dir)],
        cwd=None,
        timeout=120,
        log=log,
    )


def _sync_dist(
    build_out_dir: Path, webview_dist: Path, commit: str, base_path: str, log: logging.Logger
) -> None:
    if webview_dist.exists():
        shutil.rmtree(webview_dist)
    (webview_dist / "assets" / "decoded").mkdir(parents=True)
    (webview_dist / "fonts").mkdir(parents=True)

    shutil.copy2(build_out_dir / "index.html", webview_dist / "index.html")
    for pattern in ("*.js", "*.css"):
        for src in (build_out_dir / "assets").glob(pattern):
            shutil.copy2(src, webview_dist / "assets" / src.name)
    for name in ("furniture-catalog.json", "asset-index.json"):
        shutil.copy2(build_out_dir / "assets" / name, webview_dist / "assets" / name)
    for src in (build_out_dir / "assets" / "decoded").glob("*.json"):
        shutil.copy2(src, webview_dist / "assets" / "decoded" / src.name)
    for src in (build_out_dir / "fonts").glob("*"):
        shutil.copy2(src, webview_dist / "fonts" / src.name)

    asset_index = json.loads((build_out_dir / "assets" / "asset-index.json").read_text("utf-8"))
    default_layout = asset_index.get("defaultLayout")
    if not default_layout:
        raise WebviewBuildError("built asset-index.json has no defaultLayout")
    shutil.copy2(
        build_out_dir / "assets" / default_layout, webview_dist / "assets" / default_layout
    )

    (webview_dist / _BUILT_COMMIT_MARKER).write_text(commit + "\n", encoding="utf-8")
    (webview_dist / _BUILT_BASE_PATH_MARKER).write_text(base_path + "\n", encoding="utf-8")
    log.info(
        "pixelagents: webview built from pixel-agents-hq/pixel-agents@%s (base %s)",
        commit,
        base_path,
    )
