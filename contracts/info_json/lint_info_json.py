#!/usr/bin/env python3
"""Fail if the repo or any cog `info.json`, or the root `README.md`, drops
something Red's "Becoming an Approved Cog Creator" guide requires
(https://docs.discord.red/en/latest/guide_cog_creators.html).

Checks, all static (stdlib `json` only -- no cog, redbot or discord import):

- **Repo `info.json`**: non-empty `author`, `short`, `description`, and an
  `install_msg` (the guide's disclosure for the repo as a whole).
- **Every cog `info.json`** (any top-level package with an `info.json`
  whose `type` is not `SHARED_LIBRARY` -- derived from the tree, not a
  hardcoded list): `type` exactly `COG` (Red skips any other value, such
  as a lowercase `cog`, as an unknown installable), `name` matching its
  folder, non-empty `author`, `short`, `description`, `install_msg` and
  `end_user_data_statement`, plus `requirements`, `min_bot_version`,
  `max_bot_version`, `min_python_version`, `hidden` and `type`. Every cog here talks to an
  outside service, stores data outside Config, or needs another cog
  installed first, so every one carries an `install_msg`.
- **One tested Red range**: every cog calls Red's private cog manager
  (`bot._cog_mgr`, see docs/dependency-loading.md "Private Red APIs"), so
  the guide wants each one locked with `min_bot_version` *and*
  `max_bot_version`. Both must be `X.Y.Z` with min <= max, and every cog
  plus the cookiecutter template's defaults must carry the same range --
  the one `--red-versions` hands to the CI jobs that test against real Red.
- **`required_cogs` disclosed**: Downloader never installs `required_cogs`,
  so each one must be named in the cog's `install_msg`.
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
     python -m contracts.info_json.lint_info_json --red-versions
       (prints the JSON list of Red versions CI installs: min and max)
"""

from __future__ import annotations

import json
import re
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
COG_REQUIRED_PRESENT = (
    "requirements",
    "min_bot_version",
    "max_bot_version",
    "min_python_version",
    "hidden",
    "type",
)
BOT_VERSION_KEYS = ("min_bot_version", "max_bot_version")
BOT_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
COOKIECUTTER_JSON = Path(".cookiecutter") / "cog-cookiecutter" / "cookiecutter.json"

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
    """Every top-level package with an `info.json` that is not a shared
    library -- including one whose `type` is misspelt, which
    `check_cog_info` then reports instead of silently skipping."""

    cogs: dict[str, dict[str, Any]] = {}
    for info in sorted(root.glob("*/info.json")):
        data = _load(info)
        if data.get("type", "COG") != "SHARED_LIBRARY":
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
    if "type" in data and data["type"] != "COG":
        problems.append(f"{label}: 'type' must be 'COG' or 'SHARED_LIBRARY', not {data['type']!r}")
    problems += _bot_version_problems(label, data)
    install_msg = data.get("install_msg")
    if isinstance(install_msg, str):
        problems += [
            f"{label}: 'install_msg' does not name required cog {dep!r}"
            for dep in data.get("required_cogs") or {}
            if not re.search(rf"\b{re.escape(dep)}\b", install_msg)
        ]
    return problems


def _version_tuple(value: object) -> tuple[int, ...] | None:
    if isinstance(value, str) and BOT_VERSION_RE.fullmatch(value):
        return tuple(int(part) for part in value.split("."))
    return None


def _bot_version_problems(label: str, data: dict[str, Any]) -> list[str]:
    problems = [
        f"{label}: {key!r} must be an 'X.Y.Z' Red version, not {data[key]!r}"
        for key in BOT_VERSION_KEYS
        if key in data and _version_tuple(data[key]) is None
    ]
    low = _version_tuple(data.get("min_bot_version"))
    high = _version_tuple(data.get("max_bot_version"))
    if low is not None and high is not None and low > high:
        problems.append(f"{label}: 'min_bot_version' is newer than 'max_bot_version'")
    return problems


def _bot_range(data: dict[str, Any]) -> tuple[object, object]:
    return data.get("min_bot_version"), data.get("max_bot_version")


def check_bot_version_range(
    cogs: dict[str, dict[str, Any]], template: dict[str, Any] | None = None
) -> list[str]:
    """Every cog (and the cookiecutter defaults) must pin the same Red range."""

    ranges = {name: _bot_range(data) for name, data in cogs.items()}
    if template is not None:
        ranges[str(COOKIECUTTER_JSON)] = _bot_range(template)
    if len(set(ranges.values())) <= 1:
        return []
    listing = ", ".join(f"{name} {low}..{high}" for name, (low, high) in sorted(ranges.items()))
    return [f"min/max_bot_version must be the same everywhere: {listing}"]


def tested_red_versions(root: Path = REPO_ROOT) -> list[str]:
    """The Red versions CI tests against: the shared range's two ends."""

    ranges = {_bot_range(data) for data in discover_cogs(root).values()}
    if len(ranges) != 1:
        raise ValueError("cogs do not share one min/max_bot_version range")
    low, high = ranges.pop()
    return [str(version) for version in dict.fromkeys((low, high))]


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
    template = root / COOKIECUTTER_JSON
    problems += check_bot_version_range(cogs, _load(template) if template.is_file() else None)
    readme = root / "README.md"
    problems += check_readme(
        readme.read_text(encoding="utf-8") if readme.is_file() else None, sorted(cogs)
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--red-versions"]:
        print(json.dumps(tested_red_versions()))
        return 0
    if args:
        print("usage: python -m contracts.info_json.lint_info_json [--red-versions]")
        return 2
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
