from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from contracts.info_json.lint_info_json import (
    MAX_INSTALL_MSG,
    README_REQUIRED,
    check_bot_version_range,
    check_cog_info,
    check_deps_project,
    check_deps_tag,
    check_pin_fix,
    check_readme,
    check_repo_info,
    check_requirements,
    check_template_requirements,
    deps_requirement,
    discover_cogs,
    lint,
    main,
    tested_red_versions,
)

REQUIREMENT = deps_requirement("1")
PYPROJECT = '[project]\nname = "pixel-agents-deps"\nversion = "1"\ndependencies = ["anyio<4.15"]\n'


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
        "max_bot_version": "3.5.24",
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
    return "\n".join(
        [*README_REQUIRED, *(f"`{cog}`" for cog in cogs), f'[p]pipinstall "{REQUIREMENT}"']
    )


def _project(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "pixel-agents-deps",
        "version": "1",
        "dependencies": ["mcp>=1.29,<2", "anyio<4.15"],
    }
    data.update(overrides)
    return data


def _write_deps(root: Path, text: str = PYPROJECT) -> None:
    (root / "requirements").mkdir(exist_ok=True)
    (root / "requirements" / "pyproject.toml").write_text(text)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
    )


class TestRealRepository(unittest.TestCase):
    def test_the_real_repository_passes(self) -> None:
        self.assertEqual(lint(), [])

    def test_shared_libraries_are_not_cogs(self) -> None:
        cogs = discover_cogs()

        self.assertIn("corridor", cogs)
        self.assertNotIn("contracts", cogs)
        self.assertNotIn("e2e", cogs)

    def test_red_versions_are_the_shared_range(self) -> None:
        corridor = discover_cogs()["corridor"]
        expected = list(dict.fromkeys([corridor["min_bot_version"], corridor["max_bot_version"]]))

        self.assertEqual(tested_red_versions(), expected)


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

    def test_missing_max_bot_version_fails(self) -> None:
        data = _cog()
        del data["max_bot_version"]

        self.assertEqual(
            check_cog_info("newcog", data), ["newcog/info.json: missing 'max_bot_version'"]
        )

    def test_bot_version_must_be_dotted_numbers(self) -> None:
        self.assertEqual(
            check_cog_info("newcog", _cog(max_bot_version="3.5")),
            ["newcog/info.json: 'max_bot_version' must be an 'X.Y.Z' Red version, not '3.5'"],
        )

    def test_min_bot_version_must_not_exceed_max(self) -> None:
        self.assertEqual(
            check_cog_info("newcog", _cog(min_bot_version="3.5.25")),
            ["newcog/info.json: 'min_bot_version' is newer than 'max_bot_version'"],
        )

    def test_bot_versions_compare_numerically(self) -> None:
        data = _cog(min_bot_version="3.5.9", max_bot_version="3.5.24")

        self.assertEqual(check_cog_info("newcog", data), [])

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


class TestBotVersionRange(unittest.TestCase):
    def test_one_shared_range_passes(self) -> None:
        cogs = {"alpha": _cog("alpha"), "beta": _cog("beta")}

        self.assertEqual(check_bot_version_range(cogs, _cog()), [])

    def test_a_cog_out_of_step_fails(self) -> None:
        cogs = {"alpha": _cog("alpha"), "beta": _cog("beta", max_bot_version="3.5.25")}

        self.assertEqual(
            check_bot_version_range(cogs),
            [
                "min/max_bot_version must be the same everywhere: "
                "alpha 3.5.24..3.5.24, beta 3.5.24..3.5.25"
            ],
        )

    def test_the_cookiecutter_defaults_must_match(self) -> None:
        template = {"min_bot_version": "3.5.24", "max_bot_version": "3.5.23"}

        problems = check_bot_version_range({"alpha": _cog("alpha")}, template)

        self.assertEqual(len(problems), 1)
        self.assertIn("cookiecutter.json 3.5.24..3.5.23", problems[0])

    def test_red_versions_lists_each_end_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha").mkdir()
            data = _cog("alpha", min_bot_version="3.5.20")
            (root / "alpha" / "info.json").write_text(json.dumps(data))

            self.assertEqual(tested_red_versions(root), ["3.5.20", "3.5.24"])

    def test_red_versions_refuses_a_split_range(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, high in (("alpha", "3.5.24"), ("beta", "3.5.25")):
                (root / name).mkdir()
                data = _cog(name, max_bot_version=high)
                (root / name / "info.json").write_text(json.dumps(data))

            with self.assertRaises(ValueError):
                tested_red_versions(root)

    def test_unknown_argument_is_a_usage_error(self) -> None:
        self.assertEqual(main(["--nope"]), 2)


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
            _write_deps(root)
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
            _write_deps(root)

            self.assertEqual(
                lint(root),
                ["newcog/info.json: 'type' must be 'COG' or 'SHARED_LIBRARY', not 'cog'"],
            )

    def test_a_missing_meta_package_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "info.json").write_text(json.dumps(_repo()))
            (root / "README.md").write_text(_readme("newcog"))
            (root / "newcog").mkdir()
            (root / "newcog" / "info.json").write_text(json.dumps(_cog()))

            self.assertEqual(lint(root), ["requirements/pyproject.toml: missing"])

    def test_a_cog_with_its_own_pin_is_caught(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "info.json").write_text(json.dumps(_repo()))
            (root / "README.md").write_text(_readme("newcog"))
            (root / "newcog").mkdir()
            data = _cog(required_cogs={"corridor": "url"}, requirements=[REQUIREMENT, "anyio<4.15"])
            (root / "newcog" / "info.json").write_text(json.dumps(data))
            _write_deps(root)

            problems = lint(root)

            self.assertEqual(len(problems), 1)
            self.assertIn("newcog/info.json: 'requirements' must be", problems[0])


class TestRequirements(unittest.TestCase):
    def test_requirement_names_the_versioned_tag(self) -> None:
        self.assertEqual(
            REQUIREMENT,
            "pixel-agents-deps @ git+https://github.com/pixel-agents-hq/pixel-agents-cogs"
            "@pixel-agents-deps-v1#subdirectory=requirements",
        )

    def test_corridor_and_its_dependents_need_exactly_the_requirement(self) -> None:
        cogs = {
            "corridor": _cog("corridor", requirements=[REQUIREMENT]),
            "alpha": _cog("alpha", required_cogs={"corridor": "url"}, requirements=[REQUIREMENT]),
        }

        self.assertEqual(check_requirements(cogs, REQUIREMENT), [])

    def test_a_dependent_without_the_requirement_fails(self) -> None:
        cogs = {"alpha": _cog("alpha", required_cogs={"corridor": "url"})}

        problems = check_requirements(cogs, REQUIREMENT)

        self.assertEqual(len(problems), 1)
        self.assertNotIn(" or []", problems[0])

    def test_a_stale_ref_fails(self) -> None:
        cogs = {"corridor": _cog("corridor", requirements=[deps_requirement("0")])}

        self.assertEqual(len(check_requirements(cogs, REQUIREMENT)), 1)

    def test_an_extra_spelling_fails(self) -> None:
        variant = REQUIREMENT.replace("pixel-agents-deps @", "pixel-agents-deps[cctv] @")
        cogs = {"corridor": _cog("corridor", requirements=[variant])}

        self.assertEqual(len(check_requirements(cogs, REQUIREMENT)), 1)

    def test_a_corridor_free_cog_may_list_nothing_or_the_requirement(self) -> None:
        cogs = {"alpha": _cog("alpha"), "beta": _cog("beta", requirements=[REQUIREMENT])}

        self.assertEqual(check_requirements(cogs, REQUIREMENT), [])

    def test_a_corridor_free_cog_with_its_own_requirement_fails(self) -> None:
        cogs = {"alpha": _cog("alpha", requirements=["packaging"])}

        problems = check_requirements(cogs, REQUIREMENT)

        self.assertEqual(len(problems), 1)
        self.assertIn(" or []", problems[0])

    def test_template_must_carry_the_requirement(self) -> None:
        good = '"requirements": ' + json.dumps([REQUIREMENT])

        self.assertEqual(check_template_requirements(good, REQUIREMENT), [])
        self.assertEqual(len(check_template_requirements('"requirements": []', REQUIREMENT)), 1)

    def test_project_name_and_version_are_checked(self) -> None:
        self.assertEqual(check_deps_project(_project()), [])
        self.assertEqual(len(check_deps_project(_project(name="other", version="1.0"))), 2)


class TestPinFix(unittest.TestCase):
    def test_readme_and_install_msg_with_the_fix_pass(self) -> None:
        fix = f'[p]pipinstall "{REQUIREMENT}"'
        readme = f'{fix}\n[p]pipinstall "anyio<4.15"\n'

        self.assertEqual(check_pin_fix(readme, _cog(install_msg=f"Run {fix}."), _project()), [])

    def test_missing_fix_fails(self) -> None:
        problems = check_pin_fix("", _cog(), _project())

        self.assertEqual(len(problems), 2)

    def test_a_stale_pin_in_the_readme_fails(self) -> None:
        readme = f'[p]pipinstall "{REQUIREMENT}"\n[p]pipinstall "anyio<4.14"\n'

        problems = check_pin_fix(readme, None, _project())

        self.assertEqual(len(problems), 1)
        self.assertIn("'anyio<4.14'", problems[0])


class TestDepsTag(unittest.TestCase):
    def _tagged_repo(self, root: Path) -> None:
        _write_deps(root)
        _git(root, "init", "-q")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "deps")
        _git(root, "tag", "pixel-agents-deps-v1")

    def test_matching_tag_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tagged_repo(root)

            self.assertEqual(check_deps_tag(root, _project(), required=True), [])

    def test_tag_with_other_content_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tagged_repo(root)
            _write_deps(root, PYPROJECT.replace("anyio<4.15", "anyio<4.16"))

            problems = check_deps_tag(root, _project(), required=False)

            self.assertEqual(len(problems), 1)
            self.assertIn("different requirements/pyproject.toml", problems[0])

    def test_missing_tag_fails_only_when_required(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_deps(root)

            self.assertEqual(check_deps_tag(root, _project(), required=False), [])
            self.assertEqual(len(check_deps_tag(root, _project(), required=True)), 1)


if __name__ == "__main__":
    unittest.main()
