#!/usr/bin/env bash
# DATA-1A operator-PC status: tmux liveness and published Parquet part count.
# Create-only inspection. No secrets. Does not start, stop, or resume a run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1a_lib.sh
source "${SCRIPT_DIR}/data1a_lib.sh"

ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1a_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1a_default_tmux_session)}"
RUN_ID="${RUN_ID:-}"

if [[ -n "${RUN_ID}" ]]; then
  data1a_require_run_id "${RUN_ID}"
else
  mapfile -t RUN_IDS < <(data1a_list_run_ids "${ARTIFACT_ROOT}")
  if [[ "${#RUN_IDS[@]}" -eq 1 ]]; then
    RUN_ID="${RUN_IDS[0]}"
  elif [[ "${#RUN_IDS[@]}" -eq 0 ]]; then
    echo "No DATA-1A run directories under ${ARTIFACT_ROOT}/data-1a/hyperliquid/BTC-PERP/" >&2
    echo "Set RUN_ID to inspect a specific run." >&2
    RUN_ID=""
  else
    echo "Multiple DATA-1A runs found; set RUN_ID to select one:" >&2
    printf '%s\n' "${RUN_IDS[@]}" >&2
    echo "artifact_root=${ARTIFACT_ROOT}"
    echo "tmux_session=${TMUX_SESSION}"
    echo "tmux_alive=$(data1a_tmux_alive "${TMUX_SESSION}")"
    echo "run_id="
    echo "run_dir="
    echo "claim_present=no"
    echo "health_present=no"
    echo "health_status="
    echo "parquet_parts=0"
    exit 2
  fi
fi

echo "artifact_root=${ARTIFACT_ROOT}"
echo "tmux_session=${TMUX_SESSION}"
echo "tmux_alive=$(data1a_tmux_alive "${TMUX_SESSION}")"

if [[ -z "${RUN_ID}" ]]; then
  echo "run_id="
  echo "run_dir="
  echo "claim_present=no"
  echo "health_present=no"
  echo "health_status="
  echo "parquet_parts=0"
  exit 1
fi

RUN_DIR="$(data1a_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
CLAIM_PATH="${RUN_DIR}/capture-claim.json"
HEALTH_PATH="${RUN_DIR}/capture-health.json"
RAW_DIR="${RUN_DIR}/raw"

claim_present=no
health_present=no
health_status=""
[[ -f "${CLAIM_PATH}" ]] && claim_present=yes
if [[ -f "${HEALTH_PATH}" ]]; then
  health_present=yes
  health_status="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' < "${HEALTH_PATH}")"
fi

echo "run_id=${RUN_ID}"
echo "run_dir=${RUN_DIR}"
echo "claim_present=${claim_present}"
echo "health_present=${health_present}"
echo "health_status=${health_status}"
echo "parquet_parts=$(data1a_parquet_part_count "${RAW_DIR}")"
