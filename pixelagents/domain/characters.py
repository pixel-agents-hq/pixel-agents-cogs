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

from .office import AgentKey, GenuineAgentKey, OfficeIdentity

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


def character_key(identity: OfficeIdentity) -> str:
    """The character record's key: one per Discord account (every guild and
    both pages share it) or per genuine agent."""

    if isinstance(identity, GenuineAgentKey):
        return f"agent:{identity.agent_key}"
    return f"user:{identity.user_id}"


def deal_text(identity: OfficeIdentity) -> str:
    """The free text pixatar deals a character from when none was set."""

    if isinstance(identity, GenuineAgentKey):
        return identity.agent_key
    return str(identity.user_id)


def profile_id(identity: OfficeIdentity) -> str:
    """The roster id the webview keys this character's profile by."""

    if isinstance(identity, AgentKey):
        return f"u{identity.user_id}"
    return f"a-{_PROFILE_ID_UNSAFE.sub('-', identity.agent_key)}"[:64]


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
