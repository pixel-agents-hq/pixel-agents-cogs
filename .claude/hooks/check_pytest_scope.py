#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block a pytest invocation that spans more than one
cog in this repo, or that gives pytest no path at all (which collects every
cog into one process by default).

Why this exists: each cog directory (corridor, architect, pico, toolbox,
testbench, deskutils, floorplan, pixelagents, ...) installs its own,
mutually incompatible `sys.modules["redbot.core"]`/`discord` stub in its own
conftest.py. Running more than one cog's suite in the same pytest process
means whichever cog's conftest imports first silently wins that stub for the
rest of the run -- every other cog's tests then fail against the wrong
stub shapes. These failures look like real bugs (AssertionError, wrong
isinstance checks, etc.) but are pure test-process contamination; the fix
is always "run each cog in its own pytest invocation", never a code change.
Rediscovering that by reading a giant failure list is a slow, repeatable
waste -- this hook fails fast with a one-line explanation instead.

See ../STOP_HOOKS.md for the full writeup.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

# pytest flags that consume the next token as a value (not a test path).
# Also covers python's own "-m" (as in `python -m pytest ...`), which
# consumes "pytest" itself -- handled the same way rather than special-cased.
_VALUE_FLAGS = {
    "-m",
    "-k",
    "-n",
    "-p",
    "--maxfail",
    "--timeout",
    "--durations",
    "--rootdir",
    "--basetemp",
    "--cov",
    "--tb",
    "--dist",
}

# Rough, unquoted split on shell chaining operators (and newlines -- a
# multi-line Bash tool call is just as sequential as `;`-joined commands)
# so a command with multiple SEPARATE pytest invocations (which don't share
# a process, so don't conflict) isn't flagged for the union of cogs across
# all of them. Doesn't understand quoting/subshells around these operators
# -- a rare enough shape in practice that the simplification is worth the
# size.
_CHAIN_SPLIT_RE = re.compile(r"&&|\|\||;|\n")


def _discover_cogs(root: str) -> set[str]:
    """A "cog" here is any immediate subdirectory of the repo root that
    installs its own conftest.py (directly or under tests/) -- exactly the
    set of directories whose test suites can't safely share one pytest
    process. Discovered fresh each run instead of hardcoded, so adding or
    removing a cog never needs a matching edit to this hook."""

    cogs: set[str] = set()
    try:
        entries = os.listdir(root)
    except OSError:
        return cogs
    for name in entries:
        path = os.path.join(root, name)
        if not os.path.isdir(path) or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(path, "conftest.py")) or os.path.isfile(
            os.path.join(path, "tests", "conftest.py")
        ):
            cogs.add(name)
    return cogs


def _cogs_and_targets_in_pytest_invocation(tokens: list[str], known_cogs: set[str]) -> tuple[
    set[str], list[str]
] | None:
    """Returns (cogs_referenced, positional_targets) for one pytest
    invocation among `tokens`, or None if `tokens` doesn't invoke pytest at
    all. Only tokens after the "pytest" word are scanned -- everything
    before it (python, -m, uv run, ...) is invocation machinery, not a test
    target."""

    try:
        pytest_index = tokens.index("pytest")
    except ValueError:
        return None

    cogs: set[str] = set()
    targets: list[str] = []
    skip_next = False
    for token in tokens[pytest_index + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_FLAGS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        targets.append(token)
        parts = [p for p in token.split("/") if p and p != "."]
        for part in parts:
            if part in known_cogs:
                cogs.add(part)
                break
    return cogs, targets


def _check_command(command: str, known_cogs: set[str]) -> str | None:
    """Returns a blocking reason string, or None if `command` is fine."""

    for sub_command in _CHAIN_SPLIT_RE.split(command):
        try:
            tokens = shlex.split(sub_command)
        except ValueError:
            continue  # unbalanced quotes etc. -- not this hook's job to lint
        result = _cogs_and_targets_in_pytest_invocation(tokens, known_cogs)
        if result is None:
            continue
        cogs, targets = result

        if len(cogs) >= 2:
            cog_list = ", ".join(sorted(cogs))
            run_lines = "\n".join(f"  python -m pytest -q {cog}" for cog in sorted(cogs))
            return (
                f"This pytest command spans multiple cogs in one process: {cog_list}.\n"
                "Each cog installs its own conflicting redbot/discord test stub in its "
                "own conftest.py -- running more than one together makes whichever "
                "collects first silently win the stub for the rest, producing "
                "unrelated, misleading failures (not real bugs). Run each cog in its "
                "own pytest invocation instead, e.g.:\n" + run_lines
            )

        if not targets:
            cog_list = ", ".join(sorted(known_cogs))
            return (
                "This pytest command gives no test path, so it would collect every "
                f"cog's suite into ONE process: {cog_list}.\n"
                "Each cog installs its own conflicting redbot/discord test stub in its "
                "own conftest.py -- running more than one together makes whichever "
                "collects first silently win the stub for the rest, producing "
                "unrelated, misleading failures (not real bugs). Specify exactly one "
                "cog directory, e.g.:\n  python -m pytest -q corridor"
            )

    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed input -- fail open, never block on our own bug

    if payload.get("tool_name") != "Bash":
        return 0
    command = payload.get("tool_input", {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return 0

    known_cogs = _discover_cogs(os.getcwd())
    if len(known_cogs) < 2:
        return 0  # nothing to conflict with in this checkout

    reason = _check_command(command, known_cogs)
    if reason is None:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
