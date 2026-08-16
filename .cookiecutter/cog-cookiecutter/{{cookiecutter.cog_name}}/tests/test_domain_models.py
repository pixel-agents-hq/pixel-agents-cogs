"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import CounterSnapshot


def test_counter_snapshot_holds_its_fields() -> None:
    snapshot = CounterSnapshot(guild_id=1, count=2)

    assert snapshot.guild_id == 1
    assert snapshot.count == 2


def test_counter_snapshot_is_frozen() -> None:
    snapshot = CounterSnapshot(guild_id=1, count=2)

    with pytest.raises(FrozenInstanceError):
        snapshot.count = 3  # type: ignore[misc]
