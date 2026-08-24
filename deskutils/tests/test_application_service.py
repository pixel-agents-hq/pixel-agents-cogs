"""TimeService is fully testable without Red: a fixed FakeClock satisfies
the Clock protocol, no unittest.mock or real system clock needed."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..application import TimeService, UnknownTimeZoneError
from ..domain import CurrentTime

FIXED_INSTANT = datetime(2026, 8, 23, 12, 30, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, instant: datetime = FIXED_INSTANT) -> None:
        self._instant = instant

    def now(self) -> CurrentTime:
        return CurrentTime(utc=self._instant)


class TestTimeService(unittest.TestCase):
    def setUp(self) -> None:
        self.service = TimeService(FakeClock())

    def test_now_reads_through_to_the_clock(self) -> None:
        snapshot = self.service.now()

        self.assertEqual(snapshot.utc, FIXED_INSTANT)

    def test_resolve_zone_returns_a_usable_zoneinfo(self) -> None:
        zone = self.service.resolve_zone("America/New_York")

        self.assertEqual(zone, ZoneInfo("America/New_York"))
        # 2026-08-23 is inside DST for the US, so New York is UTC-4 (EDT).
        localized = FIXED_INSTANT.astimezone(zone)
        self.assertEqual(localized.utcoffset(), timedelta(hours=-4))

    def test_resolve_zone_rejects_an_unknown_name(self) -> None:
        with self.assertRaises(UnknownTimeZoneError):
            self.service.resolve_zone("Not/A_Real_Zone")

    def test_resolve_zone_rejects_a_malformed_key(self) -> None:
        with self.assertRaises(UnknownTimeZoneError):
            self.service.resolve_zone("/etc/passwd")
