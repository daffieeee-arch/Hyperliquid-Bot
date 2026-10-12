#!/usr/bin/env bash
# Stop and remove the always-on Claude Code Remote Control service.
#   sudo /usr/local/libexec/claude-rc/claude-rc-uninstall
# The stop sends Ctrl+C first, so its sessions stay resumable for about four
# hours with a manual `claude remote-control` in the same folder.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=claude_rc_lib.sh
source "${SCRIPT_DIR}/claude_rc_lib.sh"

UNIT_PATH="${CLAUDE_RC_UNIT_PATH:-/etc/systemd/system/${CLAUDE_RC_UNIT_NAME}}"
LIBEXEC_DIR="${CLAUDE_RC_LIBEXEC_DIR:-/usr/local/libexec/claude-rc}"
STATUS_LINK="${CLAUDE_RC_STATUS_LINK:-/usr/local/bin/claude-rc-status}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

systemctl disable --now "${CLAUDE_RC_UNIT_NAME}" 2>/dev/null || true
rm -f "${UNIT_PATH}" "${STATUS_LINK}" \
  "${LIBEXEC_DIR}/claude-rc-run" "${LIBEXEC_DIR}/claude-rc-status" \
  "${LIBEXEC_DIR}/claude-rc-uninstall" "${LIBEXEC_DIR}/claude_rc_lib.sh"
rmdir "${LIBEXEC_DIR}" 2>/dev/null || true
systemctl daemon-reload
echo "Removed ${CLAUDE_RC_UNIT_NAME}."
