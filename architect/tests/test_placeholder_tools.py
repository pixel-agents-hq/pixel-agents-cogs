"""Placeholder tools have real schemas but no real effect -- see
docs/architect-design.md section 8. These tests only pin that shape down,
not any future real behavior."""

from __future__ import annotations

import unittest

from ..tools.placeholder_tools import (
    BreakDownTaskInput,
    BreakDownTaskOutput,
    BreakDownTaskTool,
    ReviewDesignInput,
    ReviewDesignOutput,
    ReviewDesignTool,
)


class TestReviewDesignTool(unittest.IsolatedAsyncioTestCase):
    async def test_handler_always_reports_not_implemented(self) -> None:
        tool = ReviewDesignTool()

        output = await tool.handler(ReviewDesignInput(topic="anything"))

        assert isinstance(output, ReviewDesignOutput)
        self.assertEqual(output.status, "not_implemented")

    def test_input_schema_has_a_required_topic(self) -> None:
        schema = ReviewDesignTool().Input.model_json_schema()

        self.assertIn("topic", schema["properties"])
        self.assertIn("topic", schema["required"])


class TestBreakDownTaskTool(unittest.IsolatedAsyncioTestCase):
    async def test_handler_always_reports_not_implemented(self) -> None:
        tool = BreakDownTaskTool()

        output = await tool.handler(BreakDownTaskInput(task="anything"))

        assert isinstance(output, BreakDownTaskOutput)
        self.assertEqual(output.status, "not_implemented")

    def test_input_schema_has_a_required_task(self) -> None:
        schema = BreakDownTaskTool().Input.model_json_schema()

        self.assertIn("task", schema["properties"])
        self.assertIn("task", schema["required"])
