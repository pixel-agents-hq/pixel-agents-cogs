"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from ..domain import CurrentTime, TextStatistics


def test_current_time_holds_its_utc_instant() -> None:
    instant = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

    snapshot = CurrentTime(utc=instant)

    assert snapshot.utc == instant


def test_current_time_is_frozen() -> None:
    snapshot = CurrentTime(utc=datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC))

    with pytest.raises(FrozenInstanceError):
        snapshot.utc = datetime.now(UTC)  # type: ignore[misc]


def test_epoch_seconds_is_whole_seconds_since_epoch() -> None:
    snapshot = CurrentTime(utc=datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC))

    assert snapshot.epoch_seconds == 1


def test_epoch_seconds_truncates_sub_second_precision() -> None:
    snapshot = CurrentTime(utc=datetime(1970, 1, 1, 0, 0, 1, 999_000, tzinfo=UTC))

    assert snapshot.epoch_seconds == 1


def test_text_statistics_is_a_frozen_value() -> None:
    statistics = TextStatistics(characters=10, words=2)

    with pytest.raises(FrozenInstanceError):
        statistics.words = 3  # type: ignore[misc]
