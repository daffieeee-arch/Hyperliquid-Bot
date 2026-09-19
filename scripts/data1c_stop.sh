#!/usr/bin/env bash
# DATA-1C operator stop helper.
# Create-only: sends SIGINT via tmux C-c. Never resumes or reuses the same run_id.
# Never attaches to or stops Phase A tmux sessions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1c_lib.sh
source "${SCRIPT_DIR}/data1c_lib.sh"

TMUX_SESSION="${TMUX_SESSION:-$(data1c_default_tmux_session)}"
data1_refuse_test_env_live_stop "${TMUX_SESSION}"

data1c_refuse_protected_tmux_session "${TMUX_SESSION}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed." >&2
  echo "Never resume an existing run_id; start a new run_id after an operator stop." >&2
  echo "Never send C-c to Phase A capture sessions from this helper." >&2
  exit 1
fi

if ! tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
  echo "tmux session ${TMUX_SESSION} is not running. Nothing to stop." >&2
  echo "Never resume an existing run_id; start a new run_id after an operator stop." >&2
  echo "Never send C-c to Phase A capture sessions from this helper." >&2
  exit 1
fi

tmux send-keys -t "${TMUX_SESSION}" C-c
echo "tmux_session=${TMUX_SESSION}"
echo "signal=SIGINT"
echo "status=OPERATOR_STOP_REQUESTED"
echo "resume_policy=never resume or overwrite an existing DATA-1C run directory"
echo "never_touch=hl-capture,bn-capture,bv-capture,bv-std-capture,kr-capture"
