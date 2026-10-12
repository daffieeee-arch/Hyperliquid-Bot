#!/usr/bin/env bash
# Always-on Claude Code Remote Control supervisor for the VPS.
#
# systemd (claude-rc.service, see claude_rc_install.sh) runs this as the
# operator user. It starts `claude remote-control` in a detached tmux session
# on a private socket, so it never shares a tmux server with the capture or
# research sessions, and stays in the foreground while that session lives.
# When claude exits, this script prints the last screen to the journal and
# exits non-zero; systemd then starts it again.
#
# `--stop` sends Ctrl+C, the documented way to stop the server that keeps its
# sessions resumable for about four hours.

set -euo pipefail

# The installer puts claude_rc_lib.sh next to the installed copy of this file.
SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=claude_rc_lib.sh
source "${SCRIPT_DIR}/claude_rc_lib.sh"

SOCKET="${CLAUDE_RC_TMUX_SOCKET:-${CLAUDE_RC_DEFAULT_SOCKET}}"
SESSION="${CLAUDE_RC_TMUX_SESSION:-${CLAUDE_RC_DEFAULT_SESSION}}"
POLL_SECONDS="${CLAUDE_RC_POLL_SECONDS:-15}"
# Time for a clean exit after the first Ctrl+C, then after a confirming second.
FIRST_STOP_WAIT_SECONDS="${CLAUDE_RC_FIRST_STOP_WAIT_SECONDS:-10}"
STOP_WAIT_SECONDS="${CLAUDE_RC_STOP_WAIT_SECONDS:-20}"
# A missing repo or binary is retried by systemd; the pause keeps that slow.
MISSING_PAUSE_SECONDS="${CLAUDE_RC_MISSING_PAUSE_SECONDS:-60}"

rc_tmux() {
  tmux -L "${SOCKET}" "$@"
}

session_alive() {
  rc_tmux has-session -t "${SESSION}" 2>/dev/null
}

# Unknown counts as alive: a failed tmux query must not end a healthy server.
# A session that is really gone is caught by session_alive.
pane_dead() {
  local dead
  dead="$(rc_tmux display-message -p -t "${SESSION}" '#{pane_dead}' 2>/dev/null)" || return 1
  [[ "${dead}" == "1" ]]
}

wait_for_exit() {
  local limit="$1" waited=0
  while session_alive && ! pane_dead && ((waited < limit)); do
    sleep 1
    waited=$((waited + 1))
  done
}

stop_session() {
  session_alive || return 0
  if ! pane_dead; then
    rc_tmux send-keys -t "${SESSION}" C-c 2>/dev/null || true
    wait_for_exit "${FIRST_STOP_WAIT_SECONDS}"
    # A second Ctrl+C confirms the exit where claude asks for one.
    if session_alive && ! pane_dead; then
      rc_tmux send-keys -t "${SESSION}" C-c 2>/dev/null || true
      wait_for_exit "${STOP_WAIT_SECONDS}"
    fi
  fi
  rc_tmux kill-session -t "${SESSION}" 2>/dev/null || true
}

case "${1:-}" in
  --stop)
    stop_session
    exit 0
    ;;
  "") ;;
  *)
    echo "Usage: $0 [--stop]" >&2
    exit 2
    ;;
esac

REPO="${CLAUDE_RC_REPO:?Set CLAUDE_RC_REPO to the repository checkout}"
CLAUDE_BIN="${CLAUDE_RC_BIN:?Set CLAUDE_RC_BIN to the claude executable}"
NAME="${CLAUDE_RC_NAME:-}"

if [[ ! -d "${REPO}" ]]; then
  echo "claude-rc: waiting, repo checkout is missing: ${REPO}" >&2
  sleep "${MISSING_PAUSE_SECONDS}"
  exit 1
fi
if [[ ! -x "${CLAUDE_BIN}" ]]; then
  echo "claude-rc: waiting, claude is not executable: ${CLAUDE_BIN}" >&2
  sleep "${MISSING_PAUSE_SECONDS}"
  exit 1
fi

# A session left by a crashed run would put two servers on one directory.
stop_session

args=(remote-control)
if [[ -n "${NAME}" ]]; then
  args+=(--name "${NAME}")
fi

# remain-on-exit keeps the last screen (for example a login prompt) readable
# after claude exits, so it can be copied to the journal below. It is set on
# the private server before the session exists, so even an instant exit keeps it.
rc_tmux start-server \; set-option -wg remain-on-exit on \; \
  new-session -d -s "${SESSION}" -c "${REPO}" -x 200 -y 50 -- "${CLAUDE_BIN}" "${args[@]}"
echo "claude-rc: remote-control started in ${REPO} (tmux -L ${SOCKET} attach -t ${SESSION})"

while session_alive && ! pane_dead; do
  sleep "${POLL_SECONDS}"
done

if session_alive; then
  status="$(rc_tmux display-message -p -t "${SESSION}" '#{pane_dead_status}' 2>/dev/null || true)"
  echo "claude-rc: remote-control exited with status ${status:-unknown}; last screen:" >&2
  rc_tmux capture-pane -p -J -t "${SESSION}" -S -40 2>/dev/null | claude_rc_trim_blank_tail >&2 || true
  rc_tmux kill-session -t "${SESSION}" 2>/dev/null || true
else
  echo "claude-rc: the tmux session ended" >&2
fi
exit 1
