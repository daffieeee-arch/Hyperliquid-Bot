#!/usr/bin/env bash
# Shared names and helpers for the claude_rc_* scripts. The installer copies
# this file next to the installed scripts, which source it from their folder.

CLAUDE_RC_UNIT_NAME="claude-rc.service"
CLAUDE_RC_DEFAULT_UNIT_PATH="/etc/systemd/system/${CLAUDE_RC_UNIT_NAME}"
CLAUDE_RC_DEFAULT_LIBEXEC_DIR="/usr/local/libexec/claude-rc"
CLAUDE_RC_DEFAULT_STATUS_LINK="/usr/local/bin/claude-rc-status"
CLAUDE_RC_DEFAULT_SOCKET="claude-rc"
CLAUDE_RC_DEFAULT_SESSION="claude-rc"

# Repository script -> installed name in the libexec folder.
CLAUDE_RC_INSTALLED_FILES=(
  "claude_rc_run.sh:claude-rc-run"
  "claude_rc_lib.sh:claude_rc_lib.sh"
  "claude_rc_status.sh:claude-rc-status"
  "claude_rc_uninstall.sh:claude-rc-uninstall"
)

# Tests point this at a fake /proc tree.
CLAUDE_RC_PROC="${CLAUDE_RC_PROC_ROOT:-/proc}"

CLAUDE_RC_SOCKET="${CLAUDE_RC_TMUX_SOCKET:-${CLAUDE_RC_DEFAULT_SOCKET}}"
CLAUDE_RC_SESSION="${CLAUDE_RC_TMUX_SESSION:-${CLAUDE_RC_DEFAULT_SESSION}}"

# tmux on the private socket only. TMUX_TMPDIR is dropped so the service,
# the operator's SSH shells and a login shell that sets it in ~/.profile all
# find the socket in the same place (/tmp/tmux-<uid>). -f /dev/null keeps the
# operator's ~/.tmux.conf (hooks, destroy-unattached, base-index, a custom
# dead-pane line) away from the server the supervisor relies on.
# CLAUDE_RC_RUN_AS (status under sudo) asks as the service user, whose
# socket it is.
claude_rc_tmux() {
  local -a run_as=()
  if [[ -n "${CLAUDE_RC_RUN_AS:-}" ]]; then
    run_as=(sudo -u "${CLAUDE_RC_RUN_AS}")
  fi
  "${run_as[@]}" env -u TMUX_TMPDIR tmux -f /dev/null -L "${CLAUDE_RC_SOCKET}" "$@"
}

# Exact-name session target: a bare name also matches a session that merely
# starts with it.
claude_rc_session_target() {
  printf '=%s' "${CLAUDE_RC_SESSION}"
}

# claude's own pane. The supervisor stores its id in the session option
# @claude_rc_pane, so a second window the operator opens there never becomes
# the target. Before that (or for an older session) it is the active pane.
claude_rc_pane_target() {
  local pane
  pane="$(claude_rc_tmux show-options -qv -t "=${CLAUDE_RC_SESSION}:" @claude_rc_pane 2>/dev/null || true)"
  printf '%s' "${pane:-=${CLAUDE_RC_SESSION}:}"
}

# Same rules as data1_in_test_or_ci_env (scripts/data1_operator_isolation.sh).
claude_rc_in_test_or_ci() {
  [[ -n "${PYTEST_CURRENT_TEST:-}" ]] && return 0
  case "${CI:-}" in
    true | 1 | yes) return 0 ;;
  esac
  [[ "${HYPERLIQUID_BOT_TEST_ISOLATION:-}" == "1" ]]
}

# Tests and CI run as the same user as the live service on the VPS, so they
# must never reach its socket: refuse unless an isolated socket is set.
claude_rc_refuse_live_socket_under_test() {
  if claude_rc_in_test_or_ci && [[ "${CLAUDE_RC_SOCKET}" == "${CLAUDE_RC_DEFAULT_SOCKET}" ]]; then
    echo "Refuse: test/CI isolation forbids the live ${CLAUDE_RC_DEFAULT_SOCKET} tmux socket." >&2
    echo "Set CLAUDE_RC_TMUX_SOCKET to an isolated name such as pytest-claude-rc-<id>." >&2
    echo "status=TEST_ISOLATION_NOOP" >&2
    return 1
  fi
}

# Print stdin without the blank rows tmux pads below the last line.
claude_rc_trim_blank_tail() {
  awk 'NF { last = NR } { row[NR] = $0 } END { for (i = 1; i <= last; i++) print row[i] }'
}

# True when pid is a `claude remote-control` server: its program is claude,
# or an interpreter (node, bun, a shell) running a claude script or the
# claude-code cli, and a later argument is exactly
# remote-control. `rg remote-control .../claude-code` does not match: its
# program is rg. Best effort: an npm install sets its process title and
# hides its arguments.
claude_rc_is_server_pid() {
  local -a argv
  mapfile -t argv < <(tr '\0' '\n' <"${CLAUDE_RC_PROC}/$1/cmdline" 2>/dev/null) || return 1
  ((${#argv[@]} >= 2)) || return 1
  local i=1
  case "${argv[0]##*/}" in
    claude) ;;
    node | nodejs | bun | bash | sh)
      # The script may follow interpreter flags, some with separate values.
      while ((i < ${#argv[@]})); do
        [[ "${argv[i]}" != -* && ("${argv[i]##*/}" == "claude" || "${argv[i]}" == *claude-code*) ]] \
          && break
        i=$((i + 1))
      done
      ((i < ${#argv[@]})) || return 1
      i=$((i + 1))
      ;;
    *) return 1 ;;
  esac
  local arg
  for arg in "${argv[@]:i}"; do
    [[ "${arg}" == "remote-control" ]] && return 0
  done
  return 1
}

claude_rc_parent_pid() {
  awk '/^PPid:/ { print $2 }' "${CLAUDE_RC_PROC}/$1/status" 2>/dev/null
}

# True when pid descends from ancestor (or is it).
claude_rc_descends_from() {
  local pid="$1" ancestor="$2"
  while [[ -n "${pid}" && "${pid}" -gt 1 ]]; do
    [[ "${pid}" == "${ancestor}" ]] && return 0
    pid="$(claude_rc_parent_pid "${pid}")"
  done
  return 1
}

# True when pid is the service's private tmux server. The server keeps the
# command line it was started with (`tmux -L claude-rc start-server ...`).
claude_rc_is_private_tmux_server_pid() {
  [[ "$(cat "${CLAUDE_RC_PROC}/$1/comm" 2>/dev/null)" == tmux* ]] || return 1
  local -a argv
  mapfile -t argv < <(tr '\0' '\n' <"${CLAUDE_RC_PROC}/$1/cmdline" 2>/dev/null) || return 1
  local i
  for ((i = 0; i + 1 < ${#argv[@]}; i++)); do
    [[ "${argv[i]}" == "-L" && "${argv[i + 1]}" == "${CLAUDE_RC_SOCKET}" ]] && return 0
  done
  return 1
}

# True when this shell runs in a Claude session the service serves, or in
# another window of the service's private tmux server (a restart ends both).
# The first tmux server among the parents decides: the private one means
# inside, any other is a server that a session started, holding operator
# windows. Following parents survives `sudo -i` or `su -`, which move the
# shell out of the service's cgroup. With no tmux server among the parents, a
# process still in the service's cgroup is a detached session process, so
# inside; anything else (an SSH shell) is outside. This never reads claude's
# own command line, which an npm install hides.
# The optional argument is the pid to check (default: this shell).
claude_rc_inside_service() {
  local start="${1:-$$}" pid
  pid="${start}"
  while [[ -n "${pid}" && "${pid}" -gt 1 ]]; do
    if [[ "$(cat "${CLAUDE_RC_PROC}/${pid}/comm" 2>/dev/null)" == tmux* ]]; then
      claude_rc_is_private_tmux_server_pid "${pid}"
      return
    fi
    pid="$(claude_rc_parent_pid "${pid}")"
  done
  grep -q "/${CLAUDE_RC_UNIT_NAME}" "${CLAUDE_RC_PROC}/${start}/cgroup" 2>/dev/null
}
