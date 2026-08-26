"""Satisfies pixelagents' `SeatRepository` Protocol with a no-op.

architect never renders Discord agent presence -- there is no character
sprite/palette-assignment concept in this webview at all, only the static
office layout (see docs/architect-design.md). `pixelagents.application.office.OfficeService`
still requires a `SeatRepository` to construct, so this is the empty
implementation that satisfies it without persisting anything.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

MutationResult = TypeVar("MutationResult")


class NullSeatRepository:
    async def seats(self) -> dict[str, dict[str, object]]:
        return {}

    async def mutate_seats(
        self, mutation: Callable[[dict[str, dict[str, object]]], MutationResult]
    ) -> MutationResult:
        return mutation({})


__all__ = ["NullSeatRepository"]
