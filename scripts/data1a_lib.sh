# Shared DATA-1A operator-PC helpers. Sourced by data1a_*.sh.
# Public Hyperliquid capture only. Create-only. Never print secret values.

data1a_default_artifact_root() {
  printf '%s\n' "${HOME}/hyperliquid-artifacts/reconstructable"
}

data1a_default_repo_root() {
  printf '%s\n' "${HOME}/code/Hyperliquid-Bot-main"
}

data1a_default_tmux_session() {
  printf '%s\n' "hl-capture"
}

data1a_require_run_id() {
  local run_id="$1"
  if [[ ! "${run_id}" =~ ^[a-z0-9._-]{1,64}$ ]]; then
    echo "run_id must be 1-64 lowercase ASCII letters, digits, dot, dash, or underscore." >&2
    return 1
  fi
}

data1a_run_dir() {
  local artifact_root="$1"
  local run_id="$2"
  printf '%s\n' "${artifact_root}/data-1a/hyperliquid/BTC-PERP/${run_id}"
}

data1a_refuse_live_modes() {
  local mode="${TRADING_MODE-}"
  local d41="${D41_EXECUTION_MODE-}"
  if [[ -n "${mode}" && "${mode}" != "PAPER" ]]; then
    echo "Refuse to start: TRADING_MODE is set and is not PAPER. Value not treated as a secret, but LIVE/SHADOW/TESTNET are forbidden." >&2
    echo "TRADING_MODE=${mode}" >&2
    return 1
  fi
  if [[ -n "${d41}" && "${d41}" != "PAPER" ]]; then
    echo "Refuse to start: D41_EXECUTION_MODE is set and is not PAPER." >&2
    echo "D41_EXECUTION_MODE=${d41}" >&2
    return 1
  fi
}

data1a_refuse_protected_keys() {
  local name
  for name in \
    HYPERLIQUID_PK \
    HYPERLIQUID_TESTNET_PK \
    HYPERLIQUID_VAULT \
    HYPERLIQUID_TESTNET_VAULT \
    HYPERLIQUID_ACCOUNT_ADDRESS
  do
    if [[ -n "${!name:-}" ]]; then
      echo "Refuse to start: protected environment name ${name} is set." >&2
      echo "Value not printed." >&2
      return 1
    fi
  done
}

data1a_require_duration() {
  local duration="$1"
  if [[ ! "${duration}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "DURATION_SECONDS must be a number between 1 and 604800." >&2
    return 1
  fi
  if ! awk -v d="${duration}" 'BEGIN { exit !(d >= 1 && d <= 604800) }'; then
    echo "DURATION_SECONDS must be between 1 and 604800." >&2
    return 1
  fi
}

data1a_tmux_alive() {
  local session="$1"
  if ! command -v tmux >/dev/null 2>&1; then
    printf '%s\n' "tmux-missing"
    return 0
  fi
  if tmux has-session -t "${session}" 2>/dev/null; then
    printf '%s\n' "yes"
  else
    printf '%s\n' "no"
  fi
}

data1a_list_run_ids() {
  local artifact_root="$1"
  local parent="${artifact_root}/data-1a/hyperliquid/BTC-PERP"
  if [[ ! -d "${parent}" ]]; then
    return 0
  fi
  find "${parent}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort
}

data1a_parquet_part_count() {
  local raw_dir="$1"
  if [[ ! -d "${raw_dir}" ]]; then
    printf '%s\n' "0"
    return 0
  fi
  find "${raw_dir}" -maxdepth 1 -type f -name 'part-*.parquet' | wc -l | tr -d ' '
}
