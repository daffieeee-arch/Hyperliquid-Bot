# Shared DATA-1B operator-PC helpers. Sourced by data1b_*.sh.
# Public Kraken L2+trades by default. Optional L3 via KRAKEN_WS_* only.
# Create-only. Never print secret values.
# Never attach to, resume, or stop hl-capture, bn-capture, or bv-capture.

data1b_default_artifact_root() {
  printf '%s\n' "${HOME}/hyperliquid-artifacts/reconstructable"
}

data1b_default_repo_root() {
  printf '%s\n' "${HOME}/code/Hyperliquid-Bot-main"
}

data1b_default_tmux_session() {
  printf '%s\n' "kr-capture"
}

data1b_protected_hl_session() {
  printf '%s\n' "hl-capture"
}

data1b_protected_bn_session() {
  printf '%s\n' "bn-capture"
}

data1b_protected_bv_session() {
  printf '%s\n' "bv-capture"
}

data1b_require_run_id() {
  local run_id="$1"
  if [[ ! "${run_id}" =~ ^[a-z0-9._-]{1,64}$ ]]; then
    echo "run_id must be 1-64 lowercase ASCII letters, digits, dot, dash, or underscore." >&2
    return 1
  fi
}

data1b_run_dir() {
  local artifact_root="$1"
  local run_id="$2"
  printf '%s\n' "${artifact_root}/data-1b/kraken/BTC-EUR/${run_id}"
}

data1b_refuse_live_modes() {
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

data1b_refuse_protected_keys() {
  local name
  for name in \
    HYPERLIQUID_PK \
    HYPERLIQUID_TESTNET_PK \
    HYPERLIQUID_VAULT \
    HYPERLIQUID_TESTNET_VAULT \
    HYPERLIQUID_ACCOUNT_ADDRESS \
    BINANCE_API_KEY \
    BINANCE_API_SECRET \
    BINANCE_SECRET \
    BINANCE_API_KEY_TESTNET \
    BINANCE_TESTNET_API_SECRET \
    BITVAVO_API_KEY \
    BITVAVO_API_SECRET \
    BITVAVO_ACCESS_KEY \
    BITVAVO_SECRET \
    BITVAVO_SIGNING_KEY \
    KRAKEN_API_KEY \
    KRAKEN_API_SECRET \
    KRAKEN_PRIVATE_KEY \
    OKX_API_KEY \
    OKX_SECRET_KEY \
    OKX_PASSPHRASE
  do
    if [[ -n "${!name:-}" ]]; then
      echo "Refuse to start: protected environment name ${name} is set." >&2
      echo "Value not printed." >&2
      return 1
    fi
  done
}

data1b_l3_keys_present() {
  if [[ -n "${KRAKEN_WS_API_KEY:-}" && -n "${KRAKEN_WS_API_SECRET:-}" ]]; then
    printf '%s\n' "yes"
  elif [[ -n "${KRAKEN_WS_API_KEY:-}" || -n "${KRAKEN_WS_API_SECRET:-}" ]]; then
    echo "Refuse: optional L3 requires both KRAKEN_WS_API_KEY and KRAKEN_WS_API_SECRET. Values not printed." >&2
    return 1
  else
    printf '%s\n' "no"
  fi
}

data1b_refuse_protected_tmux_session() {
  local session="$1"
  if [[ "${session}" == "$(data1b_protected_hl_session)" ]]; then
    echo "Refuse: DATA-1B must not use tmux session hl-capture. That session is the live DATA-1A retain. Use kr-capture." >&2
    return 1
  fi
  if [[ "${session}" == "$(data1b_protected_bn_session)" ]]; then
    echo "Refuse: DATA-1B must not use tmux session bn-capture. That session is the DATA-1F retain. Use kr-capture." >&2
    return 1
  fi
  if [[ "${session}" == "$(data1b_protected_bv_session)" ]]; then
    echo "Refuse: DATA-1B must not use tmux session bv-capture. That session is the DATA-1E retain. Use kr-capture." >&2
    return 1
  fi
}

data1b_require_duration() {
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

data1b_tmux_alive() {
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

data1b_list_run_ids() {
  local artifact_root="$1"
  local parent="${artifact_root}/data-1b/kraken/BTC-EUR"
  if [[ ! -d "${parent}" ]]; then
    return 0
  fi
  find "${parent}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort
}

data1b_parquet_part_count() {
  local raw_dir="$1"
  if [[ ! -d "${raw_dir}" ]]; then
    printf '%s\n' "0"
    return 0
  fi
  find "${raw_dir}" -maxdepth 1 -type f -name 'part-*.parquet' | wc -l | tr -d ' '
}
