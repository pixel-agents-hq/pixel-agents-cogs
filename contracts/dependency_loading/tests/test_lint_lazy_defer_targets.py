from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from contracts.dependency_loading.lint_lazy_defer_targets import (
    REPO_ROOT,
    discover_lazy_defer_targets,
    find_bare_cross_cog_imports,
)


class TestDiscoverLazyDeferTargets(unittest.TestCase):
    def test_finds_pixelagents_as_the_sole_real_target(self) -> None:
        targets = discover_lazy_defer_targets()

        self.assertEqual(set(targets), {"pixelagents"})
        callers = {p.parts[-2] for p in targets["pixelagents"]}
        self.assertEqual(callers, {"architect", "floorplan", "painter"})


class TestFindBareCrossCogImports(unittest.TestCase):
    def test_the_real_pixelagents_package_has_no_violations(self) -> None:
        self.assertEqual(find_bare_cross_cog_imports("pixelagents"), [])

    def test_detects_a_bare_import_reachable_through_an_unguarded_chain(self) -> None:
        """Synthetic fixture reproducing the exact incident this script
        exists to catch: `__init__.py` eagerly imports a submodule that
        itself eagerly imports another submodule with a bare cross-cog
        import -- neither hop is guarded, so both must be walked."""

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "newcog"
            adapters = pkg / "adapters"
            adapters.mkdir(parents=True)
            (pkg / "__init__.py").write_text("from .newcog import NewCog\n")
            (pkg / "newcog.py").write_text("from .adapters.cog_base import CogBase\n")
            (adapters / "__init__.py").write_text("")
            (adapters / "cog_base.py").write_text("from corridor.domain import ReplyMode\n")

            with patch("contracts.dependency_loading.lint_lazy_defer_targets.REPO_ROOT", root):
                violations = find_bare_cross_cog_imports("newcog")

        self.assertEqual(len(violations), 1)
        path, lineno, name = violations[0]
        self.assertEqual(path.name, "cog_base.py")
        self.assertEqual(lineno, 1)
        self.assertEqual(name, "corridor")

    def test_a_type_checking_guarded_import_is_not_followed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "newcog"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from .newcog import NewCog\n"
            )
            (pkg / "newcog.py").write_text("from corridor.domain import ReplyMode\n")

            with patch("contracts.dependency_loading.lint_lazy_defer_targets.REPO_ROOT", root):
                violations = find_bare_cross_cog_imports("newcog")

        self.assertEqual(violations, [])

    def test_a_function_scoped_import_is_not_followed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "newcog"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(
                "async def setup(bot):\n    from .newcog import NewCog\n"
            )
            (pkg / "newcog.py").write_text("from corridor.domain import ReplyMode\n")

            with patch("contracts.dependency_loading.lint_lazy_defer_targets.REPO_ROOT", root):
                violations = find_bare_cross_cog_imports("newcog")

        self.assertEqual(violations, [])


class TestRepoRootIsTheActualRepo(unittest.TestCase):
    def test_repo_root_points_at_this_checkout(self) -> None:
        self.assertTrue((REPO_ROOT / "pyproject.toml").is_file())


if __name__ == "__main__":
    unittest.main()
