#!/usr/bin/env python3
"""Generate the current Pixel Index contract status site and JSON API.

The input directory contains one structured result per environment, produced
by verify.py. The output is a self-contained GitHub Pages artifact: it keeps no
history and exposes the same snapshot as accessible HTML, a versioned JSON API,
and Shields-compatible endpoint documents.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

RESULT_STATUSES = {"pass", "fail", "unknown"}
ENDPOINT_STATUSES = {"pass", "fail", "skipped"}
STATUS_PRIORITY = {"fail": 0, "unknown": 1, "pass": 2}
ENVIRONMENT_PRIORITY = {"production": 0, "staging": 1}
STATUS_PRESENTATION = {
    "pass": {"icon": "✓", "label": "Compatible", "badge": "compatible", "color": "brightgreen"},
    "fail": {"icon": "×", "label": "Incompatible", "badge": "incompatible", "color": "red"},
    "unknown": {"icon": "?", "label": "Unknown", "badge": "unknown", "color": "lightgrey"},
}
ENDPOINT_PRESENTATION = {
    "pass": ("✓", "Pass"),
    "fail": ("×", "Fail"),
    "skipped": ("!", "Skipped"),
    "unknown": ("?", "Unknown"),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def load_results(results_dir: Path) -> list[dict]:
    results: dict[str, dict] = {}
    for path in sorted(results_dir.rglob("*.json")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read contract result {path}: {exc}") from exc

        environment = result.get("environment")
        if not isinstance(environment, str) or not environment:
            raise ValueError(f"Contract result {path} has no environment name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", environment):
            raise ValueError(f"Contract result {path} has an unsafe environment name: {environment!r}")
        if environment in results:
            raise ValueError(f"Duplicate contract result for {environment}")

        status = result.get("status")
        if status not in RESULT_STATUSES:
            result["status"] = "unknown"
            result["detail"] = f"Unrecognized contract result status: {status!r}"

        endpoints = result.get("endpoints")
        if not isinstance(endpoints, list):
            result["endpoints"] = []
            result["status"] = "unknown"
            result["detail"] = "Contract result did not contain endpoint results."
        else:
            for endpoint in endpoints:
                if endpoint.get("status") not in ENDPOINT_STATUSES:
                    endpoint["status"] = "unknown"
                    result["status"] = "unknown"
                    result["detail"] = "Contract result contained an unrecognized endpoint status."

        result["counts"] = {
            name: sum(endpoint.get("status") == name for endpoint in result["endpoints"])
            for name in ("pass", "fail", "skipped")
        }
        results[environment] = result

    if not results:
        raise ValueError(f"No contract result JSON files found below {results_dir}")

    return sorted(results.values(), key=lambda item: (ENVIRONMENT_PRIORITY.get(item["environment"], 100), item["environment"]))


def build_snapshot(
    results: list[dict],
    *,
    repository: str,
    branch: str,
    commit: str,
    run_id: int,
    run_url: str,
    event: str,
    generated_at: datetime,
    valid_for_hours: float,
) -> dict:
    overall = min((result["status"] for result in results), key=lambda value: STATUS_PRIORITY[value])
    repository_url = f"https://github.com/{repository}"
    environments = {result["environment"]: result for result in results}
    return {
        "schema_version": 1,
        "service": "pixel-index-contract",
        "repository": {"name": repository, "url": repository_url},
        "default_branch": {
            "name": branch,
            "commit": commit,
            "commit_url": f"{repository_url}/commit/{commit}",
        },
        "generated_at": isoformat(generated_at),
        "valid_until": isoformat(generated_at + timedelta(hours=valid_for_hours)),
        "run": {"id": run_id, "url": run_url, "event": event},
        "overall": overall,
        "environments": environments,
    }


def badge_document(label: str, status: str) -> dict:
    presentation = STATUS_PRESENTATION[status]
    document = {
        "schemaVersion": 1,
        "label": label,
        "message": presentation["badge"],
        "color": presentation["color"],
        "cacheSeconds": 600,
    }
    if status != "pass":
        document["isError"] = True
    return document


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render_environment_cards(environments: Iterable[dict]) -> str:
    cards = []
    for environment in environments:
        name = environment["environment"]
        status = environment["status"]
        presentation = STATUS_PRESENTATION[status]
        counts = environment["counts"]
        checked_at = environment.get("checked_at")
        checked = f'<time datetime="{escape(checked_at)}">{escape(checked_at)}</time>' if checked_at else "Not completed"
        cards.append(
            f"""
            <article class="environment-card status-{escape(status)}">
              <div class="card-heading">
                <div>
                  <p class="eyebrow">Environment</p>
                  <h2>{escape(name.title())}</h2>
                </div>
                <span class="status-pill"><span aria-hidden="true">{presentation['icon']}</span> {presentation['label']}</span>
              </div>
              <p class="counts"><strong>{counts['pass']}</strong> passed · <strong>{counts['fail']}</strong> failed · <strong>{counts['skipped']}</strong> skipped</p>
              <dl>
                <div><dt>Checked</dt><dd>{checked}</dd></div>
                <div><dt>Target</dt><dd><a href="{escape(environment.get('base_url', ''))}">{escape(environment.get('base_url', 'Unknown'))}</a></dd></div>
              </dl>
              {f'<p class="environment-detail">{escape(environment.get("detail"))}</p>' if environment.get('detail') else ''}
            </article>
            """
        )
    return "\n".join(cards)


def endpoint_names(environments: Iterable[dict]) -> list[str]:
    names: list[str] = []
    for environment in environments:
        for endpoint in environment["endpoints"]:
            name = str(endpoint.get("name", "unknown"))
            if name not in names:
                names.append(name)
    return names


def render_endpoint_table(environments: list[dict]) -> str:
    names = endpoint_names(environments)
    headers = "".join(f"<th scope=\"col\">{escape(environment['environment'].title())}</th>" for environment in environments)
    rows = []
    for name in names:
        cells = []
        for environment in environments:
            endpoint = next((item for item in environment["endpoints"] if item.get("name") == name), None)
            status = endpoint.get("status", "unknown") if endpoint else "unknown"
            icon, label = ENDPOINT_PRESENTATION[status]
            detail = endpoint.get("detail", "") if endpoint else "No endpoint result was produced."
            details = f'<span class="endpoint-detail">{escape(detail)}</span>' if detail else ""
            cells.append(
                f'<td class="endpoint-status status-{escape(status)}"><span class="endpoint-label"><span aria-hidden="true">{icon}</span> {label}</span>{details}</td>'
            )
        rows.append(f'<tr><th scope="row"><code>{escape(name)}</code></th>{"".join(cells)}</tr>')

    if not rows:
        rows.append(f'<tr><td colspan="{len(environments) + 1}" class="empty">No endpoint results were produced.</td></tr>')
    return f"""
      <div class="table-scroll">
        <table>
          <thead><tr><th scope="col">Endpoint</th>{headers}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    """


def render_html(snapshot: dict) -> str:
    environments = list(snapshot["environments"].values())
    overall = STATUS_PRESENTATION[snapshot["overall"]]
    branch = snapshot["default_branch"]
    run = snapshot["run"]
    short_commit = branch["commit"][:7]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Current office-cogs compatibility with the Pixel Index production and staging APIs.">
  <title>Pixel Index contract status</title>
  <style>
    :root {{ color-scheme: light dark; --bg: #f4f6f8; --surface: #fff; --text: #172033; --muted: #657087; --line: #dce1e8; --pass: #137333; --pass-bg: #e6f4ea; --fail: #b3261e; --fail-bg: #fce8e6; --unknown: #5f6368; --unknown-bg: #eef0f2; --skipped: #8a5d00; --skipped-bg: #fff4ce; --link: #0969da; --shadow: 0 12px 32px rgba(30, 42, 62, .08); }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1080px, calc(100% - 32px)); margin: 0 auto; padding: 56px 0 72px; }}
    a {{ color: var(--link); }}
    header {{ margin-bottom: 30px; }}
    h1, h2 {{ line-height: 1.15; margin: 0; }}
    h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); letter-spacing: -.04em; max-width: 760px; }}
    h2 {{ font-size: 1.3rem; }}
    .eyebrow {{ color: var(--muted); font-size: .74rem; font-weight: 750; letter-spacing: .12em; margin: 0 0 7px; text-transform: uppercase; }}
    .lede {{ color: var(--muted); font-size: 1.05rem; max-width: 700px; }}
    .overall {{ align-items: center; background: var(--surface); border: 1px solid var(--line); border-left: 6px solid var(--unknown); border-radius: 16px; box-shadow: var(--shadow); display: flex; gap: 18px; justify-content: space-between; margin: 28px 0; padding: 20px 22px; }}
    .overall.status-pass {{ border-left-color: var(--pass); }} .overall.status-fail {{ border-left-color: var(--fail); }}
    .overall strong {{ display: block; font-size: 1.15rem; }} .overall p {{ color: var(--muted); margin: 2px 0 0; }}
    .status-pill, .endpoint-label {{ align-items: center; border-radius: 999px; display: inline-flex; font-size: .85rem; font-weight: 720; gap: 6px; padding: 5px 10px; white-space: nowrap; }}
    .status-pass .status-pill, .endpoint-status.status-pass .endpoint-label {{ background: var(--pass-bg); color: var(--pass); }}
    .status-fail .status-pill, .endpoint-status.status-fail .endpoint-label {{ background: var(--fail-bg); color: var(--fail); }}
    .status-unknown .status-pill, .endpoint-status.status-unknown .endpoint-label {{ background: var(--unknown-bg); color: var(--unknown); }}
    .endpoint-status.status-skipped .endpoint-label {{ background: var(--skipped-bg); color: var(--skipped); }}
    .environment-grid {{ display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); margin-bottom: 36px; }}
    .environment-card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow); padding: 22px; }}
    .card-heading {{ align-items: flex-start; display: flex; gap: 15px; justify-content: space-between; }}
    .counts {{ color: var(--muted); }} .counts strong {{ color: var(--text); }}
    dl {{ margin: 20px 0 0; }} dl div {{ border-top: 1px solid var(--line); display: grid; gap: 16px; grid-template-columns: 72px 1fr; padding: 10px 0; }}
    dt {{ color: var(--muted); }} dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    .environment-detail {{ background: var(--unknown-bg); border-radius: 8px; margin: 12px 0 0; padding: 10px 12px; }}
    section {{ margin-top: 34px; }} section > h2 {{ margin-bottom: 14px; }}
    .table-scroll {{ background: var(--surface); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); overflow-x: auto; }}
    table {{ border-collapse: collapse; min-width: 680px; width: 100%; }} th, td {{ border-bottom: 1px solid var(--line); padding: 14px 16px; text-align: left; vertical-align: top; }} thead th {{ color: var(--muted); font-size: .8rem; letter-spacing: .04em; text-transform: uppercase; }} tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
    code {{ background: var(--unknown-bg); border-radius: 5px; padding: 2px 5px; }}
    .endpoint-detail {{ color: var(--muted); display: block; font-size: .84rem; margin-top: 7px; max-width: 360px; overflow-wrap: anywhere; }}
    .metadata {{ color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 18px; list-style: none; padding: 0; }}
    .api-links {{ display: flex; flex-wrap: wrap; gap: 10px; }} .api-links a {{ background: var(--surface); border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; text-decoration: none; }}
    .stale-banner {{ background: var(--skipped-bg); border: 1px solid #dfbd69; border-radius: 10px; color: #604400; display: none; margin: 18px 0; padding: 12px 15px; }} body.is-stale .stale-banner {{ display: block; }}
    footer {{ border-top: 1px solid var(--line); color: var(--muted); margin-top: 42px; padding-top: 22px; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0d1117; --surface: #161b22; --text: #e6edf3; --muted: #9da7b3; --line: #30363d; --pass: #56d364; --pass-bg: #173b23; --fail: #ff7b72; --fail-bg: #4c1f21; --unknown: #b1bac4; --unknown-bg: #262c34; --skipped: #e3b341; --skipped-bg: #3b2e12; --link: #58a6ff; --shadow: none; }} .stale-banner {{ color: #f1d488; }} }}
    @media (max-width: 600px) {{ main {{ padding-top: 32px; }} .overall {{ align-items: flex-start; flex-direction: column; }} .card-heading {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body data-generated-at="{escape(snapshot['generated_at'])}" data-valid-until="{escape(snapshot['valid_until'])}">
  <main>
    <header>
      <p class="eyebrow">office-cogs · consumer contract</p>
      <h1>Pixel Index compatibility</h1>
      <p class="lede">The latest contract check from the repository's default branch against the production and staging APIs.</p>
      <div class="stale-banner" role="alert"><strong>This result is stale.</strong> The expected refresh window has passed; follow the workflow link before relying on it.</div>
    </header>

    <div class="overall status-{escape(snapshot['overall'])}">
      <div><strong>Overall status</strong><p id="freshness">Generated {escape(snapshot['generated_at'])}</p></div>
      <span class="status-pill"><span aria-hidden="true">{overall['icon']}</span> {overall['label']}</span>
    </div>

    <div class="environment-grid">
      {render_environment_cards(environments)}
    </div>

    <section aria-labelledby="endpoint-heading">
      <h2 id="endpoint-heading">Endpoint results</h2>
      {render_endpoint_table(environments)}
    </section>

    <section aria-labelledby="run-heading">
      <h2 id="run-heading">Result metadata</h2>
      <ul class="metadata">
        <li>Branch <strong>{escape(branch['name'])}</strong></li>
        <li>Commit <a href="{escape(branch['commit_url'])}"><code>{escape(short_commit)}</code></a></li>
        <li>Workflow <a href="{escape(run['url'])}">run {escape(run['id'])}</a></li>
        <li>Event <strong>{escape(run['event'])}</strong></li>
        <li>Valid until <time datetime="{escape(snapshot['valid_until'])}">{escape(snapshot['valid_until'])}</time></li>
      </ul>
    </section>

    <section aria-labelledby="api-heading">
      <h2 id="api-heading">JSON API</h2>
      <div class="api-links">
        <a href="api/v1/status.json">Complete status</a>
        {''.join(f'<a href="api/v1/environments/{quote(environment["environment"], safe="")}.json">{escape(environment["environment"].title())}</a>' for environment in environments)}
        <a href="api/v1/badges/overall.json">Overall badge endpoint</a>
        {''.join(f'<a href="api/v1/badges/{quote(environment["environment"], safe="")}.json">{escape(environment["environment"].title())} badge endpoint</a>' for environment in environments)}
      </div>
    </section>

    <footer>This is a consumer-driven compatibility check for office-cogs, not a complete Pixel Index availability monitor.</footer>
  </main>
  <script>
    (() => {{
      const body = document.body;
      const generatedAt = new Date(body.dataset.generatedAt);
      const validUntil = new Date(body.dataset.validUntil);
      const freshness = document.getElementById('freshness');
      const update = () => {{
        const minutes = Math.max(0, Math.floor((Date.now() - generatedAt.getTime()) / 60000));
        const age = minutes < 1 ? 'just now' : minutes < 60 ? `${{minutes}} minute${{minutes === 1 ? '' : 's'}} ago` : `${{Math.floor(minutes / 60)}} hour${{Math.floor(minutes / 60) === 1 ? '' : 's'}} ago`;
        freshness.textContent = `Generated ${{age}}`;
        body.classList.toggle('is-stale', Date.now() > validUntil.getTime());
      }};
      update();
      window.setInterval(update, 60000);
    }})();
  </script>
</body>
</html>
"""


def generate_site(
    results_dir: Path,
    output_dir: Path,
    *,
    repository: str,
    branch: str,
    commit: str,
    run_id: int,
    run_url: str,
    event: str,
    valid_for_hours: float = 12,
    generated_at: datetime | None = None,
) -> dict:
    results = load_results(results_dir)
    snapshot = build_snapshot(
        results,
        repository=repository,
        branch=branch,
        commit=commit,
        run_id=run_id,
        run_url=run_url,
        event=event,
        generated_at=generated_at or utc_now(),
        valid_for_hours=valid_for_hours,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "status.json", snapshot)
    write_json(output_dir / "api" / "v1" / "status.json", snapshot)
    for environment in results:
        name = environment["environment"]
        environment_document = {
            "schema_version": snapshot["schema_version"],
            "generated_at": snapshot["generated_at"],
            "valid_until": snapshot["valid_until"],
            "default_branch": snapshot["default_branch"],
            "run": snapshot["run"],
            "environment": environment,
        }
        write_json(output_dir / "api" / "v1" / "environments" / f"{name}.json", environment_document)
        write_json(output_dir / "api" / "v1" / "badges" / f"{name}.json", badge_document(f"Pixel Index {name}", environment["status"]))

    write_json(output_dir / "api" / "v1" / "badges" / "overall.json", badge_document("Pixel Index contract", snapshot["overall"]))
    (output_dir / "index.html").write_text(render_html(snapshot), encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--valid-for-hours", type=float, default=12)
    args = parser.parse_args()

    snapshot = generate_site(
        args.results_dir,
        args.output_dir,
        repository=args.repository,
        branch=args.branch,
        commit=args.commit,
        run_id=args.run_id,
        run_url=args.run_url,
        event=args.event,
        valid_for_hours=args.valid_for_hours,
    )
    print(f"Generated {args.output_dir} with overall status: {snapshot['overall']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
