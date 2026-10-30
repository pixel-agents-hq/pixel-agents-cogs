from __future__ import annotations

import unittest

from ..domain import (
    DependencyCycleError,
    closure,
    dependents,
    load_order,
    unload_order,
)

# The real office-cogs shape: corridor at the bottom, pixelagents in the
# middle, cctv/floorplan on top of both.
REQUIRES = {
    "corridor": (),
    "pixelagents": ("corridor",),
    "cctv": ("corridor", "pixelagents"),
    "floorplan": ("corridor", "pixelagents"),
    "deskutils": ("corridor",),
    "dashboard": (),
}


class TestClosure(unittest.TestCase):
    def test_includes_transitive_requirements(self) -> None:
        result = closure(["cctv"], REQUIRES)

        self.assertEqual(result.names, {"cctv", "pixelagents", "corridor"})
        self.assertEqual(result.unknown, {})

    def test_reports_unknown_dependencies_without_failing(self) -> None:
        result = closure(["toolbox"], {**REQUIRES, "toolbox": ("corridor", "nodejs")})

        self.assertEqual(result.names, {"toolbox", "corridor"})
        self.assertEqual(result.unknown, {"toolbox": ("nodejs",)})


class TestLoadOrder(unittest.TestCase):
    def test_dependencies_come_first(self) -> None:
        order = load_order(REQUIRES, REQUIRES)

        for name, deps in REQUIRES.items():
            for dep in deps:
                self.assertLess(order.index(dep), order.index(name), f"{dep} before {name}")

    def test_ties_break_alphabetically(self) -> None:
        self.assertEqual(
            load_order(REQUIRES, REQUIRES),
            ["corridor", "dashboard", "deskutils", "pixelagents", "cctv", "floorplan"],
        )

    def test_only_edges_inside_the_set_count(self) -> None:
        self.assertEqual(load_order(["cctv", "deskutils"], REQUIRES), ["cctv", "deskutils"])

    def test_cycle_raises_with_the_cycle_members(self) -> None:
        with self.assertRaises(DependencyCycleError) as caught:
            load_order(["a", "b", "c"], {"a": ("b",), "b": ("a",), "c": ()})

        self.assertEqual(caught.exception.names, ("a", "b"))

    def test_self_dependency_is_a_cycle(self) -> None:
        with self.assertRaises(DependencyCycleError):
            load_order(["a"], {"a": ("a",)})


class TestUnloadOrder(unittest.TestCase):
    def test_dependents(self) -> None:
        self.assertEqual(dependents(["pixelagents"], REQUIRES), {"cctv", "floorplan"})

    def test_unloading_corridor_takes_every_loaded_dependent_first(self) -> None:
        loaded = {"corridor", "pixelagents", "cctv", "deskutils", "dashboard"}

        order = unload_order(["corridor"], loaded, REQUIRES)

        # Reverse of the alphabetical-tie-break load order.
        self.assertEqual(order, ["cctv", "pixelagents", "deskutils", "corridor"])

    def test_unloaded_names_are_left_out(self) -> None:
        self.assertEqual(unload_order(["cctv"], {"corridor"}, REQUIRES), [])
