#!/usr/bin/env python3
"""Fail if pixelagents.py reads a field from a Pixel Index response model
that doesn't exist on it — the field-level counterpart to lint_endpoints.py.

pixelagents.py parses Pixel Index responses into the pydantic models in
pixelagents/models.py and accesses them by attribute (entry.furniture,
d.author.displayName, ...). That means a typo, a renamed field, or reading a
field nobody added to the model shows up to mypy as a plain attr-defined
error ("LayoutSummary has no attribute ...") — no bespoke schema-drift
tooling needed for this half of the problem.

We don't adopt mypy for the whole file: pixelagents.py imports discord.py
and redbot, which aren't installed for this lightweight check and would
otherwise need real type stubs to check meaningfully. Running with
ignore_missing_imports resolves that cleanly — those modules become `Any`
everywhere, so nothing there is checked — but it also means unrelated
pre-existing mypy findings elsewhere in the file (dashboard decorators,
Optional handling, etc.) are still reported. Since fixing those is a
separate, larger undertaking, this script narrows the failure condition to
errors that actually name one of our contract models, and treats everything
else as informational.

Run: python -m contracts.pixel_index.lint_model_usage
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pixelagents import models as _models

REPO_ROOT = Path(__file__).resolve().parents[2]
MYPY_CONFIG = Path(__file__).with_name("mypy.ini")
TARGET = REPO_ROOT / "pixelagents" / "pixelagents.py"

# Only fail on mypy errors that mention one of our contract models by name —
# that's the signal that a field read doesn't exist on the modeled response
# shape. Everything else in this dynamically-typed, discord.py-heavy file is
# out of scope for this check.
_MODEL_NAMES = tuple(
    name
    for name in dir(_models)
    if isinstance(getattr(_models, name), type) and issubclass(getattr(_models, name), _models.BaseModel)
)


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(MYPY_CONFIG), str(TARGET)],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    print(output)

    model_errors = [
        line for line in output.splitlines() if "error:" in line and any(name in line for name in _MODEL_NAMES)
    ]

    if model_errors:
        print("\nContract model field drift detected (pixelagents.py reads a field its model doesn't declare):")
        for line in model_errors:
            print(f"  {line}")
        print(
            "\nEither pixelagents.py has a typo, or pixelagents/models.py needs the field added — "
            "see docs/contract-testing.md."
        )
        return 1

    print("No field-level drift between pixelagents.py and pixelagents/models.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
