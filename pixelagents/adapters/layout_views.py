"""Discord Components V2 views for browsing the Pixel Index catalogue."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from ..application.catalogue import CatalogueService
from ..contracts.pixel_index import LayoutDetail, LayoutListResponse


def absolute_url(base: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


class LayoutBrowseView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """Paginated Pixel Index search results, rendered with Components V2."""

    def __init__(
        self,
        catalogue: CatalogueService,
        owner_id: int,
        *,
        query: str | None,
        tag: str | None,
        sort: str,
        pages: list[LayoutListResponse],
        page_index: int,
        api_base: str,
        web_base: str,
    ) -> None:
        super().__init__(timeout=180)
        self.catalogue = catalogue
        self.owner_id = owner_id
        self.query = query
        self.tag = tag
        self.sort = sort
        self.pages = pages
        self.page_index = page_index
        self.api_base = api_base
        self.web_base = web_base
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this search can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        page = self.pages[self.page_index]
        layouts = page.layouts
        total = page.total if page.total is not None else len(layouts)

        header_bits = [f"**Pixel Index layouts** — {total} match(es)"]
        if self.query:
            header_bits.append(f"matching `{self.query}`")
        if self.tag:
            header_bits.append(f"tagged `{self.tag}`")
        container = discord.ui.Container(discord.ui.TextDisplay(" ".join(header_bits)))

        for entry in layouts:
            display_name = entry.author.displayName if entry.author else None
            visible_cols = entry.visibleCols if entry.visibleCols is not None else "?"
            visible_rows = entry.visibleRows if entry.visibleRows is not None else "?"
            stats = (
                f"{visible_cols}×{visible_rows} · "
                f"{entry.furniture or 0} furniture · by {display_name or 'unknown'}"
            )
            lines = [f"**{entry.title or entry.slug}**", stats]
            if entry.tags:
                lines.append("_" + ", ".join(entry.tags) + "_")
            thumbnail_path = entry.files.thumbnail if entry.files else None
            accessory = (
                discord.ui.Thumbnail(absolute_url(self.api_base, thumbnail_path))
                if thumbnail_path
                else discord.ui.Button(label="?", disabled=True)
            )
            container.add_item(
                discord.ui.Section(discord.ui.TextDisplay("\n".join(lines)), accessory=accessory)
            )

        select_row = discord.ui.ActionRow()
        select = discord.ui.Select(
            placeholder="View a layout…",
            options=[
                discord.SelectOption(
                    label=(entry.title or entry.slug)[:100],
                    value=entry.slug,
                    description=(entry.description or "")[:100] or None,
                )
                for entry in layouts
            ],
        )
        select.callback = self._make_select_callback(select)
        select_row.add_item(select)
        container.add_item(select_row)

        nav_row = discord.ui.ActionRow()
        previous = discord.ui.Button(
            label="◀ Prev",
            style=discord.ButtonStyle.secondary,
            disabled=self.page_index == 0,
        )
        previous.callback = self._on_prev
        at_last_known_page = self.page_index >= len(self.pages) - 1
        following = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=at_last_known_page and page.nextCursor is None,
        )
        following.callback = self._on_next
        nav_row.add_item(previous)
        nav_row.add_item(following)
        container.add_item(nav_row)
        self.add_item(container)

    def _make_select_callback(
        self, select: discord.ui.Select
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def on_select(interaction: discord.Interaction) -> None:
            result = await self.catalogue.detail(select.values[0])
            if result.error is not None:
                await interaction.response.send_message(result.error.message, ephemeral=True)
                return
            detail = result.value
            if detail is None:
                await interaction.response.send_message(
                    "Pixel Index returned an unexpected response. Try again later.",
                    ephemeral=True,
                )
                return
            detail_view = LayoutDetailView(
                self.catalogue,
                self.owner_id,
                detail,
                api_base=self.api_base,
                web_base=self.web_base,
                back=self,
            )
            await interaction.response.edit_message(view=detail_view)

        return on_select

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index == 0:
            await interaction.response.defer()
            return
        new_view = LayoutBrowseView(
            self.catalogue,
            self.owner_id,
            query=self.query,
            tag=self.tag,
            sort=self.sort,
            pages=self.pages,
            page_index=self.page_index - 1,
            api_base=self.api_base,
            web_base=self.web_base,
        )
        await interaction.response.edit_message(view=new_view)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < len(self.pages):
            new_pages = self.pages
            new_index = self.page_index + 1
        else:
            cursor = self.pages[self.page_index].nextCursor
            if not cursor:
                await interaction.response.defer()
                return
            result = await self.catalogue.search(
                query=self.query,
                tag=self.tag,
                sort=self.sort,
                cursor=cursor,
            )
            if result.error is not None:
                await interaction.response.send_message(result.error.message, ephemeral=True)
                return
            page = result.value
            if page is None:
                await interaction.response.send_message(
                    "Pixel Index returned an unexpected response. Try again later.",
                    ephemeral=True,
                )
                return
            new_pages = [*self.pages, page]
            new_index = len(new_pages) - 1
        new_view = LayoutBrowseView(
            self.catalogue,
            self.owner_id,
            query=self.query,
            tag=self.tag,
            sort=self.sort,
            pages=new_pages,
            page_index=new_index,
            api_base=self.api_base,
            web_base=self.web_base,
        )
        await interaction.response.edit_message(view=new_view)


class LayoutDetailView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """A single Pixel Index layout, rendered with Components V2."""

    def __init__(
        self,
        catalogue: CatalogueService,
        owner_id: int,
        detail: LayoutDetail,
        *,
        api_base: str,
        web_base: str,
        back: LayoutBrowseView | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.catalogue = catalogue
        self.owner_id = owner_id
        self.detail = detail
        self.api_base = api_base
        self.web_base = web_base
        self.back = back
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        detail = self.detail
        display_name = detail.author.displayName if detail.author else None
        lines = [f"**{detail.title or detail.slug}**"]
        if detail.description:
            lines.append(detail.description)
        visible_cols = detail.visibleCols if detail.visibleCols is not None else "?"
        visible_rows = detail.visibleRows if detail.visibleRows is not None else "?"
        lines.append(
            f"{visible_cols}×{visible_rows} · "
            f"{detail.furniture or 0} furniture · {detail.areas or 0} areas · "
            f"{detail.pets or 0} pets · {detail.seats or 0} seats"
        )
        lines.append(f"By {display_name or 'unknown'}")
        if detail.tags:
            lines.append("Tags: " + ", ".join(detail.tags))

        container = discord.ui.Container(discord.ui.TextDisplay("\n".join(lines)))
        preview_path = detail.files.preview if detail.files else None
        if preview_path:
            container.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(absolute_url(self.api_base, preview_path))
                )
            )

        actions = discord.ui.ActionRow()
        download_path = detail.files.layout if detail.files else None
        if download_path:
            actions.add_item(
                discord.ui.Button(
                    label="Download JSON",
                    url=absolute_url(self.api_base, download_path),
                )
            )
        if detail.slug:
            actions.add_item(
                discord.ui.Button(
                    label="View on site",
                    url=absolute_url(self.web_base, f"/layouts/{detail.slug}"),
                )
            )
        load_button = discord.ui.Button(label="Load into office", style=discord.ButtonStyle.primary)
        load_button.callback = self._on_load
        actions.add_item(load_button)
        if self.back is not None:
            back_button = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)
            back_button.callback = self._on_back
            actions.add_item(back_button)
        container.add_item(actions)
        self.add_item(container)

    async def _on_load(self, interaction: discord.Interaction) -> None:
        result = await self.catalogue.load_layout(interaction.user.id, self.detail.slug)
        if result.error is not None:
            message = result.error.message
        else:
            message = result.value or "The layout could not be loaded. Try again later."
        await interaction.response.send_message(message, ephemeral=True)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self.back)


__all__ = ["LayoutBrowseView", "LayoutDetailView", "absolute_url"]
