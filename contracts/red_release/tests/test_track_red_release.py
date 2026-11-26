from __future__ import annotations

import json
import os
import shutil
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from contracts.info_json.lint_info_json import COOKIECUTTER_JSON, REPO_ROOT, lint
from contracts.red_release.track_red_release import (
    add_month,
    bump,
    bump_kind,
    bump_targets,
    bump_text,
    closed_pulls_url,
    labels,
    log_fence,
    main,
    marker,
    parse_final,
    pending_releases,
    plan,
    rejected_version,
    render_body,
    stale_labels,
    title,
)


def _release(tag: str, published: str = "2026-03-06T01:36:27Z", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": published,
        "html_url": f"https://github.com/Cog-Creators/Red-DiscordBot/releases/tag/{tag}",
    }
    data.update(overrides)
    return data


def _pypi(*versions: str, yanked: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "releases": {
            version: [{"yanked": version in yanked, "requires_python": "<3.12,>=3.8.1"}]
            for version in versions
        }
    }


def _pull(closed_at: str, merged: bool, latest: str | None) -> dict[str, Any]:
    return {
        "closed_at": closed_at,
        "merged_at": closed_at if merged else None,
        "body": f"Bump.\n\n{marker(latest)}\n" if latest else "Bump.",
    }


class TestVersions(unittest.TestCase):
    def test_only_final_versions_parse(self) -> None:
        self.assertEqual(parse_final("3.5.24"), (3, 5, 24))
        self.assertEqual(parse_final("v3.6.0"), (3, 6, 0))
        for text in ("3.0.0rc1", "3.0.0b16", "3.5.24.post1", "3.5", ""):
            with self.subTest(text=text):
                self.assertIsNone(parse_final(text))

    def test_bump_kind(self) -> None:
        self.assertEqual(bump_kind((3, 5, 24), (3, 5, 25)), "patch")
        self.assertEqual(bump_kind((3, 5, 24), (3, 6, 0)), "minor")
        self.assertEqual(bump_kind((3, 5, 24), (4, 0, 0)), "major")


class TestPendingReleases(unittest.TestCase):
    def test_newer_final_installable_releases_oldest_first(self) -> None:
        releases = [_release("3.5.26"), _release("3.5.25"), _release("3.5.24")]

        pending = pending_releases("3.5.24", releases, _pypi("3.5.24", "3.5.25", "3.5.26"))

        self.assertEqual([release.version for release in pending], ["3.5.25", "3.5.26"])
        self.assertEqual(pending[0].published, "2026-03-06")
        self.assertEqual(pending[0].requires_python, "<3.12,>=3.8.1")

    def test_drafts_prereleases_and_non_final_tags_are_ignored(self) -> None:
        releases = [
            _release("3.5.25", draft=True),
            _release("3.5.26", prerelease=True),
            _release("3.6.0rc1"),
        ]

        pending = pending_releases("3.5.24", releases, _pypi("3.5.25", "3.5.26", "3.6.0rc1"))

        self.assertEqual(pending, [])

    def test_a_release_not_yet_on_pypi_waits(self) -> None:
        self.assertEqual(pending_releases("3.5.24", [_release("3.5.25")], _pypi()), [])

    def test_a_yanked_release_is_skipped(self) -> None:
        pending = pending_releases(
            "3.5.24", [_release("3.5.25")], _pypi("3.5.25", yanked=("3.5.25",))
        )

        self.assertEqual(pending, [])

    def test_versions_compare_numerically(self) -> None:
        pending = pending_releases("3.5.9", [_release("3.5.10")], _pypi("3.5.10"))

        self.assertEqual([release.version for release in pending], ["3.5.10"])


class TestDeadline(unittest.TestCase):
    def test_add_month_clamps_to_the_month_end(self) -> None:
        self.assertEqual(add_month(date(2026, 3, 6)), date(2026, 4, 6))
        self.assertEqual(add_month(date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(add_month(date(2026, 12, 15)), date(2027, 1, 15))

    def test_body_counts_from_the_oldest_pending_release(self) -> None:
        data = plan(
            "3.5.24",
            [
                _release("3.5.25", "2026-11-01T00:00:00Z"),
                _release("3.5.26", "2026-11-10T00:00:00Z"),
            ],
            _pypi("3.5.25", "3.5.26"),
            [],
        )

        body = render_body(data, 0, "", True, date(2026, 11, 17))

        self.assertIn("**Delisting deadline: 2026-12-01**", body)
        self.assertNotIn("overdue", body)
        self.assertIn("overdue", render_body(data, 0, "", True, date(2026, 12, 2)))


class TestPlan(unittest.TestCase):
    releases = [_release("3.5.25"), _release("3.5.24")]
    pypi = _pypi("3.5.24", "3.5.25")

    def test_nothing_newer_does_nothing(self) -> None:
        data = plan("3.5.25", self.releases, self.pypi, [])

        self.assertEqual(data["action"], "none")
        self.assertEqual(data["latest"], "3.5.25")

    def test_a_newer_release_bumps_to_the_latest(self) -> None:
        data = plan("3.5.24", [_release("3.5.26"), *self.releases], _pypi("3.5.25", "3.5.26"), [])

        self.assertEqual(data["action"], "bump")
        self.assertEqual(data["latest"], "3.5.26")
        self.assertEqual([release["version"] for release in data["pending"]], ["3.5.25", "3.5.26"])

    def test_a_rejected_bump_stays_closed_until_red_releases_again(self) -> None:
        rejected = [_pull("2026-11-10T00:00:00Z", merged=False, latest="3.5.25")]

        self.assertEqual(plan("3.5.24", self.releases, self.pypi, rejected)["action"], "skip")
        newer = plan(
            "3.5.24", [_release("3.5.26"), *self.releases], _pypi("3.5.25", "3.5.26"), rejected
        )
        self.assertEqual(newer["action"], "bump")

    def test_only_the_most_recently_closed_pull_counts(self) -> None:
        pulls = [
            _pull("2026-11-01T00:00:00Z", merged=False, latest="3.5.25"),
            _pull("2026-11-12T00:00:00Z", merged=True, latest="3.5.25"),
        ]

        self.assertIsNone(rejected_version(pulls))
        self.assertEqual(rejected_version(list(reversed(pulls[:1]))), (3, 5, 25))
        self.assertIsNone(
            rejected_version([_pull("2026-11-01T00:00:00Z", merged=False, latest=None)])
        )

    def test_closed_pulls_are_looked_up_by_the_bump_branch(self) -> None:
        url = closed_pulls_url("pixel-agents-hq/pixel-agents-cogs", "chore/red-max-bot-version")

        self.assertIn("state=closed", url)
        self.assertIn("head=pixel-agents-hq:chore/red-max-bot-version", url)


class TestBump(unittest.TestCase):
    def test_bump_text_changes_only_max_bot_version(self) -> None:
        text = (
            '{\n    "min_bot_version": "3.5.24",\n    "max_bot_version": "3.5.24",\n    "x": 1\n}\n'
        )

        self.assertEqual(
            text.replace('max_bot_version": "3.5.24', 'max_bot_version": "3.5.26'),
            bump_text(text, "3.5.26"),
        )

    def test_bump_text_needs_exactly_one_key(self) -> None:
        with self.assertRaises(ValueError):
            bump_text('{"min_bot_version": "3.5.24"}', "3.5.26")

    def test_targets_are_every_cog_and_the_cookiecutter_defaults(self) -> None:
        targets = bump_targets()

        self.assertIn(REPO_ROOT / "corridor" / "info.json", targets)
        self.assertIn(REPO_ROOT / COOKIECUTTER_JSON, targets)
        self.assertFalse({"contracts", "e2e"} & {path.parent.name for path in targets})

    def test_bumped_repository_still_passes_the_info_json_lint(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(REPO_ROOT / "info.json", root / "info.json")
            shutil.copy(REPO_ROOT / "README.md", root / "README.md")
            for path in bump_targets():
                target = root / path.relative_to(REPO_ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(path, target)

            changed = bump("9.9.9", root)

            self.assertEqual(len(changed), len(bump_targets()))
            self.assertEqual(lint(root), [])
            corridor = json.loads((root / "corridor" / "info.json").read_text(encoding="utf-8"))
            self.assertEqual(corridor["max_bot_version"], "9.9.9")
            self.assertNotEqual(corridor["min_bot_version"], "9.9.9")


class TestBody(unittest.TestCase):
    data = plan(
        "3.5.24",
        [_release("3.6.0", "2026-11-10T00:00:00Z")],
        _pypi("3.6.0"),
        [],
    )

    def test_body_ends_with_the_marker_for_the_skip_check(self) -> None:
        body = render_body(self.data, 0, "", True, date(2026, 11, 17))

        self.assertEqual(
            rejected_version([{"closed_at": "x", "merged_at": None, "body": body}]), (3, 6, 0)
        )
        self.assertIn("| 3.6.0 | 2026-11-10 | minor | `<3.12,>=3.8.1 (CI uses 3.11)` |", body)
        self.assertIn("✅ switchboard suite", body)
        self.assertNotIn("No checks will run", body)

    def test_a_failed_gate_shows_the_log_tail(self) -> None:
        log = "\n".join(f"line {n}" for n in range(100))

        body = render_body(self.data, 1, log, False, date(2026, 11, 17))

        self.assertIn("❌ switchboard suite", body)
        self.assertIn("line 99", body)
        self.assertNotIn("line 39\n", body)
        self.assertIn("No checks will run", body)

    def test_title_and_labels(self) -> None:
        self.assertEqual(title(self.data), "chore(red): raise max_bot_version to 3.6.0")
        self.assertEqual(labels(0), "red-release")
        self.assertEqual(labels(1), "red-release,red-breaking")

    def test_a_pass_after_a_fail_removes_red_breaking(self) -> None:
        failed = set(labels(1).split(","))
        passed = (failed | set(labels(0).split(","))) - {stale_labels(0)}

        self.assertEqual(passed, {"red-release"})
        self.assertEqual(stale_labels(1), "")

    def test_a_log_holding_a_fence_cannot_close_the_block(self) -> None:
        log = "before\n```\n`````\nafter"

        body = render_body(self.data, 1, log, True, date(2026, 11, 17))

        self.assertEqual(log_fence(log), "``````")
        self.assertIn("``````\nbefore\n```\n`````\nafter\n``````", body)
        self.assertEqual(log_fence("no backticks"), "```")


class TestCli(unittest.TestCase):
    def test_body_writes_title_and_labels_to_github_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plan.json").write_text(json.dumps(TestBody.data), encoding="utf-8")
            output = root / "output"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                code = main(
                    [
                        "body",
                        str(root / "plan.json"),
                        "--gate-code",
                        "0",
                        "--gate-log",
                        str(root / "missing.log"),
                        "--out",
                        str(root / "body.md"),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("title=chore(red): raise max_bot_version to 3.6.0\n", output.read_text())
            self.assertIn("labels=red-release\n", output.read_text())
            self.assertIn("remove-labels=red-breaking\n", output.read_text())
            self.assertIn(marker("3.6.0"), (root / "body.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
