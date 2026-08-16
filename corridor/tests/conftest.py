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


class FakeMember:
    def __init__(self, member_id: int, guild: FakeGuild, role_ids: tuple[int, ...] = ()) -> None:
        self.id = member_id
        self.guild = guild
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeUser:
    class _Avatar:
        url = "https://example.com/bot-avatar.png"

    display_avatar = _Avatar()


class FakeBot:
    def __init__(self, owner_ids: frozenset[int] = frozenset()) -> None:
        self.owner_ids = owner_ids
        self.user = FakeUser()
        self._guilds: dict[int, FakeGuild] = {}
        self._cogs: dict[str, Any] = {}
        self.unload_extension_calls: list[str] = []
        self.unload_extension_failures: set[str] = set()

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


class FakeContext:
    def __init__(self, author: FakeMember, guild: FakeGuild) -> None:
        self.author = author
        self.guild = guild
        self.sent: list[dict[str, Any]] = []

    async def send(
        self, content: str | None = None, *, embed: Any = None, **kwargs: object
    ) -> None:
        self.sent.append({"content": content, "embed": embed, **kwargs})
