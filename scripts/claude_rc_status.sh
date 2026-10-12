#!/usr/bin/env bash
# Read-only status of the always-on Claude Code Remote Control service.
# Installed as /usr/local/bin/claude-rc-status; run it as the operator, no sudo.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=claude_rc_lib.sh
source "${SCRIPT_DIR}/claude_rc_lib.sh"

SOCKET="${CLAUDE_RC_TMUX_SOCKET:-${CLAUDE_RC_DEFAULT_SOCKET}}"
SESSION="${CLAUDE_RC_TMUX_SESSION:-${CLAUDE_RC_DEFAULT_SESSION}}"

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

echo
if tmux -L "${SOCKET}" has-session -t "=${SESSION}" 2>/dev/null; then
  echo "screen of claude remote-control (tmux -L ${SOCKET} attach -t ${SESSION} to open it, Ctrl+B then D to leave):"
  tmux -L "${SOCKET}" capture-pane -p -J -t "=${SESSION}:" 2>/dev/null | claude_rc_trim_blank_tail
else
  echo "no claude remote-control screen is running right now."
fi
