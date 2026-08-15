"""Structural contract for the shared Pixel Agents office layout."""

from __future__ import annotations

from typing import Literal, TypeAlias, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic import (
    JsonValue as PydanticJsonValue,
)

JsonValue = PydanticJsonValue
RawOfficeLayout: TypeAlias = dict[str, JsonValue]


class OfficeLayout(BaseModel):
    """Validate the layout invariants relied on by the browser.

    Tile and furniture entries deliberately remain opaque.  They are owned by
    the bundled web application and Pixel Index, while this service only needs
    the grid dimensions and collection sizes to be safe.  ``extra="allow"``
    and :meth:`to_raw` preserve fields from newer upstream layout revisions.
    """

    model_config = ConfigDict(extra="allow", strict=True)

    version: Literal[1]
    cols: int = Field(gt=0)
    rows: int = Field(gt=0)
    tiles: list[JsonValue]
    furniture: list[JsonValue]
    tile_colors: list[JsonValue] | None = Field(default=None, alias="tileColors")

    @model_validator(mode="after")
    def validate_grid_lengths(self) -> OfficeLayout:
        expected = self.cols * self.rows
        if len(self.tiles) != expected:
            raise ValueError(f"tiles must contain exactly {expected} entries")
        if self.tile_colors is not None and len(self.tile_colors) != expected:
            raise ValueError(f"tileColors must contain exactly {expected} entries")
        return self

    def to_raw(self) -> RawOfficeLayout:
        """Return the validated input shape without dropping unknown fields."""

        return cast(
            RawOfficeLayout,
            self.model_dump(mode="python", by_alias=True, exclude_unset=True),
        )
