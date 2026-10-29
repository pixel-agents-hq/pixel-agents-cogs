"""Office characters: the pixatar look each member or genuine agent wears.

A character is stored as its pixatar seed code (`px.…`) plus the Pixel Agents
character profile that code decodes to. Members who never chose one wear the
character pixatar deals from their Discord user id; a genuine agent wears the
one dealt from its agent key. A dealt character is stored as its code and
profile rather than re-dealt on every load, because pixatar's free-text deal
depends on its wardrobe (a vendor bump that adds parts can deal the same text
a different character) while a code always decodes to the same one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .office import OfficeIdentity

SEED_PREFIX = "px."
# Pixel Agents' own profile id rule (core/src/characterProfiles.ts).
_PROFILE_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class CharacterSource(StrEnum):
    # Dealt by pixatar from the member's user id (or the agent's key).
    DERIVED = "derived"
    # A seed code the member set, e.g. copied from Pixel Index's designer.
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class PixatarCharacter:
    seed: str
    profile: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CharacterRecord:
    seed: str
    source: CharacterSource
    profile: dict[str, Any]

    def to_raw(self) -> dict[str, Any]:
        return {"seed": self.seed, "source": self.source.value, "profile": dict(self.profile)}


class PixatarFailure(Exception):
    """pixatar refused a request or could not run. `code` is pixatar's own
    (`invalid-seed`, `cli-not-found`, `cli-exited`, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _agent_key(identity: OfficeIdentity) -> str | None:
    """A genuine agent's key, or None for a Discord member.

    Read off the value, not `isinstance`: Red re-executes every cached
    `pixelagents.*` module on each load (see `pixelagents/__init__.py`), so a
    key built by a consuming cog can be an instance of an earlier execution's
    class.
    """

    agent_key = getattr(identity, "agent_key", None)
    return agent_key if isinstance(agent_key, str) else None


def character_key(identity: OfficeIdentity) -> str:
    """The character record's key: one per Discord account (every guild and
    both pages share it) or per genuine agent."""

    agent_key = _agent_key(identity)
    if agent_key is not None:
        return f"agent:{agent_key}"
    return f"user:{identity.user_id}"  # type: ignore[union-attr]


def deal_text(identity: OfficeIdentity) -> str:
    """The free text pixatar deals a character from when none was set."""

    agent_key = _agent_key(identity)
    if agent_key is not None:
        return agent_key
    return str(identity.user_id)  # type: ignore[union-attr]


def profile_id(identity: OfficeIdentity) -> str:
    """The roster id the webview keys this character's profile by."""

    agent_key = _agent_key(identity)
    if agent_key is None:
        return f"u{identity.user_id}"  # type: ignore[union-attr]
    return f"a-{_PROFILE_ID_UNSAFE.sub('-', agent_key)}"[:64]


def is_seed_code(seed: str) -> bool:
    return seed.startswith(SEED_PREFIX)


__all__ = [
    "SEED_PREFIX",
    "CharacterRecord",
    "CharacterSource",
    "PixatarCharacter",
    "PixatarFailure",
    "character_key",
    "deal_text",
    "is_seed_code",
    "profile_id",
]
