from __future__ import annotations

import unittest

from packaging.markers import default_environment

from ..domain import InstalledDistribution, unsatisfied
from ..domain.requirements import same_direct_url

ENV = {**default_environment(), "python_version": "3.11", "sys_platform": "linux"}
DEPS_URL = (
    "git+https://github.com/pixel-agents-hq/pixel-agents-cogs@{ref}#subdirectory=requirements"
)


def lookup_from(**dists: InstalledDistribution):  # type: ignore[no-untyped-def]
    return dists.get


class TestUnsatisfied(unittest.TestCase):
    def test_missing_distribution(self) -> None:
        self.assertEqual(unsatisfied(["uvicorn"], lookup_from(), environment=ENV), ("uvicorn",))

    def test_version_specifier(self) -> None:
        lookup = lookup_from(pydantic=InstalledDistribution("2.12.0"))

        self.assertEqual(
            unsatisfied(["pydantic>=2.11.3,<2.12"], lookup, environment=ENV),
            ("pydantic>=2.11.3,<2.12",),
        )
        self.assertEqual(unsatisfied(["pydantic>=2"], lookup, environment=ENV), ())

    def test_names_are_canonicalised(self) -> None:
        lookup = lookup_from(**{"typing-inspection": InstalledDistribution("0.4.1")})

        self.assertEqual(unsatisfied(["typing_inspection<0.4.3"], lookup, environment=ENV), ())

    def test_marker_that_does_not_apply_is_satisfied(self) -> None:
        self.assertEqual(
            unsatisfied(['pywin32; sys_platform == "win32"'], lookup_from(), environment=ENV), ()
        )

    def test_extra_needs_its_own_requirements(self) -> None:
        a2a = InstalledDistribution(
            "0.3.0",
            requires=(
                "httpx>=0.28",
                'starlette>=0.40; extra == "http-server"',
                'grpcio; extra == "grpc"',
            ),
        )

        without = lookup_from(**{"a2a-sdk": a2a})
        self.assertEqual(
            unsatisfied(["a2a-sdk[http-server]"], without, environment=ENV),
            ("a2a-sdk[http-server]",),
        )
        with_starlette = lookup_from(
            **{"a2a-sdk": a2a, "starlette": InstalledDistribution("0.47.0")}
        )
        self.assertEqual(unsatisfied(["a2a-sdk[http-server]"], with_starlette, environment=ENV), ())
        # Base dependencies (httpx) and other extras (grpc) are not checked.
        self.assertEqual(unsatisfied(["a2a-sdk"], without, environment=ENV), ())

    def test_direct_reference_needs_its_own_base_requirements(self) -> None:
        deps = InstalledDistribution(
            "1",
            requires=(
                "anyio<4.15",
                "a2a-sdk[http-server]",
                'pywin32; sys_platform == "win32"',
                'pytest; extra == "test"',
            ),
            direct_url=DEPS_URL.format(ref="pixel-agents-deps-v1"),
        )
        a2a = InstalledDistribution("0.3.0", requires=('starlette; extra == "http-server"',))
        raw = "pixel-agents-deps @ " + DEPS_URL.format(ref="pixel-agents-deps-v1")
        held = {
            "pixel-agents-deps": deps,
            "a2a-sdk": a2a,
            "starlette": InstalledDistribution("0.47.0"),
            "anyio": InstalledDistribution("4.14.2"),
        }

        self.assertEqual(unsatisfied([raw], lookup_from(**held), environment=ENV), ())
        bumped = {**held, "anyio": InstalledDistribution("4.16.0")}
        self.assertEqual(unsatisfied([raw], lookup_from(**bumped), environment=ENV), (raw,))
        without_starlette = {k: v for k, v in held.items() if k != "starlette"}
        self.assertEqual(
            unsatisfied([raw], lookup_from(**without_starlette), environment=ENV), (raw,)
        )

    def test_direct_reference_installed_from_another_ref_is_unsatisfied(self) -> None:
        v1 = InstalledDistribution(
            "1", requires=("foo",), direct_url=DEPS_URL.format(ref="pixel-agents-deps-v1")
        )
        lookup = lookup_from(**{"pixel-agents-deps": v1, "foo": InstalledDistribution("2.1")})
        v2 = "pixel-agents-deps @ " + DEPS_URL.format(ref="pixel-agents-deps-v2")

        self.assertEqual(unsatisfied([v2], lookup, environment=ENV), (v2,))

    def test_direct_reference_without_a_recorded_origin_is_unsatisfied(self) -> None:
        lookup = lookup_from(**{"pixel-agents-deps": InstalledDistribution("1")})
        raw = "pixel-agents-deps @ " + DEPS_URL.format(ref="pixel-agents-deps-v1")

        self.assertEqual(unsatisfied([raw], lookup, environment=ENV), (raw,))

    def test_direct_url_comparison_ignores_dot_git_case_and_slashes(self) -> None:
        installed = "git+https://GitHub.com/o/r@v1#subdirectory=requirements/"

        self.assertTrue(
            same_direct_url(
                "git+https://github.com/o/r.git/@v1#subdirectory=requirements", installed
            )
        )
        self.assertFalse(same_direct_url("git+https://github.com/o/r@v1", installed))
        self.assertFalse(
            same_direct_url("git+https://github.com/o/r#subdirectory=requirements", installed)
        )
        self.assertTrue(
            same_direct_url("git+ssh://git@github.com/o/r", "git+ssh://git@github.com/o/r")
        )
        self.assertTrue(
            same_direct_url("https://x.invalid/p-1.tar.gz", "https://x.invalid/p-1.tar.gz")
        )

    def test_bare_vcs_url_is_satisfied_by_a_distribution_named_after_it(self) -> None:
        raw = "git+https://github.com/AAA3A-AAA3A/AAA3A_utils.git"
        installed = lookup_from(**{"aaa3a-utils": InstalledDistribution("0.1")})

        self.assertEqual(unsatisfied([raw], installed, environment=ENV), ())
        self.assertEqual(unsatisfied([raw], lookup_from(), environment=ENV), (raw,))

    def test_egg_fragment_and_ref_pins(self) -> None:
        installed = lookup_from(mything=InstalledDistribution("1"), repo=InstalledDistribution("1"))

        for raw in (
            "git+https://example.invalid/x.git#egg=mything",
            "git+https://example.invalid/repo.git@v1.2",
        ):
            self.assertEqual(unsatisfied([raw], installed, environment=ENV), (), raw)

    def test_unparseable_requirement_is_always_reported(self) -> None:
        raw = "not a requirement !!"

        self.assertEqual(unsatisfied([raw], lookup_from(), environment=ENV), (raw,))

    def test_non_pep440_installed_version_is_reported(self) -> None:
        lookup = lookup_from(odd=InstalledDistribution("not-a-version"))

        self.assertEqual(unsatisfied(["odd>=1"], lookup, environment=ENV), ("odd>=1",))
