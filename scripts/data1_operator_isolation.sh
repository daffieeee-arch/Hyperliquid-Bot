# Shared fail-closed isolation for DATA-1A/B/E/F operator helpers.
# Sourced by data1*_lib.sh. Never print secret values.
# Tests/CI must never stop live collectors, shared tmux sockets, or evidence.

data1_protected_live_sessions() {
  printf '%s\n' "hl-capture bn-capture bv-capture kr-capture"
}

data1_in_test_or_ci_env() {
  if [[ -n "${PYTEST_CURRENT_TEST:-}" ]]; then
    return 0
  fi
  case "${CI:-}" in
    true|1|yes) return 0 ;;
  esac
  if [[ "${HYPERLIQUID_BOT_TEST_ISOLATION:-}" == "1" ]]; then
    return 0
  fi
  return 1
}

data1_is_protected_live_session() {
  local session="$1"
  case "${session}" in
    hl-capture|bn-capture|bv-capture|kr-capture) return 0 ;;
  esac
  return 1
}

data1_refuse_broad_kills() {
  : # Helpers must not call pkill, killall, or tmux kill-server.
}

data1_refuse_test_env_live_stop() {
  local session="$1"
  if data1_in_test_or_ci_env && data1_is_protected_live_session "${session}"; then
    echo "Refuse: test/CI isolation forbids stopping live capture session ${session}." >&2
    echo "Use an isolated fake TMUX_SESSION such as pytest-hl-isolated." >&2
    echo "status=TEST_ISOLATION_NOOP" >&2
    echo "never_touch=hl-capture,bn-capture,bv-capture,kr-capture" >&2
    return 1
  fi
}

data1_refuse_test_env_live_start() {
  local session="$1"
  local check_only="${2:-0}"
  if [[ "${check_only}" == "1" ]]; then
    return 0
  fi
  if data1_in_test_or_ci_env && data1_is_protected_live_session "${session}"; then
    echo "Refuse: test/CI isolation forbids starting live capture session ${session}." >&2
    echo "status=TEST_ISOLATION_NOOP" >&2
    return 1
  fi
}
