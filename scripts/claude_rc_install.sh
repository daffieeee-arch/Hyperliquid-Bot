#!/usr/bin/env bash
# Install the always-on Claude Code Remote Control service on the VPS.
#
#   sudo bash <worktree>/scripts/claude_rc_install.sh            install or update, then (re)start
#   bash <worktree>/scripts/claude_rc_install.sh --print-unit    show the unit, change nothing
#
# The service runs `claude remote-control` as the operator (the sudo caller),
# never as root, with that user's normal rights. Root owns the unit and the
# installed scripts. Run this from an SSH terminal, from a fresh worktree of
# origin/main (docs/runbooks/claude-remote-control-vps.md), not from a Claude
# session the service serves.

set -euo pipefail

# Everything runs from main(), so bash has read the whole file before root
# executes any of it; an edit to the file while it runs changes nothing.
main() {
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT="$(dirname "${SCRIPT_DIR}")"

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
OPERATOR_HOME="$(getent passwd "${OPERATOR}" | cut -d: -f6 || true)"
if [[ -z "${OPERATOR_HOME}" ]]; then
  echo "Refuse: unknown user ${OPERATOR}." >&2
  exit 1
fi
OPERATOR_GROUP="$(id -gn "${OPERATOR}")"

as_operator() {
  if [[ "$(id -un)" == "${OPERATOR}" ]]; then
    "$@"
  else
    sudo -u "${OPERATOR}" -H "$@"
  fi
}

# Root installs these scripts and runs this one. The checkout is writable by
# every Claude session, so before root uses anything else from it (even the
# shared lib), copy the scripts to a root-only folder and keep going only if
# each copy matches origin/main byte for byte. That stops local edits and a
# swap between check and install. It cannot vouch for this file itself or
# for a stolen operator account, which can move the local origin/main; the
# fresh worktree in the runbook is what the operator relies on for that.
if [[ "${PRINT_ONLY}" -eq 1 ]]; then
  LIB_DIR="${SCRIPT_DIR}"
else
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0" >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "Refuse: git is not installed." >&2
    exit 1
  fi
  if ! as_operator git -C "${CHECKOUT}" rev-parse --verify --quiet origin/main >/dev/null; then
    echo "Refuse: ${CHECKOUT} has no origin/main to compare with. Run 'git fetch origin main' first." >&2
    exit 1
  fi
  STAGING="$(mktemp -d)"
  trap 'rm -rf "${STAGING}"' EXIT
  for source_file in "${SCRIPT_DIR}"/claude_rc_*.sh; do
    name="$(basename "${source_file}")"
    if [[ -L "${source_file}" || ! -f "${source_file}" ]]; then
      echo "Refuse: ${source_file} is missing or not a regular file." >&2
      exit 1
    fi
    cp -- "${source_file}" "${STAGING}/${name}"
    if ! as_operator git -C "${CHECKOUT}" show "origin/main:scripts/${name}" 2>/dev/null \
      | cmp -s - "${STAGING}/${name}"; then
      echo "Refuse: scripts/${name} in ${CHECKOUT} differs from origin/main." >&2
      echo "Install from a fresh worktree of origin/main, as docs/runbooks/claude-remote-control-vps.md shows." >&2
      exit 1
    fi
  done
  # The copies are verified and root-owned; let the operator read them too.
  chmod 0755 "${STAGING}"
  chmod 0644 "${STAGING}"/*
  LIB_DIR="${STAGING}"
fi
# shellcheck source=claude_rc_lib.sh
source "${LIB_DIR}/claude_rc_lib.sh"

UNIT_PATH="${CLAUDE_RC_UNIT_PATH:-${CLAUDE_RC_DEFAULT_UNIT_PATH}}"
LIBEXEC_DIR="${CLAUDE_RC_LIBEXEC_DIR:-${CLAUDE_RC_DEFAULT_LIBEXEC_DIR}}"
STATUS_LINK="${CLAUDE_RC_STATUS_LINK:-${CLAUDE_RC_DEFAULT_STATUS_LINK}}"
RUNNER="${LIBEXEC_DIR}/claude-rc-run"
REPO="${CLAUDE_RC_REPO:-${OPERATOR_HOME}/Hyperliquid Project/Hyperliquid-Bot}"
NAME="${CLAUDE_RC_NAME:-hyperliquid-vps}"

usable_claude() {
  [[ "$1" == /* && -x "$1" && ! -d "$1" ]]
}

find_claude() {
  local candidate
  for candidate in \
    "${OPERATOR_HOME}/.local/bin/claude" \
    "${OPERATOR_HOME}/.claude/local/claude" \
    /usr/local/bin/claude \
    /usr/bin/claude; do
    if usable_claude "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  # npm or nvm installs only show up on the operator's interactive PATH. The
  # shell gets no terminal (setsid, stdin /dev/null) and 20 s, so a .bashrc
  # that attaches tmux or prompts cannot hang the installer, and it may print
  # other lines, so keep only an executable absolute path.
  as_operator timeout 20 setsid bash -ic 'command -v claude' </dev/null 2>/dev/null \
    | while IFS= read -r candidate; do
      if usable_claude "${candidate}"; then
        printf '%s\n' "${candidate}"
        break
      fi
    done || true
}

CLAUDE_BIN="${CLAUDE_RC_BIN:-$(find_claude)}"
if [[ -z "${CLAUDE_BIN}" ]] || ! usable_claude "${CLAUDE_BIN}"; then
  echo "Refuse: no executable claude found for ${OPERATOR} (${CLAUDE_BIN:-none})." >&2
  echo "Install Claude Code for ${OPERATOR}, or set CLAUDE_RC_BIN to its absolute path." >&2
  exit 1
fi
CLAUDE_DIR="$(dirname "${CLAUDE_BIN}")"

# systemd expands % and $ and ends a quoted value at ", so such values could
# start the wrong thing. Refuse them instead of escaping.
for value in "${REPO}" "${CLAUDE_BIN}" "${NAME}" "${OPERATOR_HOME}" "${LIBEXEC_DIR}"; do
  if [[ "${value}" =~ [\"\\%\$\`] || "${value}" == *$'\n'* ]]; then
    echo "Refuse: unsupported character in: ${value}" >&2
    exit 1
  fi
done
# ExecStart is not quoted, and PATH entries are split at ':'.
if [[ "${LIBEXEC_DIR}" =~ [[:space:]] ]]; then
  echo "Refuse: the libexec directory may not contain spaces: ${LIBEXEC_DIR}" >&2
  exit 1
fi
if [[ "${CLAUDE_DIR}" == *:* || "${OPERATOR_HOME}" == *:* ]]; then
  echo "Refuse: a ':' in ${CLAUDE_DIR} or ${OPERATOR_HOME} would break PATH." >&2
  exit 1
fi

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
Environment="PATH=${CLAUDE_DIR}:${OPERATOR_HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment=TERM=xterm-256color
ExecStart=${RUNNER}
ExecStop=${RUNNER} --stop
Restart=always
RestartSec=30
TimeoutStopSec=40
# Only the supervisor is signalled on stop. Processes that Claude sessions
# start (capture or research tmux sessions, downloads) live in this cgroup
# too and must outlive a restart; ExecStop ends claude itself with Ctrl+C.
KillMode=process

[Install]
WantedBy=multi-user.target
EOF
}

if [[ "${PRINT_ONLY}" -eq 1 ]]; then
  render_unit
  exit 0
fi

if claude_rc_inside_service; then
  echo "Refuse: this runs in a session of ${CLAUDE_RC_UNIT_NAME}; the restart would end that session." >&2
  echo "Run it from an SSH terminal instead." >&2
  exit 1
fi
for tool in tmux systemctl awk sha256sum; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "Refuse: ${tool} is not installed." >&2
    exit 1
  fi
done
if [[ ! -d "${REPO}" ]]; then
  echo "Refuse: repo checkout is missing: ${REPO}" >&2
  exit 1
fi

# A second remote-control server outside the service would compete with it.
# The service's own server is the pane process of its private tmux session;
# it and anything it started are not competitors.
# shellcheck disable=SC2016  # expanded by the operator's shell
service_pid="$(as_operator bash -c \
  'source "$1"; claude_rc_tmux display-message -p -t "$(claude_rc_pane_target)" "#{pane_pid}"' \
  bash "${LIB_DIR}/claude_rc_lib.sh" 2>/dev/null || true)"
for pid in $(pgrep -u "${OPERATOR}" -f remote-control 2>/dev/null || true); do
  if [[ -n "${service_pid}" ]] && claude_rc_descends_from "${pid}" "${service_pid}"; then
    continue
  fi
  if claude_rc_is_server_pid "${pid}"; then
    echo "Refuse: ${OPERATOR} runs 'claude remote-control' outside the service (pid ${pid})." >&2
    echo "Stop that one first (Ctrl+C in its window), then run this again." >&2
    exit 1
  fi
done

if ! as_operator "${CLAUDE_BIN}" auth status >/dev/null 2>&1; then
  echo "Warning: ${OPERATOR} does not look logged in. Run 'claude auth login' as ${OPERATOR}." >&2
fi

# Every file the lib lists must be staged before anything is replaced.
for entry in "${CLAUDE_RC_INSTALLED_FILES[@]}"; do
  if [[ ! -f "${STAGING}/${entry%%:*}" ]]; then
    echo "Refuse: scripts/${entry%%:*} is missing from ${CHECKOUT}." >&2
    exit 1
  fi
done

# The service and the helpers run these root-owned copies, never the checkout.
install -d -m 0755 -o root -g root "${LIBEXEC_DIR}"
for entry in "${CLAUDE_RC_INSTALLED_FILES[@]}"; do
  source_name="${entry%%:*}"
  installed_name="${entry#*:}"
  mode=0755
  [[ "${installed_name}" == *.sh ]] && mode=0644
  install -m "${mode}" -o root -g root "${STAGING}/${source_name}" "${LIBEXEC_DIR}/${installed_name}"
done
ln -sfn "${LIBEXEC_DIR}/claude-rc-status" "${STATUS_LINK}"
render_unit >"${STAGING}/${CLAUDE_RC_UNIT_NAME}"
install -m 0644 -o root -g root "${STAGING}/${CLAUDE_RC_UNIT_NAME}" "${UNIT_PATH}"

systemctl daemon-reload
systemctl enable "${CLAUDE_RC_UNIT_NAME}" >/dev/null
systemctl restart "${CLAUDE_RC_UNIT_NAME}"

echo "Installed ${CLAUDE_RC_UNIT_NAME}: claude remote-control runs as ${OPERATOR} in ${REPO}."
echo "Session title in the Claude app: ${NAME}"
echo "Check it with: claude-rc-status"
}

main "$@"
exit
