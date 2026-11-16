from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from contracts.info_json.lint_info_json import (
    MAX_INSTALL_MSG,
    README_REQUIRED,
    check_cog_info,
    check_readme,
    check_repo_info,
    discover_cogs,
    lint,
)


def _cog(name: str = "newcog", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name,
        "author": ["Someone"],
        "short": "Short.",
        "description": "Description.",
        "install_msg": "Install corridor too.",
        "end_user_data_statement": "Stores nothing.",
        "requirements": [],
        "min_bot_version": "3.5.24",
        "min_python_version": [3, 11, 0],
        "hidden": False,
        "type": "COG",
    }
    data.update(overrides)
    return data


def _repo(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "author": ["Someone"],
        "short": "Short.",
        "description": "Description.",
        "install_msg": "These cogs call an LLM.",
    }
    data.update(overrides)
    return data


def _readme(*cogs: str) -> str:
    return "\n".join([*README_REQUIRED, *(f"`{cog}`" for cog in cogs)])


class TestRealRepository(unittest.TestCase):
    def test_the_real_repository_passes(self) -> None:
        self.assertEqual(lint(), [])

    def test_shared_libraries_are_not_cogs(self) -> None:
        cogs = discover_cogs()

        self.assertIn("corridor", cogs)
        self.assertNotIn("contracts", cogs)
        self.assertNotIn("e2e", cogs)


class TestRepoInfo(unittest.TestCase):
    def test_complete_repo_info_passes(self) -> None:
        self.assertEqual(check_repo_info(_repo()), [])

    def test_missing_install_msg_fails(self) -> None:
        data = _repo()
        del data["install_msg"]

        self.assertEqual(
            check_repo_info(data), ["info.json: 'install_msg' must be a non-empty string"]
        )

    def test_stale_wording_fails(self) -> None:
        problems = check_repo_info(_repo(install_msg="Thanks for using Office-Cogs."))

        self.assertEqual(
            problems, ["info.json: 'install_msg' contains stale wording 'office-cogs'"]
        )

    def test_empty_author_fails(self) -> None:
        self.assertEqual(
            check_repo_info(_repo(author=[])),
            ["info.json: 'author' must be a non-empty list of names"],
        )


class TestCogInfo(unittest.TestCase):
    def test_complete_cog_info_passes(self) -> None:
        self.assertEqual(check_cog_info("newcog", _cog()), [])

    def test_missing_disclosure_fails(self) -> None:
        data = _cog()
        del data["install_msg"]

        self.assertEqual(
            check_cog_info("newcog", data),
            ["newcog/info.json: 'install_msg' must be a non-empty string"],
        )

    def test_wrong_case_key_is_reported_as_unknown(self) -> None:
        data = _cog()
        data["Install_msg"] = data.pop("install_msg")

        self.assertIn("newcog/info.json: unknown key 'Install_msg'", check_cog_info("newcog", data))

    def test_name_must_match_folder(self) -> None:
        self.assertEqual(
            check_cog_info("newcog", _cog(name="other")),
            ["newcog/info.json: 'name' must be 'newcog'"],
        )

    def test_missing_version_keys_fail(self) -> None:
        data = _cog()
        del data["min_python_version"]

        self.assertEqual(
            check_cog_info("newcog", data), ["newcog/info.json: missing 'min_python_version'"]
        )

    def test_overlong_install_msg_fails(self) -> None:
        problems = check_cog_info("newcog", _cog(install_msg="x" * (MAX_INSTALL_MSG + 1)))

        self.assertEqual(len(problems), 1)
        self.assertIn("'install_msg' is", problems[0])

    def test_misspelt_type_fails(self) -> None:
        self.assertEqual(
            check_cog_info("newcog", _cog(type="cog")),
            ["newcog/info.json: 'type' must be 'COG' or 'SHARED_LIBRARY', not 'cog'"],
        )

    def test_required_cog_must_be_named_in_install_msg(self) -> None:
        data = _cog(required_cogs={"corridor": "url", "pixelagents": "url"})

        self.assertEqual(
            check_cog_info("newcog", data),
            ["newcog/info.json: 'install_msg' does not name required cog 'pixelagents'"],
        )

    def test_required_cog_needs_a_whole_word_match(self) -> None:
        data = _cog(install_msg="Install corridors.", required_cogs={"corridor": "url"})

        self.assertEqual(
            check_cog_info("newcog", data),
            ["newcog/info.json: 'install_msg' does not name required cog 'corridor'"],
        )


class TestReadme(unittest.TestCase):
    def test_missing_readme_fails(self) -> None:
        self.assertEqual(check_readme(None, []), ["README.md: missing at the repository root"])

    def test_every_cog_must_be_named(self) -> None:
        self.assertEqual(
            check_readme(_readme("alpha"), ["alpha", "beta"]),
            ["README.md: does not name cog `beta`"],
        )

    def test_install_instructions_are_required(self) -> None:
        text = _readme("alpha").replace("[p]cog install", "cog install")

        self.assertEqual(
            check_readme(text, ["alpha"]),
            ["README.md: missing '[p]cog install pixel-agents-cogs'"],
        )


class TestLintTree(unittest.TestCase):
    def test_a_new_cog_without_install_msg_is_caught(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "info.json").write_text(json.dumps(_repo()))
            (root / "README.md").write_text(_readme("newcog"))
            (root / "newcog").mkdir()
            data = _cog()
            del data["install_msg"]
            (root / "newcog" / "info.json").write_text(json.dumps(data))
            (root / "lib").mkdir()
            (root / "lib" / "info.json").write_text(
                json.dumps({"type": "SHARED_LIBRARY", "hidden": True})
            )

            self.assertEqual(
                lint(root), ["newcog/info.json: 'install_msg' must be a non-empty string"]
            )

    def test_a_misspelt_type_is_reported_not_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "info.json").write_text(json.dumps(_repo()))
            (root / "README.md").write_text(_readme("newcog"))
            (root / "newcog").mkdir()
            (root / "newcog" / "info.json").write_text(json.dumps(_cog(type="cog")))

            self.assertEqual(
                lint(root),
                ["newcog/info.json: 'type' must be 'COG' or 'SHARED_LIBRARY', not 'cog'"],
            )


if __name__ == "__main__":
    unittest.main()
