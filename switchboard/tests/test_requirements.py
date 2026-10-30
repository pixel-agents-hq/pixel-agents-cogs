from __future__ import annotations

import unittest

from packaging.markers import default_environment

from ..domain import InstalledDistribution, unsatisfied

ENV = {**default_environment(), "python_version": "3.11", "sys_platform": "linux"}


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
