"""Infrastructure for vendoring and building the Pixel Agents webview.

Deliberately does NOT re-export `.settings` (`RedSettingsRepository`,
`CONFIG_IDENTIFIER`, `GLOBAL_DEFAULTS`) here: that module needs
`redbot.core.Config` at import time, and every other submodule in this
package (`webview_build`, `pixel_agents_adapter`, `furniture_styles`,
`color_names`) does not. Re-exporting it from this package's own
`__init__.py` would mean importing *any* of those redbot-free submodules
transitively requires redbot too, since Python always runs a package's
`__init__.py` before any of its submodules -- a real regression this
comment exists to prevent reintroducing (it broke
`contracts/pixel_agents/verify_outbound.py` and its tests, which
deliberately import only `pixelagents.application.office`-shaped things
and expect that to stay redbot-free). Nothing in this repo actually
imports `RedSettingsRepository`/`CONFIG_IDENTIFIER`/`GLOBAL_DEFAULTS` via
this package-level path -- every real consumer already imports
`..infrastructure.settings` directly (see `pixelagents/adapters/cog_base.py`).
"""

from .webview_build import (
    RELATIVE_BASE_PATH,
    BuildOutcome,
    BuildResult,
    WebviewBuildError,
    build_webview,
    built_base_path,
    built_commit,
    ensure_webview_built,
    missing_tools,
    owner_notification_for,
    pinned_commit,
)

__all__ = [
    "RELATIVE_BASE_PATH",
    "BuildOutcome",
    "BuildResult",
    "WebviewBuildError",
    "build_webview",
    "built_base_path",
    "built_commit",
    "ensure_webview_built",
    "missing_tools",
    "owner_notification_for",
    "pinned_commit",
]
