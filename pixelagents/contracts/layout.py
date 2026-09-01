"""Structural contract for raw Pixel Agents office layouts."""

from __future__ import annotations

from typing import Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

RawOfficeLayout: TypeAlias = dict[str, JsonValue]


class OfficeLayout(BaseModel):
    """Validate browser-critical shape while preserving upstream extensions."""

    model_config = ConfigDict(extra="allow", strict=True)

    version: Literal[1]
    cols: int = Field(gt=0)
    rows: int = Field(gt=0)
    tiles: list[JsonValue]
    furniture: list[JsonValue]
    tile_colors: list[JsonValue] | None = Field(default=None, alias="tileColors")

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the integer 1")
        return cast(Literal[1], value)

    @model_validator(mode="after")
    def validate_grid_lengths(self) -> OfficeLayout:
        expected = self.cols * self.rows
        if len(self.tiles) != expected:
            raise ValueError(f"tiles must contain exactly {expected} entries")
        if self.tile_colors is not None and len(self.tile_colors) != expected:
            raise ValueError(f"tileColors must contain exactly {expected} entries")
        return self

    def to_raw(self) -> RawOfficeLayout:
        return cast(
            RawOfficeLayout,
            self.model_dump(mode="python", by_alias=True, exclude_unset=True),
        )


__all__ = ["JsonValue", "OfficeLayout", "RawOfficeLayout"]
