"""Wall-clock implementation of the application layer's Clock protocol."""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain import CurrentTime


class SystemClock:
    """Reads the real system clock. The only adapter that touches
    `datetime.now()` -- everything above this layer takes a `CurrentTime`
    value, so tests can supply a fixed one instead of monkeypatching
    stdlib."""

    def now(self) -> CurrentTime:
        return CurrentTime(utc=datetime.now(UTC))
