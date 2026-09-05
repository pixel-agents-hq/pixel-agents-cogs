#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh pr edit` in this environment and
redirect to the REST call that actually works.

Why this exists: this environment's `gh` token lacks the `read:org`/
`read:discussion` scopes `gh pr edit`'s GraphQL mutation resolves on the
way to the actual edit (even though the edit itself needs neither scope),
so it always fails with a wall of "Your token has not been granted the
required scopes..." text -- never a real permissions problem with the
edit itself, always the same fix: `gh api repos/<owner>/<repo>/pulls/
<number> -X PATCH -f title="..." -f body="..."` instead, which uses the
REST API and never touches that GraphQL path. Rediscovering that by
reading the same scope-error wall a second time is a slow, repeatable
waste -- this hook fails fast with the working command instead.

See ../STOP_HOOKS.md for the full writeup.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

# gh's own global flags that take a value, so they're not mistaken for the
# "pr"/"edit" subcommand words when they appear before the subcommand
# (e.g. `gh --repo owner/repo pr edit 82 ...`).
_GLOBAL_VALUE_FLAGS = {"-R", "--repo", "--hostname"}

# `gh pr edit`'s own flags that take a value, so that value isn't mistaken
# for the `<number>`/`<url>`/`<branch>` target (e.g. `--title "x"` -- "x"
# is the flag's value, not the target).
_PR_EDIT_VALUE_FLAGS = {
    "--title",
    "--body",
    "--body-file",
    "-F",
    "--base",
    "-B",
    "--milestone",
    "-m",
    "--add-assignee",
    "--remove-assignee",
    "--add-label",
    "--remove-label",
    "--add-project",
    "--remove-project",
    "--add-reviewer",
    "--remove-reviewer",
}

# Rough, unquoted split on shell chaining operators (and newlines -- a
# multi-line Bash tool call is just as sequential as `;`-joined commands),
# matching check_pytest_scope.py's own splitting so a command with other,
# unrelated `gh` calls chained alongside a `gh pr edit` isn't over- or
# under-flagged.
_CHAIN_SPLIT_RE = re.compile(r"&&|\|\||;|\n")


def _pr_edit_target(tokens: list[str]) -> str | None:
    """Returns the `<number>`/`<url>`/`<branch>` argument if `tokens`
    invokes `gh pr edit` with an explicit target, `""` if it invokes it
    with no explicit target (edits the current branch's PR), or `None` if
    `tokens` doesn't invoke `gh pr edit` at all."""

    if not tokens:
        return None
    first = tokens[0]
    if first != "gh" and not first.endswith("/gh"):
        return None

    subcommand_words: list[str] = []
    index = 1
    while index < len(tokens) and len(subcommand_words) < 2:
        token = tokens[index]
        if token in _GLOBAL_VALUE_FLAGS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        subcommand_words.append(token)
        index += 1
    if subcommand_words != ["pr", "edit"]:
        return None

    # The first remaining non-flag token (that isn't a value-flag's own
    # value) is the PR number/url/branch -- gh accepts everything else as
    # flags only.
    skip_next = False
    for token in tokens[index:]:
        if skip_next:
            skip_next = False
            continue
        if token in _PR_EDIT_VALUE_FLAGS:
            skip_next = True
            continue
        if not token.startswith("-"):
            return token
    return ""


def _check_command(command: str) -> str | None:
    """Returns a blocking reason string, or None if `command` is fine."""

    for sub_command in _CHAIN_SPLIT_RE.split(command):
        try:
            tokens = shlex.split(sub_command)
        except ValueError:
            continue  # unbalanced quotes etc. -- not this hook's job to lint
        target = _pr_edit_target(tokens)
        if target is None:
            continue

        pr_ref = target or "<number>"
        return (
            "`gh pr edit` fails in this environment: its GraphQL mutation "
            "resolves fields (login/name/slug) that need the 'read:org'/"
            "'read:discussion' scopes this token doesn't have, even though "
            "the edit itself needs neither. Use the REST API directly "
            "instead, which never touches that path:\n"
            f"  gh api repos/<owner>/<repo>/pulls/{pr_ref} -X PATCH \\\n"
            '    -f title="..." -f body="..."\n'
            "(owner/repo: from `gh repo view --json owner,name`, or read "
            "off the PR's URL. Omit either -f flag to leave that field "
            "unchanged.)"
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

    reason = _check_command(command)
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
