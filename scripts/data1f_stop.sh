#!/usr/bin/env bash
# DATA-1F operator-PC stop helper for WSL2 Ubuntu.
# Create-only: sends SIGINT via tmux C-c. Never resumes or reuses the same run_id.
# Never attaches to or stops tmux hl-capture.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1f_lib.sh
source "${SCRIPT_DIR}/data1f_lib.sh"

TMUX_SESSION="${TMUX_SESSION:-$(data1f_default_tmux_session)}"

data1f_refuse_hl_tmux_session "${TMUX_SESSION}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed." >&2
  echo "Never resume an existing run_id; start a new run_id after an operator stop." >&2
  echo "Never send C-c to hl-capture from this helper." >&2
  exit 1
fi

if ! tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
  echo "tmux session ${TMUX_SESSION} is not running. Nothing to stop." >&2
  echo "Never resume an existing run_id; start a new run_id after an operator stop." >&2
  echo "Never send C-c to hl-capture from this helper." >&2
  exit 1
fi

tmux send-keys -t "${TMUX_SESSION}" C-c
echo "tmux_session=${TMUX_SESSION}"
echo "signal=SIGINT"
echo "status=OPERATOR_STOP_REQUESTED"
echo "resume_policy=never resume or overwrite an existing DATA-1F run directory"
echo "never_touch=hl-capture"
