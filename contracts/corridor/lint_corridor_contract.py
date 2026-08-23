#!/usr/bin/env python3
"""Doc cross-reference check for contracts/corridor/corridor.yaml.

Structural correctness (does corridor.yaml match corridor/domain/models.py?)
is generate_corridor_contract.py --check's job now that corridor's pub/sub
domain model is real -- see that module. This file's only remaining job:
every name declared in corridor.yaml is still mentioned in the design
doc's own prose, so the doc can't silently drift out of sync with the
model it describes.

Run: python -m contracts.corridor.lint_corridor_contract
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = Path(__file__).with_name("corridor.yaml")
DESIGN_DOC_PATH = REPO_ROOT / "docs" / "corridor-pubsub-design.md"


def load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def check(contract: dict, doc_text: str) -> list[str]:
    events = contract.get("events")
    if not isinstance(events, dict):
        return ["corridor.yaml has no top-level 'events' mapping"]

    return [
        f"{name!r} declared in corridor.yaml but not mentioned in {DESIGN_DOC_PATH.name}"
        for name in events
        if name not in doc_text
    ]


def main() -> int:
    contract = load_contract()
    doc_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    problems = check(contract, doc_text)
    if problems:
        print("corridor.yaml doc cross-reference violation(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Every event in corridor.yaml is mentioned in docs/corridor-pubsub-design.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
