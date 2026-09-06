#!/usr/bin/env bash
# DATA-1B operator-PC status: tmux liveness and published Parquet part count.
# Create-only inspection. No secrets. Does not start, stop, or resume a run.
# Never attaches to or stops tmux hl-capture, bn-capture, or bv-capture.
# When RUN_ID is unset, defaults to the freshest live retain.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1b_lib.sh
source "${SCRIPT_DIR}/data1b_lib.sh"
# shellcheck source=data1_status_lib.sh
source "${SCRIPT_DIR}/data1_status_lib.sh"

ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1b_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1b_default_tmux_session)}"
RUN_ID="${RUN_ID:-}"
RUN_ID_SOURCE=""
RUN_PARENT="${ARTIFACT_ROOT}/data-1b/kraken/$(data1b_product_segment)"

data1b_refuse_protected_tmux_session "${TMUX_SESSION}"

if [[ -n "${RUN_ID}" ]]; then
  data1b_require_run_id "${RUN_ID}"
  RUN_ID_SOURCE="explicit"
else
  mapfile -t RUN_IDS < <(data1b_list_run_ids "${ARTIFACT_ROOT}")
  LIVE_ID="$(data1_status_pick_freshest_live "${RUN_PARENT}")"
  if [[ -n "${LIVE_ID}" ]]; then
    RUN_ID="${LIVE_ID}"
    RUN_ID_SOURCE="auto-detect"
  elif [[ "${#RUN_IDS[@]}" -eq 1 ]]; then
    RUN_ID="${RUN_IDS[0]}"
    RUN_ID_SOURCE="auto-detect"
  elif [[ "${#RUN_IDS[@]}" -eq 0 ]]; then
    echo "No DATA-1B run directories under ${RUN_PARENT}/" >&2
    echo "Retired BTC-EUR is not the current contract and is not listed. Set RUN_ID to inspect a specific USD run." >&2
    RUN_ID=""
  else
    echo "Multiple DATA-1B runs found; none are a live retain. Set RUN_ID to select one:" >&2
    printf '%s\n' "${RUN_IDS[@]}" >&2
    echo "artifact_root=${ARTIFACT_ROOT}"
    echo "tmux_session=${TMUX_SESSION}"
    echo "tmux_alive=$(data1b_tmux_alive "${TMUX_SESSION}")"
    echo "hl_capture_tmux_alive=$(data1b_tmux_alive "$(data1b_protected_hl_session)")"
    echo "bn_capture_tmux_alive=$(data1b_tmux_alive "$(data1b_protected_bn_session)")"
    echo "bv_capture_tmux_alive=$(data1b_tmux_alive "$(data1b_protected_bv_session)")"
    echo "run_id="
    echo "run_id_source="
    echo "run_dir="
    echo "claim_present=no"
    echo "health_present=no"
    echo "health_status="
    echo "parquet_parts=0"
    data1_status_print_unbound_transport_hints
    echo "never_touch=hl-capture,bn-capture,bv-capture"
    exit 2
  fi
fi

echo "artifact_root=${ARTIFACT_ROOT}"
echo "tmux_session=${TMUX_SESSION}"
echo "tmux_alive=$(data1b_tmux_alive "${TMUX_SESSION}")"
echo "hl_capture_tmux_alive=$(data1b_tmux_alive "$(data1b_protected_hl_session)")"
echo "bn_capture_tmux_alive=$(data1b_tmux_alive "$(data1b_protected_bn_session)")"
echo "bv_capture_tmux_alive=$(data1b_tmux_alive "$(data1b_protected_bv_session)")"

if [[ -z "${RUN_ID}" ]]; then
  echo "run_id="
  echo "run_id_source="
  echo "run_dir="
  echo "claim_present=no"
  echo "health_present=no"
  echo "health_status="
  echo "parquet_parts=0"
  data1_status_print_unbound_transport_hints
  echo "never_touch=hl-capture,bn-capture,bv-capture"
  exit 1
fi

RUN_DIR="$(data1b_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
CLAIM_PATH="${RUN_DIR}/capture-claim.json"
HEALTH_PATH="${RUN_DIR}/capture-health.json"
RAW_DIR="${RUN_DIR}/raw"

claim_present=no
health_present=no
health_status=""
[[ -f "${CLAIM_PATH}" ]] && claim_present=yes
if [[ -f "${HEALTH_PATH}" ]]; then
  health_present=yes
  health_status="$(data1_status_health_status "${HEALTH_PATH}")"
fi

echo "run_id=${RUN_ID}"
echo "run_id_source=${RUN_ID_SOURCE}"
echo "run_dir=${RUN_DIR}"
echo "path_contract=$(data1b_path_contract_id)"
echo "product=$(data1b_product_segment)"
echo "wire_product=$(data1b_wire_product)"
echo "claim_present=${claim_present}"
echo "health_present=${health_present}"
echo "health_status=${health_status}"
echo "parquet_parts=$(data1b_parquet_part_count "${RAW_DIR}")"
data1_status_print_transport_hints "${RUN_DIR}" "${RUN_ID}" "${ARTIFACT_ROOT}"
echo "never_touch=hl-capture,bn-capture,bv-capture"
