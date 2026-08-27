"""`[p]architect office ...`: Discord commands calling the *same*
`OfficeLayoutService` methods the LLM tools use (`tools/office_tools.py`)
-- one mutation surface, two callers, per
docs/architect-semantic-ir-design.md sections 7/8. Owner-only: this
mutates architect's live office layout and broadcasts to connected
webview clients, the same risk tier as `[p]architect a2a host/port`.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..application.office_layout_service import OfficeLayoutService, OfficeValidationError
from ..domain.office_ir import FurnitureKind, GridPosition, GridRect, TileKind
from .commands import CommandsMixin


class OfficeCommandsMixin:
    """Requires `self._office_layout_service: OfficeLayoutService` and
    `self._corridor` (both provided by CogBase). Nests under
    `CommandsMixin.architect_group`, so this class must be mixed in
    alongside `CommandsMixin` (see `architect.py`)."""

    _office_layout_service: OfficeLayoutService
    _corridor: Any

    @CommandsMixin.architect_group.group(name="office", invoke_without_command=True)
    @commands.is_owner()
    async def office_group(self, ctx: commands.Context) -> None:
        """Manage architect's own office layout. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @office_group.command(name="describe")
    @commands.is_owner()
    async def office_describe(self, ctx: commands.Context) -> None:
        """Summarize the current office: zones, furniture, seats."""

        office = await self._office_layout_service.describe()
        description = (
            f"{office.width}x{office.height} grid -- {len(office.zones)} zone(s), "
            f"{len(office.furniture)} furniture item(s), {len(office.seats)} seat(s)."
        )
        await self._corridor.send_reply(ctx, description=description)  # type: ignore[attr-defined]

    @office_group.command(name="painttiles")
    @commands.is_owner()
    async def office_paint_tiles(
        self,
        ctx: commands.Context,
        col: int,
        row: int,
        width: int,
        height: int,
        kind: str,
        material: int | None = None,
        color: str | None = None,
    ) -> None:
        """Paint a rectangle to `kind` ("floor" or "wall"). `material`
        (1-9) is required for "floor"."""

        try:
            resolved_kind = TileKind(kind)
        except ValueError:
            valid = ", ".join(k.value for k in (TileKind.FLOOR, TileKind.WALL))
            await self._corridor.send_reply(  # type: ignore[attr-defined]
                ctx, description=f"Unknown kind {kind!r}. Valid kinds: {valid}."
            )
            return
        try:
            await self._office_layout_service.paint_tiles(
                area=GridRect(GridPosition(col, row), width, height),
                kind=resolved_kind,
                material=material,
                color=color,
            )
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"Painted ({col}, {row}) {width}x{height} to {resolved_kind.value}."
        )

    @office_group.command(name="describetiles")
    @commands.is_owner()
    async def office_describe_tiles(
        self, ctx: commands.Context, col: int, row: int, width: int, height: int
    ) -> None:
        """Show the exact per-tile state of a bounded region."""

        try:
            tiles = await self._office_layout_service.describe_tiles(
                area=GridRect(GridPosition(col, row), width, height)
            )
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        lines = []
        for offset, tile in enumerate(tiles):
            position = GridPosition(col + offset % width, row + offset // width)
            lines.append(
                f"({position.col}, {position.row}): {tile.kind.value}"
                f"{f' material={tile.material}' if tile.material is not None else ''}"
                f"{f' color={tile.color}' if tile.color is not None else ''}"
                f"{f' zone={tile.zone_label}' if tile.zone_label is not None else ''}"
            )
        await self._corridor.send_reply(ctx, description="\n".join(lines))  # type: ignore[attr-defined]

    @office_group.command(name="place")
    @commands.is_owner()
    async def office_place_furniture(
        self, ctx: commands.Context, kind: str, style: str, col: int, row: int
    ) -> None:
        """Place a piece of furniture at an exact position."""

        try:
            resolved_kind = FurnitureKind(kind)
        except ValueError:
            valid = ", ".join(k.value for k in FurnitureKind)
            await self._corridor.send_reply(  # type: ignore[attr-defined]
                ctx, description=f"Unknown kind {kind!r}. Valid kinds: {valid}."
            )
            return
        try:
            item = await self._office_layout_service.place_furniture(
                kind=resolved_kind, style=style, position=GridPosition(col, row)
            )
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(  # type: ignore[attr-defined]
            ctx,
            description=(
                f"Placed `{item.id}` ({item.style}) at ({item.position.col}, {item.position.row})."
            ),
        )

    @office_group.command(name="move")
    @commands.is_owner()
    async def office_move_furniture(
        self, ctx: commands.Context, furniture_id: str, col: int, row: int
    ) -> None:
        """Move an existing furniture item to (col, row)."""

        try:
            item = await self._office_layout_service.move_furniture(
                furniture_id=furniture_id, position=GridPosition(col, row)
            )
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"Moved `{item.id}` to ({item.position.col}, {item.position.row})."
        )

    @office_group.command(name="remove")
    @commands.is_owner()
    async def office_remove_furniture(self, ctx: commands.Context, furniture_id: str) -> None:
        """Remove a furniture item."""

        try:
            await self._office_layout_service.remove_furniture(furniture_id=furniture_id)
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(ctx, description=f"Removed `{furniture_id}`.")  # type: ignore[attr-defined]

    @office_group.command(name="createzone")
    @commands.is_owner()
    async def office_create_zone(
        self,
        ctx: commands.Context,
        label: str,
        color: str,
        col: int,
        row: int,
        width: int,
        height: int,
    ) -> None:
        """Create a named overlay zone at (col, row) with the given size."""

        try:
            zone = await self._office_layout_service.create_zone(
                label=label, color=color, tiles=GridRect(GridPosition(col, row), width, height)
            )
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"Created zone `{zone.id}` ({zone.label})."
        )

    @office_group.command(name="resizezone")
    @commands.is_owner()
    async def office_resize_zone(
        self, ctx: commands.Context, zone_id: str, col: int, row: int, width: int, height: int
    ) -> None:
        """Replace a zone's tile region."""

        try:
            zone = await self._office_layout_service.resize_zone(
                zone_id=zone_id, tiles=GridRect(GridPosition(col, row), width, height)
            )
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"Resized zone `{zone.id}` ({zone.label})."
        )

    @office_group.command(name="removezone")
    @commands.is_owner()
    async def office_remove_zone(self, ctx: commands.Context, zone_id: str) -> None:
        """Remove a zone."""

        try:
            await self._office_layout_service.remove_zone(zone_id=zone_id)
        except OfficeValidationError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))  # type: ignore[attr-defined]
            return
        await self._corridor.send_reply(ctx, description=f"Removed zone `{zone_id}`.")  # type: ignore[attr-defined]


__all__ = ["OfficeCommandsMixin"]
