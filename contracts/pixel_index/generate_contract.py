#!/usr/bin/env python3
"""Generate contract.yaml from ENDPOINTS (contracts/pixel_index/endpoints.py)
and the pydantic models they reference (floorplan/contracts/pixel_index.py).

contract.yaml is a build artifact: gitignored, regenerated on every run of
verify.py. Never hand-edit it — edit floorplan/contracts/pixel_index.py
(what fields we depend on) or endpoints.py (which endpoints, params,
chaining) instead, and this file falls out of those automatically.

Run directly for inspection: python -m contracts.pixel_index.generate_contract
"""
from __future__ import annotations

from pathlib import Path

import yaml

from contracts.pixel_index.endpoints import ENDPOINTS

CONTRACT_PATH = Path(__file__).with_name("contract.yaml")

_HEADER = (
    "# AUTO-GENERATED - do not edit by hand, do not commit (see .gitignore).\n"
    "# Source of truth: floorplan/contracts/pixel_index.py + "
    "contracts/pixel_index/endpoints.py\n"
    "# Regenerate: python -m contracts.pixel_index.generate_contract\n"
)


def build_contract() -> dict:
    endpoints = []
    for ep in ENDPOINTS:
        entry: dict = {
            "name": ep.name,
            "method": ep.method,
            "path": ep.path,
            "expect_status": ep.expect_status,
        }
        if ep.query:
            entry["query"] = ep.query
        if ep.path_params:
            entry["path_params"] = ep.path_params
        if ep.requires_var:
            entry["requires_var"] = ep.requires_var
        if ep.derive:
            entry["derive"] = ep.derive
        if ep.model is not None:
            entry["schema"] = ep.model.model_json_schema()
        endpoints.append(entry)
    return {"version": 1, "endpoints": endpoints}


def main() -> Path:
    contract = build_contract()
    with open(CONTRACT_PATH, "w", encoding="utf-8") as fh:
        fh.write(_HEADER)
        yaml.safe_dump(contract, fh, sort_keys=False)
    return CONTRACT_PATH


if __name__ == "__main__":
    path = main()
    print(f"Wrote {path}")
