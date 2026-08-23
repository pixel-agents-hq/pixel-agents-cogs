#!/usr/bin/env python3
"""Well-formedness check for contracts/corridor/corridor.yaml.

corridor's actual EventBusService/domain dataclasses don't exist yet (see
docs/corridor-pubsub-design.md's "Implementation status" table), so this
does NOT compare corridor.yaml against real Python code -- that's follow-up
work once the implementation PR lands. This only asserts:
  1. corridor.yaml parses as YAML and has the expected top-level shape.
  2. Its `events` set is EXACTLY the six dataclasses
     docs/corridor-pubsub-design.md's "Domain model" section defines --
     neither more nor fewer, so the doc and this file can't drift apart
     silently.
  3. Every event name is actually mentioned in the design doc's text (a
     cheap cross-reference, in the same spirit as
     contracts/pixel_index/lint_model_usage.py's name-substring approach).
  4. Every declared field has a non-empty `type`.

Run: python -m contracts.corridor.lint_corridor_contract
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = Path(__file__).with_name("corridor.yaml")
DESIGN_DOC_PATH = REPO_ROOT / "docs" / "corridor-pubsub-design.md"

_EXPECTED_EVENTS = {
    "AgentRef",
    "AgentReplied",
    "AgentToolStarted",
    "AgentStatusChanged",
    "AgentHighlighted",
    "AgentUnhighlighted",
}


def load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def check(contract: dict, doc_text: str) -> list[str]:
    problems: list[str] = []
    events = contract.get("events")
    if not isinstance(events, dict):
        return ["corridor.yaml has no top-level 'events' mapping"]

    actual = set(events)
    missing = _EXPECTED_EVENTS - actual
    extra = actual - _EXPECTED_EVENTS
    if missing:
        problems.append(f"missing event(s) vs. the design doc's domain model: {sorted(missing)}")
    if extra:
        problems.append(
            f"undocumented event(s) not in the design doc's domain model: {sorted(extra)}"
        )

    for name in actual:
        if name not in doc_text:
            problems.append(
                f"{name!r} declared in corridor.yaml but not mentioned in {DESIGN_DOC_PATH.name}"
            )
        fields = events[name].get("fields") or {}
        for field_name, field_def in fields.items():
            if not (isinstance(field_def, dict) and field_def.get("type")):
                problems.append(f"{name}.{field_name} has no declared type")

    return problems


def main() -> int:
    contract = load_contract()
    doc_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    problems = check(contract, doc_text)
    if problems:
        print("corridor.yaml well-formedness violation(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("corridor.yaml is well-formed and matches docs/corridor-pubsub-design.md's domain model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
