#!/usr/bin/env bash
# Stop and remove the always-on Claude Code Remote Control service.
#   sudo bash scripts/claude_rc_uninstall.sh
# The stop sends Ctrl+C first, so its sessions stay resumable for about four
# hours with a manual `claude remote-control` in the same folder.

set -euo pipefail

UNIT_NAME="claude-rc.service"
UNIT_PATH="${CLAUDE_RC_UNIT_PATH:-/etc/systemd/system/${UNIT_NAME}}"
LIBEXEC_DIR="${CLAUDE_RC_LIBEXEC_DIR:-/usr/local/libexec/claude-rc}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

systemctl disable --now "${UNIT_NAME}" 2>/dev/null || true
rm -f "${UNIT_PATH}" "${LIBEXEC_DIR}/claude-rc-run"
rmdir "${LIBEXEC_DIR}" 2>/dev/null || true
systemctl daemon-reload
echo "Removed ${UNIT_NAME}."
