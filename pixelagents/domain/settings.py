"""Framework-free validation for administrator-controlled setting values."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def normalize_http_url(value: str) -> str:
    """Normalize an absolute HTTP(S) base URL or reject it."""

    clean = value.strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL.")
    return clean


_COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_COMMIT_URL_RE = re.compile(
    r"^https://github\.com/pixel-agents-hq/pixel-agents/(?:tree|commit)/"
    r"([0-9a-fA-F]{7,40})(?:[/?#].*)?$"
)


def parse_commit_ref(value: str) -> str:
    """Validate and normalize a user-supplied Pixel Agents commit reference.

    Accepts either a raw commit hash (7-40 hex characters) or a link to it
    (`https://github.com/pixel-agents-hq/pixel-agents/tree/<hash>` or
    `.../commit/<hash>`), and returns the lowercased hash alone.
    """

    clean = value.strip()
    url_match = _COMMIT_URL_RE.match(clean)
    candidate = url_match.group(1) if url_match else clean
    if not _COMMIT_HASH_RE.match(candidate):
        raise ValueError(
            "Expected a commit hash or a "
            "https://github.com/pixel-agents-hq/pixel-agents/tree/<hash> link."
        )
    return candidate.lower()


__all__ = ["normalize_http_url", "parse_commit_ref"]
