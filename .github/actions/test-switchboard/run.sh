#!/usr/bin/env bash
# Run switchboard's end-to-end checks against a real, freshly created Red
# instance: start it, run the `fresh` phase, then restart it with an emptied
# requirements lib dir and run the `restarted` (autorepair) phase.
#
# Env: DISCORD_TOKEN (required), SOURCE=addpath|downloader (required),
#      WORKSPACE (default: repo root), RPC_PORT (default 6133),
#      DATA_DIR (default: a new temp dir).
set -euo pipefail

: "${DISCORD_TOKEN:?DISCORD_TOKEN is required}"
: "${SOURCE:?SOURCE must be addpath or downloader}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE:-$(cd "${HERE}/../../.." && pwd)}"
RPC_PORT="${RPC_PORT:-6133}"
DATA_DIR="${DATA_DIR:-$(mktemp -d)}"
INSTANCE="switchboard_ci"
LOG_DIR="${LOG_DIR:-${DATA_DIR}/logs}"
HELPER="${HERE}/switchboard_e2e.py"
# The same dashboard stub check-cogs loads, so floorplan doesn't DM the bot
# owner about a missing dashboard. Served from a --cog-path, which also
# covers switchboard's cli_path classification.
FIXTURES="${WORKSPACE}/.github/actions/test-red-discordbot-downloader-local/fixtures"
mkdir -p "${LOG_DIR}"

BOT_PID=""
start_bot() {
  local log="$1"
  echo "Starting Red (log: ${log})"
  python -m redbot "${INSTANCE}" \
    --token "${DISCORD_TOKEN}" \
    --prefix "!" \
    --rpc --rpc-port "${RPC_PORT}" \
    --cog-path "${FIXTURES}" \
    --load-cogs switchboard dashboard \
    > "${log}" 2>&1 &
  BOT_PID=$!
  sleep 5
  if ! kill -0 "${BOT_PID}" 2>/dev/null; then
    echo "Red exited during startup:"
    tail -n 100 "${log}"
    exit 1
  fi
}

stop_bot() {
  if [[ -n "${BOT_PID}" ]]; then
    kill -INT "${BOT_PID}" 2>/dev/null || true
    wait "${BOT_PID}" 2>/dev/null || true
    BOT_PID=""
  fi
}

on_exit() {
  local status=$?
  stop_bot
  if [[ ${status} -ne 0 ]]; then
    for log in "${LOG_DIR}"/*.log; do
      [[ -f "${log}" ]] || continue
      echo "===== tail of ${log}"
      tail -n 200 "${log}"
    done
  fi
  exit "${status}"
}
trap on_exit EXIT

echo "Creating instance ${INSTANCE} at ${DATA_DIR}/data"
redbot-setup --no-prompt --instance-name "${INSTANCE}" --data-path "${DATA_DIR}/data" \
  --backend json --overwrite-existing-instance
python "${HELPER}" prepare --instance "${INSTANCE}" --source "${SOURCE}" --workspace "${WORKSPACE}"

start_bot "${LOG_DIR}/fresh.log"
python "${HELPER}" fresh --port "${RPC_PORT}" --workspace "${WORKSPACE}" --source "${SOURCE}"
stop_bot

# A fresh container's lib dir is empty: Red's own autoload must now fail on
# corridor, and switchboard's autorepair has to install and load it all again.
LIB_DIR="${DATA_DIR}/data/cogs/Downloader/lib"
echo "Emptying ${LIB_DIR}"
find "${LIB_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

start_bot "${LOG_DIR}/restarted.log"
python "${HELPER}" restarted --port "${RPC_PORT}" --workspace "${WORKSPACE}"

if ! grep -q "Failed to load package corridor" "${LOG_DIR}/restarted.log"; then
  echo "Expected Red's own startup autoload to fail on corridor; autorepair was not exercised."
  exit 1
fi
if ! grep -q "switchboard.pip: pip install succeeded" "${LOG_DIR}/restarted.log"; then
  echo "Expected autorepair to reinstall requirements after the restart."
  exit 1
fi
echo "Switchboard end-to-end checks passed (${SOURCE})"
