#!/usr/bin/env bash
# Stop and remove the always-on Claude Code Remote Control service.
#   sudo /usr/local/libexec/claude-rc/claude-rc-uninstall
# The stop sends Ctrl+C first, so its sessions stay resumable for about four
# hours with a manual `claude remote-control` in the same folder.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=claude_rc_lib.sh
source "${SCRIPT_DIR}/claude_rc_lib.sh"

UNIT_PATH="${CLAUDE_RC_UNIT_PATH:-${CLAUDE_RC_DEFAULT_UNIT_PATH}}"
LIBEXEC_DIR="${CLAUDE_RC_LIBEXEC_DIR:-${CLAUDE_RC_DEFAULT_LIBEXEC_DIR}}"
STATUS_LINK="${CLAUDE_RC_STATUS_LINK:-${CLAUDE_RC_DEFAULT_STATUS_LINK}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi
if claude_rc_inside_service; then
  echo "Refuse: this runs in a session of ${CLAUDE_RC_UNIT_NAME}; stopping it would end this session first." >&2
  echo "Run it from an SSH terminal instead." >&2
  exit 1
fi

if ! systemctl disable --now "${CLAUDE_RC_UNIT_NAME}"; then
  if systemctl is-active --quiet "${CLAUDE_RC_UNIT_NAME}"; then
    echo "Refuse: ${CLAUDE_RC_UNIT_NAME} is still running; nothing was removed." >&2
    exit 1
  fi
fi
rm -f "${UNIT_PATH}"
# Remove the status link only if it is the one the installer made.
if [[ -L "${STATUS_LINK}" && "$(readlink "${STATUS_LINK}")" == "${LIBEXEC_DIR}/"* ]]; then
  rm -f "${STATUS_LINK}"
fi
for entry in "${CLAUDE_RC_INSTALLED_FILES[@]}"; do
  rm -f "${LIBEXEC_DIR}/${entry#*:}"
done
rmdir "${LIBEXEC_DIR}" 2>/dev/null || true
systemctl daemon-reload
echo "Removed ${CLAUDE_RC_UNIT_NAME}."
