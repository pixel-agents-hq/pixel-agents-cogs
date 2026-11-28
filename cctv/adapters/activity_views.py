"""The `[p]cctv url` button that opens the office inside Discord: it answers
its interaction with Discord's `LAUNCH_ACTIVITY` response."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import discord

log = logging.getLogger("red.pixel-agents-cogs.cctv")
# Fixed, so a button posted before a restart still works once CCTV is loaded
# again (the view is registered as persistent in `cog_load`).
LAUNCH_CUSTOM_ID = "cctv:activity:launch"
LAUNCH_LABEL = "Open in Discord"
_OFF = "The bot owner has switched the office Activity off."
_FAILED = "Discord couldn't start the office Activity here. Try the App Launcher instead."


class ActivityLaunchView(discord.ui.View):
    def __init__(self, enabled: Callable[[], Awaitable[bool]]) -> None:
        super().__init__(timeout=None)
        self._enabled = enabled
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=LAUNCH_LABEL, style=discord.ButtonStyle.primary, custom_id=LAUNCH_CUSTOM_ID
        )
        cast(Any, button).callback = self.launch
        self.add_item(button)

    async def launch(self, interaction: discord.Interaction[Any]) -> None:
        if not await self._enabled():
            await interaction.response.send_message(_OFF, ephemeral=True)
            return
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as exc:
            # Activities not switched on for the application, or not on this
            # platform: say so instead of a silent "interaction failed".
            log.info("cctv: Activity launch refused: %s", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message(_FAILED, ephemeral=True)


__all__ = ["LAUNCH_CUSTOM_ID", "LAUNCH_LABEL", "ActivityLaunchView"]
