#!/usr/bin/env bash
# PAPER capture-failure alert CLI.
# Dry-run unless --send. Never prints the webhook URL or authorization value.
# Does not attach to tmux, send keys, or restart a collector.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
cd "${REPO_ROOT}"
# pyproject.toml sets package = false, so uv does not put src on sys.path.
# Relative to the repo root after cd, so a checkout path with spaces still works.
export PYTHONPATH=src
set +e
uv run --frozen python -m hyperliquid_bot.capture_alert_cli "$@"
status=$?
set -e
exit "${status}"
