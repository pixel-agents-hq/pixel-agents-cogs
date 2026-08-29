"""FeedbackService is fully testable without Red/corridor/mcp: a plain
in-memory fake repository and a plain recording `post` callable satisfy
its two dependencies, no unittest.mock needed."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from ..application.feedback_service import (
    CHANNEL_UNAVAILABLE_MESSAGE,
    NOT_CONFIGURED_MESSAGE,
    FeedbackService,
)
from ..domain import ErrorReport, ImprovementSuggestion, Severity


class FakeRepository:
    def __init__(self, channel: tuple[int, int] | None = None) -> None:
        self._channel = channel

    async def feedback_channel(self) -> tuple[int, int] | None:
        return self._channel


class FakePoster:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[tuple[int, int, str, str, Sequence[tuple[str, str]]]] = []

    async def __call__(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str,
        fields: Sequence[tuple[str, str]],
    ) -> bool:
        self.calls.append((guild_id, channel_id, title, description, fields))
        return self.succeeds


def _report(**overrides: object) -> ErrorReport:
    defaults: dict[str, object] = dict(
        source="architect",
        what_happened="misread a tool description",
        expected="tool X to accept a string",
        actual="tool X required an int",
        severity=Severity.MEDIUM,
    )
    defaults.update(overrides)
    return ErrorReport(**defaults)  # type: ignore[arg-type]


def _suggestion(**overrides: object) -> ImprovementSuggestion:
    defaults: dict[str, object] = dict(
        source="architect",
        area="tool descriptions",
        observation="tool X's description didn't mention the type",
        suggestion="add the expected type to the description",
    )
    defaults.update(overrides)
    return ImprovementSuggestion(**defaults)  # type: ignore[arg-type]


class TestFeedbackService(unittest.IsolatedAsyncioTestCase):
    async def test_report_error_posts_to_the_configured_channel(self) -> None:
        poster = FakePoster()
        service = FeedbackService(FakeRepository((10, 20)), post=poster)

        result = await service.report_error(_report())

        self.assertEqual(result, {"status": "ok"})
        [(guild_id, channel_id, title, description, fields)] = poster.calls
        self.assertEqual((guild_id, channel_id), (10, 20))
        self.assertIn("medium", title)
        self.assertEqual(description, "misread a tool description")
        self.assertIn(("Source", "architect"), fields)

    async def test_suggest_improvement_posts_to_the_configured_channel(self) -> None:
        poster = FakePoster()
        service = FeedbackService(FakeRepository((10, 20)), post=poster)

        result = await service.suggest_improvement(_suggestion())

        self.assertEqual(result, {"status": "ok"})
        [(guild_id, channel_id, title, description, fields)] = poster.calls
        self.assertEqual((guild_id, channel_id), (10, 20))
        self.assertEqual(description, "tool X's description didn't mention the type")
        self.assertIn(("Area", "tool descriptions"), fields)

    async def test_report_error_with_no_configured_channel_fails_closed(self) -> None:
        poster = FakePoster()
        service = FeedbackService(FakeRepository(None), post=poster)

        result = await service.report_error(_report())

        self.assertEqual(
            result,
            {"status": "error", "error": "not_configured", "message": NOT_CONFIGURED_MESSAGE},
        )
        self.assertEqual(poster.calls, [])

    async def test_report_error_when_the_channel_cannot_be_reached_fails_closed(self) -> None:
        poster = FakePoster(succeeds=False)
        service = FeedbackService(FakeRepository((10, 20)), post=poster)

        result = await service.report_error(_report())

        self.assertEqual(
            result,
            {
                "status": "error",
                "error": "channel_unavailable",
                "message": CHANNEL_UNAVAILABLE_MESSAGE,
            },
        )


if __name__ == "__main__":
    unittest.main()
