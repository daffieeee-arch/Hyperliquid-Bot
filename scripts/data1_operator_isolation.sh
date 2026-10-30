# Shared fail-closed isolation for all six DATA-1 capture operator lanes.
# Sourced by data1*_lib.sh. Never print secret values.

data1_protected_live_sessions() {
  printf '%s\n' "hl-capture bn-capture bv-capture bv-std-capture kr-capture okx-capture"
}

data1_in_test_or_ci_env() {
  [[ -n "${PYTEST_CURRENT_TEST:-}" || -n "${CI:-}" || -n "${HYPERLIQUID_BOT_TEST_ISOLATION:-}" ]]
}

data1_is_protected_live_session() {
  case "$1" in
    hl-capture|bn-capture|bv-capture|bv-std-capture|kr-capture|okx-capture) return 0 ;;
  esac
  return 1
}

data1_is_isolated_fake_session() {
  case "$1" in
    pytest-hl-isolated|pytest-bn-isolated|pytest-bv-isolated|pytest-bv-std-isolated|pytest-kr-isolated|pytest-okx-isolated) return 0 ;;
  esac
  return 1
}

data1_refuse_broad_kills() {
  : # Helpers must not call pkill, killall, or tmux kill-server.
}

data1_require_safe_operator_session() {
  local session="$1"
  local LC_ALL=C
  if [[ ! "${session}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$ ]]; then
    echo "Refuse: use a simple, bounded tmux session name, without target selectors." >&2
    echo "status=TEST_ISOLATION_NOOP" >&2
    return 1
  fi
  if data1_in_test_or_ci_env && ! data1_is_isolated_fake_session "${session}"; then
    echo "Refuse: test/CI isolation forbids capture session ${session}." >&2
    echo "Use an explicit isolated fake TMUX_SESSION such as pytest-hl-isolated." >&2
    echo "status=TEST_ISOLATION_NOOP" >&2
    echo "never_touch=$(data1_protected_live_sessions)" >&2
    return 1
  fi
}

data1_refuse_test_env_live_stop() {
  data1_require_safe_operator_session "$1"
}

data1_refuse_test_env_live_start() {
  # --check-only must not bypass isolation: it also queries tmux sessions.
  data1_require_safe_operator_session "$1"
}
