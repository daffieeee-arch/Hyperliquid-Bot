#!/usr/bin/env bash
# Shared names for the claude_rc_* scripts. The installer copies this file
# next to the installed scripts, which source it from their own folder.

CLAUDE_RC_UNIT_NAME="claude-rc.service"
CLAUDE_RC_DEFAULT_SOCKET="claude-rc"
CLAUDE_RC_DEFAULT_SESSION="claude-rc"

# Print stdin without the blank rows tmux pads below the last line.
claude_rc_trim_blank_tail() {
  awk 'NF { last = NR } { row[NR] = $0 } END { for (i = 1; i <= last; i++) print row[i] }'
}

# True when the calling process runs inside claude-rc.service, i.e. from a
# Claude session that the service itself serves.
claude_rc_inside_service() {
  grep -q "/${CLAUDE_RC_UNIT_NAME}" "/proc/$$/cgroup" 2>/dev/null
}
