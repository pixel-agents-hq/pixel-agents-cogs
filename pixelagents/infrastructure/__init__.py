"""Infrastructure for vendoring and building the Pixel Agents webview."""

from .settings import CONFIG_IDENTIFIER, GLOBAL_DEFAULTS, RedSettingsRepository
from .webview_build import (
    DEFAULT_BASE_PATH,
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
    "CONFIG_IDENTIFIER",
    "DEFAULT_BASE_PATH",
    "BuildOutcome",
    "BuildResult",
    "GLOBAL_DEFAULTS",
    "RedSettingsRepository",
    "WebviewBuildError",
    "build_webview",
    "built_base_path",
    "built_commit",
    "ensure_webview_built",
    "missing_tools",
    "owner_notification_for",
    "pinned_commit",
]
