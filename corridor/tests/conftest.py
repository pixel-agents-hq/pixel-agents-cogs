"""Shared fakes for the adapter-layer tests. Module stubbing lives in one
place (../conftest.py) and is not duplicated here."""

from __future__ import annotations

from typing import Any


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.icon = None


class _FakeGuildPermissions:
    """Mimics enough of discord.Permissions for DiscordMemberRef: the
    `.administrator` attribute it reads directly, plus iteration as
    `(name, value)` pairs, which it uses to derive `permission_names`."""

    def __init__(self, administrator: bool, permission_names: frozenset[str] = frozenset()) -> None:
        self.administrator = administrator
        self._permission_names = permission_names

    def __iter__(self) -> Any:
        names = set(self._permission_names)
        if self.administrator:
            names.add("administrator")
        for name in names:
            yield name, True


class FakeMember:
    def __init__(
        self,
        member_id: int,
        guild: FakeGuild,
        role_ids: tuple[int, ...] = (),
        is_administrator: bool = False,
        permission_names: frozenset[str] = frozenset(),
    ) -> None:
        self.id = member_id
        self.guild = guild
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.guild_permissions = _FakeGuildPermissions(is_administrator, permission_names)


class FakeUser:
    class _Avatar:
        url = "https://example.com/bot-avatar.png"

    display_avatar = _Avatar()


# Red's default embed colour (`[p]set colour` unset).
RED_EMBED_COLOUR = 0xE74C3C


class FakeColour:
    def __init__(self, value: int) -> None:
        self.value = value


class FakeBot:
    def __init__(
        self,
        owner_ids: frozenset[int] = frozenset(),
        valid_prefixes: tuple[str, ...] = (";",),
    ) -> None:
        self.owner_ids = owner_ids
        self.user = FakeUser()
        self._guilds: dict[int, FakeGuild] = {}
        self._cogs: dict[str, Any] = {}
        self.unload_extension_calls: list[str] = []
        self.unload_extension_failures: set[str] = set()
        self._valid_prefixes = valid_prefixes
        # (cog class name, guild id) pairs `[p]command disablecog` turned off.
        self.disabled_cogs: set[tuple[str, int]] = set()

    async def cog_disabled_in_guild(self, cog: Any, guild: Any) -> bool:
        return guild is not None and (type(cog).__name__, guild.id) in self.disabled_cogs

    async def get_valid_prefixes(self) -> list[str]:
        return list(self._valid_prefixes)

    async def get_embed_colour(self, location: Any) -> FakeColour:
        return FakeColour(RED_EMBED_COLOUR)

    def register_guild(self, guild: FakeGuild) -> None:
        self._guilds[guild.id] = guild

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guilds.get(guild_id)

    def add_cog(self, cog: Any) -> None:
        self._cogs[type(cog).__name__] = cog

    def get_cog(self, name: str) -> Any:
        return self._cogs.get(name)

    async def unload_extension(self, name: str) -> None:
        self.unload_extension_calls.append(name)
        if name in self.unload_extension_failures:
            raise RuntimeError(f"simulated failure unloading {name!r}")


class FakeMessage:
    def __init__(self) -> None:
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class FakeChannel:
    """Stands in for `discord.abc.Messageable` at `send_channel_reply`'s
    call sites -- same `.send()` recording shape as `FakeContext`, since
    both ultimately reach `build_reply_payload`'s same kwargs."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self, content: str | None = None, *, embed: Any = None, **kwargs: object
    ) -> None:
        self.sent.append({"content": content, "embed": embed, **kwargs})


class FakeContext:
    def __init__(self, author: FakeMember, guild: FakeGuild, clean_prefix: str = ";") -> None:
        self.author = author
        self.guild = guild
        self.clean_prefix = clean_prefix
        self.sent: list[dict[str, Any]] = []
        self.message = FakeMessage()

    async def embed_color(self) -> FakeColour:
        return FakeColour(RED_EMBED_COLOUR)

    async def send(
        self, content: str | None = None, *, embed: Any = None, **kwargs: object
    ) -> None:
        self.sent.append({"content": content, "embed": embed, **kwargs})
