"""Name the fix when a pinned requirement breaks corridor's import.

Red's `typing-extensions` in site-packages shadows the copy in Downloader's
lib dir, so `requirements/pyproject.toml` pins anyio, pydantic and
typing-inspection to releases that import with it. A requirement installed
in another pip run (another repo's cog, or an install from before the
`pixel-agents-deps` meta-package) can still upgrade one of them, and corridor
then fails with, for example, `cannot import name 'sentinel' from
'typing_extensions'`. Stdlib only: this must import when nothing else does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

DEPS_NAME = "pixel-agents-deps"

# Top-level modules whose import error means a pin no longer holds.
PIN_SENSITIVE_MODULES = frozenset(
    {"typing_extensions", "anyio", "pydantic", "pydantic_core", "typing_inspection"}
)

_FROM_MODULE_RE = re.compile(r"cannot import name '[^']+' from '([\w.]+)'")
# pydantic's own check when its pydantic-core is from another release.
_PYDANTIC_CORE_MISMATCH = "pydantic-core version"


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _failing_module(exc: ImportError) -> str | None:
    if exc.name:
        return exc.name
    match = _FROM_MODULE_RE.search(str(exc))
    return match.group(1) if match else None


def is_pin_break(exc: BaseException) -> bool:
    """Whether `exc`, or anything it was raised from, is a broken pin."""

    for link in _chain(exc):
        if _PYDANTIC_CORE_MISMATCH in str(link):
            return True
        if isinstance(link, ImportError) and not isinstance(link, ModuleNotFoundError):
            module = _failing_module(link)
            if module is not None and module.split(".", 1)[0] in PIN_SENSITIVE_MODULES:
                return True
    return False


def pin_break_message(exc: BaseException, requirements: Iterable[str]) -> str | None:
    """The load error to show for a pin break, or None for any other error.

    `requirements` is corridor's info.json list; the fix reinstalls its
    meta-package requirement, which puts every pin in one pip run.
    """

    if not is_pin_break(exc):
        return None
    requirement = next(
        (req for req in requirements if req.replace(" ", "").startswith(f"{DEPS_NAME}@")),
        None,
    )
    target = f'"{requirement}"' if requirement else DEPS_NAME
    return (
        f"{exc}. A package corridor pins was upgraded past what Red's typing-extensions "
        f"supports, usually by a requirement installed in another pip run. "
        f"Fix: [p]pipinstall {target}, then restart the bot."
    )
