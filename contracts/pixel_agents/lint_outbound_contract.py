#!/usr/bin/env python3
"""Fail if pixelagents.contracts.outbound's builders emit a message that
doesn't match the committed pixel-agents-consumer-contract.yaml -- the fast,
offline counterpart to verify_outbound.py's live vendor check.

Reuses verify_outbound._capture_messages() (already fully offline -- it
drives OfficeService/PresenceService with an in-memory repository, no
network) so there's exactly one place that knows how to exercise these
helpers through a realistic sequence; this only adds the schema source (our
own committed contract) and the pass/fail wiring.

_capture_messages() also captures bootstrap messages that aren't part of
pixelagents.contracts.outbound at all (providerCapabilities, the asset*Loaded
family, settingsLoaded, areaMappingsLoaded, layoutLoaded -- built inline in
OfficeService.bootstrap_messages, or owned by floorplan/contracts/outbound.py
for layoutLoaded specifically) -- those are out of scope for this contract by
design, so this filters the captured set down to message types
generate_consumer_contract.build_contract() currently derives from
outbound.py's own TypedDicts (computed live, not read from the possibly-stale
committed yaml) before checking against the committed contract.

This does NOT need a vendor clone -- it checks captured messages against our
own committed contract, not against upstream. Real upstream drift is caught
by verify_outbound.py's live consumer_contract_drift check instead (runs
against the real vendored core/asyncapi.yaml, in
.github/workflows/contract-checks.yml).

Run: python -m contracts.pixel_agents.lint_outbound_contract
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

from contracts.pixel_agents.generate_consumer_contract import build_contract as _build_live_contract
from contracts.pixel_agents.verify_outbound import _capture_messages

CONTRACT_PATH = Path(__file__).with_name("pixel-agents-consumer-contract.yaml")


def _load_contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _outbound_message_types() -> set[str]:
    """The message types outbound.py's TypedDicts define right now -- not
    read from the (possibly stale) committed yaml, so a type outbound.py
    added but the committed contract hasn't caught up to yet still gets
    checked (and correctly reported as missing), rather than silently
    filtered out alongside the genuinely-out-of-scope bootstrap messages."""

    return set(_build_live_contract()["messages"])


def check(contract: dict[str, Any], messages: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    for message in messages:
        message_type = message.get("type")
        schema = contract["messages"].get(message_type)
        if schema is None:
            problems.append(f"no contract entry for captured message type {message_type!r}")
            continue
        errors = sorted(Draft7Validator(schema).iter_errors(message), key=lambda e: e.path)
        if errors:
            problems.append(
                f"{message_type!r} failed pixel-agents-consumer-contract.yaml: "
                + "; ".join(e.message for e in errors)
            )
    return not problems, problems


def main() -> int:
    contract = _load_contract()
    in_scope_types = _outbound_message_types()
    messages = [m for m in asyncio.run(_capture_messages()) if m.get("type") in in_scope_types]
    ok, problems = check(contract, messages)
    if not ok:
        print("pixel-agents-consumer-contract.yaml violation(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"All {len(messages)} captured outbound message(s) match pixel-agents-consumer-contract.yaml."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
