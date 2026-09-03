"""Shared input parsing for `adapters/commands.py` and
`adapters/create_agent_panel.py` -- split out from `commands.py` itself so
the panel module can import it without a circular import (`commands.py`
imports the panel's views to send them)."""

from __future__ import annotations

# A bot owner may reset an agent's request_timeout_seconds back to "use
# corridor's own default" with any of these literals, case-insensitively --
# accepted by the create modal, `create`'s optional positional arg, and
# `requesttimeout`.
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
            f"{raw!r} is not a valid request_timeout_seconds -- give a positive number of "
            "seconds, or `default` to use corridor's own default"
        )
    if value <= 0:
        return None, "request_timeout_seconds must be a positive number, or `default`"
    return value, None


def parse_max_tool_calls(raw: str) -> tuple[int | None, str | None]:
    """Returns `(value, error)` -- exactly one is `None`. Used by the create
    modal, where every field arrives as a plain `TextInput` string rather
    than an already-`int`-converted Discord command argument."""

    try:
        value = int(raw)
    except ValueError:
        return None, f"{raw!r} is not a valid max_tool_calls -- give a positive whole number"
    if value < 1:
        return None, "max_tool_calls must be a positive integer"
    return value, None


__all__ = ["TIMEOUT_DEFAULT_LITERALS", "parse_max_tool_calls", "parse_request_timeout"]
