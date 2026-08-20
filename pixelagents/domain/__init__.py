"""Framework-independent validation used by the Pixel Agents build cog."""

from .settings import parse_commit_ref, parse_webview_base_path

__all__ = ["parse_commit_ref", "parse_webview_base_path"]
