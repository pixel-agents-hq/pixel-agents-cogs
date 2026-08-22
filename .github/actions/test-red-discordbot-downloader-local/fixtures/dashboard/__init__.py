"""CI-only stand-in for Red Web Dashboard's "Dashboard" cog.

test_downloader_cogs.py copies this into the real Red instance's install
path and loads it via RPC before exercising the repo's own cogs, so
floorplan.dashboard_cog_loaded()'s bot.get_cog("Dashboard").rpc
.third_parties_handler check is satisfied in CI the same way it would be
against a real dashboard install -- without that, floorplan's cog_load()
DMs the bot owner on every single CI run (see floorplan/adapters/dashboard.py).
"""

from __future__ import annotations

import types

from redbot.core import commands


class Dashboard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.rpc = types.SimpleNamespace(third_parties_handler=object())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dashboard(bot))
