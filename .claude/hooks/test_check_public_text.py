"""Run: python3 -m unittest discover -s .claude/hooks -p 'test_*.py'"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_SPEC = importlib.util.spec_from_file_location(
    "check_public_text", Path(__file__).with_name("check_public_text.py")
)
assert _SPEC and _SPEC.loader
hook = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hook)


class TestCheckPublicText(unittest.TestCase):
    def blocked(self, command: str, cwd: Path = Path(".")) -> bool:
        return hook._check(command, cwd) is not None

    def test_token_wording_in_publishing_commands_is_blocked(self) -> None:
        for command in (
            'git commit -m "use a classic PAT with public_repo"',
            'gh pr create --title t --body "needs a fine-grained token"',
            "gh issue comment 1 --body 'the bot is an outside collaborator'",
            'gh api repos/o/r/pulls/1 -X PATCH -f body="token lacks the read:org scope"',
            "gh -R o/r pr comment 3 --body 'needs a personal access token'",
            "gh pr merge 130 --squash --subject 'docs: x' --body 'uses a classic PAT'",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.blocked(command))

    def test_body_files_are_scanned(self) -> None:
        with TemporaryDirectory() as tmp:
            Path(tmp, "body.md").write_text("Set the secret (a classic PAT).", encoding="utf-8")

            self.assertTrue(self.blocked("gh pr create --body-file body.md", Path(tmp)))
            self.assertTrue(self.blocked("git commit -F body.md", Path(tmp)))
            self.assertTrue(self.blocked("git commit --file=body.md", Path(tmp)))
            self.assertTrue(self.blocked("gh pr merge 130 --squash --body-file body.md", Path(tmp)))
            self.assertTrue(self.blocked("gh pr merge 130 --body-file=body.md", Path(tmp)))
            self.assertTrue(
                self.blocked("gh api repos/o/r/issues/1/comments -F body=@body.md", Path(tmp))
            )

    def test_purpose_only_wording_and_other_commands_pass(self) -> None:
        for command in (
            'git commit -m "open the bump PR with the AUTOMATION_TOKEN secret so CI runs"',
            'gh pr create --title t --body "uses the AUTOMATION_TOKEN repository secret"',
            "gh pr view 129 --json body",
            "gh pr merge 130 --squash --subject 'docs: add AGENTS.md (#130)'",
            "gh api repos/o/r/pulls/1",
            "grep -rn public_repo .",
            "echo PATH=$PATH",
            "python3 - <<'EOF'\ntext = 'use `gh pr edit` for token scopes'\nEOF",
        ):
            with self.subTest(command=command):
                self.assertFalse(self.blocked(command))


if __name__ == "__main__":
    unittest.main()
