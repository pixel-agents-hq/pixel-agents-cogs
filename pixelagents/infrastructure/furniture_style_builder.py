"""Derive a semantic furniture-style manifest from the built furniture
catalog.

`furniture-catalog.json` (built upstream by `buildFurnitureCatalog()`/
`flattenManifest()` in the vendored `pixel-agents` repo, synced into
`webview_dist/` by `webview_build.py::_sync_dist`) is a *rendering*
catalog: one entry per concrete sprite variant, keyed by an opaque asset
ID (`WOODEN_CHAIR_FRONT`, `WOODEN_CHAIR_SIDE:left`, ...). Consumers that
want to reason semantically -- "place a wooden chair facing north" --
need the rotation/mirror/state variants collapsed back into one named
*style* with a small set of facings, not 4+ separate asset IDs to know
about individually.

This module performs that collapse once, at webview-build time, so the
result (`furniture-styles.json`) is a **generated artifact that always
matches whatever `pixel-agents` commit is actually vendored** -- see
`docs/architect-semantic-ir-design.md` section 6.4. It has no filesystem/
network access of its own; the build orchestration
(`webview_build.py::_build_furniture_styles`) is what reads/writes the
actual files, so this stays trivially unit-testable against a bare
catalog list.

v2 (docs/architect-semantic-ir-design.md section 6.4): each facing now
carries its own real footprint (`footprint_width`/`footprint_height`/
`background_tiles`), read directly from that catalog entry's own fields
-- never rotated or derived from a single canonical (w,h) pair, since
real assets don't rotate that way (confirmed directly: `DESK_FRONT` is
3x2, `DESK_SIDE` -- the same style, rotated -- is 1x4, not a transpose of
one pair). Plus two style-level booleans, `can_place_on_walls`/
`can_place_on_surfaces`, read from the catalog's own
`canPlaceOnWalls`/`canPlaceOnSurfaces` fields.
"""

from __future__ import annotations

from typing import Any

# Bump whenever `build_furniture_style_manifest`'s *output shape* changes
# (a field added/removed/renamed on styles or facings) -- checked by
# `webview_build.is_up_to_date()` against a marker file alongside the
# vendored commit/base-path markers, so a host with an already-built
# `webview_dist/` from before a schema change self-heals on its next
# `cog_load()` instead of indefinitely serving a `furniture-styles.json`
# shaped for the old schema (the exact incident that made the flat
# `{"south": "DESK_FRONT"}` facing shape -> nested
# `{"south": {"catalog_id": ..., "footprint_width": ...}}` change in this
# module crash every consumer's `FurnitureStyleManifest.from_raw()` on any
# host whose vendored `pixel-agents` commit hadn't also changed).
MANIFEST_SCHEMA_VERSION = 2

# Pixel Agents' own category vocabulary is small and closed -- the catalog
# itself enumerates it (`FURNITURE_CATEGORIES` in the vendored
# `furnitureCatalog.ts`) -- unlike the much larger, more volatile
# per-asset-ID table this replaces, hand-maintaining this 7-entry mapping
# is a fine, stable seam. A category not in this table (a genuinely new
# one upstream has never shipped before) is omitted from the manifest
# rather than crashing the build -- see `build_furniture_style_manifest`.
_CATEGORY_TO_KIND: dict[str, str] = {
    "desks": "desk",
    "chairs": "seating",
    "storage": "storage",
    "decor": "decor",
    "electronics": "electronics",
    "wall": "wall_fixture",
    "misc": "misc",
}

# Catalog `orientation` -> semantic `Direction` (architect/domain/office_ir.py).
_ORIENTATION_TO_FACING: dict[str, str] = {
    "front": "south",
    "back": "north",
    "left": "west",
    "right": "east",
    "side": "east",
}


def _footprint(entry: dict[str, Any]) -> dict[str, Any]:
    """The real, per-entry footprint record -- never guessed, always read
    straight from this specific catalog entry's own fields."""

    return {
        "footprint_width": entry["footprintW"],
        "footprint_height": entry["footprintH"],
        "background_tiles": entry.get("backgroundTiles", 0),
    }


def build_furniture_style_manifest(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse a flat furniture catalog into `{"styles": [...]}`.

    One style per distinct `(groupId or id)`, with a `facings` map keyed
    by semantic direction and pointing at a `{catalog_id, footprint_width,
    footprint_height, background_tiles}` record for that direction. See
    `docs/architect-semantic-ir-design.md` section 6.4 for the full
    derivation rules this implements.
    """

    # "On" state variants are excluded entirely: only the off/stateless
    # variant of a style is ever addressed by kind/style/facing (§6.4 rule
    # 5) -- on/off has no IR concept yet.
    on_state_ids = {entry["id"] for entry in catalog if entry.get("state") == "on"}

    groups: dict[str, list[dict[str, Any]]] = {}
    kinds: dict[str, str] = {}
    labels: dict[str, str] = {}
    for entry in catalog:
        if entry["id"] in on_state_ids:
            continue
        kind = _CATEGORY_TO_KIND.get(entry.get("category", ""))
        if kind is None:
            continue  # unrecognized category -- omit, don't crash (§6.4 rule 6)

        style_id = str(entry.get("groupId") or entry["id"]).lower()
        groups.setdefault(style_id, []).append(entry)
        kinds.setdefault(style_id, kind)
        # Prefer the front/stateless variant's label when more than one
        # entry shares a style -- it's the one with no orientation/state
        # suffix to strip, and the first one encountered is good enough
        # for entries where every variant shares the same label anyway.
        if style_id not in labels or entry.get("orientation") in (None, "front"):
            labels[style_id] = str(entry.get("label", style_id))

    styles = []
    for style_id, entries in groups.items():
        facings: dict[str, dict[str, Any]] = {}
        for entry in entries:
            orientation = entry.get("orientation")
            if orientation is None:
                continue
            facing = _ORIENTATION_TO_FACING.get(orientation)
            if facing is None:
                continue
            facings[facing] = {"catalog_id": entry["id"], **_footprint(entry)}
            if entry.get("mirrorSide") and orientation == "side":
                facings["west"] = {"catalog_id": f"{entry['id']}:left", **_footprint(entry)}

        default_facing = "south" if "south" in facings else (next(iter(facings), None))
        style: dict[str, Any] = {
            "style": style_id,
            "kind": kinds[style_id],
            "label": labels[style_id],
            "can_place_on_walls": any(entry.get("canPlaceOnWalls") for entry in entries),
            "can_place_on_surfaces": any(entry.get("canPlaceOnSurfaces") for entry in entries),
            "facings": facings,
            "default_facing": default_facing,
        }
        if not facings:
            # No orientation at all -- e.g. CUSHIONED_BENCH, WHITEBOARD,
            # BIN, most decor -- style_id is a *lower-cased* handle for
            # LLM/tool use, not the real Pixel Agents asset id, which
            # keeps whatever case it was authored in (almost always
            # upper). Without recording the real id (and its footprint)
            # separately here, the only id a consumer could reverse-lookup
            # from is the lower-cased style id itself, which never matches
            # any real `furniture[].type` string in Pixel JSON -- every
            # such item would silently fail to decode. `entries[0]` is
            # arbitrary only in the (unseen in practice) case where two
            # facing-less entries share one groupId; picking the first is
            # consistent with the label-selection tie-break above.
            style["catalog_id"] = entries[0]["id"]
            style.update(_footprint(entries[0]))
        styles.append(style)

    styles.sort(key=lambda entry: str(entry["style"]))
    return {"styles": styles}


__all__ = ["MANIFEST_SCHEMA_VERSION", "build_furniture_style_manifest"]
