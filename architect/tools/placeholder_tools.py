"""Placeholder tools: real schemas and descriptions, no real effect.

Implementing what these tools actually do is explicitly out of scope for
this iteration (see docs/architect-design.md section 8) -- each handler
returns a static acknowledgement mapping without touching Discord,
corridor, or any external system. They exist so architect's A2A agent card
advertises a non-empty skill set and the tool-calling loop has something
real to exercise end to end.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewDesignInput(BaseModel):
    topic: str = Field(description="What to review, e.g. a proposed change or approach.")


class ReviewDesignOutput(BaseModel):
    status: str = "not_implemented"
    message: str = "Design review is not implemented yet."


class ReviewDesignTool:
    name = "review_design"
    description = "Review a proposed design or approach. Not implemented yet."

    @property
    def Input(self) -> type[BaseModel]:
        return ReviewDesignInput

    @property
    def Output(self) -> type[BaseModel]:
        return ReviewDesignOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        return ReviewDesignOutput()


class BreakDownTaskInput(BaseModel):
    task: str = Field(description="A task or goal to break down into steps.")


class BreakDownTaskOutput(BaseModel):
    status: str = "not_implemented"
    message: str = "Task breakdown is not implemented yet."


class BreakDownTaskTool:
    name = "break_down_task"
    description = "Break a task or goal down into concrete steps. Not implemented yet."

    @property
    def Input(self) -> type[BaseModel]:
        return BreakDownTaskInput

    @property
    def Output(self) -> type[BaseModel]:
        return BreakDownTaskOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        return BreakDownTaskOutput()


__all__ = [
    "BreakDownTaskInput",
    "BreakDownTaskOutput",
    "BreakDownTaskTool",
    "ReviewDesignInput",
    "ReviewDesignOutput",
    "ReviewDesignTool",
]
