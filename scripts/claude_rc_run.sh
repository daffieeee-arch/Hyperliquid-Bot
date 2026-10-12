#!/usr/bin/env bash
# Always-on Claude Code Remote Control supervisor for the VPS.
#
# systemd (claude-rc.service, see claude_rc_install.sh) runs this as the
# operator user. It starts `claude remote-control` in a detached tmux session
# on a private socket and stays in the foreground while that session lives.
# claude starts through a login shell and without TMUX/TMUX_PANE, so it gets
# the operator's ~/.profile, and tmux sessions that Claude sessions start
# (captures, research jobs) land on the operator's default tmux server, never
# on this private one.
#
# When claude exits, this prints its last screen to the journal (once per
# distinct screen) and exits 0; systemd starts it again. A missing repo or
# binary exits 1. `--stop` sends the documented Ctrl+C, which keeps the
# sessions resumable for about four hours.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=claude_rc_lib.sh
source "${SCRIPT_DIR}/claude_rc_lib.sh"

POLL_SECONDS="${CLAUDE_RC_POLL_SECONDS:-15}"
# Time for a clean exit after the first Ctrl+C, then after a confirming second.
FIRST_STOP_WAIT_SECONDS="${CLAUDE_RC_FIRST_STOP_WAIT_SECONDS:-10}"
STOP_WAIT_SECONDS="${CLAUDE_RC_STOP_WAIT_SECONDS:-20}"
# A missing repo or binary is retried by systemd; the pause keeps that slow.
MISSING_PAUSE_SECONDS="${CLAUDE_RC_MISSING_PAUSE_SECONDS:-60}"
STATE_DIR="${CLAUDE_RC_STATE_DIR:-${HOME}/.cache/claude-rc}"

SESSION_TARGET="$(claude_rc_session_target)"
PANE_TARGET="$(claude_rc_pane_target)"

session_alive() {
  claude_rc_tmux has-session -t "${SESSION_TARGET}" 2>/dev/null
}

# Unknown counts as alive: a failed tmux query must not end a healthy server.
# A session that is really gone is caught by session_alive.
pane_dead() {
  local dead
  dead="$(claude_rc_tmux display-message -p -t "${PANE_TARGET}" '#{pane_dead}' 2>/dev/null)" || return 1
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
    claude_rc_tmux send-keys -t "${PANE_TARGET}" C-c 2>/dev/null || true
    wait_for_exit "${FIRST_STOP_WAIT_SECONDS}"
    # A second Ctrl+C confirms the exit where claude asks for one.
    if session_alive && ! pane_dead; then
      claude_rc_tmux send-keys -t "${PANE_TARGET}" C-c 2>/dev/null || true
      wait_for_exit "${STOP_WAIT_SECONDS}"
    fi
  fi
  claude_rc_tmux kill-session -t "${SESSION_TARGET}" 2>/dev/null || true
}

# Print the last screen unless it equals the one printed before, so a claude
# that exits at once on every restart (an expired login) does not flood the
# journal with the same 40 lines every 30 seconds.
report_last_screen() {
  local status="$1" screen digest previous=""
  # tmux's own "Pane is dead (status, time)" line differs on every exit; drop
  # it and squeeze runs of blank rows before comparing.
  screen="$(claude_rc_tmux capture-pane -p -J -t "${PANE_TARGET}" -S -40 2>/dev/null \
    | grep -v '^Pane is dead (' | cat -s | claude_rc_trim_blank_tail || true)"
  digest="$(printf '%s\n' "${status}" "${screen}" | sha256sum | cut -d' ' -f1)"
  mkdir -p "${STATE_DIR}" 2>/dev/null || true
  previous="$(cat "${STATE_DIR}/last-screen.sha256" 2>/dev/null || true)"
  if [[ "${digest}" == "${previous}" ]]; then
    echo "claude-rc: remote-control exited with status ${status}; same screen as the last exit" >&2
    return 0
  fi
  printf '%s\n' "${digest}" >"${STATE_DIR}/last-screen.sha256" 2>/dev/null || true
  echo "claude-rc: remote-control exited with status ${status}; last screen:" >&2
  printf '%s\n' "${screen}" >&2
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
# after claude exits. It is set on the private server before the session
# exists, so even an instant exit keeps it. The login shell only loads
# ~/.profile and then becomes claude with the same arguments.
# shellcheck disable=SC2016  # $0 and $@ belong to the inner shell
claude_rc_tmux start-server \; set-option -wg remain-on-exit on \; \
  new-session -d -s "${CLAUDE_RC_SESSION}" -c "${REPO}" -x 200 -y 50 \
  -- env -u TMUX -u TMUX_PANE bash -lc 'exec "$0" "$@"' "${CLAUDE_BIN}" "${args[@]}"
echo "claude-rc: remote-control started in ${REPO} (tmux -L ${CLAUDE_RC_SOCKET} attach -t ${CLAUDE_RC_SESSION})"

# A stop from systemd ends with SIGTERM after ExecStop; that is not a failure.
trap 'exit 0' TERM
while session_alive && ! pane_dead; do
  sleep "${POLL_SECONDS}" &
  wait "$!" || true
done

if session_alive; then
  status="$(claude_rc_tmux display-message -p -t "${PANE_TARGET}" '#{pane_dead_status}' 2>/dev/null || true)"
  report_last_screen "${status:-unknown}"
  claude_rc_tmux kill-session -t "${SESSION_TARGET}" 2>/dev/null || true
else
  echo "claude-rc: the tmux session ended" >&2
fi
# Exit 0: systemd restarts the service either way (Restart=always), and a
# planned stop must not leave the unit marked failed.
exit 0
