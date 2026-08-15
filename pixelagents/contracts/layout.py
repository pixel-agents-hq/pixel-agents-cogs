"""Structural contract for the shared Pixel Agents office layout."""

from __future__ import annotations

from typing import Literal, TypeAlias, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
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

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> Literal[1]:
        """Reject numeric lookalikes such as ``1.0`` and ``True``.

        Pydantic's handling of ``Literal[1]`` changed across v2 releases and
        can accept values that compare equal to ``1`` even in strict mode.
        The wire contract requires the JSON integer used by upstream.
        """

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
        """Return the validated input shape without dropping unknown fields."""

        return cast(
            RawOfficeLayout,
            self.model_dump(mode="python", by_alias=True, exclude_unset=True),
        )
