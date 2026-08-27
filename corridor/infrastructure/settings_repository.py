"""Red Config-backed storage for corridor's guild-wide shared settings.

Config keys and scopes are the canonical registration contract once real
data exists under this identifier -- do not change casually after release.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import (
    RESERVED_GROUP_KEYS,
    A2ASettings,
    GuildSettings,
    IconPreference,
    IconSource,
    LLMSettings,
    PermissionGroupDef,
    PermissionSettings,
    ReplyMode,
    ReplyPreferences,
)

CONFIG_IDENTIFIER = 0x636F72726964  # "corrid" in hex

# Moved here from pico (see docs/architect-design.md) -- both pico and
# architect share one LLM connection, so it lives wherever every dependent
# already reaches through required_cogs.
DEFAULT_LLM_BASE_URL = "https://litellm.nntin.xyz/"
DEFAULT_LLM_MODEL = "chatgpt/gpt-5.4"

# Moved here from architect (see docs/agent-directory-design.md) -- there is
# now exactly one A2A listener process-wide, owned by corridor, not one per
# agent. Same defaults architect's own former a2a_host/a2a_port fields used.
DEFAULT_A2A_HOST = "127.0.0.1"
DEFAULT_A2A_PORT = 8931

GLOBAL_DEFAULTS: dict[str, object] = {
    "llm_base_url": DEFAULT_LLM_BASE_URL,
    "llm_api_key": None,
    "llm_model": DEFAULT_LLM_MODEL,
    "a2a_host": DEFAULT_A2A_HOST,
    "a2a_port": DEFAULT_A2A_PORT,
}

DEFAULT_PERMISSION_GROUPS: list[dict[str, object]] = [
    {
        "key": "building_manager",
        "label": "Building Manager",
        "role_ids": [],
        "permission_names": [],
    },
    {"key": "keyholder", "label": "Keyholder", "role_ids": [], "permission_names": []},
]

GUILD_DEFAULTS: dict[str, object] = {
    "reply_mode": ReplyMode.EMBED.value,
    "show_timestamp": True,
    "footer_text": None,
    "icon_source": IconSource.BOT.value,
    "icon_custom_url": None,
    "permission_groups": DEFAULT_PERMISSION_GROUPS,
    "owner_label": "Owner",
    "employee_label": "Employee",
}


def _group_from_dict(data: dict[str, object]) -> PermissionGroupDef:
    return PermissionGroupDef(
        key=cast(str, data["key"]),
        label=cast(str, data["label"]),
        role_ids=frozenset(cast("list[int]", data.get("role_ids", []))),
        permission_names=frozenset(cast("list[str]", data.get("permission_names", []))),
    )


def _group_to_dict(group: PermissionGroupDef) -> dict[str, object]:
    return {
        "key": group.key,
        "label": group.label,
        "role_ids": sorted(group.role_ids),
        "permission_names": sorted(group.permission_names),
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
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def guild_settings(self, guild_id: int) -> GuildSettings:
        guild = self._config.guild_from_id(guild_id)
        raw_groups = cast("list[dict[str, object]]", await guild.permission_groups())
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
                groups=tuple(_group_from_dict(entry) for entry in raw_groups),
                owner_label=cast(str, await guild.owner_label()),
                employee_label=cast(str, await guild.employee_label()),
            ),
        )

    async def llm_settings(self) -> LLMSettings:
        return LLMSettings(
            llm_base_url=cast(str, await self._config.llm_base_url()),
            llm_api_key=cast("str | None", await self._config.llm_api_key()),
            llm_model=cast("str | None", await self._config.llm_model()),
        )

    async def set_llm_base_url(self, value: str) -> None:
        await self._config.llm_base_url.set(value)

    async def set_llm_api_key(self, value: str) -> None:
        await self._config.llm_api_key.set(value)

    async def set_llm_model(self, value: str) -> None:
        await self._config.llm_model.set(value)

    async def a2a_settings(self) -> A2ASettings:
        return A2ASettings(
            a2a_host=cast(str, await self._config.a2a_host()),
            a2a_port=cast(int, await self._config.a2a_port()),
        )

    async def set_a2a_host(self, value: str) -> None:
        await self._config.a2a_host.set(value)

    async def set_a2a_port(self, value: int) -> None:
        if isinstance(value, bool) or not 1 <= value <= 65535:
            raise ValueError("Port must be an integer from 1 through 65535.")
        await self._config.a2a_port.set(value)

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

    async def set_owner_label(self, guild_id: int, label: str) -> None:
        await self._config.guild_from_id(guild_id).owner_label.set(label)

    async def set_employee_label(self, guild_id: int, label: str) -> None:
        await self._config.guild_from_id(guild_id).employee_label.set(label)

    async def _load_groups(self, guild_id: int) -> list[PermissionGroupDef]:
        raw_groups = cast(
            "list[dict[str, object]]",
            await self._config.guild_from_id(guild_id).permission_groups(),
        )
        return [_group_from_dict(entry) for entry in raw_groups]

    async def _save_groups(self, guild_id: int, groups: list[PermissionGroupDef]) -> None:
        await self._config.guild_from_id(guild_id).permission_groups.set(
            [_group_to_dict(group) for group in groups]
        )

    async def list_permission_groups(self, guild_id: int) -> tuple[PermissionGroupDef, ...]:
        return tuple(await self._load_groups(guild_id))

    async def add_permission_group(
        self,
        guild_id: int,
        key: str,
        label: str,
        role_ids: frozenset[int] = frozenset(),
        permission_names: frozenset[str] = frozenset(),
    ) -> None:
        if key in RESERVED_GROUP_KEYS:
            raise ValueError(f"{key!r} is a reserved group key")
        groups = await self._load_groups(guild_id)
        if any(group.key == key for group in groups):
            raise ValueError(f"a group with key {key!r} already exists")
        groups.append(
            PermissionGroupDef(
                key=key, label=label, role_ids=role_ids, permission_names=permission_names
            )
        )
        await self._save_groups(guild_id, groups)

    async def remove_permission_group(self, guild_id: int, key: str) -> None:
        groups = await self._load_groups(guild_id)
        remaining = [group for group in groups if group.key != key]
        await self._save_groups(guild_id, remaining)

    async def set_group_role_ids(self, guild_id: int, key: str, role_ids: frozenset[int]) -> None:
        groups = await self._load_groups(guild_id)
        updated = [
            PermissionGroupDef(
                key=group.key,
                label=group.label,
                role_ids=role_ids,
                permission_names=group.permission_names,
            )
            if group.key == key
            else group
            for group in groups
        ]
        await self._save_groups(guild_id, updated)

    async def set_group_permissions(
        self, guild_id: int, key: str, permission_names: frozenset[str]
    ) -> None:
        groups = await self._load_groups(guild_id)
        updated = [
            PermissionGroupDef(
                key=group.key,
                label=group.label,
                role_ids=group.role_ids,
                permission_names=permission_names,
            )
            if group.key == key
            else group
            for group in groups
        ]
        await self._save_groups(guild_id, updated)

    async def set_group_label(self, guild_id: int, key: str, label: str) -> None:
        groups = await self._load_groups(guild_id)
        updated = [
            PermissionGroupDef(
                key=group.key,
                label=label,
                role_ids=group.role_ids,
                permission_names=group.permission_names,
            )
            if group.key == key
            else group
            for group in groups
        ]
        await self._save_groups(guild_id, updated)
