"""Framework-agnostic application logic.

TimeService depends only on the Clock Protocol below, never on
datetime.now() directly -- swap in a fixed/fake clock in tests without
monkeypatching stdlib.
"""

from __future__ import annotations

from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..domain import CurrentTime


class Clock(Protocol):
    """The one thing TimeService needs from the outside world."""

    def now(self) -> CurrentTime: ...


class UnknownTimeZoneError(ValueError):
    """Raised when a caller-supplied zone name (e.g. from
    `[p]deskutils time <zone>`) doesn't resolve to a known IANA tz database
    entry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown time zone: {name!r}")
        self.name = name


class TimeService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def now(self) -> CurrentTime:
        return self._clock.now()

    def resolve_zone(self, name: str) -> ZoneInfo:
        """Look up an IANA zone name, e.g. `America/New_York`.

        ZoneInfo raises `ValueError` (not just `ZoneInfoNotFoundError`) for
        some malformed keys, e.g. absolute paths -- both mean the same thing
        to a caller: this isn't a zone we can localize to.
        """

        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise UnknownTimeZoneError(name) from exc
