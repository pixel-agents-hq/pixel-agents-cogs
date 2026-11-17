#!/usr/bin/env python3
"""Raise `max_bot_version` when Red-DiscordBot ships a release newer than it.

Every cog pins one tested Red range (docs/dependency-loading.md, "Private Red
APIs"), so Downloader refuses to install the cogs on a newer Red until the
range is raised, and the Red Index delists a cog a new Red breaks unless it
is fixed within a month. `.github/workflows/red-release.yml` runs this daily
to open, or update, one pull request that raises the ceiling.

Stdlib only, like the other contracts lints. Three steps:

- **plan**: the ceiling is `max_bot_version`, read through
  `contracts.info_json.lint_info_json.tested_red_versions` (the same range
  `--red-versions` hands CI). A Red version is pending when it is a final
  `X.Y.Z` GitHub release of Cog-Creators/Red-DiscordBot (not a draft or
  pre-release) newer than the ceiling *and* installable from PyPI (uploaded,
  not yanked) -- GitHub publishes a few minutes before PyPI, and CI installs
  from PyPI. If the most recently closed pull request from the bump branch
  was closed without merging and its body's marker names a version at least
  as new as the latest pending one, the plan is to skip: a deliberately
  rejected bump stays closed until Red releases again.
- **bump**: rewrites `max_bot_version` in every cog `info.json` and in the
  cookiecutter defaults, leaving each file's formatting alone.
- **body**: the pull request's title, labels and body -- the pending
  versions, release notes, the delisting deadline (the oldest pending
  release's date plus one month, since any of them may be the one that
  breaks us) and the switchboard gate's result.

Run: python -m contracts.red_release.track_red_release plan --repo OWNER/NAME \
         --branch BRANCH --out plan.json
     python -m contracts.red_release.track_red_release bump plan.json
     python -m contracts.red_release.track_red_release body plan.json \
         --gate-code N --gate-log gate.log --out body.md [--no-ci-token]

`plan` and `body` also write their results to `$GITHUB_OUTPUT` when set.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from contracts.info_json.lint_info_json import (
    COOKIECUTTER_JSON,
    REPO_ROOT,
    discover_cogs,
    tested_red_versions,
)

RED_REPO = "Cog-Creators/Red-DiscordBot"
RED_RELEASES_URL = f"https://api.github.com/repos/{RED_REPO}/releases?per_page=100"
RED_PYPI_URL = "https://pypi.org/pypi/Red-DiscordBot/json"
RED_CHANGELOG_URL = "https://docs.discord.red/en/stable/changelog.html"
CI_PYTHON = "3.11"

MARKER_RE = re.compile(r"<!-- red-release latest=(\d+\.\d+\.\d+) -->")
FINAL_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
MAX_BOT_VERSION_RE = re.compile(r'("max_bot_version"\s*:\s*")\d+\.\d+\.\d+(")')
GATE_LOG_LINES = 60

Version = tuple[int, int, int]


def parse_final(text: str) -> Version | None:
    """`X.Y.Z` (optionally `vX.Y.Z`) as a tuple; None for anything else,
    such as `3.0.0rc1` or `3.5.24.post1`."""

    match = FINAL_RE.fullmatch(text.strip())
    if match is None:
        return None
    major, minor, micro = (int(part) for part in match.groups())
    return major, minor, micro


def _text(version: Version) -> str:
    return ".".join(str(part) for part in version)


def bump_kind(ceiling: Version, version: Version) -> str:
    if version[0] != ceiling[0]:
        return "major"
    if version[1] != ceiling[1]:
        return "minor"
    return "patch"


@dataclass(frozen=True)
class PendingRelease:
    version: str
    published: str  # ISO date, from the GitHub release
    url: str
    requires_python: str | None
    kind: str


def installable_on_pypi(pypi: dict[str, Any], version: str) -> bool:
    files = pypi.get("releases", {}).get(version) or []
    return any(not file.get("yanked", False) for file in files)


def _requires_python(pypi: dict[str, Any], version: str) -> str | None:
    for file in pypi.get("releases", {}).get(version) or []:
        if file.get("requires_python"):
            return str(file["requires_python"])
    return None


def pending_releases(
    ceiling: str, releases: list[dict[str, Any]], pypi: dict[str, Any]
) -> list[PendingRelease]:
    """Final releases newer than `ceiling` that PyPI can install, oldest first."""

    low = parse_final(ceiling)
    if low is None:
        raise ValueError(f"max_bot_version {ceiling!r} is not X.Y.Z")
    found: dict[Version, PendingRelease] = {}
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        version = parse_final(str(release.get("tag_name", "")))
        if version is None or version <= low:
            continue
        text = _text(version)
        if not installable_on_pypi(pypi, text):
            continue
        found[version] = PendingRelease(
            version=text,
            published=str(release.get("published_at") or "")[:10],
            url=str(release.get("html_url", "")),
            requires_python=_requires_python(pypi, text),
            kind=bump_kind(low, version),
        )
    return [found[version] for version in sorted(found)]


def add_month(day: date) -> date:
    """The same day next month, clamped to that month's last day."""

    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(
        year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])
    )


def delisting_deadline(pending: list[PendingRelease]) -> date | None:
    dates = [date.fromisoformat(release.published) for release in pending if release.published]
    return add_month(min(dates)) if dates else None


def marker(latest: str) -> str:
    return f"<!-- red-release latest={latest} -->"


def rejected_version(closed_pulls: list[dict[str, Any]]) -> Version | None:
    """The marker version of the most recently closed bump PR, if it was
    closed without merging; None when it was merged or has no marker."""

    closed = [pull for pull in closed_pulls if pull.get("closed_at")]
    if not closed:
        return None
    newest = max(closed, key=lambda pull: str(pull["closed_at"]))
    if newest.get("merged_at"):
        return None
    match = MARKER_RE.search(str(newest.get("body") or ""))
    return parse_final(match.group(1)) if match else None


def plan(
    ceiling: str,
    releases: list[dict[str, Any]],
    pypi: dict[str, Any],
    closed_pulls: list[dict[str, Any]],
) -> dict[str, Any]:
    pending = pending_releases(ceiling, releases, pypi)
    latest = pending[-1].version if pending else ceiling
    action = "bump" if pending else "none"
    rejected = rejected_version(closed_pulls)
    latest_version = parse_final(latest)
    if pending and rejected is not None and latest_version is not None:
        if rejected >= latest_version:
            action = "skip"
    return {
        "action": action,
        "ceiling": ceiling,
        "latest": latest,
        "pending": [asdict(release) for release in pending],
    }


def bump_text(text: str, version: str) -> str:
    """`text` with its single `max_bot_version` value set to `version`."""

    new, count = MAX_BOT_VERSION_RE.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise ValueError(f"expected one max_bot_version, found {count}")
    return new


def bump_targets(root: Path = REPO_ROOT) -> list[Path]:
    return [root / name / "info.json" for name in sorted(discover_cogs(root))] + [
        root / COOKIECUTTER_JSON
    ]


def bump(version: str, root: Path = REPO_ROOT) -> list[Path]:
    if parse_final(version) is None:
        raise ValueError(f"{version!r} is not X.Y.Z")
    changed = []
    for path in bump_targets(root):
        text = path.read_text(encoding="utf-8")
        new = bump_text(text, version)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(path)
    return changed


def _gate_section(code: int, log: str) -> list[str]:
    if code == 0:
        return [
            "### ✅ switchboard suite on the new Red: passed",
            "",
            "`python -m pytest -q switchboard/` passed against the bumped tree, including",
            "`tests/test_red_private_api.py`, which checks every private Red API this repo",
            'relies on (docs/dependency-loading.md, "Private Red APIs").',
        ]
    tail = "\n".join(log.splitlines()[-GATE_LOG_LINES:])
    return [
        "### ❌ switchboard suite on the new Red: failed",
        "",
        "Installing the new Red or running `python -m pytest -q switchboard/` against the",
        "bumped tree failed. Fix what `tests/test_red_private_api.py` reports before merging.",
        "",
        "```",
        tail,
        "```",
    ]


def render_body(
    data: dict[str, Any], gate_code: int, gate_log: str, ci_token: bool, today: date
) -> str:
    pending = [PendingRelease(**release) for release in data["pending"]]
    latest = data["latest"]
    lines = [
        f"Red-DiscordBot {latest} is out. Every cog pins `max_bot_version` "
        f"{data['ceiling']}, so Downloader refuses to install them on it.",
        "This raises `max_bot_version` to "
        f"{latest} in every cog `info.json` and in "
        "`.cookiecutter/cog-cookiecutter/cookiecutter.json`; `min_bot_version` stays, "
        "so CI keeps testing both ends of the range.",
        "",
        "| Version | Released | Kind | Requires Python | Notes |",
        "|---|---|---|---|---|",
    ]
    for release in pending:
        python = release.requires_python or "unknown"
        if release.kind != "patch":
            python += f" (CI uses {CI_PYTHON})"
        lines.append(
            f"| {release.version} | {release.published or 'unknown'} | {release.kind} "
            f"| `{python}` | [release notes]({release.url}) |"
        )
    lines += ["", f"Red's changelog: {RED_CHANGELOG_URL}", ""]
    deadline = delisting_deadline(pending)
    if deadline is not None:
        overdue = " ⚠️ **overdue**" if today > deadline else ""
        lines += [
            f"**Delisting deadline: {deadline.isoformat()}**{overdue} -- the oldest pending "
            "release's date plus one month. A cog a new Red breaks must be fixed within a "
            "month to stay on the Red Index.",
            "",
        ]
    lines += _gate_section(gate_code, gate_log)
    lines += [
        "",
        "### Before merging",
        "",
        "- [ ] `switchboard.yml` and `check-cogs.yml` are green on both ends of the range "
        "(they read it through `--red-versions`)",
        "- [ ] Red's release notes show no core/Downloader change the gate cannot see",
        "- [ ] Every private API in docs/dependency-loading.md still holds; update the docs "
        "if one changed",
    ]
    if not ci_token:
        lines += [
            "",
            "### ⚠️ No checks will run on this pull request",
            "",
            "It was opened with `GITHUB_TOKEN`, and GitHub does not trigger `pull_request`",
            "workflows for anything that token creates. Set the `AUTOMATION_TOKEN` repository",
            "secret to get real checks here.",
        ]
    lines += [
        "",
        "Opened by `.github/workflows/red-release.yml`. Refs #128.",
        "",
        marker(latest),
    ]
    return "\n".join(lines) + "\n"


def title(data: dict[str, Any]) -> str:
    return f"chore(red): raise max_bot_version to {data['latest']}"


def labels(gate_code: int) -> str:
    return "red-release" if gate_code == 0 else "red-release,red-breaking"


def _get_json(url: str) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "pixel-agents-cogs-red-release"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def closed_pulls_url(repo: str, branch: str) -> str:
    owner = repo.split("/", 1)[0]
    return (
        f"https://api.github.com/repos/{repo}/pulls?state=closed&head={owner}:{branch}"
        "&sort=updated&direction=desc&per_page=20"
    )


def _write_outputs(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m contracts.red_release.track_red_release")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--repo", required=True)
    plan_parser.add_argument("--branch", required=True)
    plan_parser.add_argument("--out", type=Path, required=True)
    bump_parser = commands.add_parser("bump")
    bump_parser.add_argument("plan", type=Path)
    body_parser = commands.add_parser("body")
    body_parser.add_argument("plan", type=Path)
    body_parser.add_argument("--gate-code", type=int, required=True)
    body_parser.add_argument("--gate-log", type=Path, required=True)
    body_parser.add_argument("--out", type=Path, required=True)
    body_parser.add_argument("--no-ci-token", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "plan":
        ceiling = tested_red_versions()[-1]
        result = plan(
            ceiling,
            _get_json(RED_RELEASES_URL),
            _get_json(RED_PYPI_URL),
            _get_json(closed_pulls_url(args.repo, args.branch)),
        )
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        _write_outputs({key: result[key] for key in ("action", "ceiling", "latest")})
        pending = ", ".join(release["version"] for release in result["pending"]) or "none"
        print(f"max_bot_version {ceiling}; pending Red releases: {pending}; {result['action']}")
        return 0

    data = json.loads(args.plan.read_text(encoding="utf-8"))
    if args.command == "bump":
        for path in bump(data["latest"]):
            print(f"raised max_bot_version to {data['latest']} in {path.relative_to(REPO_ROOT)}")
        return 0

    log = args.gate_log.read_text(encoding="utf-8") if args.gate_log.is_file() else ""
    today = datetime.now(UTC).date()
    args.out.write_text(
        render_body(data, args.gate_code, log, not args.no_ci_token, today), encoding="utf-8"
    )
    _write_outputs({"title": title(data), "labels": labels(args.gate_code)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
