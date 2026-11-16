"""Read Downloader's record of which cogs `[p]cog install` put in place.

Through Downloader's public `installed_cogs()` while the Downloader cog is
loaded. Red has no public route while it is unloaded, so then this reads
the same record straight from Downloader's Config -- a private detail,
pinned by tests/test_red_private_api.py and info.json's min/max_bot_version.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

log = logging.getLogger("red.pixel-agents-cogs.switchboard.downloader")

# redbot/cogs/downloader/downloader.py: Config.get_conf(self, identifier=998240343)
DOWNLOADER_CONFIG_IDENTIFIER = 998240343


async def read_downloader_index(bot: Any) -> dict[str, str]:
    """module name -> repo name for every Downloader-installed cog."""

    downloader = bot.get_cog("Downloader")
    if downloader is not None:
        try:
            modules = await downloader.installed_cogs()
        except Exception:
            # installed_cogs() rebuilds each record from disk; a stale one
            # must not hide the rest, so read the raw record instead.
            log.warning("Downloader.installed_cogs() failed; reading its Config", exc_info=True)
        else:
            return {str(module.name): str(module.repo_name) for module in modules}

    from redbot.core import Config

    config = Config.get_conf(None, identifier=DOWNLOADER_CONFIG_IDENTIFIER, cog_name="Downloader")
    # Unregistered here (force_registration off), so a never-used Downloader
    # reads back as None rather than its {} default.
    raw = await config.installed_cogs()
    return index_from_installed_cogs(raw)


def index_from_installed_cogs(raw: object) -> dict[str, str]:
    """Parse Downloader's `installed_cogs` global: {repo: {module: {...}}}."""

    index: dict[str, str] = {}
    if not isinstance(raw, Mapping):
        return index
    for repo_name, modules in raw.items():
        if not isinstance(modules, Mapping):
            continue
        for module_name in modules:
            index[str(module_name)] = str(repo_name)
    return index
