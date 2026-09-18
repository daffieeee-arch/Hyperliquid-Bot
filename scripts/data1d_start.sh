#!/usr/bin/env bash
# DATA-1D VPS-first start helper.
# Create-only public Bitvavo Standard BTC-EUR capture. Never resumes a run_id.
# Never attaches to or stops hl-capture, bn-capture, bv-capture, or kr-capture.
# Default tmux session: bv-std-capture (alongside Pro bv-capture).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1d_lib.sh
source "${SCRIPT_DIR}/data1d_lib.sh"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--check-only]" >&2
  exit 2
fi

REPO_ROOT="${REPO_ROOT:-$(data1d_default_repo_root)}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1d_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1d_default_tmux_session)}"
DURATION_SECONDS="${DURATION_SECONDS:-}"
RUN_ID="${RUN_ID:-}"
INCLUDE_CANDLES_FLAG="$(data1d_include_candles_flag)"
CANDLE_INTERVAL_VALUE="$(data1d_candle_interval)"

data1d_refuse_live_modes
data1d_refuse_protected_keys
data1_refuse_test_env_live_start "${TMUX_SESSION}" "${CHECK_ONLY}"
data1d_refuse_protected_tmux_session "${TMUX_SESSION}"

if [[ -z "${DURATION_SECONDS}" ]]; then
  echo "Set DURATION_SECONDS explicitly (1 through 604800). Example retained window: 259200." >&2
  echo "Do not start a multi-day DATA-1D retain until CoS assigns the window and Chupa gives explicit OK for 72h." >&2
  exit 1
fi
data1d_require_duration "${DURATION_SECONDS}"

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
fi
data1d_require_run_id "${RUN_ID}"

RUN_DIR="$(data1d_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
if [[ -e "${RUN_DIR}" ]]; then
  echo "Refuse to start: DATA-1D run directory already exists (create-only, no resume): ${RUN_DIR}" >&2
  exit 1
fi
data1d_refuse_pro_path_collision "${ARTIFACT_ROOT}" "${RUN_ID}"

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Refuse to start: repo checkout is missing: ${REPO_ROOT}" >&2
  exit 1
fi

tmux_state="$(data1d_tmux_alive "${TMUX_SESSION}")"
if [[ "${tmux_state}" == "yes" ]]; then
  echo "Refuse to start: tmux session ${TMUX_SESSION} already exists. Stop it or choose a new TMUX_SESSION. Never resume the same run_id. Never use bv-capture (Pro)." >&2
  exit 1
fi
if [[ "${CHECK_ONLY}" -eq 0 && "${tmux_state}" == "tmux-missing" ]]; then
  echo "Refuse to start: tmux is not installed." >&2
  exit 1
fi

hl_state="$(data1d_tmux_alive "$(data1d_protected_hl_session)")"
bn_state="$(data1d_tmux_alive "$(data1d_protected_bn_session)")"
bv_state="$(data1d_tmux_alive "$(data1d_protected_bv_session)")"
kr_state="$(data1d_tmux_alive "$(data1d_protected_kr_session)")"
echo "check_only=${CHECK_ONLY}"
echo "repo_root=${REPO_ROOT}"
echo "artifact_root=${ARTIFACT_ROOT}"
echo "run_id=${RUN_ID}"
echo "run_dir=${RUN_DIR}"
echo "path_contract=data-1d-bitvavo-btc-eur-v1"
echo "product=BTC-EUR"
echo "feed_product=standard"
echo "tmux_session=${TMUX_SESSION}"
echo "duration_seconds=${DURATION_SECONDS}"
echo "tmux_alive=${tmux_state}"
echo "hl_capture_tmux_alive=${hl_state}"
echo "bn_capture_tmux_alive=${bn_state}"
echo "bv_capture_tmux_alive=${bv_state}"
echo "kr_capture_tmux_alive=${kr_state}"
echo "include_candles=${INCLUDE_CANDLES_FLAG}"
if [[ "${INCLUDE_CANDLES_FLAG}" == "yes" ]]; then
  echo "candle_interval=${CANDLE_INTERVAL_VALUE}"
else
  echo "candle_interval="
fi
echo "signing=false"
echo "credentialless=true"
echo "authenticated=false"
echo "mdpro_fallback=false"
echo "data1e_path_fallback=false"
echo "twenty_four_seven=false"
echo "wait_for_cos=true"
echo "coexists_with_bv_capture=true"
echo "disk_estimate_72h_gb=3-5"
echo "capture_log=${RUN_DIR}/capture-${RUN_ID}.log"
echo "tmux_log=${ARTIFACT_ROOT}/logs/capture-${RUN_ID}.log"
echo "do_not_interrupt_72h=true"
echo "cloud_agents_must_not_stop=true"
data1_phase_a_echo_profile "${DURATION_SECONDS}"
data1_phase_a_echo_pin_policy
data1_bandwidth_echo_policy
data1_phase_a_require_chupa_ok_for_72h "${DURATION_SECONDS}" "${CHECK_ONLY}"
data1_bandwidth_require_hard_for_72h "${DURATION_SECONDS}" "${CHECK_ONLY}"

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  echo "status=CHECK_ONLY"
  echo "do_not_start_now=true"
  exit 0
fi

mkdir -p "${ARTIFACT_ROOT}"

unset TRADING_MODE
unset D41_EXECUTION_MODE

mkdir -p "${ARTIFACT_ROOT}/logs"
BANDWIDTH_WRAP="$(data1_bandwidth_wrap_command)"
CANDLE_ARGS=""
if [[ "${INCLUDE_CANDLES_FLAG}" == "yes" ]]; then
  CANDLE_ARGS="--include-candles --candle-interval $(printf '%q' "${CANDLE_INTERVAL_VALUE}")"
fi
tmux new-session -d -s "${TMUX_SESSION}" \
  "cd $(printf '%q' "${REPO_ROOT}") && unset TRADING_MODE D41_EXECUTION_MODE && export PYTHONPATH=src && exec ${BANDWIDTH_WRAP} uv run --frozen python -m hyperliquid_bot.bitvavo_standard_research --artifact-root $(printf '%q' "${ARTIFACT_ROOT}") --run-id $(printf '%q' "${RUN_ID}") --duration-seconds $(printf '%q' "${DURATION_SECONDS}") ${CANDLE_ARGS}"
tmux pipe-pane -t "${TMUX_SESSION}" -o "cat >> $(printf '%q' "${ARTIFACT_ROOT}/logs/capture-${RUN_ID}.log")" || true

echo "status=STARTED"
echo "stop_with=tmux send-keys -t ${TMUX_SESSION} C-c"
echo "never_touch=hl-capture,bn-capture,bv-capture,kr-capture"
echo "do_not_send_c_c_during_72h_evidence=true"
