#!/usr/bin/env bash
# DATA-1F operator-PC start helper for WSL2 Ubuntu.
# Create-only public Binance capture. No secrets. Never resumes a run_id.
# Never attaches to or stops tmux hl-capture.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1f_lib.sh
source "${SCRIPT_DIR}/data1f_lib.sh"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--check-only]" >&2
  exit 2
fi

REPO_ROOT="${REPO_ROOT:-$(data1f_default_repo_root)}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1f_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1f_default_tmux_session)}"
DURATION_SECONDS="${DURATION_SECONDS:-}"
RUN_ID="${RUN_ID:-}"

data1f_refuse_live_modes
data1f_refuse_protected_keys
data1f_refuse_hl_tmux_session "${TMUX_SESSION}"

if [[ -z "${DURATION_SECONDS}" ]]; then
  echo "Set DURATION_SECONDS explicitly (1 through 604800). Example retained window: 259200." >&2
  echo "Do not start a multi-day DATA-1F retain until the TerraPC DATA-1A hl-capture run finishes and CoS assigns this window." >&2
  exit 1
fi
data1f_require_duration "${DURATION_SECONDS}"

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
fi
data1f_require_run_id "${RUN_ID}"

RUN_DIR="$(data1f_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
if [[ -e "${RUN_DIR}" ]]; then
  echo "Refuse to start: DATA-1F run directory already exists (create-only, no resume): ${RUN_DIR}" >&2
  exit 1
fi

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Refuse to start: repo checkout is missing: ${REPO_ROOT}" >&2
  exit 1
fi

tmux_state="$(data1f_tmux_alive "${TMUX_SESSION}")"
if [[ "${tmux_state}" == "yes" ]]; then
  echo "Refuse to start: tmux session ${TMUX_SESSION} already exists. Stop it or choose a new TMUX_SESSION. Never resume the same run_id. Never use hl-capture." >&2
  exit 1
fi
if [[ "${CHECK_ONLY}" -eq 0 && "${tmux_state}" == "tmux-missing" ]]; then
  echo "Refuse to start: tmux is not installed." >&2
  exit 1
fi

hl_state="$(data1f_tmux_alive "$(data1f_protected_hl_session)")"
echo "check_only=${CHECK_ONLY}"
echo "repo_root=${REPO_ROOT}"
echo "artifact_root=${ARTIFACT_ROOT}"
echo "run_id=${RUN_ID}"
echo "run_dir=${RUN_DIR}"
echo "tmux_session=${TMUX_SESSION}"
echo "duration_seconds=${DURATION_SECONDS}"
echo "tmux_alive=${tmux_state}"
echo "hl_capture_tmux_alive=${hl_state}"
echo "signing=false"
echo "credentialless=true"
echo "twenty_four_seven=false"
echo "wait_for_hl_and_cos=true"

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  echo "status=CHECK_ONLY"
  echo "do_not_start_now=true"
  exit 0
fi

mkdir -p "${ARTIFACT_ROOT}"

# The Python writer creates the run directory. Do not mkdir the run_id path here.
unset TRADING_MODE
unset D41_EXECUTION_MODE

tmux new-session -d -s "${TMUX_SESSION}" \
  "cd $(printf '%q' "${REPO_ROOT}") && unset TRADING_MODE D41_EXECUTION_MODE && export PYTHONPATH=src && exec uv run --frozen python -m hyperliquid_bot.binance_public_research --artifact-root $(printf '%q' "${ARTIFACT_ROOT}") --run-id $(printf '%q' "${RUN_ID}") --duration-seconds $(printf '%q' "${DURATION_SECONDS}")"

echo "status=STARTED"
echo "stop_with=tmux send-keys -t ${TMUX_SESSION} C-c"
echo "never_touch=hl-capture"
