# Floorplan permissions

Floorplan delegates authorization to Corridor and owns no role configuration.

Catalogue search and detail are public Employee operations. Loading a selected
layout into the Discord aggregate requires either:

- bot ownership; or
- the `keyholder` Corridor capability in a guild where the user can be
  resolved.

Configure capability groups with `[p]corridorsettings`. Groups are independent:
membership in `building_manager` does not imply `keyholder`.

Browser editing is not a Floorplan concern. CCTV separately authorizes writes to
its Discord page using bot-owner/keyholder access in a CCTV-enabled guild; its
editor page is intentionally open.
