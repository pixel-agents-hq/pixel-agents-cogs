"""Runtime clone-and-build of the pixatar CLI from a pinned Pixel Index commit.

pixatar (`@pixel-index/pixatar`, `packages/pixatar` in pixel-agents-hq/index)
turns a seed into a Pixel Agents character profile: free text deterministically,
or a `px.` seed code exactly. It is not published to any registry, so -- like the
webview (see `webview_build.py`) -- it is cloned at the commit pinned in
`pixatar_vendor.commit` and built into Red's per-cog data directory at
`cog_load()` time.

It draws with the wardrobe of the Pixel Agents commit it bakes in (its
`VENDOR.commit`), which has to be one the vendored webview
(`webview_vendor.commit`) contains, or a profile could name parts the office
doesn't have. See .claude/skills/advance-vendor/SKILL.md.

Synchronous and slow (git clone, `npm ci`, a tsup build): callers run
`build_pixatar` through `asyncio.to_thread`.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .webview_build import _build_lock

REPO_URL = "https://github.com/pixel-agents-hq/index.git"
REQUIRED_TOOLS = ("git", "node", "npm")
# The CLI the Python client runs, e.g. `node /path/to/pixatar/dist/cli.js`;
# when set, nothing is built.
CLI_ENV_VAR = "PIXATAR_CLI"
WORKSPACE = "@pixel-index/pixatar"

_PIN_FILE = Path(__file__).with_name("pixatar_vendor.commit")
_BUILT_COMMIT_MARKER = ".built_commit"


class PixatarBuildError(RuntimeError):
    """The pixatar CLI could not be built; `missing_tools` names absent
    `git`/`node`/`npm`, as `WebviewBuildError` does."""

    def __init__(self, message: str, *, missing_tools: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing_tools = missing_tools


@dataclass(frozen=True)
class PixatarBuildOutcome:
    ok: bool
    status_line: str
    command: tuple[str, ...] = ()
    error: str | None = None
    missing_tools: tuple[str, ...] = ()


def pinned_commit() -> str:
    """The Pixel Index commit the pixatar CLI is built from."""

    return _PIN_FILE.read_text(encoding="utf-8").strip()


def _commit_url(commit: str) -> str:
    return f"{REPO_URL.removesuffix('.git')}/tree/{commit}"


def pixatar_dir(cog_data_dir: Path) -> Path:
    return cog_data_dir / "pixatar"


def cli_path(cog_data_dir: Path) -> Path:
    return pixatar_dir(cog_data_dir) / "index" / "packages" / "pixatar" / "dist" / "cli.js"


def built_commit(cog_data_dir: Path) -> str | None:
    try:
        marker = pixatar_dir(cog_data_dir) / _BUILT_COMMIT_MARKER
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_up_to_date(cog_data_dir: Path, commit: str) -> bool:
    return cli_path(cog_data_dir).is_file() and built_commit(cog_data_dir) == commit


def ensure_pixatar_built(
    cog_data_dir: Path, *, logger: logging.Logger | None = None, force: bool = False
) -> tuple[bool, tuple[str, ...]]:
    """Build the pinned CLI if it is missing or stale; return whether it was
    rebuilt and the argv that runs it. Raises `PixatarBuildError`."""

    log = logger or logging.getLogger(__name__)
    commit = pinned_commit()
    node = shutil.which("node") or "node"
    command = (node, str(cli_path(cog_data_dir)))
    if not force and is_up_to_date(cog_data_dir, commit):
        return False, command

    with _build_lock(cog_data_dir, "pixatar_build.lock"):
        if not force and is_up_to_date(cog_data_dir, commit):
            return False, command
        missing = tuple(tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None)
        if missing:
            raise PixatarBuildError(
                f"missing required tool(s) to build pixatar: {', '.join(missing)}",
                missing_tools=missing,
            )
        root = pixatar_dir(cog_data_dir)
        index_dir = root / "index"
        (root / _BUILT_COMMIT_MARKER).unlink(missing_ok=True)
        _checkout(index_dir, commit, log)
        # Scripts off for the install (the root's husky `prepare` has no
        # business on a bot host), on for the build: its `prebuild` bakes the
        # wardrobe from the vendor/pixel-agents submodule into the bundle.
        _run(
            [
                "npm",
                "ci",
                f"--workspace={WORKSPACE}",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            cwd=index_dir,
            timeout=600,
            log=log,
        )
        _run(
            ["npm", "run", "build", f"--workspace={WORKSPACE}"], cwd=index_dir, timeout=300, log=log
        )
        _run([*command, "--version"], cwd=None, timeout=60, log=log)
        (root / _BUILT_COMMIT_MARKER).write_text(commit + "\n", encoding="utf-8")
        log.info("pixelagents: pixatar built from %s@%s", REPO_URL, commit)
        return True, command


def build_pixatar(
    cog_data_dir: Path, *, logger: logging.Logger, force: bool = False
) -> PixatarBuildOutcome:
    """`ensure_pixatar_built`, but reports rather than raises. An explicit
    `PIXATAR_CLI` wins over building, and nothing is built while the Python
    client that would drive it (this cog's `pixatar` requirement) is
    missing."""

    if importlib.util.find_spec("pixatar") is None:
        return PixatarBuildOutcome(
            ok=False,
            status_line="⚠️ pixatar's Python client is not installed (reinstall pixelagents).",
            error="pixatar Python client not installed",
        )

    override = os.environ.get(CLI_ENV_VAR, "").strip()
    if override:
        return PixatarBuildOutcome(
            ok=True, status_line=f"✅ pixatar from `{CLI_ENV_VAR}` (`{override}`)."
        )
    try:
        rebuilt, command = ensure_pixatar_built(cog_data_dir, logger=logger, force=force)
    except PixatarBuildError as exc:
        logger.error("pixelagents: could not build pixatar: %s", exc)
        return PixatarBuildOutcome(
            ok=False,
            status_line=f"⚠️ Could not build pixatar: {exc}",
            error=str(exc),
            missing_tools=exc.missing_tools,
        )
    except Exception as exc:  # must never break cog_load or the rebuild command
        logger.exception("pixelagents: unexpected error building pixatar")
        return PixatarBuildOutcome(
            ok=False, status_line=f"⚠️ Could not build pixatar: {exc}", error=str(exc)
        )
    commit = pinned_commit()
    verb = "built" if rebuilt else "already up to date"
    link = f"[pixel-agents-hq/index@{commit[:7]}]({_commit_url(commit)})"
    return PixatarBuildOutcome(ok=True, status_line=f"✅ pixatar {verb} ({link}).", command=command)


def _run(args: list[str], *, cwd: Path | None, timeout: float, log: logging.Logger) -> None:
    log.info("pixelagents: pixatar build: %s", " ".join(args))
    try:
        subprocess.run(args, cwd=cwd, check=True, timeout=timeout, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PixatarBuildError(
            f"required tool not found: {args[0]}", missing_tools=(args[0],)
        ) from exc
    except subprocess.CalledProcessError as exc:
        output = ((exc.stderr or "") + (exc.stdout or "")).strip()
        raise PixatarBuildError(
            f"`{' '.join(args)}` failed (exit {exc.returncode}): {output[-2000:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PixatarBuildError(f"`{' '.join(args)}` timed out after {timeout}s") from exc


def _checkout(index_dir: Path, commit: str, log: logging.Logger) -> None:
    git = ["git", "-C", str(index_dir)]
    if not (index_dir / ".git").exists():
        index_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", REPO_URL, str(index_dir)], cwd=None, timeout=300, log=log)
    else:
        _run([*git, "fetch", "origin"], cwd=None, timeout=300, log=log)
    _run([*git, "checkout", "--detach", commit], cwd=None, timeout=60, log=log)
    # Only the wardrobe's source is needed, at the commit the index pins.
    _run(
        [*git, "submodule", "update", "--init", "--depth", "1", "vendor/pixel-agents"],
        cwd=None,
        timeout=300,
        log=log,
    )


__all__ = [
    "CLI_ENV_VAR",
    "REPO_URL",
    "PixatarBuildError",
    "PixatarBuildOutcome",
    "build_pixatar",
    "built_commit",
    "cli_path",
    "ensure_pixatar_built",
    "is_up_to_date",
    "pinned_commit",
]
