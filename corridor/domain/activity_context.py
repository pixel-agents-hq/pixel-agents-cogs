"""Which guild a genuine agent's current turn is working for.

A genuine agent (architect, painter, animator, a bootcamp agent) has no
guild of its own, but each turn it runs was asked for from one: pico
consulting it in a guild channel, `[p]bootcamp ask` in a guild, or one
agent consulting another inside such a turn. The guild travels with the
A2A request as message metadata (`A2A_GUILD_METADATA_KEY`, a string --
protobuf `Struct` numbers are doubles and would round a snowflake), and
`GenericAgentExecutor` sets it here for the duration of the turn, so the
agent's activity events can carry it (`context_guild_id`) without every
callback signature having to pass it along. See
docs/corridor-pubsub-design.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

A2A_GUILD_METADATA_KEY = "guild_id"

_ACTIVITY_GUILD: ContextVar[int | None] = ContextVar("corridor_activity_guild_id", default=None)


def activity_guild_id() -> int | None:
    """The guild the current agent turn is working for, if known."""

    return _ACTIVITY_GUILD.get()


@contextmanager
def activity_guild(guild_id: int | None) -> Iterator[None]:
    """Run the enclosed agent turn on behalf of `guild_id`."""

    token = _ACTIVITY_GUILD.set(guild_id)
    try:
        yield
    finally:
        _ACTIVITY_GUILD.reset(token)


def guild_metadata(guild_id: int | None) -> dict[str, str]:
    """A2A message metadata naming `guild_id` (empty when unknown)."""

    return {} if guild_id is None else {A2A_GUILD_METADATA_KEY: str(guild_id)}


def guild_from_metadata(metadata: object) -> int | None:
    """The guild an inbound A2A message names, or None. Accepts a protobuf
    `Struct` or a plain mapping; anything malformed reads as unknown."""

    if metadata is None:
        return None
    try:
        if A2A_GUILD_METADATA_KEY not in metadata:  # type: ignore[operator]
            return None
        raw = metadata[A2A_GUILD_METADATA_KEY]  # type: ignore[index]
    except Exception:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    text = str(raw).strip()
    if not text.isdecimal():
        return None
    value = int(text)
    return value if value > 0 else None


__all__ = [
    "A2A_GUILD_METADATA_KEY",
    "activity_guild",
    "activity_guild_id",
    "guild_from_metadata",
    "guild_metadata",
]
