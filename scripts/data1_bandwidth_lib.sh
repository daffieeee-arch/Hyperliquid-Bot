# Process-scoped capture bandwidth policy for DATA-1A/B/E/F helpers.
# Caps: <=100 Mbit/s download, <=5 Mbit/s upload, and <=10% of measured upload.
# trickle is the supported per-command limiter. A monitor-with-stop is not a limiter.
# Never print secret values. Never shape the whole house or the whole WSL NIC.

data1_bandwidth_download_mbit() {
  printf '%s\n' "100"
}

data1_bandwidth_upload_mbit() {
  printf '%s\n' "5"
}

data1_bandwidth_download_kib() {
  # 100 Mbit/s = 12800 KiB/s
  printf '%s\n' "12800"
}

data1_bandwidth_upload_kib() {
  # 5 Mbit/s = 640 KiB/s
  printf '%s\n' "640"
}

data1_bandwidth_limiter_kind() {
  if [[ "${CAPTURE_BANDWIDTH_ENFORCED:-}" == "attested" ]]; then
    printf '%s\n' "attested"
    return 0
  fi
  if command -v trickle >/dev/null 2>&1; then
    printf '%s\n' "trickle"
    return 0
  fi
  printf '%s\n' "missing"
}

data1_bandwidth_is_hard() {
  local kind
  kind="$(data1_bandwidth_limiter_kind)"
  if [[ "${kind}" == "trickle" || "${kind}" == "attested" ]]; then
    printf '%s\n' "yes"
  else
    printf '%s\n' "no"
  fi
}

data1_bandwidth_wrap_command() {
  local kind
  kind="$(data1_bandwidth_limiter_kind)"
  if [[ "${kind}" == "trickle" ]]; then
    printf '%s\n' "trickle -s -d $(data1_bandwidth_download_kib) -u $(data1_bandwidth_upload_kib)"
    return 0
  fi
  printf '%s\n' ""
}

data1_bandwidth_echo_policy() {
  echo "bandwidth_download_cap_mbit=$(data1_bandwidth_download_mbit)"
  echo "bandwidth_upload_cap_mbit=$(data1_bandwidth_upload_mbit)"
  echo "bandwidth_limiter_kind=$(data1_bandwidth_limiter_kind)"
  echo "bandwidth_limiter_hard=$(data1_bandwidth_is_hard)"
  echo "bandwidth_scope=capture-processes-only"
  echo "bandwidth_monitor_is_not_limiter=true"
  echo "bandwidth_measure_hooks=nethogs,ss,cgroup_io"
}

data1_bandwidth_require_hard_for_72h() {
  local duration="$1"
  local check_only="${2:-0}"
  if awk -v d="${duration}" 'BEGIN { exit !(d == 259200) }'; then
    if [[ "$(data1_bandwidth_is_hard)" != "yes" ]]; then
      echo "Refuse: 72h retain requires a process-scoped hard limiter (trickle or CAPTURE_BANDWIDTH_ENFORCED=attested)." >&2
      echo "A monitor-with-stop is not a hard rate limiter. Do not raise the budget or drop samples." >&2
      if [[ "${check_only}" == "1" ]]; then
        echo "bandwidth_conflict=true"
        return 0
      fi
      return 1
    fi
  fi
}
