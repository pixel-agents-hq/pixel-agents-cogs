"""Read Downloader's record of which cogs `[p]cog install` put in place.

Read straight from Downloader's Config, so the answer does not depend on
the Downloader cog being loaded.
"""

from __future__ import annotations

from collections.abc import Mapping

# redbot/cogs/downloader/downloader.py: Config.get_conf(self, identifier=998240343)
DOWNLOADER_CONFIG_IDENTIFIER = 998240343


async def read_downloader_index() -> dict[str, str]:
    """module name -> repo name for every Downloader-installed cog."""

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
