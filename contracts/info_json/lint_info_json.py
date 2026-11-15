#!/usr/bin/env python3
"""Fail if the repo or any cog `info.json`, or the root `README.md`, drops
something Red's "Becoming an Approved Cog Creator" guide requires
(https://docs.discord.red/en/latest/guide_cog_creators.html).

Checks, all static (stdlib `json` only -- no cog, redbot or discord import):

- **Repo `info.json`**: non-empty `author`, `short`, `description`, and an
  `install_msg` (the guide's disclosure for the repo as a whole).
- **Every cog `info.json`** (any top-level package with an `info.json`
  whose `type` is not `SHARED_LIBRARY` -- derived from the tree, not a
  hardcoded list): `name` matching its folder, non-empty `author`,
  `short`, `description`, `install_msg` and `end_user_data_statement`,
  plus `requirements`, `min_bot_version`, `min_python_version`, `hidden`
  and `type`. Every cog here talks to an outside service, stores data
  outside Config, or needs another cog installed first, so every one
  carries an `install_msg`.
- **Known keys only**: Red matches `info.json` keys case-sensitively and
  silently ignores unknown ones, so a typo such as `Install_msg` would drop
  a disclosure without any error. Allowed keys are Red's `REPO_SCHEMA` /
  `INSTALLABLE_SCHEMA` (`redbot/core/_downloader/info_schemas.py`) plus the
  JSON-schema-only `$schema`, `name` and `permissions`.
- **`install_msg` length**: Downloader sends it with one plain
  `ctx.send`, after replacing `[p]` with the prefix, so it must stay well
  under Discord's 2000-character limit.
- **Stale wording**: no `info.json` string may name the repo by an old
  name or describe it with a blanket insecurity claim (`STALE_PHRASES`).
- **Root `README.md`**: the repository name as its title, the `[p]repo add`
  and `[p]cog install` instructions, a Credits section, and every cog
  named in backticks.

Run: python -m contracts.info_json.lint_info_json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_NAME = "pixel-agents-cogs"
REPO_URL = "https://github.com/pixel-agents-hq/pixel-agents-cogs"

# Red's REPO_SCHEMA keys, plus the JSON-schema-only `$schema`/`name`.
REPO_KEYS = frozenset({"$schema", "name", "author", "short", "description", "install_msg"})
# REPO_SCHEMA + INSTALLABLE_SCHEMA, plus the JSON-schema-only keys.
COG_KEYS = REPO_KEYS | frozenset(
    {
        "min_bot_version",
        "max_bot_version",
        "min_python_version",
        "hidden",
        "disabled",
        "required_cogs",
        "requirements",
        "tags",
        "type",
        "end_user_data_statement",
        "permissions",
    }
)
REPO_REQUIRED_TEXT = ("short", "description", "install_msg")
COG_REQUIRED_TEXT = ("short", "description", "install_msg", "end_user_data_statement")
COG_REQUIRED_PRESENT = ("requirements", "min_bot_version", "min_python_version", "hidden", "type")

# Leaves headroom under Discord's 2000-character message limit for `[p]`
# expanding to a long prefix.
MAX_INSTALL_MSG = 1500

STALE_PHRASES = ("office-cogs", "security nightmare")

README_REQUIRED = (
    f"# {REPO_NAME}",
    f"[p]repo add {REPO_NAME} {REPO_URL}",
    f"[p]cog install {REPO_NAME}",
    "## Credits",
)


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def discover_cogs(root: Path = REPO_ROOT) -> dict[str, dict[str, Any]]:
    """Every top-level package with an `info.json` that Red treats as a cog."""

    cogs: dict[str, dict[str, Any]] = {}
    for info in sorted(root.glob("*/info.json")):
        data = _load(info)
        if data.get("type", "COG") == "COG":
            cogs[info.parent.name] = data
    return cogs


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _common_problems(label: str, data: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    problems = [f"{label}: unknown key {key!r}" for key in sorted(set(data) - allowed)]
    author = data.get("author")
    if not (isinstance(author, list) and author and all(_is_text(a) for a in author)):
        problems.append(f"{label}: 'author' must be a non-empty list of names")
    for key, value in data.items():
        texts = value if isinstance(value, list) else [value]
        for text in texts:
            if not isinstance(text, str):
                continue
            for phrase in STALE_PHRASES:
                if phrase in text.lower():
                    problems.append(f"{label}: {key!r} contains stale wording {phrase!r}")
    install_msg = data.get("install_msg")
    if isinstance(install_msg, str) and len(install_msg) > MAX_INSTALL_MSG:
        problems.append(
            f"{label}: 'install_msg' is {len(install_msg)} characters (max {MAX_INSTALL_MSG})"
        )
    return problems


def check_repo_info(data: dict[str, Any]) -> list[str]:
    problems = _common_problems("info.json", data, REPO_KEYS)
    problems += [
        f"info.json: {key!r} must be a non-empty string"
        for key in REPO_REQUIRED_TEXT
        if not _is_text(data.get(key))
    ]
    return problems


def check_cog_info(name: str, data: dict[str, Any]) -> list[str]:
    label = f"{name}/info.json"
    problems = _common_problems(label, data, COG_KEYS)
    if data.get("name") != name:
        problems.append(f"{label}: 'name' must be {name!r}")
    problems += [
        f"{label}: {key!r} must be a non-empty string"
        for key in COG_REQUIRED_TEXT
        if not _is_text(data.get(key))
    ]
    problems += [f"{label}: missing {key!r}" for key in COG_REQUIRED_PRESENT if key not in data]
    return problems


def check_readme(text: str | None, cog_names: list[str]) -> list[str]:
    if text is None:
        return ["README.md: missing at the repository root"]
    problems = [
        f"README.md: missing {needle!r}" for needle in README_REQUIRED if needle not in text
    ]
    problems += [
        f"README.md: does not name cog `{name}`" for name in cog_names if f"`{name}`" not in text
    ]
    return problems


def lint(root: Path = REPO_ROOT) -> list[str]:
    problems = check_repo_info(_load(root / "info.json"))
    cogs = discover_cogs(root)
    for name, data in cogs.items():
        problems += check_cog_info(name, data)
    readme = root / "README.md"
    problems += check_readme(
        readme.read_text(encoding="utf-8") if readme.is_file() else None, sorted(cogs)
    )
    return problems


def main() -> int:
    problems = lint()
    if problems:
        print("info.json / README.md problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"Repo info.json, {len(discover_cogs())} cog info.json files and README.md are complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
