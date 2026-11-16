#!/usr/bin/env python3
"""Fail if a cog drifts from the runtime-hygiene rules Red's "Becoming an
Approved Cog Creator" guide recommends
(https://docs.discord.red/en/latest/guide_cog_creators.html), see
docs/runtime-hygiene.md.

Checks, all static (stdlib `ast` only -- no cog, redbot or discord import),
over every cog package (`contracts.info_json.lint_info_json.discover_cogs`)
plus the cookiecutter template, skipping `tests/` and `conftest.py`:

- **One logger namespace**: every `getLogger(...)` call names
  `red.pixel-agents-cogs.<cog>` or a child of it
  (`red.pixel-agents-cogs.<cog>.<sub>`), where `<cog>` is the package the
  file lives in. `f"red.pixel-agents-cogs.{__name__}"` is also accepted --
  Red imports each cog as a top-level package, so `__name__` already
  starts with `<cog>.`. A bare `getLogger()` (the root logger) or any
  other name fails.
- **No blocking call on the event loop**: no call in `BLOCKING_CALLS`
  (subprocess, `time.sleep`, `urllib.request.urlopen`, `requests`, bulk
  `shutil` and whole-file `Path` IO) written directly inside an
  `async def` body. Put the work in a sync helper and run it through
  `asyncio.to_thread`, or use the asyncio/aiohttp equivalent. Calls inside
  a nested sync `def`/`lambda` are not on the loop and are not flagged.

Run: python -m contracts.runtime_hygiene.lint_runtime_hygiene
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from contracts.info_json.lint_info_json import REPO_ROOT, discover_cogs

LOGGER_ROOT = "red.pixel-agents-cogs"
TEMPLATE_DIR = Path(".cookiecutter") / "cog-cookiecutter" / "{{cookiecutter.cog_name}}"
# Package name the template's `{{ ... }}` expressions are rendered to.
TEMPLATE_COG = "templatecog"
_JINJA = re.compile(r"\{\{.*?\}\}")

# Dotted call names (as written) that block the event loop. A Path method
# is matched on its attribute name alone, whatever object it is called on.
BLOCKING_CALLS = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "os.system",
        "time.sleep",
        "urllib.request.urlopen",
        "urlopen",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.request",
        "shutil.rmtree",
        "shutil.copytree",
        "shutil.move",
        "shutil.copyfileobj",
    }
)
BLOCKING_METHODS = frozenset({"read_text", "write_text", "read_bytes", "write_bytes"})


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _is_getlogger(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "getLogger") or (
        isinstance(func, ast.Attribute) and func.attr == "getLogger"
    )


def _logger_name_ok(call: ast.Call, cog: str) -> bool:
    if len(call.args) != 1 or call.keywords:
        return False
    (arg,) = call.args
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        base = f"{LOGGER_ROOT}.{cog}"
        return arg.value == base or arg.value.startswith(base + ".")
    if isinstance(arg, ast.JoinedStr) and len(arg.values) == 2:
        prefix, value = arg.values
        return (
            isinstance(prefix, ast.Constant)
            and prefix.value == f"{LOGGER_ROOT}."
            and isinstance(value, ast.FormattedValue)
            and isinstance(value.value, ast.Name)
            and value.value.id == "__name__"
            and value.format_spec is None
            and value.conversion == -1
        )
    return False


def _blocking_calls(tree: ast.AST) -> Iterator[tuple[int, str, str]]:
    """(line, call, enclosing async def) for every blocking call that runs
    directly on the event loop."""

    def walk(node: ast.AST, coroutine: str | None) -> Iterator[tuple[int, str, str]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                yield from walk(child, child.name)
                continue
            if isinstance(child, (ast.FunctionDef, ast.Lambda, ast.ClassDef)):
                yield from walk(child, None)
                continue
            if coroutine is not None and isinstance(child, ast.Call):
                name = _dotted(child.func)
                attr = child.func.attr if isinstance(child.func, ast.Attribute) else None
                if name in BLOCKING_CALLS or attr in BLOCKING_METHODS:
                    yield child.lineno, name or str(attr), coroutine
            yield from walk(child, coroutine)

    yield from walk(tree, None)


def check_source(source: str, cog: str, label: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{label}: cannot parse: {exc}"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_getlogger(node) and not _logger_name_ok(node, cog):
            problems.append(
                f"{label}:{node.lineno}: logger {ast.unparse(node)} must be "
                f'"{LOGGER_ROOT}.{cog}[.<sub>]" or f"{LOGGER_ROOT}.{{__name__}}"'
            )
    for line, call, coroutine in _blocking_calls(tree):
        problems.append(
            f"{label}:{line}: {call}() blocks the event loop inside async def "
            f"{coroutine}; run it through asyncio.to_thread"
        )
    return problems


def _python_files(package: Path) -> Iterator[Path]:
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).parts
        if "tests" in relative or path.name == "conftest.py" or "node_modules" in relative:
            continue
        yield path


def lint(root: Path = REPO_ROOT) -> list[str]:
    problems: list[str] = []
    for cog in discover_cogs(root):
        for path in _python_files(root / cog):
            label = str(path.relative_to(root))
            problems += check_source(path.read_text(encoding="utf-8"), cog, label)
    template = root / TEMPLATE_DIR
    if template.is_dir():
        for path in _python_files(template):
            source = _JINJA.sub(TEMPLATE_COG, path.read_text(encoding="utf-8"))
            problems += check_source(source, TEMPLATE_COG, str(path.relative_to(root)))
    return problems


def main(argv: list[str] | None = None) -> int:
    if sys.argv[1:] if argv is None else argv:
        print("usage: python -m contracts.runtime_hygiene.lint_runtime_hygiene")
        return 2
    problems = lint()
    if problems:
        print("Runtime hygiene problems (see docs/runtime-hygiene.md):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"{len(discover_cogs())} cogs and the cookiecutter template pass runtime hygiene.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
