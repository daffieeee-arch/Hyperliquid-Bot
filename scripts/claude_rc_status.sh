#!/usr/bin/env bash
# Read-only status of the always-on Claude Code Remote Control service.
# Run as the operator, without sudo: bash scripts/claude_rc_status.sh

set -euo pipefail

UNIT_NAME="claude-rc.service"
SOCKET="${CLAUDE_RC_TMUX_SOCKET:-claude-rc}"
SESSION="${CLAUDE_RC_TMUX_SESSION:-claude-rc}"

echo "service: $(systemctl is-active "${UNIT_NAME}" 2>/dev/null || true) ($(systemctl is-enabled "${UNIT_NAME}" 2>/dev/null || echo not-installed))"

echo
echo "last service log lines:"
if ! journalctl -u "${UNIT_NAME}" -n 15 --no-pager 2>/dev/null; then
  echo "  (journal not readable for $(id -un); try: sudo journalctl -u ${UNIT_NAME} -n 15)"
fi

echo
if tmux -L "${SOCKET}" has-session -t "${SESSION}" 2>/dev/null; then
  echo "screen of claude remote-control (tmux -L ${SOCKET} attach -t ${SESSION} to open it, Ctrl+B then D to leave):"
  tmux -L "${SOCKET}" capture-pane -p -J -t "${SESSION}" 2>/dev/null \
    | awk 'NF { last = NR } { row[NR] = $0 } END { for (i = 1; i <= last; i++) print row[i] }'
else
  echo "no claude remote-control screen is running right now."
fi
