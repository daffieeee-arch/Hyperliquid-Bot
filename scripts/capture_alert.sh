#!/usr/bin/env bash
# PAPER capture-failure alert CLI.
# Dry-run unless --send. Never prints the webhook URL or authorization value.
# Does not attach to tmux, send keys, or restart a collector.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
cd "${REPO_ROOT}"
exec uv run --frozen python -m hyperliquid_bot.capture_alert_cli "$@"
