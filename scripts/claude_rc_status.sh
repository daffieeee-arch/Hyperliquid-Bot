#!/usr/bin/env bash
# Read-only status of the always-on Claude Code Remote Control service.
# Installed as /usr/local/bin/claude-rc-status; run it as the operator, no sudo.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=claude_rc_lib.sh
source "${SCRIPT_DIR}/claude_rc_lib.sh"

# is-active and is-enabled print their answer and exit non-zero for most of them.
active="$(systemctl is-active "${CLAUDE_RC_UNIT_NAME}" 2>/dev/null || true)"
enabled="$(systemctl is-enabled "${CLAUDE_RC_UNIT_NAME}" 2>/dev/null || true)"
echo "service: ${active:-unknown} (${enabled:-not installed})"

echo
echo "last service log lines:"
log="$(journalctl -u "${CLAUDE_RC_UNIT_NAME}" -n 15 --no-pager -q 2>/dev/null || true)"
if [[ -n "${log}" ]]; then
  printf '%s\n' "${log}"
else
  echo "  (none visible to $(id -un); if that looks wrong: sudo journalctl -u ${CLAUDE_RC_UNIT_NAME} -n 15)"
fi

# The screen lives on the service user's private tmux socket; under sudo,
# ask as that user, not as root.
service_user="$(systemctl show -p User --value "${CLAUDE_RC_UNIT_NAME}" 2>/dev/null || true)"
state_dir="${CLAUDE_RC_STATE_DIR:-${HOME}/.cache/claude-rc}"
if [[ "${EUID}" -eq 0 && -n "${service_user}" && "${service_user}" != "root" ]]; then
  export CLAUDE_RC_RUN_AS="${service_user}"
  state_dir="$(getent passwd "${service_user}" | cut -d: -f6 || true)/.cache/claude-rc"
fi

echo
if claude_rc_tmux has-session -t "$(claude_rc_session_target)" 2>/dev/null; then
  echo "screen of claude remote-control (env -u TMUX_TMPDIR tmux -L ${CLAUDE_RC_SOCKET} attach -t ${CLAUDE_RC_SESSION} to open it, Ctrl+B then D to leave):"
  # The session can end between the two calls; the status stays read-only and quiet.
  claude_rc_tmux capture-pane -p -J -t "$(claude_rc_pane_target)" 2>/dev/null \
    | claude_rc_trim_blank_tail || true
else
  echo "no claude remote-control screen is running right now."
fi
if [[ -s "${state_dir}/last-screen.txt" ]]; then
  echo
  echo "last exit of claude remote-control (${state_dir}/last-screen.txt):"
  cat "${state_dir}/last-screen.txt"
fi
