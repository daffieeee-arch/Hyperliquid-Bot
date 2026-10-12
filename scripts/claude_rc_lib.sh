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

CLAUDE_RC_SOCKET="${CLAUDE_RC_TMUX_SOCKET:-${CLAUDE_RC_DEFAULT_SOCKET}}"
CLAUDE_RC_SESSION="${CLAUDE_RC_TMUX_SESSION:-${CLAUDE_RC_DEFAULT_SESSION}}"

# tmux on the private socket only. TMUX_TMPDIR is dropped so the service,
# the operator's SSH shells and a login shell that sets it in ~/.profile all
# find the socket in the same place (/tmp/tmux-<uid>).
claude_rc_tmux() {
  env -u TMUX_TMPDIR tmux -L "${CLAUDE_RC_SOCKET}" "$@"
}

# Exact-name targets: a bare name also matches a session that merely starts
# with it. "=name" targets the session, "=name:" its active pane.
claude_rc_session_target() {
  printf '=%s' "${CLAUDE_RC_SESSION}"
}

claude_rc_pane_target() {
  printf '=%s:' "${CLAUDE_RC_SESSION}"
}

# Print stdin without the blank rows tmux pads below the last line.
claude_rc_trim_blank_tail() {
  awk 'NF { last = NR } { row[NR] = $0 } END { for (i = 1; i <= last; i++) print row[i] }'
}

# True when pid is a `claude remote-control` server: its program is claude
# (the binary, or an interpreter running a claude script or the claude-code
# cli) and a later argument is exactly remote-control. `rg remote-control
# .../claude-code` does not match: its program is rg. Best effort: an npm
# install sets its process title and hides its arguments.
claude_rc_is_server_pid() {
  local -a argv
  mapfile -t argv < <(tr '\0' '\n' <"/proc/$1/cmdline" 2>/dev/null) || return 1
  ((${#argv[@]} >= 2)) || return 1
  local first=1
  case "${argv[0]##*/}" in
    claude) ;;
    node | nodejs | bun | bash | sh)
      [[ "${argv[1]##*/}" == "claude" || "${argv[1]}" == *claude-code* ]] || return 1
      first=2
      ;;
    *) return 1 ;;
  esac
  local arg
  for arg in "${argv[@]:first}"; do
    [[ "${arg}" == "remote-control" ]] && return 0
  done
  return 1
}

claude_rc_parent_pid() {
  awk '/^PPid:/ { print $2 }' "/proc/$1/status" 2>/dev/null
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
  [[ "$(cat "/proc/$1/comm" 2>/dev/null)" == tmux* ]] || return 1
  local -a argv
  mapfile -t argv < <(tr '\0' '\n' <"/proc/$1/cmdline" 2>/dev/null) || return 1
  local i
  for ((i = 0; i + 1 < ${#argv[@]}; i++)); do
    [[ "${argv[i]}" == "-L" && "${argv[i + 1]}" == "${CLAUDE_RC_SOCKET}" ]] && return 0
  done
  return 1
}

# True when this shell runs in a Claude session the service serves, or in
# another window of the service's private tmux server (a restart ends both).
# Outside the service's cgroup it never does. Inside it, the first tmux
# server among the parents decides: the private one means inside; any other
# is a server that a session started, holding operator windows. Reaching no
# tmux server at all (a detached session process) counts as inside. This
# does not read claude's own command line, which an npm install hides.
claude_rc_inside_service() {
  grep -q "/${CLAUDE_RC_UNIT_NAME}" "/proc/$$/cgroup" 2>/dev/null || return 1
  local pid="$$"
  while [[ -n "${pid}" && "${pid}" -gt 1 ]]; do
    if [[ "$(cat "/proc/${pid}/comm" 2>/dev/null)" == tmux* ]]; then
      claude_rc_is_private_tmux_server_pid "${pid}"
      return
    fi
    pid="$(claude_rc_parent_pid "${pid}")"
  done
  return 0
}
