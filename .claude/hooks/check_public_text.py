#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block publishing text that describes credentials.

Why this exists: PR bodies, issue comments and commit messages are public,
and GitHub keeps their edit history, so a sentence describing what kind of
token a secret holds, what it can access, or whose account it belongs to
can't be fully taken back once posted. AGENTS.md's rule is to name a secret
by its name and purpose only; this hook catches the common slips before
`gh pr|issue|release create/edit/comment/review`, `gh pr merge`, a writing
`gh api` call,
or `git commit` runs.

It scans the command text (which covers `-m`, `--body`, `-f body=...` and
heredocs) plus any file passed as `--body-file`, `-F`/`--file`, `--input`
(with a space or `=`) or `-F field=@path`. Staged repository files are not
scanned. It is a tripwire for wording, not a secret scanner.

See ../STOP_HOOKS.md for the full writeup.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

# Only where a command starts (line start, or after ; && || | ( $( ), so a
# file edit whose text merely mentions `gh pr edit` isn't mistaken for one.
_COMMAND_START = r"(?:^|[;&|(]|\$\()\s*(?:\S*/)?"
_PUBLISHING_RE = re.compile(
    _COMMAND_START + r"(?:gh(?:\s+(?:-R|--repo)\s+\S+)?\s+(?:pr|issue|release)\s+"
    r"(?:create|edit|comment|review|merge)\b"
    r"|git(?:\s+-C\s+\S+)?\s+commit\b)",
    re.MULTILINE,
)
_GH_API_WRITE_RE = re.compile(
    _COMMAND_START + r"gh\s+api\b[^\n]*?\s(?:-f|-F|--field|--raw-field|--input)\b",
    re.MULTILINE,
)

# Each entry: (pattern, what it reveals). Case-insensitive unless noted.
_SENSITIVE = [
    (
        re.compile(
            r"\b(?:public_repo|repo:status|repo_deployment|"
            r"(?:read|write|admin):(?:org|discussion|packages|repo_hook|public_key|gpg_key)|"
            r"delete:packages|read:user|user:email)\b",
            re.IGNORECASE,
        ),
        "a token scope name",
    ),
    (
        re.compile(
            r"\b(?:classic|fine[- ]grained)\s+(?:PAT|personal access token|token)s?\b",
            re.IGNORECASE,
        ),
        "a token type",
    ),
    (re.compile(r"\bpersonal access tokens?\b", re.IGNORECASE), "a token type"),
    (re.compile(r"\bPATs?\b"), "a token type"),
    (
        re.compile(
            r"\btokens?\b[^.\n]{0,40}\bscopes?\b|\bscopes?\b[^.\n]{0,40}\btokens?\b", re.IGNORECASE
        ),
        "what a token can access",
    ),
    (re.compile(r"\boutside collaborators?\b", re.IGNORECASE), "an account's access level"),
]

_FILE_FLAGS = {"--body-file", "-F", "--file", "--input"}


def _referenced_files(command: str, cwd: Path) -> list[Path]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    files = []
    for index, token in enumerate(tokens):
        value = None
        if token in _FILE_FLAGS and index + 1 < len(tokens):
            value = tokens[index + 1]
        elif token.startswith(tuple(f"{flag}=" for flag in _FILE_FLAGS if flag.startswith("--"))):
            value = token.split("=", 1)[1]
        if value is None:
            continue
        if "=@" in value:  # gh api -F body=@path
            value = value.split("=@", 1)[1]
        elif "=" in value:  # gh api -F key=value: the value is in the command text
            continue
        if value and value != "-":
            files.append(cwd / value)
    return files


def _check(command: str, cwd: Path) -> str | None:
    if not (_PUBLISHING_RE.search(command) or _GH_API_WRITE_RE.search(command)):
        return None
    texts = [command]
    for path in _referenced_files(command, cwd):
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    for text in texts:
        for pattern, reveals in _SENSITIVE:
            match = pattern.search(text)
            if match:
                return (
                    f"This would publish {reveals} ({match.group(0)!r}) in a commit, PR, "
                    "issue or comment. Public text names a secret only by its name and "
                    "purpose (AGENTS.md, 'No sensitive information'): say what it is for, "
                    "not what kind of token it is, what it can access, or whose account "
                    "it belongs to. Rephrase and run the command again."
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

    reason = _check(command, Path(payload.get("cwd") or "."))
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
