# Shared DATA-1 operator-PC status helpers.
# Create-only inspection. PAPER public captures only. Never print secret values.
# When RUN_ID is unset, status defaults to the freshest live retain.

data1_status_fresh_max_s() {
  local raw="${STATUS_FRESH_MAX_S:-${COCKPIT_CAPTURE_FRESH_MAX_S:-180}}"
  if [[ ! "${raw}" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "180"
    return 0
  fi
  printf '%s\n' "${raw}"
}

data1_status_health_status() {
  local health_path="$1"
  if [[ ! -f "${health_path}" ]]; then
    return 0
  fi
  python3 -c 'import json,sys
try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
status = payload.get("status", "") if isinstance(payload, dict) else ""
print(status if isinstance(status, str) else "")
' < "${health_path}" 2>/dev/null || true
}

data1_status_is_stopped_health() {
  case "$1" in
    COMPLETED|FAILED|OPERATOR_STOP)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

data1_status_last_part_mtime() {
  local raw_dir="$1"
  if [[ ! -d "${raw_dir}" ]]; then
    return 1
  fi
  local newest
  newest="$(find "${raw_dir}" -maxdepth 1 -type f -name 'part-*.parquet' -printf '%T@\n' | LC_ALL=C sort -n | tail -1)"
  if [[ -z "${newest}" ]]; then
    return 1
  fi
  printf '%s\n' "${newest}"
}

data1_status_is_live_run_dir() {
  local run_dir="$1"
  if [[ ! -f "${run_dir}/capture-claim.json" ]]; then
    return 1
  fi
  local health_status
  health_status="$(data1_status_health_status "${run_dir}/capture-health.json")"
  if data1_status_is_stopped_health "${health_status}"; then
    return 1
  fi
  local mtime
  mtime="$(data1_status_last_part_mtime "${run_dir}/raw" || true)"
  if [[ -z "${mtime}" ]]; then
    return 1
  fi
  local now fresh
  now="$(date +%s)"
  fresh="$(data1_status_fresh_max_s)"
  awk -v now="${now}" -v mtime="${mtime}" -v fresh="${fresh}" 'BEGIN { exit !((now - mtime) <= fresh + 0) }'
}

data1_status_pick_freshest_live() {
  local parent="$1"
  if [[ ! -d "${parent}" ]]; then
    return 0
  fi
  local best_id="" best_mtime=""
  local dir run_id mtime
  while IFS= read -r dir; do
    [[ -n "${dir}" ]] || continue
    run_id="$(basename "${dir}")"
    if ! data1_status_is_live_run_dir "${dir}"; then
      continue
    fi
    mtime="$(data1_status_last_part_mtime "${dir}/raw")"
    if [[ -z "${best_id}" ]]; then
      best_id="${run_id}"
      best_mtime="${mtime}"
      continue
    fi
    if awk -v a="${mtime}" -v b="${best_mtime}" 'BEGIN { exit !(a > b) }'; then
      best_id="${run_id}"
      best_mtime="${mtime}"
    elif awk -v a="${mtime}" -v b="${best_mtime}" 'BEGIN { exit !(a == b) }' \
      && [[ "${run_id}" > "${best_id}" ]]; then
      best_id="${run_id}"
      best_mtime="${mtime}"
    fi
  done < <(find "${parent}" -mindepth 1 -maxdepth 1 -type d -printf '%p\n' | LC_ALL=C sort)
  if [[ -n "${best_id}" ]]; then
    printf '%s\n' "${best_id}"
  fi
}

data1_status_print_unbound_transport_hints() {
  echo "transport_hints_source=n/a"
  echo "transport_reconnects=n/a"
  echo "transport_gaps=n/a"
}

data1_status_print_transport_hints() {
  local run_dir="${1:-}"
  local run_id="${2:-}"
  local artifact_root="${3:-}"
  local helper
  helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/data1_status_hints.py"
  if [[ -z "${run_dir}" || -z "${run_id}" || ! -d "${run_dir}" || ! -f "${helper}" ]]; then
    data1_status_print_unbound_transport_hints
    return 0
  fi
  python3 "${helper}" \
    --run-dir "${run_dir}" \
    --run-id "${run_id}" \
    --artifact-root "${artifact_root}" \
    || data1_status_print_unbound_transport_hints
}
