from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from contracts.ast_call_graph import (
    FunctionDef,
    crawl_call_graph,
    dotted_call_name,
    index_functions,
)


def _function_def(source: str) -> FunctionDef:
    """Parse `source` (one top-level def) into a FunctionDef, for tests that
    only need crawl_call_graph and don't need real files on disk."""

    tree = ast.parse(source)
    (node,) = tree.body
    assert isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    return FunctionDef(path=Path("<test>"), node=node)


class DottedCallNameTests(unittest.TestCase):
    def test_flattens_attribute_chain(self) -> None:
        call = ast.parse("ctx.interaction.response.send_message()").body[0].value  # type: ignore[attr-defined]
        self.assertEqual(dotted_call_name(call.func), "ctx.interaction.response.send_message")

    def test_bare_name(self) -> None:
        call = ast.parse("helper()").body[0].value  # type: ignore[attr-defined]
        self.assertEqual(dotted_call_name(call.func), "helper")

    def test_unresolvable_base_returns_none(self) -> None:
        # `foo().bar` -- the base is a call result, not a Name -- can't be
        # statically resolved to a dotted string.
        call = ast.parse("foo().bar()").body[0].value  # type: ignore[attr-defined]
        self.assertIsNone(dotted_call_name(call.func))


class IndexFunctionsTests(unittest.TestCase):
    def test_indexes_production_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "a.py").write_text("def handler():\n    pass\n")
            (root / "conftest.py").write_text("def handler():\n    pass\n")
            (root / "tests" / "test_a.py").write_text("def handler():\n    pass\n")

            index = index_functions(root)

            self.assertIn("handler", index)
            self.assertEqual(len(index["handler"]), 1)
            self.assertEqual(index["handler"][0].path, root / "a.py")


class CrawlCallGraphTests(unittest.TestCase):
    def test_follows_self_calls_into_helper(self) -> None:
        entry = _function_def("def handler(self, ctx):\n    self._helper(ctx)\n")
        helper = _function_def("def _helper(self, ctx):\n    ctx.send()\n")
        index = {"_helper": [helper]}
        seen: list[str] = []

        crawl_call_graph(
            entry,
            index,
            follow=lambda dotted: dotted.startswith("self."),
            on_call=lambda caller, call, dotted: seen.append(dotted),
        )

        self.assertIn("self._helper", seen)
        self.assertIn("ctx.send", seen)

    def test_does_not_follow_calls_follow_rejects(self) -> None:
        entry = _function_def("def handler(self, ctx):\n    self._untracked(ctx)\n")
        untracked = _function_def("def _untracked(self, ctx):\n    ctx.send()\n")
        index = {"_untracked": [untracked]}
        seen: list[str] = []

        crawl_call_graph(
            entry,
            index,
            follow=lambda dotted: False,
            on_call=lambda caller, call, dotted: seen.append(dotted),
        )

        self.assertEqual(seen, ["self._untracked"])

    def test_mutual_recursion_terminates_without_error(self) -> None:
        """Two functions that call each other must not hang or recurse
        forever -- crawl_call_graph is iterative and node-deduplicated, so
        each is visited exactly once regardless of the cycle."""

        a = _function_def("def a(self):\n    self.b()\n")
        b = _function_def("def b(self):\n    self.a()\n")
        index = {"a": [a], "b": [b]}
        visits: list[str] = []

        crawl_call_graph(
            a,
            index,
            follow=lambda dotted: dotted.startswith("self."),
            on_call=lambda caller, call, dotted: visits.append(caller.node.name),
        )

        # Each function's body is walked at most once: `a` is the entry
        # (visited once), and `b` is reached exactly once via `self.b()`
        # despite `b` calling back into `a`.
        self.assertEqual(visits, ["a", "b"])

    def test_self_recursion_terminates_without_error(self) -> None:
        recursive = _function_def("def a(self, n):\n    self.a(n - 1)\n")
        index = {"a": [recursive]}
        visits: list[str] = []

        crawl_call_graph(
            recursive,
            index,
            follow=lambda dotted: dotted.startswith("self."),
            on_call=lambda caller, call, dotted: visits.append(dotted),
        )

        self.assertEqual(visits, ["self.a"])

    def test_diamond_shaped_graph_visits_shared_helper_once(self) -> None:
        """`entry` calls both `left` and `right`, which both call `shared`.
        A naive per-path recursive walk would visit `shared` twice; the
        node-global visited set here must visit it once."""

        entry = _function_def("def entry(self):\n    self.left()\n    self.right()\n")
        left = _function_def("def left(self):\n    self.shared()\n")
        right = _function_def("def right(self):\n    self.shared()\n")
        shared = _function_def("def shared(self):\n    self.ctx_send()\n")
        index = {"left": [left], "right": [right], "shared": [shared]}
        shared_visits = 0

        def on_call(caller: FunctionDef, call: ast.Call, dotted: str) -> None:
            nonlocal shared_visits
            if caller.node.name == "shared":
                shared_visits += 1

        crawl_call_graph(
            entry,
            index,
            follow=lambda dotted: dotted.startswith("self."),
            on_call=on_call,
        )

        self.assertEqual(shared_visits, 1)

    def test_raises_when_exceeding_max_nodes(self) -> None:
        """A long call chain past the configured ceiling fails fast with a
        clear error instead of continuing to expand -- mirrors
        ui_limits.py's MAX_TREE_NODES guard against unbounded traversal."""

        chain = [_function_def(f"def f{i}(self):\n    self.f{i + 1}()\n") for i in range(5)]
        index = {f"f{i}": [chain[i]] for i in range(5)}

        with self.assertRaisesRegex(RuntimeError, "crawl_call_graph visited more than"):
            crawl_call_graph(
                chain[0],
                index,
                follow=lambda dotted: dotted.startswith("self."),
                on_call=lambda caller, call, dotted: None,
                max_nodes=2,
            )


if __name__ == "__main__":
    unittest.main()
