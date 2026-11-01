#!/usr/bin/env bash
# Pre-populate the runner's npm cache (~/.npm) with the dependencies of the
# two things pixelagents builds on its first cog_load: the vendored Pixel
# Agents webview and the pixatar CLI (pinned Pixel Index commit).
#
# Both builds run `npm ci` against a fresh clone inside cog_load, so a cold
# npm cache makes that first load as slow as the npm registry is that day.
# The suites that load pixelagents for real (check-cogs' downloader harness,
# the e2e suite) give a single load or test a fixed budget; warming the
# cache here keeps that budget about the build, not about registry latency.
# Best-effort: a failure here only means the suites start cold.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
infra="${repo_root}/pixelagents/infrastructure"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

warm() {
  local name="$1" url="$2" commit="$3" workspace="$4"
  local dir="${work}/${name}"
  echo "🔥 Warming npm cache for ${name} (${url}@${commit}, workspace ${workspace})"
  git init -q "${dir}" &&
    git -C "${dir}" remote add origin "${url}" &&
    git -C "${dir}" fetch -q --depth 1 origin "${commit}" &&
    git -C "${dir}" checkout -q FETCH_HEAD &&
    (cd "${dir}" && npm ci --workspace="${workspace}" --ignore-scripts --no-audit --no-fund) ||
    echo "⚠️ Could not warm the npm cache for ${name}; the suite will start cold."
}

warm webview "$(tr -d '[:space:]' < "${infra}/webview_vendor.repo")" \
  "$(tr -d '[:space:]' < "${infra}/webview_vendor.commit")" webview-ui
warm pixatar "https://github.com/pixel-agents-hq/index.git" \
  "$(tr -d '[:space:]' < "${infra}/pixatar_vendor.commit")" \
  "$(python3 -c 'import re,sys; print(re.search(r"^WORKSPACE = \"([^\"]+)\"", open(sys.argv[1]).read(), re.M).group(1))' "${infra}/pixatar_build.py")"
exit 0
