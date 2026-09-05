#!/usr/bin/env python3
"""Verify the pixelagents+CCTV -> Pixel Agents (vendored webview) contract.

Consumer-driven contract check: pixelagents/infrastructure/webview_build.py clones
pixel-agents-hq/pixel-agents at the commit pinned in
pixelagents/infrastructure/webview_vendor.commit, builds its webview with npm/vite,
and cctv/infrastructure/webview.py's WebviewAssets serves the result --
pixelagents owns vendoring+building, CCTV owns serving what gets built (see
pixelagents/adapters/cog_base.py::webview_bundle_status and
cctv/adapters/cog_base.py::_sync_assets for the cross-cog handoff).
This runs that exact production path -- not a reimplementation of it -- against
the pinned commit and checks the same things a working office actually needs:
every sprite family decodes, a default layout is available, that layout
survives a real `pixel_agents_adapter.decode()` (the same function
architect/painter depend on for every layout they load), and the built
bundle's asset references all resolve. It also validates the websocket half
of the contract: pixelagents/contracts/outbound.py's builders and the
OfficeService/PresenceService application classes that call them, driven
through a realistic sequence and checked against the real
core/asyncapi.yaml this same clone already has on disk (see
verify_outbound.py).

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
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pixelagents.tests.conftest  # noqa: F401  # stubs redbot before the imports below
from cctv.infrastructure.webview import WEBVIEW_BASE_PATH, WebviewAssets
from pixelagents.infrastructure import pixel_agents_adapter, webview_build
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest

from . import verify_outbound

SPRITE_FAMILIES = ("characters", "floors", "walls", "carpets", "furniture")
_BUNDLE_ASSET_RE = re.compile(r'(?:src|href)="([^"]+)"')
# Pixelagents builds relative asset URLs; CCTV injects its own serving base.


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _check_load_assets(provider: WebviewAssets) -> tuple[bool, str]:
    missing = [name for name in SPRITE_FAMILIES if not provider.assets.get(name)]
    if missing:
        return False, f"missing decoded sprite families: {', '.join(missing)}"
    if not provider.assets.get("catalog"):
        return False, "furniture catalog is empty or missing"
    return True, ""


def _check_default_layout(provider: WebviewAssets) -> tuple[bool, str]:
    try:
        layout = webview_build.load_bundled_default_layout(provider.root)
    except (OSError, TypeError, ValueError) as exc:
        return False, f"no bundled default layout: {exc}"
    if not layout.get("tiles"):
        return False, "bundled default layout has no tiles"
    return True, ""


def _check_layout_decode(provider: WebviewAssets) -> tuple[bool, str]:
    """Exercises `pixel_agents_adapter.decode()` -- the only module in
    pixelagents that knows Pixel Agents' raw layout JSON shape, and the
    one architect/painter both go through -- against the same live,
    freshly-built default layout `_check_default_layout` loads. The sprite
    checks above only prove pixelagents' own asset-decode path works; they
    say nothing about `tileColors`/`areaTiles`/furniture-entry fields or
    catalog ids drifting out from under `decode()`, which would surface as
    a `KeyError` on a bot host, not in CI, without this check."""
    try:
        layout = webview_build.load_bundled_default_layout(provider.root)
    except (OSError, TypeError, ValueError) as exc:
        return False, f"no bundled default layout: {exc}"

    manifest_path = provider.root / "assets" / "furniture-styles.json"
    try:
        raw_manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"no furniture style manifest: {exc}"
    styles = FurnitureStyleManifest.from_raw(raw_manifest)

    try:
        office = pixel_agents_adapter.decode(layout, styles)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        return False, f"pixel_agents_adapter.decode() failed on the live default layout: {exc!r}"
    if office.grid.width <= 0 or office.grid.height <= 0:
        return False, "decoded office has an empty grid"
    return True, ""


def _check_dashboard_bundle(provider: WebviewAssets) -> tuple[bool, str]:
    response = provider.page_response("discord")
    if response.get("status") != 0:
        return False, str(response.get("error_message", "webview response was not servable"))
    source = str(response["web_content"]["source"])  # type: ignore[index]
    if f'<base href="{WEBVIEW_BASE_PATH}">' not in source:
        return False, "index.html missing the expected <base href> injection"
    bundle_paths = [match for match in _BUNDLE_ASSET_RE.findall(source) if match.startswith("./")]
    if not bundle_paths:
        return False, "index.html referenced no relative bundled asset"
    unresolved = [
        path.removeprefix("./") for path in bundle_paths if provider.resolve(path) is None
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
            for name in (
                "load_assets",
                "default_layout",
                "layout_decode",
                "dashboard_bundle",
                "outbound_messages",
                "helper_smoke",
            ):
                checks.append(
                    {"name": name, "status": "skipped", "detail": "build did not complete"}
                )
            return False, source, checks

        dist_path = Path(tmp) / "webview_dist"
        provider = WebviewAssets()
        provider.sync(
            SimpleNamespace(
                dist_path=dist_path,
                ready=True,
                detail="loaded",
                built_commit=webview_build.built_commit(dist_path),
            )
        )
        checks = [{"name": "build", "status": "pass", "detail": ""}]
        overall_ok = True
        for name, check in (
            ("load_assets", _check_load_assets),
            ("default_layout", _check_default_layout),
            ("layout_decode", _check_layout_decode),
            ("dashboard_bundle", _check_dashboard_bundle),
        ):
            ok, detail = check(provider)
            overall_ok = overall_ok and ok
            checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

        vendor_dir = Path(tmp) / "vendor" / "pixel-agents"
        for check_result in verify_outbound.run(vendor_dir):
            overall_ok = overall_ok and check_result["status"] == "pass"
            checks.append(check_result)

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
        write_result_document(
            args.output_json, build_result_document(args.env_name, source, ok, checks)
        )

    lines = [
        f"## Pixel Agents contract check — {args.env_name}",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
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
