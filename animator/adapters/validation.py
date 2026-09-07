"""Shared input parsing for `adapters/commands.py`. Mirrors bootcamp's own
`adapters/validation.py::parse_request_timeout` (animator has no per-agent
indirection -- `request_timeout_seconds` is animator's one global setting,
not one keyed by an `agent_key` argument)."""

from __future__ import annotations

# A bot owner may reset the request timeout back to "use corridor's own
# default" with any of these literals, case-insensitively.
TIMEOUT_DEFAULT_LITERALS = frozenset({"default", "none"})


def parse_request_timeout(raw: str) -> tuple[float | None, str | None]:
    """Returns `(value, error)` -- exactly one is `None`. `value` is the
    parsed `request_timeout_seconds` (`None` meaning "reset to corridor's
    own default") on success; `error` is a user-facing message on
    failure."""

    if raw.strip().lower() in TIMEOUT_DEFAULT_LITERALS:
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, (
            f"{raw!r} is not a valid request timeout -- give a positive number of seconds, "
            "or `default` to use corridor's own default"
        )
    if value <= 0:
        return None, "Request timeout must be a positive number of seconds, or `default`."
    return value, None


def parse_read_timeout(raw: str) -> tuple[float | None, str | None]:
    """Same parsing shape as `parse_request_timeout` above, for
    `read_timeout_seconds` (`GlobalSettings`) -- kept as its own function,
    not a thin alias, so its error text names the right setting and
    default (`McpClientPool`'s own "wait indefinitely", not corridor's LLM
    connection default)."""

    if raw.strip().lower() in TIMEOUT_DEFAULT_LITERALS:
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, (
            f"{raw!r} is not a valid read timeout -- give a positive number of seconds, "
            "or `default` to wait indefinitely"
        )
    if value <= 0:
        return None, "Read timeout must be a positive number of seconds, or `default`."
    return value, None


__all__ = ["TIMEOUT_DEFAULT_LITERALS", "parse_read_timeout", "parse_request_timeout"]
