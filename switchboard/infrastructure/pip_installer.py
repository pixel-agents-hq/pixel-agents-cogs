"""Install requirements exactly where Red's Downloader would."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from ..domain import InstallResult

log = logging.getLogger("red.office-cogs.switchboard.pip")

# Keep a chat-sized tail of pip's output for the reply; the full output goes
# to the log.
_OUTPUT_TAIL_CHARS = 1500


class PipInstaller:
    """Runs Downloader's own `python -m pip install -U -t <lib> ...` command.

    `target_dir` is resolved lazily (Red's data manager must be initialised
    first). The lib dir is already on sys.path -- Red appends it at startup
    -- so after `importlib.invalidate_caches()` new modules import without a
    restart.
    """

    def __init__(self, target_dir: Callable[[], Path], *, python: str = sys.executable) -> None:
        self._target_dir = target_dir
        self._python = python

    async def install(self, requirements: Sequence[str]) -> InstallResult:
        requirements = tuple(dict.fromkeys(requirements))
        if not requirements:
            return InstallResult(ok=True, ran=False)

        target = self._target_dir()
        target.mkdir(parents=True, exist_ok=True)
        command = [
            self._python,
            "-m",
            "pip",
            "install",
            "-U",
            "--disable-pip-version-check",
            "--no-input",
            "-t",
            str(target),
            *requirements,
        ]
        log.info("Installing requirements into %s: %s", target, ", ".join(requirements))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        raw, _ = await process.communicate()
        output = raw.decode("utf-8", errors="replace")
        returncode = process.returncode if process.returncode is not None else -1
        importlib.invalidate_caches()

        if returncode == 0:
            log.info("pip install succeeded")
        else:
            log.error("pip install failed with exit code %s:\n%s", returncode, output)
        return InstallResult(
            ok=returncode == 0,
            requirements=requirements,
            output=output[-_OUTPUT_TAIL_CHARS:],
            returncode=returncode,
        )


def downloader_lib_path() -> Path:
    """`<data>/cogs/Downloader/lib` -- the dir Red puts on sys.path for cog requirements."""

    from redbot.core.data_manager import cog_data_path

    return Path(cog_data_path(raw_name="Downloader")) / "lib"
