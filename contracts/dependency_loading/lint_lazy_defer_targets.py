#!/usr/bin/env python3
"""Fail if a cog reached via `corridor.dependency_loader.ensure_importable()`
has a module-scope, non-deferred cross-cog import reachable from its own
top-level `__init__.py` -- the exact incident `pixelagents/__init__.py`'s
`__getattr__` lazy-defer guard already had to fix once, generalized so the
*next* cog to become an `ensure_importable` target doesn't repeat it.

`ensure_importable` (`corridor/dependency_loader.py`) does a bare
`import <package>` to let a dependent reach a target cog's domain/
application/contracts submodules before that target is loaded as a real
Cog. If merely importing the target's `__init__.py` also pulls in a
submodule with a bare `from corridor... import X` (or from any other cog),
that submodule gets cached in `sys.modules`. Red's own `_load` always calls
`_cleanup_and_refresh_modules` before `setup()`, which unconditionally
re-execs every already-cached submodule of the package being loaded --
bypassing any lazy `__getattr__` resolution entirely -- so a later
`[p]load <target>`, at a moment its cross-cog dependency isn't currently
loaded, crashes with `ModuleNotFoundError` before that cog's own `setup()`
ever gets a chance to load its dependency. See docs/dependency-loading.md's
"module-scope-import landmine" section, and `pixelagents/__init__.py`'s own
docstring for the original incident this generalizes.

Approach, both steps pure `ast`-based (no cog imports, no redbot/discord
dependency needed -- matching every other `contracts/` lint's static-only
design):

1. `discover_lazy_defer_targets()` scans every cog's own `__init__.py` for
   `ensure_importable(bot, "<pkg>")` call literals -- derived from source,
   not a hardcoded list, so a *future* target is caught automatically.
2. For each discovered target package, `find_bare_cross_cog_imports()`
   walks its own `__init__.py`'s module-scope imports (skipping
   `if TYPE_CHECKING:` blocks and anything inside a function/class body --
   the two deferral patterns this repo already uses, see
   docs/dependency-loading.md) and follows any *local* (relative) import
   transitively across the target package's own submodules -- exactly the
   modules a bare `import <target>` would actually reach. Any module-scope
   absolute import of another cog (or corridor) found anywhere in that
   reachable set is a violation.

This deliberately does not scan every file in a target package -- only
ones reachable via an unguarded module-scope import chain starting at
`__init__.py`, since that's the actual hazard. A submodule only ever
imported lazily inside `__getattr__`/a function body is fine and correctly
never visited.

Run: python -m contracts.dependency_loading.lint_lazy_defer_targets
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict, deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every cog package this check scans for ensure_importable() call sites --
# same enumeration `contracts/discord_replies/lint_reply_channel.py` uses
# (add a new cog here when it's created). "corridor" is included in the
# cross-cog-name set below even though it never calls ensure_importable
# itself (it's a leaf package) -- it's the most common thing a bare
# module-scope import would reach.
COG_PACKAGES = (
    "architect",
    "cctv",
    "corridor",
    "deskutils",
    "floorplan",
    "painter",
    "pico",
    "pixelagents",
    "suggestionbox",
    "testbench",
    "toolbox",
)
_COG_NAMES = frozenset(COG_PACKAGES)


def _string_const(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def discover_lazy_defer_targets() -> dict[str, list[Path]]:
    """Map target package name -> every caller `__init__.py` that reaches
    it via `ensure_importable(bot, "<pkg>")`, found anywhere in that
    file's AST (not just at module scope -- the call itself always lives
    inside `setup()`)."""

    targets: dict[str, list[Path]] = defaultdict(list)
    for package in COG_PACKAGES:
        init_path = REPO_ROOT / package / "__init__.py"
        if not init_path.is_file():
            continue
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ensure_importable"
                and len(node.args) >= 2
            ):
                continue
            target = _string_const(node.args[1])
            if target is not None:
                targets[target].append(init_path)
    return targets


def _module_info(path: Path) -> tuple[str, bool]:
    """This file's own dotted module path, and whether it's a package
    (`__init__.py`) -- both needed to resolve a relative import's `level`
    correctly (an `__init__.py`'s "own package" is itself, unlike a plain
    module's, which is its parent)."""

    parts = list(path.relative_to(REPO_ROOT).parts)
    is_package = parts[-1] == "__init__.py"
    if is_package:
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts), is_package


def _resolve_relative(current: str, is_package: bool, level: int, module: str | None) -> str | None:
    base = current.split(".")
    if not is_package:
        base = base[:-1]
    if level > 1:
        strip = level - 1
        if strip >= len(base):
            return None
        base = base[:-strip]
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


def _dotted_to_path(dotted: str) -> Path | None:
    as_module = REPO_ROOT / (dotted.replace(".", "/") + ".py")
    if as_module.is_file():
        return as_module
    as_package = REPO_ROOT / dotted.replace(".", "/") / "__init__.py"
    return as_package if as_package.is_file() else None


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _module_scope_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Only direct children of the module body -- anything inside a
    function/class definition is naturally excluded (it's nested inside
    that def/class node, not `tree.body` itself), and an
    `if TYPE_CHECKING:` block's contents are explicitly skipped -- the two
    deferral patterns docs/dependency-loading.md documents."""

    found: list[ast.Import | ast.ImportFrom] = []
    for node in tree.body:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.append(node)
    return found


def _cross_cog_names(node: ast.Import | ast.ImportFrom, own_package: str) -> list[str]:
    """Every other cog (or corridor) this absolute import statement
    references at its top-level segment -- empty for a relative import or
    one that only references `own_package` itself."""

    names: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in _COG_NAMES and top != own_package:
                names.append(top)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        top = node.module.split(".")[0]
        if top in _COG_NAMES and top != own_package:
            names.append(top)
    return names


def _local_follow_targets(
    node: ast.Import | ast.ImportFrom, current: str, is_package: bool, own_package: str
) -> list[str]:
    """Dotted module path(s) this import statement locally reaches within
    `own_package` -- only relative imports count; this codebase's own
    style never writes an absolute same-package import."""

    if not isinstance(node, ast.ImportFrom) or node.level == 0:
        return []
    module_names = [node.module] if node.module else [alias.name for alias in node.names]
    resolved = (_resolve_relative(current, is_package, node.level, name) for name in module_names)
    return [
        r
        for r in resolved
        if r is not None and (r == own_package or r.startswith(own_package + "."))
    ]


def find_bare_cross_cog_imports(package: str) -> list[tuple[Path, int, str]]:
    """BFS from `package`'s own `__init__.py`, following only local
    (relative) module-scope imports across its own submodules -- exactly
    the set a bare `import <package>` would transitively cache -- and
    reporting every module-scope absolute import of another cog found
    anywhere in that reachable set."""

    init_path = REPO_ROOT / package / "__init__.py"
    if not init_path.is_file():
        return []

    violations: list[tuple[Path, int, str]] = []
    visited: set[str] = set()
    queue: deque[str] = deque([package])
    while queue:
        dotted = queue.popleft()
        if dotted in visited:
            continue
        visited.add(dotted)
        path = _dotted_to_path(dotted)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        current, is_package = _module_info(path)
        for node in _module_scope_imports(tree):
            for cross in _cross_cog_names(node, package):
                violations.append((path, node.lineno, cross))
            for target in _local_follow_targets(node, current, is_package, package):
                if target not in visited:
                    queue.append(target)
    return violations


def main() -> int:
    targets = discover_lazy_defer_targets()
    violations_by_package = {
        package: found
        for package in sorted(targets)
        if (found := find_bare_cross_cog_imports(package))
    }

    if violations_by_package:
        print(
            "A cog reached via corridor.dependency_loader.ensure_importable() has a bare "
            "module-scope cross-cog import reachable from its own __init__.py -- see this "
            "script's module docstring for why that's exactly the incident "
            "pixelagents/__init__.py's own __getattr__ guard already had to fix once:"
        )
        for package, entries in sorted(violations_by_package.items()):
            reached_by = ", ".join(sorted(str(p.relative_to(REPO_ROOT)) for p in targets[package]))
            print(f"\n  {package} (reached via ensure_importable() from: {reached_by}):")
            for path, lineno, name in sorted(entries):
                print(f"    {path.relative_to(REPO_ROOT)}:{lineno} imports {name!r}")
        print(
            "\nDefer the import into `if TYPE_CHECKING:` (annotation-only use) or a function "
            "body (runtime use) instead -- see pixelagents/__init__.py's own __getattr__ "
            "guard for the pattern."
        )
        return 1

    found_targets = ", ".join(sorted(targets)) or "none found"
    print(f"Every ensure_importable() target ({found_targets}) guards its own package import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
