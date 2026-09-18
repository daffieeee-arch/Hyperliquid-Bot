# Shared DATA-1D VPS-first operator helpers. Sourced by data1d_*.sh.
# Credential-free Bitvavo Standard BTC-EUR capture only. Create-only.
# Never print secret values.
# Never attach to, resume, or stop hl-capture, bn-capture, bv-capture, or kr-capture.
# Default tmux session is bv-std-capture (must not collide with Pro bv-capture).

_DATA1D_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=data1_operator_isolation.sh
source "${_DATA1D_LIB_DIR}/data1_operator_isolation.sh"
# shellcheck source=data1_bandwidth_lib.sh
source "${_DATA1D_LIB_DIR}/data1_bandwidth_lib.sh"
# shellcheck source=data1_phase_a_lib.sh
source "${_DATA1D_LIB_DIR}/data1_phase_a_lib.sh"

data1d_default_artifact_root() {
  printf '%s\n' "${HOME}/Hyperliquid Project/data-capture"
}

data1d_default_repo_root() {
  printf '%s\n' "${HOME}/Hyperliquid Project/Hyperliquid-Bot"
}

data1d_default_tmux_session() {
  printf '%s\n' "bv-std-capture"
}

data1d_protected_hl_session() {
  printf '%s\n' "hl-capture"
}

data1d_protected_bn_session() {
  printf '%s\n' "bn-capture"
}

data1d_protected_bv_session() {
  printf '%s\n' "bv-capture"
}

data1d_protected_kr_session() {
  printf '%s\n' "kr-capture"
}

data1d_require_run_id() {
  local run_id="$1"
  if [[ ! "${run_id}" =~ ^[a-z0-9._-]{1,64}$ ]]; then
    echo "run_id must be 1-64 lowercase ASCII letters, digits, dot, dash, or underscore." >&2
    return 1
  fi
}

data1d_run_dir() {
  local artifact_root="$1"
  local run_id="$2"
  printf '%s\n' "${artifact_root}/data-1d/bitvavo/BTC-EUR/${run_id}"
}

data1d_refuse_pro_path_collision() {
  local artifact_root="$1"
  local run_id="$2"
  local pro_dir="${artifact_root}/data-1e/bitvavo/BTC-EUR/${run_id}"
  if [[ -e "${pro_dir}" ]]; then
    echo "Refuse: DATA-1D must not reuse a DATA-1E Pro run identity path: ${pro_dir}" >&2
    echo "Standard artifacts stay under data-1d/bitvavo/BTC-EUR/<run_id>/ only." >&2
    return 1
  fi
}

data1d_refuse_live_modes() {
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

data1d_refuse_protected_keys() {
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

data1d_refuse_protected_tmux_session() {
  local session="$1"
  if [[ "${session}" == "$(data1d_protected_hl_session)" ]]; then
    echo "Refuse: DATA-1D must not use tmux session hl-capture. That session is the live DATA-1A retain. Use bv-std-capture." >&2
    return 1
  fi
  if [[ "${session}" == "$(data1d_protected_bn_session)" ]]; then
    echo "Refuse: DATA-1D must not use tmux session bn-capture. That session is the DATA-1F retain. Use bv-std-capture." >&2
    return 1
  fi
  if [[ "${session}" == "$(data1d_protected_bv_session)" ]]; then
    echo "Refuse: DATA-1D must not use tmux session bv-capture. That session is DATA-1E MD Pro. Use bv-std-capture." >&2
    return 1
  fi
  if [[ "${session}" == "$(data1d_protected_kr_session)" ]]; then
    echo "Refuse: DATA-1D must not use tmux session kr-capture. That session is the DATA-1B retain. Use bv-std-capture." >&2
    return 1
  fi
}

data1d_require_duration() {
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

data1d_tmux_alive() {
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

data1d_list_run_ids() {
  local artifact_root="$1"
  local parent="${artifact_root}/data-1d/bitvavo/BTC-EUR"
  if [[ ! -d "${parent}" ]]; then
    return 0
  fi
  find "${parent}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort
}

data1d_parquet_part_count() {
  local raw_dir="$1"
  if [[ ! -d "${raw_dir}" ]]; then
    printf '%s\n' "0"
    return 0
  fi
  find "${raw_dir}" -maxdepth 1 -type f -name 'part-*.parquet' | wc -l | tr -d ' '
}

data1d_include_candles_flag() {
  case "${INCLUDE_CANDLES:-0}" in
    1|true|yes|YES|True) printf '%s\n' "yes" ;;
    *) printf '%s\n' "no" ;;
  esac
}

data1d_candle_interval() {
  printf '%s\n' "${CANDLE_INTERVAL:-1m}"
}
