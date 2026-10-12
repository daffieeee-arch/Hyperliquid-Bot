#!/usr/bin/env bash
# Install the always-on Claude Code Remote Control service on the VPS.
#
#   sudo bash scripts/claude_rc_install.sh            install or update, then (re)start
#   bash scripts/claude_rc_install.sh --print-unit    show the unit it would write, change nothing
#
# The service runs `claude remote-control` as the operator (the sudo caller),
# never as root, with that user's normal rights. Root owns the unit and the
# supervisor copy so a session cannot rewrite what restarts it. See
# docs/runbooks/claude-remote-control-vps.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="claude-rc.service"
UNIT_PATH="${CLAUDE_RC_UNIT_PATH:-/etc/systemd/system/${UNIT_NAME}}"
LIBEXEC_DIR="${CLAUDE_RC_LIBEXEC_DIR:-/usr/local/libexec/claude-rc}"
RUNNER="${LIBEXEC_DIR}/claude-rc-run"

PRINT_ONLY=0
if [[ "${1:-}" == "--print-unit" ]]; then
  PRINT_ONLY=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: sudo bash $0   |   bash $0 --print-unit" >&2
  exit 2
fi

OPERATOR="${CLAUDE_RC_USER:-${SUDO_USER:-$(id -un)}}"
if [[ -z "${OPERATOR}" || "${OPERATOR}" == "root" ]]; then
  echo "Refuse: run this with sudo from the operator account, not as root itself." >&2
  exit 1
fi
OPERATOR_HOME="$(getent passwd "${OPERATOR}" | cut -d: -f6)"
if [[ -z "${OPERATOR_HOME}" ]]; then
  echo "Refuse: unknown user ${OPERATOR}." >&2
  exit 1
fi
OPERATOR_GROUP="$(id -gn "${OPERATOR}")"

REPO="${CLAUDE_RC_REPO:-${OPERATOR_HOME}/Hyperliquid Project/Hyperliquid-Bot}"
NAME="${CLAUDE_RC_NAME:-hyperliquid-vps}"

find_claude() {
  local candidate
  for candidate in \
    "${OPERATOR_HOME}/.local/bin/claude" \
    "${OPERATOR_HOME}/.claude/local/claude" \
    /usr/local/bin/claude \
    /usr/bin/claude; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  # npm or nvm installs only show up on the operator's interactive PATH.
  if [[ "$(id -un)" == "${OPERATOR}" ]]; then
    command -v claude 2>/dev/null || true
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "${OPERATOR}" -i bash -ic 'command -v claude' 2>/dev/null | tail -n 1 || true
  fi
}

CLAUDE_BIN="${CLAUDE_RC_BIN:-$(find_claude)}"
if [[ -z "${CLAUDE_BIN}" ]]; then
  echo "Refuse: claude was not found for ${OPERATOR}. Install Claude Code, or set CLAUDE_RC_BIN." >&2
  exit 1
fi

# systemd expands % and $ and ends a quoted value at ", so such paths could
# start the wrong thing. Refuse them instead of escaping.
for value in "${REPO}" "${CLAUDE_BIN}" "${NAME}" "${OPERATOR_HOME}"; do
  if [[ "${value}" =~ [\"\\%\$\`] || "${value}" == *$'\n'* ]]; then
    echo "Refuse: unsupported character in: ${value}" >&2
    exit 1
  fi
done

render_unit() {
  cat <<EOF
# Installed by scripts/claude_rc_install.sh; see docs/runbooks/claude-remote-control-vps.md.
[Unit]
Description=Claude Code Remote Control (always on) for ${OPERATOR}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${OPERATOR}
Group=${OPERATOR_GROUP}
Environment="CLAUDE_RC_REPO=${REPO}"
Environment="CLAUDE_RC_BIN=${CLAUDE_BIN}"
Environment="CLAUDE_RC_NAME=${NAME}"
Environment="PATH=$(dirname "${CLAUDE_BIN}"):${OPERATOR_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment=TERM=xterm-256color
ExecStart=${RUNNER}
ExecStop=${RUNNER} --stop
Restart=always
RestartSec=30
TimeoutStopSec=40
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF
}

if [[ "${PRINT_ONLY}" -eq 1 ]]; then
  render_unit
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi
for tool in tmux systemctl awk; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "Refuse: ${tool} is not installed." >&2
    exit 1
  fi
done
if [[ ! -d "${REPO}" ]]; then
  echo "Refuse: repo checkout is missing: ${REPO}" >&2
  exit 1
fi
if [[ ! -x "${CLAUDE_BIN}" ]]; then
  echo "Refuse: not executable: ${CLAUDE_BIN}" >&2
  exit 1
fi

# A second remote-control server in the same folder would compete with the service.
if ! systemctl is-active --quiet "${UNIT_NAME}" \
  && pgrep -u "${OPERATOR}" -f "claude.* remote-control" >/dev/null 2>&1; then
  echo "Refuse: ${OPERATOR} already runs 'claude remote-control' by hand." >&2
  echo "Stop that one first (Ctrl+C in its tmux window), then run this again." >&2
  exit 1
fi

if ! sudo -u "${OPERATOR}" -H "${CLAUDE_BIN}" auth status >/dev/null 2>&1; then
  echo "Warning: ${OPERATOR} does not look logged in. Run 'claude auth login' as ${OPERATOR}." >&2
fi

install -d -m 0755 -o root -g root "${LIBEXEC_DIR}"
install -m 0755 -o root -g root "${SCRIPT_DIR}/claude_rc_run.sh" "${RUNNER}"
unit_tmp="$(mktemp)"
render_unit >"${unit_tmp}"
install -m 0644 -o root -g root "${unit_tmp}" "${UNIT_PATH}"
rm -f "${unit_tmp}"

systemctl daemon-reload
systemctl enable "${UNIT_NAME}" >/dev/null
systemctl restart "${UNIT_NAME}"

echo "Installed ${UNIT_NAME}: claude remote-control runs as ${OPERATOR} in ${REPO}."
echo "Session title in the Claude app: ${NAME}"
echo "Check it with: bash scripts/claude_rc_status.sh"
