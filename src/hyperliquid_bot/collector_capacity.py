"""Read-only host memory admission and pressure stops for native public capture.

ARC credit is deliberately discounted clean, evictable DATA, never ghost entries,
metadata or a reservation. Kernel/module identity and ARC limits are explicit
operator pins, so an upgrade cannot silently change the accounting contract.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GIB = 1024**3
LEGACY_PSI = "short-streak-v1"
NATIVE_TREND_PSI = "native-trend-v2"
ARC_KEYS = (
    "mru_evictable_data",
    "mfu_evictable_data",
    "size",
    "c_min",
    "c_max",
    "memory_throttle_count",
    "evict_not_enough",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Nonnegative exact integer required.")
    return value


@dataclass(frozen=True)
class CapacityPolicy:
    zfs_version: str
    arc_min: int
    arc_max: int
    runtime_psi_profile: str = LEGACY_PSI

    def __post_init__(self) -> None:
        require(bool(self.zfs_version) and len(self.zfs_version) < 80, "ZFS version pin required.")
        require(0 < count(self.arc_min) <= count(self.arc_max), "Explicit ARC bounds required.")
        require(
            type(self.runtime_psi_profile) is str
            and self.runtime_psi_profile in {LEGACY_PSI, NATIVE_TREND_PSI},
            "Unreviewed runtime PSI profile.",
        )


def policy_document(policy: CapacityPolicy) -> dict[str, Any]:
    """Preserve the exact historical policy binding when no new profile is selected."""
    value: dict[str, Any] = {
        "zfs_version": policy.zfs_version,
        "arc_min": policy.arc_min,
        "arc_max": policy.arc_max,
    }
    if policy.runtime_psi_profile != LEGACY_PSI:
        value["runtime_psi_profile"] = policy.runtime_psi_profile
    return value


def material_native_trend(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Use corroborated kernel EWMAs, not two brief stall-counter spikes.

    These are exponentially weighted kernel trends, not exact boxcar windows.
    The same kind and trigger must hold at both observations. Critical avg10
    bypasses avg60 corroboration; hard low-memory/OOM/ZFS checks remain faster.
    """
    for kind, short, long, critical in (("full", 5, 2, 25), ("some", 20, 10, 50)):
        if all(
            sample["psi"][kind]["avg10"] >= short and sample["psi"][kind]["avg60"] >= long
            for sample in (previous, current)
        ) or all(sample["psi"][kind]["avg10"] >= critical for sample in (previous, current)):
            return True
    return False


def metric(sample: dict[str, Any], policy: CapacityPolicy) -> dict[str, int]:
    require(sample["zfs_version"] == policy.zfs_version, "Unreviewed ZFS version.")
    raw, total = count(sample["direct_memavailable_bytes"]), count(sample["memtotal_bytes"])
    arc = sample["arc"]
    for key in ARC_KEYS:
        count(arc[key])
    require(
        0 < total
        and raw <= total
        and arc["c_min"] == policy.arc_min
        and arc["c_max"] == policy.arc_max
        and 0 < arc["size"] <= policy.arc_max <= total,
        "Incoherent or changed ARC bounds.",
    )
    data = arc["mru_evictable_data"] + arc["mfu_evictable_data"]
    require(data <= arc["size"], "Evictable DATA exceeds ARC size.")
    credit = min(3 * GIB, min(data, max(0, arc["size"] - policy.arc_min)) // 4)
    require(raw + credit <= total, "Effective budget exceeds physical memory.")
    count(sample["global_oom_kill"])
    count(sample["monotonic_ns"])
    for kind in ("some", "full"):
        count(sample["psi"][kind]["total"])
        for key in ("avg10", "avg60", "avg300"):
            value = sample["psi"][kind][key]
            require(
                type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100,
                "Invalid PSI average.",
            )
    if policy.runtime_psi_profile == NATIVE_TREND_PSI:
        require(
            all(
                sample["psi"]["full"][key] <= sample["psi"]["some"][key]
                for key in ("avg10", "avg60", "avg300", "total")
            ),
            "PSI subset violated.",
        )
    return {
        "direct_bytes": raw,
        "clean_data_bytes": data,
        "credit_bytes": credit,
        "effective_bytes": raw + credit,
    }


def admit(samples: list[dict[str, Any]], policy: CapacityPolicy) -> list[dict[str, int]]:
    require(len(samples) == 3, "Three fresh startup samples required.")
    proofs = []
    previous = None
    for sample in samples:
        proof = metric(sample, policy)
        require(
            proof["direct_bytes"] >= 2 * GIB and proof["effective_bytes"] >= 5 * GIB,
            "Insufficient native memory admission headroom.",
        )
        for kind, bound in (("full", 1), ("some", 5)):
            require(
                all(sample["psi"][kind][key] < bound for key in ("avg10", "avg60", "avg300")),
                "Startup PSI average limit.",
            )
        require(
            all(
                sample["psi"]["full"][key] <= sample["psi"]["some"][key]
                for key in ("avg10", "avg60", "avg300", "total")
            ),
            "PSI subset violated.",
        )
        if previous is not None:
            gap = sample["monotonic_ns"] - previous["monotonic_ns"]
            require(1_000_000_000 <= gap <= 5_000_000_000, "Startup sample spacing.")
            require(
                sample["global_oom_kill"] == previous["global_oom_kill"]
                and all(
                    sample["arc"][key] == previous["arc"][key]
                    for key in ("memory_throttle_count", "evict_not_enough")
                ),
                "Startup OOM, throttle or eviction failure.",
            )
            deltas = {}
            for kind, bound in (("full", 1), ("some", 5)):
                delta = sample["psi"][kind]["total"] - previous["psi"][kind]["total"]
                require(delta >= 0 and delta * 100_000 < gap * bound, "Startup PSI interval.")
                deltas[kind] = delta
            # psi_show independently floors cumulative ns to us. A difference
            # of one us is coherent; it never reduces measured pressure itself.
            require(deltas["full"] <= deltas["some"] + 1, "Startup PSI delta subset.")
        proofs.append(proof)
        previous = sample
    return proofs


def evaluate(
    previous: dict[str, Any],
    current: dict[str, Any],
    streaks: dict[str, int],
    policy: CapacityPolicy,
) -> tuple[dict[str, int], list[str]]:
    proof = metric(current, policy)
    if policy.runtime_psi_profile == NATIVE_TREND_PSI:
        metric(previous, policy)
    gap = current["monotonic_ns"] - previous["monotonic_ns"]
    require(0 < gap <= 5_000_000_000, "Pressure supervision gap exceeds five seconds.")
    require(current["global_oom_kill"] >= previous["global_oom_kill"], "OOM counter regressed.")
    pressure = False
    deltas = {}
    for kind, bound in (("full", 1), ("some", 5)):
        delta = current["psi"][kind]["total"] - previous["psi"][kind]["total"]
        require(0 <= delta * 100_000 <= gap * 100, "Invalid PSI counter interval.")
        pressure |= delta * 100_000 >= gap * bound
        deltas[kind] = delta
    if policy.runtime_psi_profile == NATIVE_TREND_PSI:
        require(deltas["full"] <= deltas["some"] + 1, "Runtime PSI delta subset.")
    for key in ("memory_throttle_count", "evict_not_enough"):
        require(current["arc"][key] >= previous["arc"][key], "ARC failure counter regressed.")
    low = streaks.get("low", 0) + 1 if proof["direct_bytes"] < 3 * GIB // 2 else 0
    psi = streaks.get("psi", 0) + 1 if pressure else 0
    reasons = []
    if proof["direct_bytes"] < GIB or low >= 2:
        reasons.append("low-memory")
    if (
        psi >= 2
        if policy.runtime_psi_profile == LEGACY_PSI
        else material_native_trend(previous, current)
    ):
        reasons.append("memory-pressure")
    if current["global_oom_kill"] > previous["global_oom_kill"]:
        reasons.append("global-oom")
    if current["arc"]["memory_throttle_count"] > previous["arc"]["memory_throttle_count"]:
        reasons.append("zfs-throttle")
    if (
        proof["direct_bytes"] < 5 * GIB // 2
        and current["arc"]["evict_not_enough"] > previous["arc"]["evict_not_enough"]
    ):
        reasons.append("zfs-eviction-with-low-memory")
    return {"low": low, "psi": psi}, reasons


def runtime_status(
    previous: dict[str, Any] | None, current: dict[str, Any], policy: CapacityPolicy
) -> dict[str, Any]:
    """Bounded diagnostics for the existing read-only operator status projection."""
    proof = metric(current, policy)
    warning = any(
        current["psi"][kind]["avg10"] >= bound for kind, bound in (("full", 1), ("some", 5))
    )
    intervals: dict[str, float] = {}
    if previous is not None:
        gap = current["monotonic_ns"] - previous["monotonic_ns"]
        require(0 < gap <= 5_000_000_000, "Pressure supervision gap exceeds five seconds.")
        for kind, bound in (("full", 1), ("some", 5)):
            delta = current["psi"][kind]["total"] - previous["psi"][kind]["total"]
            require(0 <= delta * 100_000 <= gap * 100, "Invalid PSI counter interval.")
            intervals[kind] = delta * 100_000 / gap
            warning |= intervals[kind] >= bound
    return {
        "runtime_psi_profile": policy.runtime_psi_profile,
        "state": "warning" if warning else "healthy",
        "direct_memavailable_bytes": proof["direct_bytes"],
        "sample_monotonic_ns": current["monotonic_ns"],
        "interval_percent": intervals,
        "native_psi": current["psi"],
    }


def snapshot() -> dict[str, Any]:
    """Read only bounded kernel attributes; no ARC tuning or cache flushing."""

    def lines(path: str) -> list[str]:
        with Path(path).open("rb") as stream:
            value = stream.read(1024 * 1024 + 1)
        require(len(value) <= 1024 * 1024, "Kernel attribute bound.")
        return value.decode("ascii").splitlines()

    mem = {}
    for line in lines("/proc/meminfo"):
        name, _, rest = line.partition(":")
        if name in {"MemAvailable", "MemTotal"}:
            value, unit = rest.split()
            require(name not in mem and unit == "kB", "Memory schema changed.")
            mem[name] = int(value) * 1024
    arc = {}
    for line in lines("/proc/spl/kstat/zfs/arcstats")[2:]:
        fields = line.split()
        if len(fields) == 3 and fields[0] in ARC_KEYS:
            require(fields[0] not in arc, "Duplicate ARC key.")
            arc[fields[0]] = int(fields[2])
    psi = {}
    for line in lines("/proc/pressure/memory"):
        kind, *pairs = line.split()
        require(kind in {"some", "full"} and kind not in psi, "PSI schema changed.")
        values = dict(pair.split("=", 1) for pair in pairs)
        require(
            len(values) == len(pairs) == 4 and set(values) == {"avg10", "avg60", "avg300", "total"},
            "PSI fields changed.",
        )
        psi[kind] = {
            key: int(value) if key == "total" else float(value) for key, value in values.items()
        }
    oom = [int(line.split()[1]) for line in lines("/proc/vmstat") if line.startswith("oom_kill ")]
    require(len(oom) == 1, "OOM counter unavailable.")
    return {
        "monotonic_ns": time.monotonic_ns(),
        "zfs_version": "".join(lines("/sys/module/zfs/version")),
        "direct_memavailable_bytes": mem["MemAvailable"],
        "memtotal_bytes": mem["MemTotal"],
        "arc": arc,
        "psi": psi,
        "global_oom_kill": oom[0],
    }
