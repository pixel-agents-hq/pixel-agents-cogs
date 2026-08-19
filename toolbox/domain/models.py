"""Pure business models. Zero framework imports -- this module never imports
discord.py or redbot, so it is trivially unit-testable without either
installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeInstallation:
    """A Node.js runtime this cog installed onto the bot host.

    `install_dir` is the versioned directory containing that release's
    `bin/` (with `node`/`npm`) -- never a "current"/"latest" symlink, so a
    switch to a different version can't leave a stale path lying around.
    """

    version: str
    install_dir: str


@dataclass(frozen=True, slots=True)
class NodeStatus:
    """A point-in-time read of what this cog has installed, if anything."""

    installed: bool
    version: str | None = None
    install_dir: str | None = None
