#!/usr/bin/env bash
# Always-on Claude Code Remote Control supervisor for the VPS.
#
# systemd (claude-rc.service, see claude_rc_install.sh) runs this as the
# operator user. It starts `claude remote-control` in a detached tmux session
# on a private socket and stays in the foreground while that session lives.
# claude starts through a login shell and without TMUX/TMUX_PANE, so it gets
# the operator's login profile, and tmux sessions that Claude sessions start
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
claude_rc_refuse_live_socket_under_test || exit 2

POLL_SECONDS="${CLAUDE_RC_POLL_SECONDS:-15}"
# Time for a clean exit after Ctrl+C before the session is killed.
STOP_WAIT_SECONDS="${CLAUDE_RC_STOP_WAIT_SECONDS:-20}"
if [[ ! "${STOP_WAIT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "claude-rc: CLAUDE_RC_STOP_WAIT_SECONDS must be whole seconds; using 20" >&2
  STOP_WAIT_SECONDS=20
fi
# An identical screen is printed again once the last dump is this old.
SCREEN_RESET_MINUTES="${CLAUDE_RC_SCREEN_RESET_MINUTES:-10}"
# A missing repo or binary is retried by systemd; the pause keeps that slow.
MISSING_PAUSE_SECONDS="${CLAUDE_RC_MISSING_PAUSE_SECONDS:-60}"
STATE_DIR="${CLAUDE_RC_STATE_DIR:-${HOME}/.cache/claude-rc}"

SESSION_TARGET="$(claude_rc_session_target)"

session_alive() {
  claude_rc_tmux has-session -t "${SESSION_TARGET}" 2>/dev/null
}

# True when claude's pane is dead (remain-on-exit) or gone (closed while
# another window keeps the session). A failed tmux query counts as alive: it
# must not end a healthy server; a session that is really gone is caught by
# session_alive. Pass the pane to save the lookup.
pane_dead() {
  local pane="${1:-}" panes state
  [[ -n "${pane}" ]] || pane="$(claude_rc_pane_target)"
  if [[ "${pane}" == %* ]]; then
    panes="$(claude_rc_tmux list-panes -s -t "${SESSION_TARGET}" -F '#{pane_id} #{pane_dead}' 2>/dev/null)" \
      || return 1
    state="$(awk -v p="${pane}" '$1 == p { print $2 }' <<<"${panes}")"
    [[ -z "${state}" || "${state}" == "1" ]]
  else
    state="$(claude_rc_tmux display-message -p -t "${pane}" '#{pane_dead}' 2>/dev/null)" || return 1
    [[ "${state}" == "1" ]]
  fi
}

stop_session() {
  session_alive || return 0
  local pane
  pane="$(claude_rc_pane_target)"
  # Tell the main loop this exit is planned, so it does not report a crash.
  claude_rc_tmux set-option -t "${pane}" @claude_rc_stopping 1 2>/dev/null || true
  if ! pane_dead "${pane}"; then
    claude_rc_tmux send-keys -t "${pane}" C-c 2>/dev/null || true
    # Where claude asks for a second Ctrl+C, it must come within its short
    # confirmation window: keep watching the screen and answer as soon as it
    # asks, once, while waiting for the exit.
    local ticks=$((STOP_WAIT_SECONDS * 10)) answered=0 shown
    while ((ticks > 0)) && session_alive && ! pane_dead "${pane}"; do
      if ((answered == 0)); then
        shown="$(claude_rc_tmux capture-pane -p -t "${pane}" 2>/dev/null || true)"
        if grep -qi 'again to exit' <<<"${shown}"; then
          claude_rc_tmux send-keys -t "${pane}" C-c 2>/dev/null || true
          answered=1
        fi
      fi
      sleep 0.1
      ticks=$((ticks - 1))
    done
  fi
  claude_rc_tmux kill-session -t "${SESSION_TARGET}" 2>/dev/null || true
}

# Print the last screen unless it equals the one printed in the last 10
# minutes, so a claude that exits at once on every restart (an expired login)
# does not flood the journal with the same lines every 30 seconds. The screen
# is also kept in last-screen.txt, which claude-rc-status shows when no
# screen is running.
report_last_screen() {
  local status="$1" screen digest previous=""
  # tmux's own "Pane is dead (status, time)" line differs on every exit; drop
  # it and squeeze runs of blank rows before comparing.
  screen="$(claude_rc_tmux capture-pane -p -J -t "${pane_id:-$(claude_rc_pane_target)}" -S -40 2>/dev/null \
    | grep -v '^Pane is dead (' | cat -s | claude_rc_trim_blank_tail | tail -n 40 || true)"
  digest="$(printf '%s\n' "${status}" "${screen}" | sha256sum | cut -d' ' -f1)"
  mkdir -p "${STATE_DIR}" 2>/dev/null || true
  printf 'exit at %s with status %s\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${status}" "${screen}" \
    >"${STATE_DIR}/last-screen.txt" 2>/dev/null || true
  # An old digest (its dump may have rotated out of the journal) never mutes.
  find "${STATE_DIR}" -maxdepth 1 -name last-screen.sha256 -mmin "+${SCREEN_RESET_MINUTES}" \
    -delete 2>/dev/null || true
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

# systemd's SIGTERM (after ExecStop, KillMode=process) reaches only this
# script. ExecStop may have found no session yet if the stop came during
# start-up, so stop claude here too rather than leave it unmanaged.
trap 'stop_session; exit 0' TERM

args=(remote-control)
if [[ -n "${NAME}" ]]; then
  args+=(--name "${NAME}")
fi

# remain-on-exit keeps the last screen (for example a login prompt) readable
# after claude exits. It is set on the private server before the session
# exists, so even an instant exit keeps it. The login shell reads the
# operator's login profile (~/.bash_profile, ~/.bash_login or ~/.profile,
# as an SSH login does), runs claude and writes its exit status to a file:
# tmux 3.4 does not always report it (about half the time on a busy host).
# The shell's INT trap is a no-op handler, which exec resets, so Ctrl+C still
# reaches claude and the shell survives it long enough to write the status.
STATUS_FILE="${STATE_DIR}/exit-status"
mkdir -p "${STATE_DIR}" 2>/dev/null || true
rm -f "${STATUS_FILE}"
# shellcheck disable=SC2016  # these $ belong to the inner shell
pane_id="$(claude_rc_tmux start-server \; set-option -wg remain-on-exit on \; \
  new-session -d -P -F '#{pane_id}' -s "${CLAUDE_RC_SESSION}" -c "${REPO}" -x 200 -y 50 \
  -- env -u TMUX -u TMUX_PANE "CLAUDE_RC_STATUS_FILE=${STATUS_FILE}" bash -lc \
  'trap : INT; "$0" "$@"; rc=$?; printf "%s\n" "${rc}" >"${CLAUDE_RC_STATUS_FILE}"; exit "${rc}"' \
  "${CLAUDE_BIN}" "${args[@]}")"
claude_rc_tmux set-option -t "${pane_id}" @claude_rc_pane "${pane_id}" 2>/dev/null || true
echo "claude-rc: remote-control started in ${REPO} (env -u TMUX_TMPDIR tmux -L ${CLAUDE_RC_SOCKET} attach -t ${CLAUDE_RC_SESSION})"

while session_alive && ! pane_dead "${pane_id}"; do
  # Sleep in the background and wait, so a SIGTERM is handled at once.
  sleep "${POLL_SECONDS}" &
  wait "$!" || true
done

# Read the stop mark first: --stop sets it before its Ctrl+C, and may remove
# the session at any moment after claude exits.
stopping="$(claude_rc_tmux show-options -qv -t "=${CLAUDE_RC_SESSION}:" @claude_rc_stopping 2>/dev/null || true)"
if [[ -n "${stopping}" ]]; then
  echo "claude-rc: remote-control stopped on request" >&2
  claude_rc_tmux kill-session -t "${SESSION_TARGET}" 2>/dev/null || true
elif ! session_alive; then
  echo "claude-rc: the tmux session ended" >&2
else
  # The wrapper writes the status just before the pane dies; tmux's own
  # status is only a fallback. 128+N means claude died of signal N.
  status=""
  for _ in $(seq 1 20); do
    if [[ -s "${STATUS_FILE}" ]]; then
      status="$(head -n 1 "${STATUS_FILE}")"
      break
    fi
    sleep 0.1
  done
  if [[ -z "${status}" ]]; then
    status="$(claude_rc_tmux display-message -p -t "${pane_id}" \
      '#{?pane_dead_signal,signal #{pane_dead_signal},#{pane_dead_status}}' 2>/dev/null || true)"
  elif [[ "${status}" =~ ^[0-9]+$ ]] && ((status > 128 && status <= 128 + 64)); then
    # The shell reports a death by signal N as 128+N, the same as that exit
    # code; above 128+64 (SIGRTMAX) it can only be an exit code.
    status="${status} (signal $((status - 128)) or exit code ${status})"
  fi
  report_last_screen "${status:-unknown}"
  claude_rc_tmux kill-session -t "${SESSION_TARGET}" 2>/dev/null || true
fi
# Exit 0: systemd restarts the service either way (Restart=always), and a
# planned stop must not leave the unit marked failed.
exit 0
