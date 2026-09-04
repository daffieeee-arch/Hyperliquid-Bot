#!/usr/bin/env bash
# DATA-1F operator-PC status: tmux liveness and published Parquet part count.
# Create-only inspection. No secrets. Does not start, stop, or resume a run.
# Never attaches to or stops tmux hl-capture.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1f_lib.sh
source "${SCRIPT_DIR}/data1f_lib.sh"

ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1f_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1f_default_tmux_session)}"
RUN_ID="${RUN_ID:-}"

data1f_refuse_hl_tmux_session "${TMUX_SESSION}"

if [[ -n "${RUN_ID}" ]]; then
  data1f_require_run_id "${RUN_ID}"
else
  mapfile -t RUN_IDS < <(data1f_list_run_ids "${ARTIFACT_ROOT}")
  if [[ "${#RUN_IDS[@]}" -eq 1 ]]; then
    RUN_ID="${RUN_IDS[0]}"
  elif [[ "${#RUN_IDS[@]}" -eq 0 ]]; then
    echo "No DATA-1F run directories under ${ARTIFACT_ROOT}/data-1f/binance/BTCUSDT/" >&2
    echo "Set RUN_ID to inspect a specific run." >&2
    RUN_ID=""
  else
    echo "Multiple DATA-1F runs found; set RUN_ID to select one:" >&2
    printf '%s\n' "${RUN_IDS[@]}" >&2
    echo "artifact_root=${ARTIFACT_ROOT}"
    echo "tmux_session=${TMUX_SESSION}"
    echo "tmux_alive=$(data1f_tmux_alive "${TMUX_SESSION}")"
    echo "hl_capture_tmux_alive=$(data1f_tmux_alive "$(data1f_protected_hl_session)")"
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
echo "tmux_alive=$(data1f_tmux_alive "${TMUX_SESSION}")"
echo "hl_capture_tmux_alive=$(data1f_tmux_alive "$(data1f_protected_hl_session)")"

if [[ -z "${RUN_ID}" ]]; then
  echo "run_id="
  echo "run_dir="
  echo "claim_present=no"
  echo "health_present=no"
  echo "health_status="
  echo "parquet_parts=0"
  exit 1
fi

RUN_DIR="$(data1f_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
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
echo "parquet_parts=$(data1f_parquet_part_count "${RAW_DIR}")"
echo "never_touch=hl-capture"
