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

import contextlib
import fcntl
import hashlib
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .furniture_style_builder import MANIFEST_SCHEMA_VERSION, build_furniture_style_manifest


def _commit_url(commit: str) -> str:
    # /tree/<sha> rather than /commit/<sha>: lets a reader browse the
    # checkout at that pin, not just view the (irrelevant, single-commit)
    # diff.
    return f"{pinned_repo_url().removesuffix('.git')}/tree/{commit}"


REQUIRED_TOOLS = ("git", "node", "npm")

_PIN_FILE = Path(__file__).with_name("webview_vendor.commit")
# The repository the pinned commit is cloned from. Upstream itself whenever
# the pin is on upstream `main`; a branch on a fork while the pin has to
# carry upstream PRs that haven't merged yet, because GitHub only serves a
# commit from the repository (or pull-request ref) that actually holds it --
# `git fetch <upstream> <sha>` refuses a fork-only commit ("not our ref").
# See .claude/skills/advance-vendor/SKILL.md.
_REPO_FILE = Path(__file__).with_name("webview_vendor.repo")
_EMIT_SCRIPT = Path(__file__).parent / "webview_build_scripts" / "emit_decoded_assets.ts"
_BUILT_COMMIT_MARKER = ".built_commit"
_BUILT_BASE_PATH_MARKER = ".built_base_path"
_BUILT_MANIFEST_VERSION_MARKER = ".built_manifest_version"

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
# cctv/infrastructure/webview.py::WEBVIEW_BASE_PATH).
# Bare "./", not "./static/": the "static" path segment is a routing
# artifact of Red Dashboard's third-party static route
# (`/third-party/<cog>/static/<asset_path>`), which the consuming cog's
# injected `<base href>` already ends with -- the physical files this
# module writes into `webview_dist/` (assets/, fonts/, index.html) have no
# "static/" subfolder of their own, so doubling it here would break every
# asset URL instead of fixing them.
RELATIVE_BASE_PATH = "./"

# Upstream's MIT license, copied beside index.html in every build.
LICENSE_FILENAME = "LICENSE.txt"

# Vite's `public/` passthrough also copies upstream's raw per-tile PNGs and a
# handful of unrelated promo images into the build output. Nothing in the
# served bundle or in WebviewAssetProvider reads them -- the production
# bundle receives sprites as pre-decoded pixel arrays instead -- so only what
# is actually served gets synced: the entry HTML, the web-app manifest and
# icons it links, the hashed JS/CSS bundle, the font it references, and the
# JSON WebviewAssetProvider reads.


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


def pinned_repo_url() -> str:
    """The git URL `pinned_commit()` (and any admin override) is cloned from."""

    return _REPO_FILE.read_text(encoding="utf-8").strip()


def missing_tools() -> tuple[str, ...]:
    """Required tools not found on PATH, checked before touching the network."""

    return tuple(tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None)


def is_up_to_date(webview_dist: Path, commit: str, base_path: str) -> bool:
    """Whether `webview_dist` already matches both `commit` and `base_path`,
    and was built by a `_sync_dist` that knows about every artifact this
    version of the pipeline is supposed to produce.

    Both `commit` and `base_path` matter: a rebuild-worthy change isn't only
    a new commit -- Vite bakes `base_path` into every asset URL at build
    time too, so a change to the build convention itself (e.g. this
    codebase's original hardcoded `/third-party/pixelagents/`, later
    `/third-party/floorplan/`, now `RELATIVE_BASE_PATH`) has to invalidate
    the cache exactly the same way a commit change does -- a host whose
    pinned commit hadn't also changed would otherwise keep serving a build
    with the old convention baked in indefinitely, 404ing against whatever
    route actually tried to serve it.

    The `furniture-styles.json` existence check exists for the identical
    reason, but catching an *upgrade* rather than a config change: a host
    that already has an up-to-date `webview_dist/` from before
    `_build_furniture_styles` existed would otherwise never regenerate it
    on its own -- `commit` and `base_path` genuinely haven't changed --
    silently leaving every real furniture asset id unrecognized by
    architect's style manifest lookup (decoded as a passthrough "foreign"
    entry, reporting zero furniture and zero seats even in a layout that
    has plenty of both) until an operator happened to run
    `[p]pixelagents webview rebuild --force`. Real incident, not a
    hypothetical -- see the git history on this check.

    The `MANIFEST_SCHEMA_VERSION` check below is the same class of
    incident one level deeper: `furniture-styles.json` can *exist* and
    still be shaped for an older version of
    `build_furniture_style_manifest`'s own output schema (a field
    added/removed/renamed on styles or facings) if `commit`/`base_path`
    haven't changed since a host last built. Left unchecked, every
    consumer parsing that stale JSON against the current schema crashes
    outright (`FurnitureStyleManifest.from_raw` indexing a `str` as a
    `dict`, not a quiet degradation like the missing-file case above) --
    also a real incident, the one that made this check exist.

    A build without `LICENSE_FILENAME` (upstream's license) is stale for the
    same upgrade reason, so it is rebuilt once to carry it.
    """

    if not (webview_dist / "index.html").is_file():
        return False
    if not (webview_dist / LICENSE_FILENAME).is_file():
        return False
    if not (webview_dist / "assets" / "furniture-styles.json").is_file():
        return False
    if built_manifest_version(webview_dist) != MANIFEST_SCHEMA_VERSION:
        return False
    return built_commit(webview_dist) == commit and built_base_path(webview_dist) == base_path


def built_commit(webview_dist: Path) -> str | None:
    """The commit `webview_dist` was actually built from, if known.

    Used by `PixelAgentsBase.webview_bundle_status()` so CCTV can tell
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


def built_manifest_version(webview_dist: Path) -> int | None:
    """The `MANIFEST_SCHEMA_VERSION` `furniture-styles.json` in
    `webview_dist` was actually generated against, if known -- `None` for
    a bundle built before this marker existed (equally a mismatch,
    handled by `is_up_to_date`'s `!=` comparison with no special-casing
    needed)."""

    raw = _read_marker(webview_dist / _BUILT_MANIFEST_VERSION_MARKER)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def load_bundled_default_layout(webview_dist: Path) -> dict[str, object]:
    """Read the build-selected default layout without accepting traversal."""

    assets = webview_dist / "assets"
    index = json.loads((assets / "asset-index.json").read_text(encoding="utf-8"))
    if not isinstance(index, dict):
        raise ValueError("asset index is not an object")
    name = index.get("defaultLayout")
    if not isinstance(name, str) or not name:
        raise ValueError("asset index has no defaultLayout")
    candidate = (assets / name).resolve()
    if candidate.parent != assets.resolve():
        raise ValueError("defaultLayout resolves outside the assets directory")
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("bundled default layout is not an object")
    return cast("dict[str, object]", raw)


def _read_marker(marker: Path) -> str | None:
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@contextlib.contextmanager
def _build_lock(cog_data_dir: Path, name: str = "webview_build.lock") -> Iterator[None]:
    """Serialize concurrent builds against the same `vendor_dir` checkout.

    A real OS-level lock, not an `asyncio.Lock`: callers run this (via
    `asyncio.to_thread`) on a worker thread that can outlive the asyncio
    Task awaiting it -- Task cancellation (a caller's own timeout, a
    supervisor shutdown, ...) stops the coroutine, not the thread or the
    `git`/`npm`/`vite` subprocess already running inside it. An asyncio lock
    would release the moment the coroutine unwinds, while the orphaned
    subprocess keeps running and can still collide with a later, unrelated
    build attempt -- this is exactly the incident this lock exists to
    prevent (see docs/dependency-loading.md). `flock` is held by the OS
    against the open file descriptor for as long as the underlying process
    is alive, so a second caller queues instead of racing, no matter what
    happened to the first caller's asyncio Task.
    """

    cog_data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cog_data_dir / name
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


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

    log = logger or logging.getLogger(f"red.pixel-agents-cogs.{__name__}")
    commit = commit or pinned_commit()
    webview_dist = cog_data_dir / "webview_dist"

    if not force and is_up_to_date(webview_dist, commit, RELATIVE_BASE_PATH):
        return BuildResult(rebuilt=False, commit=commit, base_path=RELATIVE_BASE_PATH)

    with _build_lock(cog_data_dir):
        # Re-check: a caller queued behind this lock may find the build it
        # was waiting on already finished the exact work it needed.
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
        _sync_dist(
            build_out_dir,
            webview_dist,
            commit,
            RELATIVE_BASE_PATH,
            log,
            license_path=vendor_dir / "LICENSE",
        )
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
    repo = pinned_repo_url().removesuffix(".git").removeprefix("https://github.com/")
    link = f"[{repo}@{short}]({_commit_url(result.commit)})"
    status_line = f"✅ Webview {verb} ({link})."
    if activity_requested(cog_data_dir):
        # A rebuilt webview replaced `webview_dist`, Activity page included.
        activity = build_activity(cog_data_dir, logger=logger, force=result.rebuilt)
        status_line = f"{status_line}\n{activity.status_line}"
    return BuildOutcome(ok=True, status_line=status_line)


def owner_notification_for(outcome: BuildOutcome) -> str:
    """DM text for `Red.send_to_owners` when `outcome.ok` is False.

    Leaves the literal `[p]` placeholder in place -- this is a pure,
    framework-agnostic function with no `ctx`/bot to resolve a real prefix
    from, and substituting one is corridor's job, not this infrastructure
    module's. The caller passes this through
    `corridor.substitute_default_prefix(...)` before sending it.
    """

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
    repo_url = pinned_repo_url()
    if not (vendor_dir / ".git").exists():
        vendor_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", repo_url, str(vendor_dir)], cwd=None, timeout=300, log=log)
    else:
        # A checkout cloned before the pin moved repositories still points
        # `origin` at the old one, which doesn't have the new commit.
        _run(
            ["git", "-C", str(vendor_dir), "remote", "set-url", "origin", repo_url],
            cwd=None,
            timeout=60,
            log=log,
        )
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
    # end user's bot host. --no-audit/--no-fund, as the pixatar build does:
    # the registry advisory lookup is a second network round trip nobody on
    # a bot host reads, and it can stall the first cog load on its own.
    _run(
        [
            "npm",
            "ci",
            "--prefix",
            str(vendor_dir),
            "--workspace=webview-ui",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
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
    build_out_dir: Path,
    webview_dist: Path,
    commit: str,
    base_path: str,
    log: logging.Logger,
    *,
    license_path: Path,
) -> None:
    # Pixel Agents is MIT: the bundle CCTV serves is a copy of it, so the
    # upstream license travels with it (`LICENSE.txt`, served beside
    # index.html). A checkout without one is not built from.
    if not license_path.is_file():
        raise WebviewBuildError(f"the Pixel Agents checkout has no LICENSE ({license_path})")
    if webview_dist.exists():
        shutil.rmtree(webview_dist)
    (webview_dist / "assets" / "decoded").mkdir(parents=True)
    (webview_dist / "fonts").mkdir(parents=True)

    shutil.copy2(build_out_dir / "index.html", webview_dist / "index.html")
    shutil.copy2(license_path, webview_dist / LICENSE_FILENAME)
    # index.html links both: the web-app manifest and the icon it and the
    # page's own <link rel="icon"> name.
    shutil.copy2(build_out_dir / "manifest.webmanifest", webview_dist / "manifest.webmanifest")
    shutil.copytree(build_out_dir / "icons", webview_dist / "icons")
    for pattern in ("*.js", "*.css"):
        for src in (build_out_dir / "assets").glob(pattern):
            shutil.copy2(src, webview_dist / "assets" / src.name)
    for name in ("furniture-catalog.json", "asset-index.json"):
        shutil.copy2(build_out_dir / "assets" / name, webview_dist / "assets" / name)
    _build_furniture_styles(webview_dist / "assets" / "furniture-catalog.json", webview_dist)
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
    (webview_dist / _BUILT_MANIFEST_VERSION_MARKER).write_text(
        f"{MANIFEST_SCHEMA_VERSION}\n", encoding="utf-8"
    )
    log.info(
        "pixelagents: webview built from %s@%s (base %s)",
        pinned_repo_url(),
        commit,
        base_path,
    )


def _build_furniture_styles(catalog_path: Path, webview_dist: Path) -> None:
    """Derive `assets/furniture-styles.json` from the just-copied catalog.

    A generated artifact, not a hand-maintained table -- see
    `furniture_style_builder.build_furniture_style_manifest` and
    `docs/architect-semantic-ir-design.md` section 6.4. Runs on every
    build, right after `furniture-catalog.json` is synced in, so it always
    matches whatever `pixel-agents` commit this instance actually vendors.
    """

    catalog = json.loads(catalog_path.read_text("utf-8"))
    manifest = build_furniture_style_manifest(catalog)
    (webview_dist / "assets" / "furniture-styles.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


# The Discord Activity build: the same bundle, plus a page that runs the
# Embedded App SDK handshake before the webview starts. It is built only once
# a consumer asks for it (`build_activity`, e.g. when CCTV's Activity is
# switched on), and again after every webview rebuild from then on. A failed
# Activity build never fails the browser build.
#
# `activity.html` is `index.html` with every module script made inert
# (`DEFERRED_SCRIPT_TYPE`) and two classic scripts added: the consumer's own
# `activity-config.js` (it sets `window.PIXEL_AGENTS_ACTIVITY`, see
# webview_build_scripts/activity/bootstrap.ts), then `activity/bootstrap.js`,
# which runs the handshake and OAuth and then releases the module scripts.
# No inline script, so the page runs under a strict `script-src`.
ACTIVITY_PAGE = "activity.html"
ACTIVITY_CONFIG_SCRIPT = "activity-config.js"
ACTIVITY_BOOTSTRAP = "activity/bootstrap.js"
ACTIVITY_LICENSES = "activity/THIRD_PARTY_LICENSES.txt"
DEFERRED_SCRIPT_TYPE = "text/x-pixel-agents-deferred"
# Bump when the Activity build's output changes shape.
ACTIVITY_BUILD_VERSION = 1
ACTIVITY_REQUIRED_TOOLS = ("node", "npm")
_ACTIVITY_SOURCE_DIR = Path(__file__).parent / "webview_build_scripts" / "activity"
_ACTIVITY_SOURCES = ("package.json", "package-lock.json", "bootstrap.ts")
_ACTIVITY_BUILD_DIR = "activity_build"
# Present while a consumer wants the Activity build (see `activity_requested`).
_ACTIVITY_REQUESTED_MARKER = ".requested"
_BUILT_ACTIVITY_MARKER = ".built_activity"
_INSTALLED_LOCK_MARKER = ".installed_lock"
# Build tools in the lockfile, not shipped in the bundle (no license notice).
_BUILD_ONLY_PACKAGES = ("esbuild", "@esbuild/")
_MODULE_SCRIPT = re.compile(r"<script\b([^>]*?)\stype=([\"'])module\2([^>]*)>", re.IGNORECASE)
_HEAD_END = re.compile(r"</head\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class ActivityBuildOutcome:
    ok: bool
    status_line: str
    error: str | None = None


def activity_html(index_source: str) -> str:
    """`activity.html` from the built `index.html` (see `ACTIVITY_PAGE`)."""

    inert, count = _MODULE_SCRIPT.subn(
        lambda match: (
            f"<script{match[1]} type={match[2]}{DEFERRED_SCRIPT_TYPE}{match[2]}{match[3]}>"
        ),
        index_source,
    )
    if count == 0:
        raise WebviewBuildError("the built index.html has no module script to defer")
    head_end = _HEAD_END.search(inert)
    if head_end is None:
        raise WebviewBuildError("the built index.html has no </head>")
    scripts = (
        f'<script src="./{ACTIVITY_CONFIG_SCRIPT}"></script>\n'
        f'<script src="./{ACTIVITY_BOOTSTRAP}"></script>\n'
    )
    return inert[: head_end.start()] + scripts + inert[head_end.start() :]


def activity_requested(cog_data_dir: Path) -> bool:
    """Whether a consumer wants the Activity build on this host: set by
    `ensure_activity_built`, cleared by `cancel_activity`."""

    return (cog_data_dir / _ACTIVITY_BUILD_DIR / _ACTIVITY_REQUESTED_MARKER).is_file()


def cancel_activity(cog_data_dir: Path) -> bool:
    """Stop rebuilding the Activity with every webview build; whether it had
    been requested. The installed npm packages stay, so asking again later
    doesn't reinstall them."""

    marker = cog_data_dir / _ACTIVITY_BUILD_DIR / _ACTIVITY_REQUESTED_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        return False
    return True


def _activity_marker(webview_dist: Path) -> str | None:
    commit = built_commit(webview_dist)
    return f"{ACTIVITY_BUILD_VERSION}:{commit}" if commit is not None else None


def activity_is_up_to_date(webview_dist: Path) -> bool:
    """Whether `webview_dist` holds an Activity build of its current webview."""

    expected = _activity_marker(webview_dist)
    return (
        expected is not None
        and (webview_dist / ACTIVITY_PAGE).is_file()
        and (webview_dist / ACTIVITY_BOOTSTRAP).is_file()
        and _read_marker(webview_dist / _BUILT_ACTIVITY_MARKER) == expected
    )


def ensure_activity_built(
    cog_data_dir: Path, *, logger: logging.Logger | None = None, force: bool = False
) -> bool:
    """Add the Activity build to `<cog_data_dir>/webview_dist`; whether it was
    (re)built. The webview must already be built. Raises `WebviewBuildError`."""

    log = logger or logging.getLogger(f"red.pixel-agents-cogs.{__name__}")
    webview_dist = cog_data_dir / "webview_dist"
    build_dir = cog_data_dir / _ACTIVITY_BUILD_DIR
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / _ACTIVITY_REQUESTED_MARKER).write_text("1\n", encoding="utf-8")
    with _build_lock(cog_data_dir):
        if not (webview_dist / "index.html").is_file():
            raise WebviewBuildError("the webview isn't built yet")
        if not force and activity_is_up_to_date(webview_dist):
            return False
        missing = tuple(tool for tool in ACTIVITY_REQUIRED_TOOLS if shutil.which(tool) is None)
        if missing:
            raise WebviewBuildError(
                f"missing required tool(s) to build the Activity: {', '.join(missing)}",
                missing_tools=missing,
            )
        for name in _ACTIVITY_SOURCES:
            shutil.copy2(_ACTIVITY_SOURCE_DIR / name, build_dir / name)
        _install_activity_dependencies(build_dir, log)
        bundle = _bundle_activity_bootstrap(build_dir, log)
        _sync_activity(build_dir, bundle, webview_dist)
        log.info("pixelagents: Activity build added to the webview")
        return True


def _install_activity_dependencies(build_dir: Path, log: logging.Logger) -> None:
    lock = (build_dir / "package-lock.json").read_bytes()
    digest = hashlib.sha256(lock).hexdigest()
    marker = build_dir / _INSTALLED_LOCK_MARKER
    if (build_dir / "node_modules").is_dir() and _read_marker(marker) == digest:
        return
    # --ignore-scripts, as for the webview: nothing runs at install time.
    _run(
        ["npm", "ci", "--prefix", str(build_dir), "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=None,
        timeout=600,
        log=log,
    )
    marker.write_text(digest + "\n", encoding="utf-8")


def _bundle_activity_bootstrap(build_dir: Path, log: logging.Logger) -> Path:
    out = build_dir / "out" / "bootstrap.js"
    _run(
        [
            str(build_dir / "node_modules" / ".bin" / "esbuild"),
            "bootstrap.ts",
            "--bundle",
            "--format=iife",
            "--minify",
            "--target=es2020",
            "--legal-comments=eof",
            f"--outfile={out}",
        ],
        cwd=build_dir,
        timeout=120,
        log=log,
    )
    return out


def _third_party_licenses(build_dir: Path) -> str:
    """The license of every package the bootstrap bundles, from the lockfile."""

    lock = json.loads((build_dir / "package-lock.json").read_text("utf-8"))
    sections = []
    for path, entry in sorted(cast("dict[str, dict[str, object]]", lock["packages"]).items()):
        name = path.rsplit("node_modules/", 1)[-1]
        if not path or name.startswith(_BUILD_ONLY_PACKAGES):
            continue
        heading = f"{name}@{entry.get('version', '?')} ({entry.get('license', 'see package')})"
        package_dir = build_dir / path
        texts = [
            license_file.read_text("utf-8", errors="replace").strip()
            for license_file in sorted(package_dir.glob("LICEN[CS]E*"))
            if license_file.is_file()
        ]
        sections.append("\n\n".join([f"== {heading} ==", *texts]))
    return "\n\n".join(sections) + "\n"


def _sync_activity(build_dir: Path, bundle: Path, webview_dist: Path) -> None:
    page = activity_html((webview_dist / "index.html").read_text("utf-8"))
    (webview_dist / "activity").mkdir(exist_ok=True)
    shutil.copy2(bundle, webview_dist / ACTIVITY_BOOTSTRAP)
    (webview_dist / ACTIVITY_LICENSES).write_text(_third_party_licenses(build_dir), "utf-8")
    (webview_dist / ACTIVITY_PAGE).write_text(page, encoding="utf-8")
    marker = _activity_marker(webview_dist)
    assert marker is not None  # the webview is built: it has a commit marker
    (webview_dist / _BUILT_ACTIVITY_MARKER).write_text(marker + "\n", encoding="utf-8")


def build_activity(
    cog_data_dir: Path, *, logger: logging.Logger, force: bool = False
) -> ActivityBuildOutcome:
    """`ensure_activity_built`, but reports rather than raises."""

    try:
        rebuilt = ensure_activity_built(cog_data_dir, logger=logger, force=force)
    except Exception as exc:  # never breaks the browser build or a command
        logger.error("pixelagents: could not build the Activity: %s", exc)
        return ActivityBuildOutcome(
            ok=False, status_line=f"⚠️ Could not build the Activity: {exc}", error=str(exc)
        )
    verb = "built" if rebuilt else "already up to date"
    return ActivityBuildOutcome(ok=True, status_line=f"✅ Activity {verb}.")
