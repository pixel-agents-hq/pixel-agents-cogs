"""Who wears which character: dealing, storing and changing office characters.

See `domain/characters.py` for what a character is. Corridor persists the
records (one global map shared by both pages); this is the one schema-aware
surface onto them, the way `office_state.py` is for the office aggregates.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, TypeAlias, TypeVar

from ..domain import OfficeIdentity
from ..domain.characters import (
    SEED_PREFIX,
    CharacterRecord,
    CharacterSource,
    PixatarCharacter,
    PixatarFailure,
    character_key,
    deal_text,
    is_seed_code,
)

CharacterRecords: TypeAlias = dict[str, dict[str, Any]]
MutationResult = TypeVar("MutationResult")
# The profile worn when neither pixatar nor the built default cast is there
# to dress a character: the webview isn't servable then either, but a roster
# entry still has to be a valid profile.
LAST_RESORT_PROFILE: dict[str, Any] = {"body": "flat"}


class CorridorCharacterGateway(Protocol):
    async def office_characters(self) -> CharacterRecords: ...

    async def mutate_office_characters(
        self, mutation: Callable[[CharacterRecords], MutationResult]
    ) -> tuple[CharacterRecords, MutationResult]: ...


class Pixatar(Protocol):
    async def from_text(self, text: str) -> PixatarCharacter: ...
    async def from_seed(self, seed: str) -> PixatarCharacter: ...
    async def avatar_png(self, seed: str, *, scale: int) -> bytes: ...


PixatarProvider = Callable[[], Pixatar | None]
DefaultProfiles = Callable[[], Sequence[Mapping[str, Any]]]


def parse_character_record(raw: object) -> CharacterRecord | None:
    """A stored record, or None when it isn't one this version understands
    (such a record is dealt afresh and replaced)."""

    if not isinstance(raw, Mapping):
        return None
    seed, source, profile = raw.get("seed"), raw.get("source"), raw.get("profile")
    if not isinstance(seed, str) or not is_seed_code(seed):
        return None
    if source not in {member.value for member in CharacterSource}:
        return None
    if not isinstance(profile, Mapping) or not isinstance(profile.get("body"), str):
        return None
    return CharacterRecord(seed=seed, source=CharacterSource(source), profile=dict(profile))


class CharacterService:
    def __init__(
        self,
        corridor: CorridorCharacterGateway,
        pixatar: PixatarProvider,
        default_profiles: DefaultProfiles,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._corridor = corridor
        self._pixatar = pixatar
        self._default_profiles = default_profiles
        self._log = logger or logging.getLogger(__name__)

    def _required_pixatar(self) -> Pixatar:
        pixatar = self._pixatar()
        if pixatar is None:
            raise PixatarFailure("cli-not-found", "pixatar is not available on this bot")
        return pixatar

    async def _store(self, key: str, record: CharacterRecord, *, replace: bool) -> CharacterRecord:
        def write(records: CharacterRecords) -> CharacterRecord:
            existing = parse_character_record(records.get(key))
            if existing is not None and not replace:
                # Dealt concurrently (or set) while pixatar was answering.
                return existing
            records[key] = record.to_raw()
            return record

        _, stored = await self._corridor.mutate_office_characters(write)
        return stored

    async def character(self, identity: OfficeIdentity) -> CharacterRecord | None:
        """The stored character, dealt and stored first if there's none yet.
        None only while pixatar can't deal one."""

        key = character_key(identity)
        stored = parse_character_record((await self._corridor.office_characters()).get(key))
        if stored is not None:
            return stored
        pixatar = self._pixatar()
        if pixatar is None:
            return None
        try:
            dealt = await pixatar.from_text(deal_text(identity))
        except PixatarFailure as exc:
            self._log.warning("pixelagents: pixatar could not deal %s: %s", key, exc.message)
            return None
        record = CharacterRecord(dealt.seed, CharacterSource.DERIVED, dealt.profile)
        return await self._store(key, record, replace=False)

    async def profile(self, identity: OfficeIdentity) -> dict[str, Any]:
        """The profile this character wears on the canvas. Without pixatar, a
        default cast member chosen by its key, so it stays the same one."""

        record = await self.character(identity)
        if record is not None:
            return dict(record.profile)
        defaults = list(self._default_profiles())
        if not defaults:
            return dict(LAST_RESORT_PROFILE)
        digest = hashlib.sha256(character_key(identity).encode()).digest()
        chosen = dict(defaults[int.from_bytes(digest[:4], "big") % len(defaults)])
        chosen.pop("id", None)
        return chosen

    async def set_seed(self, identity: OfficeIdentity, seed: str) -> CharacterRecord:
        """Wear the character a seed code stands for. Raises `PixatarFailure`
        (`invalid-seed` for anything that isn't a readable code)."""

        seed = seed.strip()
        if not is_seed_code(seed):
            raise PixatarFailure("invalid-seed", f"A seed code starts with `{SEED_PREFIX}`.")
        chosen = await self._required_pixatar().from_seed(seed)
        record = CharacterRecord(chosen.seed, CharacterSource.CUSTOM, chosen.profile)
        return await self._store(character_key(identity), record, replace=True)

    async def reset(self, identity: OfficeIdentity) -> CharacterRecord:
        """Go back to the character dealt from the user id (or agent key)."""

        dealt = await self._required_pixatar().from_text(deal_text(identity))
        record = CharacterRecord(dealt.seed, CharacterSource.DERIVED, dealt.profile)
        return await self._store(character_key(identity), record, replace=True)

    async def avatar_png(self, record: CharacterRecord, *, scale: int = 8) -> bytes:
        return await self._required_pixatar().avatar_png(record.seed, scale=scale)

    async def avatar_file(self, identity: OfficeIdentity, directory: Path) -> Path | None:
        """This character's avatar as a PNG file under `directory`, for a
        Discord reply icon; None while pixatar can't draw it. Rendered once
        per seed code (the file is named after it), so a changed character
        gets a new file and a reply never shows a stale one."""

        record = await self.character(identity)
        if record is None:
            return None
        path = directory / f"{hashlib.sha256(record.seed.encode()).hexdigest()[:16]}.png"
        if path.is_file():
            return path
        try:
            png = await self.avatar_png(record)
        except PixatarFailure as exc:
            self._log.warning("pixelagents: no avatar for %s: %s", character_key(identity), exc)
            return None

        def write() -> None:
            directory.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".tmp")
            partial.write_bytes(png)
            partial.replace(path)

        await asyncio.to_thread(write)
        return path


__all__ = [
    "LAST_RESORT_PROFILE",
    "CharacterRecords",
    "CharacterService",
    "CorridorCharacterGateway",
    "DefaultProfiles",
    "Pixatar",
    "PixatarProvider",
    "parse_character_record",
]
