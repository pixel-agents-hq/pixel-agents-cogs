"""Shared AST call-graph crawling helpers for linters that need to see
*through* a package's local call graph, not just one function's own body --
e.g. "does this command handler eventually reach call X, even if only via a
shared helper two calls deep". `contracts/discord_replies/lint_reply_channel.py`
is the first user; anything with the same "walk a package's functions,
follow `self.<name>(...)`-shaped calls into their definitions" shape should
reuse this rather than re-implement its own crawl.

Two pieces, used together:

- `index_functions(root)` parses every production `.py` file under a
  package root exactly once and indexes every function/method definition by
  name.
- `crawl_call_graph(entry, index, follow=..., on_call=...)` walks the call
  graph reachable from one entry function, using that index to resolve
  `self.<name>(...)` calls to their definitions elsewhere in the package.

Both existed inline in a first version of `lint_reply_channel.py` as a
recursive function; see `MAX_CALL_GRAPH_NODES` below for why that changed.
"""

from __future__ import annotations

import ast
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

FuncNode = ast.AsyncFunctionDef | ast.FunctionDef

# A generous ceiling on how many functions crawl_call_graph will visit
# before giving up -- not a real limit on package size, the same way
# ui_limits.py's MAX_TREE_NODES isn't a real limit on how big a Discord UI
# gets. It exists so a call cycle (two methods that call each other, direct
# or indirect) or a pathological fan-out (a `self.<name>(...)` call whose
# name matches dozens of same-named methods across the package) fails fast
# with a clear error instead of the crawl growing without bound. The walk
# below is already iterative and node-deduplicated (see crawl_call_graph's
# docstring), so this ceiling is a backstop for "reachable subgraph is
# genuinely huge", not the only thing standing between a bad AST and an
# OOM -- but it's still worth having for the same reason ui_limits.py's is:
# fail fast and readably, not by exhausting memory. See
# ui_limits.py:MAX_TREE_NODES for the precedent (and the incident that
# motivated it there).
MAX_CALL_GRAPH_NODES = 2000


@dataclass(frozen=True)
class FunctionDef:
    """One function/method definition, located for error reporting."""

    path: Path
    node: FuncNode


def dotted_call_name(node: ast.expr) -> str | None:
    """Flatten an `a.b.c` attribute/name chain to `"a.b.c"`; anything else
    (a call result, a subscript, ...) can't be statically resolved and
    returns None -- callers should treat that as "don't know", not "flag
    it", to avoid false positives."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_call_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def index_functions(
    root: Path,
    *,
    exclude_dirs: tuple[str, ...] = ("tests",),
    exclude_files: tuple[str, ...] = ("conftest.py",),
) -> dict[str, list[FunctionDef]]:
    """Parse every `.py` file under `root` once and index every
    function/method definition by name (regardless of nesting or which
    class it belongs to -- this is a name-based approximation suited to a
    mixin-heavy codebase where a caller and a callee's definition often
    live in different files, not real symbol resolution).

    Building this once per package and reusing it across every
    `crawl_call_graph` call on that package is what keeps repeated crawls
    from re-parsing (and re-reading off disk) the same files -- do not call
    this once per entry point.
    """

    index: dict[str, list[FunctionDef]] = defaultdict(list)
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(part in exclude_dirs for part in rel_parts) or path.name in exclude_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                index[node.name].append(FunctionDef(path=path, node=node))
    return index


def crawl_call_graph(
    entry: FunctionDef,
    index: dict[str, list[FunctionDef]],
    *,
    follow: Callable[[str], bool],
    on_call: Callable[[FunctionDef, ast.Call, str], None],
    max_nodes: int = MAX_CALL_GRAPH_NODES,
) -> None:
    """Breadth-first walk of the call graph reachable from `entry`.

    Iterative, not recursive: a straightforward recursive walk here would
    both risk the interpreter's recursion limit on a deep or cyclic call
    graph, and -- without extra bookkeeping -- re-visit the same function
    once per distinct path leading to it (a diamond-shaped call graph, e.g.
    two command handlers sharing a helper that itself calls a second shared
    helper, blows that up combinatorially). This instead uses an explicit
    queue plus a single node-global `visited` set, so each function is
    walked at most once for this crawl no matter how many places call it.

    For every call found while walking each visited function's body,
    `on_call(caller, call_node, dotted_name)` fires -- including calls that
    aren't followed further -- so the caller can record whatever it cares
    about (a raw send call, a "some trusted function was reached" flag,
    ...). `dotted_name` is `dotted_call_name(call_node.func)`, or skipped
    entirely if that returned None (an unresolvable call target).

    `follow(dotted_name)` decides whether to additionally resolve that
    call's target(s) in `index` (by the call's final attribute name) and
    enqueue them -- typically "does this look like `self.<name>(...)`, and
    is `<name>` not a terminal call I don't want expanded".

    Raises `RuntimeError` if more than `max_nodes` functions are visited --
    see `MAX_CALL_GRAPH_NODES` for why that ceiling exists.
    """

    visited: set[int] = set()
    queue: deque[FunctionDef] = deque([entry])
    while queue:
        if len(visited) > max_nodes:
            raise RuntimeError(
                f"crawl_call_graph visited more than {max_nodes} functions starting from "
                f"{entry.node.name!r} ({entry.path}) -- this almost certainly means a call "
                "cycle (two methods calling each other, directly or indirectly) rather than "
                "a package that genuinely has this many reachable functions. If it does, "
                "pass a larger max_nodes explicitly."
            )
        current = queue.popleft()
        node_id = id(current.node)
        if node_id in visited:
            continue
        visited.add(node_id)

        for call in ast.walk(current.node):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            dotted = dotted_call_name(call.func)
            if dotted is None:
                continue
            on_call(current, call, dotted)
            if follow(dotted):
                for callee in index.get(call.func.attr, ()):
                    if id(callee.node) not in visited:
                        queue.append(callee)
