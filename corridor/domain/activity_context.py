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

A turn asked for from a channel the guild's `@everyone` can't read is
*private* (`activity_private()`): the flag travels in the same metadata
(`A2A_PRIVATE_METADATA_KEY`), and corridor's `publish_event` drops the
text-carrying activity events (`AgentReplied`, `AgentToolStarted`) such a
turn publishes, so a private channel's conversation never reaches an
office page.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

A2A_GUILD_METADATA_KEY = "guild_id"
A2A_PRIVATE_METADATA_KEY = "private_channel"

_ACTIVITY_GUILD: ContextVar[int | None] = ContextVar("corridor_activity_guild_id", default=None)
_ACTIVITY_PRIVATE: ContextVar[bool] = ContextVar("corridor_activity_private", default=False)


def activity_guild_id() -> int | None:
    """The guild the current agent turn is working for, if known."""

    return _ACTIVITY_GUILD.get()


def activity_private() -> bool:
    """Whether the current turn was asked for from a private channel."""

    return _ACTIVITY_PRIVATE.get()


@contextmanager
def activity_guild(guild_id: int | None, *, private: bool = False) -> Iterator[None]:
    """Run the enclosed agent turn on behalf of `guild_id`; `private` when
    it was asked for from a channel `@everyone` there can't read. A turn
    nested in a private one stays private."""

    token = _ACTIVITY_GUILD.set(guild_id)
    private_token = _ACTIVITY_PRIVATE.set(private or _ACTIVITY_PRIVATE.get())
    try:
        yield
    finally:
        _ACTIVITY_PRIVATE.reset(private_token)
        _ACTIVITY_GUILD.reset(token)


def guild_metadata(guild_id: int | None, *, private: bool | None = None) -> dict[str, str]:
    """A2A message metadata naming `guild_id` (nothing when unknown) and,
    for a private turn (`private`, by default `activity_private()`), the
    private flag."""

    metadata = {} if guild_id is None else {A2A_GUILD_METADATA_KEY: str(guild_id)}
    if activity_private() if private is None else private:
        metadata[A2A_PRIVATE_METADATA_KEY] = "true"
    return metadata


def private_from_metadata(metadata: object) -> bool:
    """Whether an inbound A2A message comes from a private turn. Anything
    other than the exact flag reads as not private."""

    if metadata is None:
        return False
    try:
        if A2A_PRIVATE_METADATA_KEY not in metadata:  # type: ignore[operator]
            return False
        return bool(metadata[A2A_PRIVATE_METADATA_KEY] == "true")  # type: ignore[index]
    except Exception:
        return False


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
    "A2A_PRIVATE_METADATA_KEY",
    "activity_guild",
    "activity_guild_id",
    "activity_private",
    "guild_from_metadata",
    "guild_metadata",
    "private_from_metadata",
]
