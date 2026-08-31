#!/usr/bin/env python3
"""Doc cross-reference check for corridor/office_state.yaml -- the
office-state counterpart to contracts/corridor/lint_corridor_contract.py.

Structural correctness (does office_state.yaml match
corridor/domain/office_state.py?) is generate_office_contract.py
--check's job. This file's only job: every name declared in
office_state.yaml is still mentioned in docs/cctv-design.md's own prose,
so the doc can't silently drift out of sync with the model it describes.

Run: python -m contracts.corridor.lint_office_contract
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "corridor" / "office_state.yaml"
DESIGN_DOC_PATH = REPO_ROOT / "docs" / "cctv-design.md"


def load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def check(contract: dict, doc_text: str) -> list[str]:
    events = contract.get("events")
    if not isinstance(events, dict):
        return ["office_state.yaml has no top-level 'events' mapping"]

    return [
        f"{name!r} declared in office_state.yaml but not mentioned in {DESIGN_DOC_PATH.name}"
        for name in events
        if name not in doc_text
    ]


def main() -> int:
    contract = load_contract()
    doc_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    problems = check(contract, doc_text)
    if problems:
        print("office_state.yaml doc cross-reference violation(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Every event in office_state.yaml is mentioned in docs/cctv-design.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
