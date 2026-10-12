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

# tmux on the private socket only.
claude_rc_tmux() {
  tmux -L "${CLAUDE_RC_SOCKET}" "$@"
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

# True when pid is a `claude remote-control` server: one argument is exactly
# remote-control and another names claude (the binary or the claude-code cli).
claude_rc_is_server_pid() {
  local args
  args="$(tr '\0' '\n' <"/proc/$1/cmdline" 2>/dev/null)" || return 1
  grep -qx 'remote-control' <<<"${args}" || return 1
  grep -qE '(^|/)claude$|claude-code' <<<"${args}"
}

# True when this shell descends from the service's remote-control server, i.e.
# runs in a Claude session the service serves. It follows parents, not the
# cgroup: a tmux server that such a session started also sits in the
# service's cgroup, but shells in it do not descend from the server.
claude_rc_inside_service() {
  local pid="$$"
  while [[ -n "${pid}" && "${pid}" -gt 1 ]]; do
    if claude_rc_is_server_pid "${pid}" \
      && grep -q "/${CLAUDE_RC_UNIT_NAME}" "/proc/${pid}/cgroup" 2>/dev/null; then
      return 0
    fi
    pid="$(awk '/^PPid:/ { print $2 }' "/proc/${pid}/status" 2>/dev/null)"
  done
  return 1
}
