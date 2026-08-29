"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import ErrorReport, ImprovementSuggestion, Severity


def test_error_report_holds_its_fields() -> None:
    report = ErrorReport(
        source="architect", what_happened="x", expected="y", actual="z", severity=Severity.HIGH
    )

    assert report.source == "architect"
    assert report.severity is Severity.HIGH


def test_error_report_is_frozen() -> None:
    report = ErrorReport(
        source="architect", what_happened="x", expected="y", actual="z", severity=Severity.LOW
    )

    with pytest.raises(FrozenInstanceError):
        report.severity = Severity.HIGH  # type: ignore[misc]


def test_improvement_suggestion_holds_its_fields() -> None:
    suggestion = ImprovementSuggestion(
        source="architect", area="docs", observation="unclear", suggestion="clarify"
    )

    assert suggestion.area == "docs"
    assert suggestion.suggestion == "clarify"


def test_severity_values() -> None:
    assert Severity.LOW.value == "low"
    assert Severity.MEDIUM.value == "medium"
    assert Severity.HIGH.value == "high"
