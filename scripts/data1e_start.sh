#!/usr/bin/env bash
# DATA-1E operator-PC start helper for WSL2 Ubuntu.
# Create-only authenticated Bitvavo MD Pro capture. Never resumes a run_id.
# Never attaches to or stops tmux hl-capture or bn-capture.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1e_lib.sh
source "${SCRIPT_DIR}/data1e_lib.sh"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--check-only]" >&2
  exit 2
fi

REPO_ROOT="${REPO_ROOT:-$(data1e_default_repo_root)}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$(data1e_default_artifact_root)}"
TMUX_SESSION="${TMUX_SESSION:-$(data1e_default_tmux_session)}"
DURATION_SECONDS="${DURATION_SECONDS:-}"
RUN_ID="${RUN_ID:-}"

data1e_refuse_live_modes
data1e_refuse_protected_keys
data1e_refuse_protected_tmux_session "${TMUX_SESSION}"

if [[ -z "${DURATION_SECONDS}" ]]; then
  echo "Set DURATION_SECONDS explicitly (1 through 604800). Example retained window: 259200." >&2
  echo "Do not start a multi-day DATA-1E retain until CoS assigns this window." >&2
  exit 1
fi
data1e_require_duration "${DURATION_SECONDS}"

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
fi
data1e_require_run_id "${RUN_ID}"

RUN_DIR="$(data1e_run_dir "${ARTIFACT_ROOT}" "${RUN_ID}")"
if [[ -e "${RUN_DIR}" ]]; then
  echo "Refuse to start: DATA-1E run directory already exists (create-only, no resume): ${RUN_DIR}" >&2
  exit 1
fi

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Refuse to start: repo checkout is missing: ${REPO_ROOT}" >&2
  exit 1
fi

tmux_state="$(data1e_tmux_alive "${TMUX_SESSION}")"
if [[ "${tmux_state}" == "yes" ]]; then
  echo "Refuse to start: tmux session ${TMUX_SESSION} already exists. Stop it or choose a new TMUX_SESSION. Never resume the same run_id. Never use hl-capture or bn-capture." >&2
  exit 1
fi
if [[ "${CHECK_ONLY}" -eq 0 && "${tmux_state}" == "tmux-missing" ]]; then
  echo "Refuse to start: tmux is not installed." >&2
  exit 1
fi

mdpro_keys="$(data1e_mdpro_keys_present)"
if [[ "${CHECK_ONLY}" -eq 0 && "${mdpro_keys}" != "yes" ]]; then
  echo "Refuse to start: BITVAVO_MDPRO_API_KEY and BITVAVO_MDPRO_API_SECRET must both be set (View-only). Values not printed." >&2
  exit 1
fi

hl_state="$(data1e_tmux_alive "$(data1e_protected_hl_session)")"
bn_state="$(data1e_tmux_alive "$(data1e_protected_bn_session)")"
echo "check_only=${CHECK_ONLY}"
echo "repo_root=${REPO_ROOT}"
echo "artifact_root=${ARTIFACT_ROOT}"
echo "run_id=${RUN_ID}"
echo "run_dir=${RUN_DIR}"
echo "tmux_session=${TMUX_SESSION}"
echo "duration_seconds=${DURATION_SECONDS}"
echo "tmux_alive=${tmux_state}"
echo "hl_capture_tmux_alive=${hl_state}"
echo "bn_capture_tmux_alive=${bn_state}"
echo "mdpro_keys_present=${mdpro_keys}"
echo "signing=false"
echo "credentialless=false"
echo "authenticated_read_only=true"
echo "twenty_four_seven=false"
echo "wait_for_cos=true"

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  echo "status=CHECK_ONLY"
  echo "do_not_start_now=true"
  exit 0
fi

mkdir -p "${ARTIFACT_ROOT}"

unset TRADING_MODE
unset D41_EXECUTION_MODE

tmux new-session -d -s "${TMUX_SESSION}" \
  "cd $(printf '%q' "${REPO_ROOT}") && unset TRADING_MODE D41_EXECUTION_MODE && export PYTHONPATH=src && exec uv run --frozen python -m hyperliquid_bot.bitvavo_mdpro_research --artifact-root $(printf '%q' "${ARTIFACT_ROOT}") --run-id $(printf '%q' "${RUN_ID}") --duration-seconds $(printf '%q' "${DURATION_SECONDS}")"

echo "status=STARTED"
echo "stop_with=tmux send-keys -t ${TMUX_SESSION} C-c"
echo "never_touch=hl-capture,bn-capture"
