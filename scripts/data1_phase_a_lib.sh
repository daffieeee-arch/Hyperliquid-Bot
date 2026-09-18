# Shared Phase A campaign helpers. Sourced by data1*_lib.sh.
# Joint 72h PAPER retain on TerraPC WSL. Never print secret values.

data1_phase_a_profile_name() {
  local duration="$1"
  if awk -v d="${duration}" 'BEGIN { exit !(d == 259200) }'; then
    printf '%s\n' "retained_72h"
    return 0
  fi
  if awk -v d="${duration}" 'BEGIN { exit !(d <= 3600) }'; then
    printf '%s\n' "data2a_short_pilot"
    return 0
  fi
  printf '%s\n' "unscoped_retained"
}

data1_phase_a_echo_profile() {
  local duration="$1"
  echo "phase_a_profile=$(data1_phase_a_profile_name "${duration}")"
  echo "phase_a_heartbeat_is_not_market_data=true"
  echo "phase_a_eth_sol_addon_start_policy=deferred"
}

data1_phase_a_require_chupa_ok_for_72h() {
  local duration="$1"
  local check_only="${2:-0}"
  if ! awk -v d="${duration}" 'BEGIN { exit !(d == 259200) }'; then
    echo "phase_a_chupa_ok=not_required"
    return 0
  fi
  if [[ "${PHASE_A_CHUPA_OK:-}" == "1" ]]; then
    echo "phase_a_chupa_ok=true"
    echo "wait_for_chupa_ok=false"
    return 0
  fi
  echo "phase_a_chupa_ok=false"
  echo "wait_for_chupa_ok=true"
  if [[ "${check_only}" == "1" ]]; then
    echo "do_not_start_72h_until_chupa_ok=true"
    return 0
  fi
  echo "Refuse: 72h Phase A retain requires PHASE_A_CHUPA_OK=1 after the joint smoke is STOPPED and Chupa gives explicit OK." >&2
  return 1
}

data1_phase_a_echo_pin_policy() {
  echo "phase_a_pinned_checkout_required=true"
  echo "phase_a_shared_dev_worktree_forbidden=true"
  echo "phase_a_per_feed_output_dirs=true"
}
