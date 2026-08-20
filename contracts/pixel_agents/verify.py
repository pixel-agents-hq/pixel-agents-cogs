#!/usr/bin/env python3
"""Verify the pixelagents+floorplan -> Pixel Agents (vendored webview) contract.

Consumer-driven contract check: pixelagents/infrastructure/webview_build.py clones
pixel-agents-hq/pixel-agents at the commit pinned in
pixelagents/infrastructure/webview_vendor.commit, builds its webview with npm/vite,
and floorplan/infrastructure/webview.py's WebviewAssetProvider serves the result --
pixelagents owns vendoring+building, floorplan owns serving what gets built (see
pixelagents/adapters/cog_base.py::webview_bundle_status and
floorplan/adapters/cog_base.py::_sync_webview_assets for the cross-cog handoff).
This runs that exact production path -- not a reimplementation of it -- against
the pinned commit and checks the same things a working office actually needs:
every sprite family decodes, a default layout is available, and the built
bundle's asset references all resolve.

Unlike Pixel Index there is only one environment here ("production": whatever
commit webview_vendor.commit currently names). Catching *upcoming* upstream
drift before it's pinned is .github/workflows/vendor-update.yml's job -- it
already gates a candidate pin with this same build path before opening a PR.
This check instead re-verifies the commit that's actually shipped, on a
schedule and on PRs that touch the build pipeline, so drift introduced after
a pin was merged (or reachable only through a full CI environment) doesn't go
unnoticed. See docs/contract-testing.md.

Run: python -m contracts.pixel_agents.verify --output-json /tmp/result.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pixelagents.tests.conftest  # noqa: F401  # stubs redbot before the imports below
from pixelagents.infrastructure import webview_build
from floorplan.infrastructure.webview import WebviewAssetProvider

SPRITE_FAMILIES = ("characters", "floors", "walls", "carpets", "furniture")
_BUNDLE_ASSET_RE = re.compile(r'(?:src|href)="([^"]+)"')
_BUNDLE_PREFIX = "/third-party/floorplan/static/"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_url(commit: str) -> str:
    return f"{webview_build.REPO_URL.removesuffix('.git')}/tree/{commit}"


def build_result_document(env_name: str, source: str, ok: bool, checks: list[dict]) -> dict:
    """Build the stable, machine-readable result consumed by the status page."""
    counts = {
        status: sum(check["status"] == status for check in checks)
        for status in ("pass", "fail", "skipped")
    }
    return {
        "schema_version": 1,
        "environment": env_name,
        "source": source,
        "status": "pass" if ok else "fail",
        "checked_at": _utc_now(),
        "counts": counts,
        "checks": checks,
    }


def write_result_document(path: str, document: dict) -> None:
    """Atomically replace a placeholder result with the completed result."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def _check_load_assets(provider: WebviewAssetProvider) -> tuple[bool, str]:
    provider.load_assets()
    missing = [name for name in SPRITE_FAMILIES if not provider.assets.get(name)]
    if missing:
        return False, f"missing decoded sprite families: {', '.join(missing)}"
    if not provider.assets.get("catalog"):
        return False, "furniture catalog is empty or missing"
    return True, ""


def _check_default_layout(provider: WebviewAssetProvider) -> tuple[bool, str]:
    layout = provider.default_layout()
    if layout is None:
        return False, "no bundled default layout"
    if not layout.get("tiles"):
        return False, "bundled default layout has no tiles"
    return True, ""


def _check_dashboard_bundle(provider: WebviewAssetProvider) -> tuple[bool, str]:
    response = provider.dashboard_webview_response()
    if response.get("status") != 0:
        return False, str(response.get("error_message", "webview response was not servable"))
    source = str(response["web_content"]["source"])  # type: ignore[index]
    bundle_paths = [
        match for match in _BUNDLE_ASSET_RE.findall(source) if match.startswith(_BUNDLE_PREFIX)
    ]
    if not bundle_paths:
        return False, "index.html referenced no bundled asset"
    unresolved = [
        path.removeprefix(_BUNDLE_PREFIX)
        for path in bundle_paths
        if provider.resolve(path.removeprefix(_BUNDLE_PREFIX)) is None
    ]
    if unresolved:
        return False, f"bundle referenced unresolved asset(s): {', '.join(unresolved)}"
    return True, ""


def run(env_name: str) -> tuple[bool, str, list[dict]]:
    commit = webview_build.pinned_commit()
    source = _source_url(commit)

    with TemporaryDirectory() as tmp:
        try:
            webview_build.ensure_webview_built(Path(tmp))
        except webview_build.WebviewBuildError as exc:
            checks = [{"name": "build", "status": "fail", "detail": str(exc)}]
            for name in ("load_assets", "default_layout", "dashboard_bundle"):
                checks.append({"name": name, "status": "skipped", "detail": "build did not complete"})
            return False, source, checks

        provider = WebviewAssetProvider(Path(tmp) / "webview_dist")
        checks = [{"name": "build", "status": "pass", "detail": ""}]
        overall_ok = True
        for name, check in (
            ("load_assets", _check_load_assets),
            ("default_layout", _check_default_layout),
            ("dashboard_bundle", _check_dashboard_bundle),
        ):
            ok, detail = check(provider)
            overall_ok = overall_ok and ok
            checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

        return overall_ok, source, checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-name", default="production", help="Label for output.")
    parser.add_argument(
        "--output-json",
        default=None,
        help="Write a structured result for the status site. The file is written before returning a failing exit code.",
    )
    args = parser.parse_args()

    ok, source, checks = run(args.env_name)

    if args.output_json:
        write_result_document(args.output_json, build_result_document(args.env_name, source, ok, checks))

    lines = [f"## Pixel Agents contract check — {args.env_name}", "", "| Check | Result | Detail |", "|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "skipped": "⚠️"}
    for c in checks:
        detail = c["detail"].replace("|", "\\|") or "-"
        lines.append(f"| {c['name']} | {icon[c['status']]} {c['status']} | {detail} |")
    report = "\n".join(lines)

    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(report + "\n\n")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
