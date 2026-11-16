"""Opaque Config persistence for the revisioned office aggregates: one
Discord office per guild, and the single editor office."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from redbot.core import Config

from ..domain import CharacterRecords, OfficeRef, OfficeState, OfficeStateKind

# Fresh store: deliberately unrelated to corridor's settings Config and every
# former floorplan/pixelagents layout identifier.
CONFIG_IDENTIFIER = 0x636374765F7374617465  # "cctv_state"

GLOBAL_DEFAULTS: dict[str, object] = {
    "editor_state": None,
    "characters": {},
}
GUILD_DEFAULTS: dict[str, object] = {
    "discord_state": None,
}


class RedOfficeStateRepository:
    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedOfficeStateRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="corridor",
        )
        config.register_global(**GLOBAL_DEFAULTS)
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    def _value(self, ref: OfficeRef) -> Any:
        if ref.kind == OfficeStateKind.DISCORD:
            return self._config.guild_from_id(ref.guild_id).discord_state
        return self._config.editor_state

    async def state(self, ref: OfficeRef) -> OfficeState | None:
        raw = cast("dict[str, Any] | None", await self._value(ref)())
        if raw is None:
            return None
        return OfficeState(
            kind=ref.kind,
            layout=deepcopy(cast("dict[str, Any]", raw["layout"])),
            seats=deepcopy(cast("dict[str, dict[str, Any]]", raw["seats"])),
            revision=cast(int, raw["revision"]),
            guild_id=ref.guild_id,
        )

    async def save(self, state: OfficeState) -> None:
        await self._value(state.ref).set(
            {
                "layout": deepcopy(state.layout),
                "seats": deepcopy(state.seats),
                "revision": state.revision,
            }
        )

    async def refs(self) -> tuple[OfficeRef, ...]:
        """Every aggregate that has been stored: each guild's Discord office
        (including guilds the bot has since left), then the editor office."""

        guilds = cast("dict[int, dict[str, Any]]", await self._config.all_guilds())
        refs = [
            OfficeRef.discord(guild_id)
            for guild_id, data in sorted(guilds.items())
            if data.get("discord_state") is not None
        ]
        if await self._config.editor_state() is not None:
            refs.append(OfficeRef.editor())
        return tuple(refs)

    async def characters(self) -> CharacterRecords:
        return deepcopy(cast("CharacterRecords", await self._config.characters()))

    async def save_characters(self, characters: CharacterRecords) -> None:
        await self._config.characters.set(deepcopy(characters))


__all__ = ["CONFIG_IDENTIFIER", "GLOBAL_DEFAULTS", "GUILD_DEFAULTS", "RedOfficeStateRepository"]
