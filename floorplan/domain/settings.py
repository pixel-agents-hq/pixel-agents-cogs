"""Framework-free validation for administrator-controlled setting values."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_http_url(value: str) -> str:
    """Normalize an absolute HTTP(S) base URL or reject it."""

    clean = value.strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL.")
    return clean


__all__ = ["normalize_http_url"]
