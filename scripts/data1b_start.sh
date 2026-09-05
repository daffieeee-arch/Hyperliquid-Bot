#!/usr/bin/env bash
# DATA-1B operator-PC start helper for WSL2 Ubuntu.
# Create-only public Kraken BTC/USD L2+trades (optional L3 via KRAKEN_WS_*).
# Never resumes a run_id. Never attaches to or stops hl-capture, bn-capture, or bv-capture.
# Retired BTC-EUR is not the current contract and is never a silent fallback.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1b_lib.sh
source "${SCRIPT_DIR}/data1b_lib.sh"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--check-only]" >&2
  exit 2
fi

REPO_ROOT="${REPO_ROOT:-$(data1b_default_repo_root)}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1b_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1b_default_tmux_session)}"
DURATION_SECONDS="${DURATION_SECONDS:-}"
RUN_ID="${RUN_ID:-}"

data1b_refuse_live_modes
data1b_refuse_protected_keys
data1b_refuse_protected_tmux_session "${TMUX_SESSION}"
l3_keys="$(data1b_l3_keys_present)"

if [[ -z "${DURATION_SECONDS}" ]]; then
  echo "Set DURATION_SECONDS explicitly (1 through 604800). Example retained window: 259200." >&2
  echo "Do not start a multi-day DATA-1B retain until CoS assigns this window." >&2
  exit 1
fi
data1b_require_duration "${DURATION_SECONDS}"

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
fi
data1b_require_run_id "${RUN_ID}"

RUN_DIR="$(data1b_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
if [[ -e "${RUN_DIR}" ]]; then
  echo "Refuse to start: DATA-1B run directory already exists (create-only, no resume): ${RUN_DIR}" >&2
  exit 1
fi
data1b_refuse_retired_eur_identity "${ARTIFACT_ROOT}" "${RUN_ID}"

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Refuse to start: repo checkout is missing: ${REPO_ROOT}" >&2
  exit 1
fi

tmux_state="$(data1b_tmux_alive "${TMUX_SESSION}")"
if [[ "${tmux_state}" == "yes" ]]; then
  echo "Refuse to start: tmux session ${TMUX_SESSION} already exists. Stop it or choose a new TMUX_SESSION. Never resume the same run_id. Never use hl-capture, bn-capture, or bv-capture." >&2
  exit 1
fi
if [[ "${CHECK_ONLY}" -eq 0 && "${tmux_state}" == "tmux-missing" ]]; then
  echo "Refuse to start: tmux is not installed." >&2
  exit 1
fi

hl_state="$(data1b_tmux_alive "$(data1b_protected_hl_session)")"
bn_state="$(data1b_tmux_alive "$(data1b_protected_bn_session)")"
bv_state="$(data1b_tmux_alive "$(data1b_protected_bv_session)")"
echo "check_only=${CHECK_ONLY}"
echo "repo_root=${REPO_ROOT}"
echo "artifact_root=${ARTIFACT_ROOT}"
echo "run_id=${RUN_ID}"
echo "run_dir=${RUN_DIR}"
echo "path_contract=$(data1b_path_contract_id)"
echo "product=$(data1b_product_segment)"
echo "wire_product=$(data1b_wire_product)"
echo "retired_eur_fallback=false"
echo "tmux_session=${TMUX_SESSION}"
echo "duration_seconds=${DURATION_SECONDS}"
echo "tmux_alive=${tmux_state}"
echo "hl_capture_tmux_alive=${hl_state}"
echo "bn_capture_tmux_alive=${bn_state}"
echo "bv_capture_tmux_alive=${bv_state}"
echo "l3_keys_present=${l3_keys}"
echo "signing=false"
if [[ "${l3_keys}" == "yes" ]]; then
  echo "credentialless=false"
  echo "authenticated_l3=true"
else
  echo "credentialless=true"
  echo "authenticated_l3=false"
fi
echo "twenty_four_seven=false"
echo "l2_depth=$(data1b_default_l2_depth)"
echo "l3_depth=$(data1b_default_l3_depth)"
echo "checksum_price_levels=$(data1b_checksum_price_levels)"
echo "wait_for_cos=true"
echo "capture_log=${RUN_DIR}/capture-${RUN_ID}.log"
echo "tmux_log=${ARTIFACT_ROOT}/logs/capture-${RUN_ID}.log"
echo "do_not_interrupt_72h=true"
echo "cloud_agents_must_not_stop=true"

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  echo "status=CHECK_ONLY"
  echo "do_not_start_now=true"
  exit 0
fi

mkdir -p "${ARTIFACT_ROOT}"

unset TRADING_MODE
unset D41_EXECUTION_MODE

mkdir -p "${ARTIFACT_ROOT}/logs"
tmux new-session -d -s "${TMUX_SESSION}" \
  "cd $(printf '%q' "${REPO_ROOT}") && unset TRADING_MODE D41_EXECUTION_MODE && export PYTHONPATH=src && exec uv run --frozen python -m hyperliquid_bot.kraken_l3_research --artifact-root $(printf '%q' "${ARTIFACT_ROOT}") --run-id $(printf '%q' "${RUN_ID}") --duration-seconds $(printf '%q' "${DURATION_SECONDS}")"
tmux pipe-pane -t "${TMUX_SESSION}" -o "cat >> $(printf '%q' "${ARTIFACT_ROOT}/logs/capture-${RUN_ID}.log")" || true

echo "status=STARTED"
echo "stop_with=tmux send-keys -t ${TMUX_SESSION} C-c"
echo "never_touch=hl-capture,bn-capture,bv-capture"
echo "do_not_send_c_c_during_72h_evidence=true"
