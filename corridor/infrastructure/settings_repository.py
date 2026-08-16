"""Red Config-backed storage for corridor's guild-wide shared settings.

Config keys and scopes are the canonical registration contract once real
data exists under this identifier -- do not change casually after release.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import (
    GuildSettings,
    IconPreference,
    IconSource,
    PermissionSettings,
    ReplyMode,
    ReplyPreferences,
)

CONFIG_IDENTIFIER = 0x636F72726964  # "corrid" in hex

GUILD_DEFAULTS: dict[str, object] = {
    "reply_mode": ReplyMode.EMBED.value,
    "show_timestamp": True,
    "footer_text": None,
    "icon_source": IconSource.BOT.value,
    "icon_custom_url": None,
    "moderator_role_ids": [],
    "privileged_role_ids": [],
}


class RedCorridorRepository:
    """The typed boundary around corridor's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedCorridorRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def guild_settings(self, guild_id: int) -> GuildSettings:
        guild = self._config.guild_from_id(guild_id)
        return GuildSettings(
            guild_id=guild_id,
            reply=ReplyPreferences(
                mode=ReplyMode(cast(str, await guild.reply_mode())),
                show_timestamp=cast(bool, await guild.show_timestamp()),
                footer_text=cast("str | None", await guild.footer_text()),
                icon=IconPreference(
                    source=IconSource(cast(str, await guild.icon_source())),
                    custom_url=cast("str | None", await guild.icon_custom_url()),
                ),
            ),
            permissions=PermissionSettings(
                moderator_role_ids=frozenset(cast(list, await guild.moderator_role_ids())),
                privileged_role_ids=frozenset(cast(list, await guild.privileged_role_ids())),
            ),
        )

    async def set_reply_mode(self, guild_id: int, mode: ReplyMode) -> None:
        await self._config.guild_from_id(guild_id).reply_mode.set(mode.value)

    async def set_show_timestamp(self, guild_id: int, value: bool) -> None:
        await self._config.guild_from_id(guild_id).show_timestamp.set(value)

    async def set_footer_text(self, guild_id: int, text: str | None) -> None:
        await self._config.guild_from_id(guild_id).footer_text.set(text)

    async def set_icon_preference(self, guild_id: int, icon: IconPreference) -> None:
        guild = self._config.guild_from_id(guild_id)
        await guild.icon_source.set(icon.source.value)
        await guild.icon_custom_url.set(icon.custom_url)

    async def set_moderator_role_ids(self, guild_id: int, role_ids: frozenset[int]) -> None:
        await self._config.guild_from_id(guild_id).moderator_role_ids.set(sorted(role_ids))

    async def set_privileged_role_ids(self, guild_id: int, role_ids: frozenset[int]) -> None:
        await self._config.guild_from_id(guild_id).privileged_role_ids.set(sorted(role_ids))

    async def add_moderator_role(self, guild_id: int, role_id: int) -> None:
        await self._add_role(guild_id, "moderator_role_ids", role_id)

    async def remove_moderator_role(self, guild_id: int, role_id: int) -> None:
        await self._remove_role(guild_id, "moderator_role_ids", role_id)

    async def add_privileged_role(self, guild_id: int, role_id: int) -> None:
        await self._add_role(guild_id, "privileged_role_ids", role_id)

    async def remove_privileged_role(self, guild_id: int, role_id: int) -> None:
        await self._remove_role(guild_id, "privileged_role_ids", role_id)

    async def _add_role(self, guild_id: int, key: str, role_id: int) -> None:
        attr = getattr(self._config.guild_from_id(guild_id), key)
        role_ids = set(cast(list, await attr()))
        role_ids.add(role_id)
        await attr.set(sorted(role_ids))

    async def _remove_role(self, guild_id: int, key: str, role_id: int) -> None:
        attr = getattr(self._config.guild_from_id(guild_id), key)
        role_ids = set(cast(list, await attr()))
        role_ids.discard(role_id)
        await attr.set(sorted(role_ids))
