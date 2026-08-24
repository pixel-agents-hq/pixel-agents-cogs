"""Pixel Index administration and public layout browsing commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from discord import app_commands
from redbot.core import commands

from corridor.domain import EMPLOYEE_KEY, llm_tool

from ..application import LAYOUT_SORT_CHOICES, CatalogueResult
from ..contracts.layout import RawOfficeLayout
from ..contracts.outbound import layout_loaded
from ..domain import normalize_http_url
from .admin_commands import AdminCommandsMixin
from .cog_base import PixelAgentsBase
from .layout_tools import (
    PERMISSION_DENIED_MESSAGE,
    SEARCH_TOOL_DESCRIPTION,
    VIEW_TOOL_DESCRIPTION,
    LayoutQuery,
    LayoutSlug,
    LayoutSort,
    LayoutTag,
    layout_detail_output,
    layout_search_output,
    search_input_error,
    tool_error,
)
from .layout_views import LayoutBrowseView, LayoutDetailView


class CatalogueCommandsMixin(PixelAgentsBase):
    """Adapt catalogue use cases to the locked Discord command tree."""

    @AdminCommandsMixin.floorplan_group.group(name="index", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def floorplan_pixelindex_group(self, ctx: commands.Context) -> None:
        """Show the configured Pixel Index endpoints and API health."""

        if ctx.invoked_subcommand is not None:
            return
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        settings = await self._settings_service.global_settings()
        ok, detail = await self._check_pixel_index_health(settings.pixel_index_api_url)
        await self._reply(
            ctx,
            f"Pixel Index API: `{settings.pixel_index_api_url}`\n"
            f"Pixel Index Web: `{settings.pixel_index_web_url}`\n"
            f"Health check: {'✅ ' + detail if ok else '🛑 ' + detail}",
        )

    async def _check_pixel_index_health(self, url: str) -> tuple[bool, str]:
        result = await self._catalogue_service.health(url)
        ok, value = self._legacy_catalogue_result(result)
        return ok, str(value)

    @staticmethod
    def _clean_url(url: str) -> str | None:
        try:
            return normalize_http_url(url)
        except ValueError:
            return None

    @floorplan_pixelindex_group.command(name="set")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(url="Pixel Index API base URL, e.g. https://pixel-index-api.nntin.xyz")
    async def cmd_pixelindex_set(self, ctx: commands.Context, url: str) -> None:
        """Set the Pixel Index API endpoint and check its health."""

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        try:
            clean = await self._settings_service.set_pixel_index_api_url(url)
        except ValueError:
            await self._reply(
                ctx, "Please provide a valid URL, e.g. `https://pixel-index-api.nntin.xyz`."
            )
            return
        ok, detail = await self._check_pixel_index_health(clean)
        await self._reply(
            ctx,
            f"Pixel Index API endpoint set to `{clean}`.\n"
            f"Health check: {'✅ ' + detail if ok else '🛑 ' + detail}",
        )

    @floorplan_pixelindex_group.command(name="setweb")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        url="Pixel Index web frontend base URL, e.g. https://pixel-index.vercel.app"
    )
    async def cmd_pixelindex_setweb(self, ctx: commands.Context, url: str) -> None:
        """Set the Pixel Index web frontend used by layout links."""

        try:
            clean = await self._settings_service.set_pixel_index_web_url(url)
        except ValueError:
            await self._reply(
                ctx, "Please provide a valid URL, e.g. `https://pixel-index.vercel.app`."
            )
            return
        await self._reply(ctx, f"Pixel Index web frontend set to `{clean}`.")

    async def _pixel_index_get(
        self, path: str, params: dict[str, str] | None = None
    ) -> tuple[bool, Any]:
        base = await self._settings_repository.pixel_index_api_url()
        result = await self._pixel_index_client.get_json(base, path, params)
        return self._legacy_catalogue_result(result)

    async def _pixel_index_search(
        self,
        *,
        query: str | None,
        tag: str | None,
        sort: str,
        cursor: str | None = None,
    ) -> tuple[bool, Any]:
        result = await self._catalogue_service.search(
            query=query, tag=tag, sort=sort, cursor=cursor
        )
        return self._legacy_catalogue_result(result)

    async def _pixel_index_layout(self, slug: str) -> tuple[bool, Any]:
        return self._legacy_catalogue_result(await self._catalogue_service.detail(slug))

    async def _load_pixel_index_layout(
        self, user_id: int, guild_id: int, slug: str
    ) -> tuple[bool, str]:
        result = await self._catalogue_service.load_layout(user_id, guild_id, slug)
        ok, value = self._legacy_catalogue_result(result)
        return ok, str(value)

    async def _publish_catalogue_layout(self, guild_id: int, layout: RawOfficeLayout) -> None:
        await self._send_to_guild(guild_id, layout_loaded(layout))

    @staticmethod
    def _legacy_catalogue_result(result: CatalogueResult[Any]) -> tuple[bool, Any]:
        if result.error is not None:
            return False, result.error.message
        return True, result.value

    @AdminCommandsMixin.floorplan_group.group(name="layout", invoke_without_command=True)
    async def floorplan_layout_group(self, ctx: commands.Context) -> None:
        """Browse shared office layouts from Pixel Index."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @floorplan_layout_group.command(name="search")
    @app_commands.describe(
        query="Text to search for in the title/description",
        tag="Only show layouts with this tag",
        sort="Sort order",
    )
    @app_commands.choices(
        sort=[app_commands.Choice(name=choice, value=choice) for choice in LAYOUT_SORT_CHOICES]
    )
    @llm_tool(
        name="floorplan_layout_search",
        description=SEARCH_TOOL_DESCRIPTION,
        required_group=EMPLOYEE_KEY,
    )
    async def cmd_layout_search(
        self,
        ctx: commands.Context,
        query: LayoutQuery = None,
        tag: LayoutTag = None,
        sort: LayoutSort = "newest",
    ) -> dict[str, object]:
        """Search Pixel Index for shared office layouts."""

        if not await self._corridor.require_permission(ctx, EMPLOYEE_KEY):
            return tool_error("permission_denied", PERMISSION_DENIED_MESSAGE)
        if ctx.interaction:
            await ctx.interaction.response.defer()
        invalid = search_input_error(query, tag, sort)
        if invalid is not None:
            error, message = invalid
            await self._send_public(ctx, message)
            return tool_error(error, message)
        if sort not in LAYOUT_SORT_CHOICES:
            sort = "newest"
        result = await self._catalogue_service.search(query=query, tag=tag, sort=sort)
        if result.error is not None:
            await self._send_public(ctx, result.error.message)
            return tool_error(result.error.code.value, result.error.message)
        page = result.value
        if page is None:
            message = "Pixel Index returned an unexpected response. Try again later."
            await self._send_public(ctx, message)
            return tool_error("unexpected_response", message)
        output = layout_search_output(page, query=query, tag=tag, sort=sort)
        if not page.layouts:
            await self._send_public(ctx, "No layouts found on Pixel Index.")
            return output
        bases = await self._catalogue_service.bases()
        await self._send_public(
            ctx,
            view=LayoutBrowseView(
                self._catalogue_service,
                ctx.author.id,
                query=query,
                tag=tag,
                sort=sort,
                pages=[page],
                page_index=0,
                api_base=bases.api,
                web_base=bases.web,
            ),
        )
        return output

    @floorplan_layout_group.command(name="view")
    @app_commands.describe(slug="Pixel Index layout slug")
    @llm_tool(
        name="floorplan_layout_view",
        description=VIEW_TOOL_DESCRIPTION,
        required_group=EMPLOYEE_KEY,
    )
    async def cmd_layout_view(
        self,
        ctx: commands.Context,
        slug: LayoutSlug,
    ) -> dict[str, object]:
        """Show a single Pixel Index layout by slug."""

        if not await self._corridor.require_permission(ctx, EMPLOYEE_KEY):
            return tool_error("permission_denied", PERMISSION_DENIED_MESSAGE)
        if ctx.interaction:
            await ctx.interaction.response.defer()
        if not isinstance(slug, str) or not slug.strip():
            message = "Please provide a non-empty Pixel Index layout slug."
            await self._send_public(ctx, message)
            return tool_error("invalid_slug", message)
        normalized_slug = slug.strip().lower()
        result = await self._catalogue_service.detail(normalized_slug)
        if result.error is not None:
            await self._send_public(ctx, result.error.message)
            return tool_error(result.error.code.value, result.error.message)
        detail = result.value
        if detail is None:
            message = "Pixel Index returned an unexpected response. Try again later."
            await self._send_public(ctx, message)
            return tool_error("unexpected_response", message)
        bases = await self._catalogue_service.bases()
        await self._send_public(
            ctx,
            view=LayoutDetailView(
                self._catalogue_service,
                ctx.author.id,
                detail,
                api_base=bases.api,
                web_base=bases.web,
            ),
        )
        return layout_detail_output(detail, api_base=bases.api, web_base=bases.web)
