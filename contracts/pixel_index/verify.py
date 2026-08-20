#!/usr/bin/env python3
"""Verify the office-cogs -> Pixel Index contract against a live environment.

Consumer-driven contract check: this only exercises the endpoints/fields
office-cogs actually depends on, so a pass here means "safe for floorplan
to point at this environment" — not "this environment's full API is
unchanged." See docs/contract-testing.md.

The contract itself (contract.yaml) is generated from floorplan/contracts/pixel_index.py +
endpoints.py on every run — see generate_contract.py — rather than hand
maintained, so it can't drift from what floorplan actually parses.

Run: python -m contracts.pixel_index.verify --base-url https://pixel-index-api-staging.nntin.xyz
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from jsonschema import Draft7Validator

from contracts.pixel_index.generate_contract import main as generate_contract

_TOKEN_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_result_document(env_name: str, base_url: str, ok: bool, results: list[dict]) -> dict:
    """Build the stable, machine-readable result consumed by the status page."""
    counts = {
        status: sum(result["status"] == status for result in results)
        for status in ("pass", "fail", "skipped")
    }
    return {
        "schema_version": 1,
        "environment": env_name,
        "base_url": base_url,
        "status": "pass" if ok else "fail",
        "checked_at": _utc_now(),
        "counts": counts,
        "endpoints": results,
    }


def write_result_document(path: str, document: dict) -> None:
    """Atomically replace a placeholder result with the completed result."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def extract(data: Any, path: str) -> Optional[Any]:
    """Resolve a dotted/bracket path like 'layouts[0].slug' against data."""
    current = data
    for token in _TOKEN_RE.findall(path):
        try:
            if token.startswith("["):
                current = current[int(token[1:-1])]
            else:
                current = current[token]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def run(contract: dict, base_url: str, timeout: float) -> tuple[bool, list[dict]]:
    context: dict[str, Any] = {}
    results: list[dict] = []
    overall_ok = True

    for step in contract["endpoints"]:
        name = step["name"]
        requires_var = step.get("requires_var")
        if requires_var and not context.get(requires_var):
            results.append({"name": name, "status": "skipped", "detail": f"no value for ${requires_var}"})
            continue

        path = step["path"]
        for key, value in (step.get("path_params") or {}).items():
            resolved = context.get(value[1:]) if isinstance(value, str) and value.startswith("$") else value
            path = path.replace("{" + key + "}", str(resolved))

        url = base_url.rstrip("/") + path
        method = step.get("method", "GET")
        try:
            resp = requests.request(method, url, params=step.get("query"), timeout=timeout)
        except requests.RequestException as exc:
            results.append({"name": name, "status": "fail", "detail": f"request error: {exc}"})
            overall_ok = False
            continue

        expect_status = step.get("expect_status", 200)
        errors: list[str] = []
        body: Any = None
        if resp.status_code != expect_status:
            errors.append(f"expected HTTP {expect_status}, got {resp.status_code}")
        elif "schema" in step:
            try:
                body = resp.json()
            except ValueError:
                errors.append("response body is not valid JSON")
            else:
                validator = Draft7Validator(step["schema"])
                for err in validator.iter_errors(body):
                    loc = ".".join(str(p) for p in err.path) or "<root>"
                    errors.append(f"{loc}: {err.message}")
        elif resp.status_code == expect_status:
            try:
                body = resp.json()
            except ValueError:
                body = None

        if errors:
            overall_ok = False
            results.append({"name": name, "status": "fail", "detail": "; ".join(errors)})
        else:
            results.append({"name": name, "status": "pass", "detail": ""})

        for var, jpath in (step.get("derive") or {}).items():
            context[var] = extract(body, jpath) if body is not None else None

    return overall_ok, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Pixel Index API base URL, e.g. https://pixel-index-api.nntin.xyz")
    parser.add_argument(
        "--contract",
        default=None,
        help="Path to a pre-generated contract.yaml. Default: regenerate it from "
        "floorplan/contracts/pixel_index.py + endpoints.py before checking.",
    )
    parser.add_argument("--env-name", default=None, help="Label for output, defaults to --base-url")
    parser.add_argument(
        "--output-json",
        default=None,
        help="Write a structured result for the status site. The file is written before returning a failing exit code.",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    env_name = args.env_name or args.base_url
    contract_path = args.contract or str(generate_contract())

    with open(contract_path, "r", encoding="utf-8") as fh:
        contract = yaml.safe_load(fh)

    ok, results = run(contract, args.base_url, args.timeout)

    if args.output_json:
        write_result_document(args.output_json, build_result_document(env_name, args.base_url, ok, results))

    lines = [f"## Pixel Index contract check — {env_name}", "", "| Endpoint | Result | Detail |", "|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "skipped": "⚠️"}
    for r in results:
        detail = r["detail"].replace("|", "\\|") or "-"
        lines.append(f"| {r['name']} | {icon[r['status']]} {r['status']} | {detail} |")
    report = "\n".join(lines)

    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(report + "\n\n")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
