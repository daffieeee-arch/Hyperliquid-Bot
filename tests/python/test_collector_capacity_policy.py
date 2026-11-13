"""Runtime PSI policy regressions on synthetic kernel samples; no host effects."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from hyperliquid_bot import collector_capacity as cap


def policy(profile: str = cap.NATIVE_TREND_PSI) -> cap.CapacityPolicy:
    return cap.CapacityPolicy("fixture-zfs", 2 * cap.GIB, 48 * cap.GIB, profile)


def observation(index: int = 0) -> dict[str, Any]:
    return {
        "monotonic_ns": (index + 1) * 1_320_000_000,
        "zfs_version": "fixture-zfs",
        "direct_memavailable_bytes": 27 * cap.GIB // 4,
        "memtotal_bytes": 64 * cap.GIB,
        "arc": {
            "size": 36 * cap.GIB,
            "c_min": 2 * cap.GIB,
            "c_max": 48 * cap.GIB,
            "mru_evictable_data": 12 * cap.GIB,
            "mfu_evictable_data": 4 * cap.GIB,
            "memory_throttle_count": 0,
            "evict_not_enough": 0,
        },
        "psi": {
            kind: {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total": 1_000_000}
            for kind in ("some", "full")
        },
        "global_oom_kill": 0,
    }


def trend(value: dict[str, Any], kind: str, short: float, long: float) -> None:
    value["psi"][kind].update(avg10=short, avg60=long)
    if kind == "full":
        value["psi"]["some"].update(avg10=short, avg60=long)


def test_profile_is_explicit_and_old_policy_binding_is_identical() -> None:
    legacy = cap.CapacityPolicy("fixture-zfs", 2 * cap.GIB, 48 * cap.GIB)
    assert legacy.runtime_psi_profile == cap.LEGACY_PSI
    assert cap.policy_document(legacy) == {
        "zfs_version": "fixture-zfs",
        "arc_min": 2 * cap.GIB,
        "arc_max": 48 * cap.GIB,
    }
    assert cap.policy_document(policy()) == cap.policy_document(legacy) | {
        "runtime_psi_profile": cap.NATIVE_TREND_PSI
    }
    with pytest.raises(ValueError, match="Unreviewed runtime PSI profile"):
        replace(legacy, runtime_psi_profile="disable-guards")


@pytest.mark.parametrize("spacing", [1_000_000_000, 1_320_000_000, 5_000_000_000])
def test_brief_host_stalls_warn_without_interrupting_capture(spacing: int) -> None:
    first, second, third = (observation(index) for index in range(3))
    # Reproduce the old false stop with substantial headroom, no hard fault,
    # and stall rates at the old full1% boundary. Some includes every full stall.
    for index, value in enumerate((first, second, third)):
        value["monotonic_ns"] = (index + 1) * spacing
        for kind in ("some", "full"):
            value["psi"][kind]["total"] += index * spacing // 100_000
    old_streaks, _ = cap.evaluate(first, second, {}, policy(cap.LEGACY_PSI))
    assert cap.evaluate(second, third, old_streaks, policy(cap.LEGACY_PSI))[1] == [
        "memory-pressure"
    ]
    streaks, reasons = cap.evaluate(first, second, {}, policy())
    assert not reasons
    assert not cap.evaluate(second, third, streaks, policy())[1]
    status = cap.runtime_status(second, third, policy())
    assert status["state"] == "warning"
    assert status["interval_percent"]["full"] == 1.0
    assert status["direct_memavailable_bytes"] == 27 * cap.GIB // 4


@pytest.mark.parametrize("kind,short,long", [("full", 5, 2), ("some", 20, 10)])
def test_same_native_short_and_long_trend_stops_at_exact_bounds(
    kind: str, short: float, long: float
) -> None:
    first, second = observation(), observation(1)
    for value in (first, second):
        trend(value, kind, short, long)
    # Stop permission comes from both native trend observations, never a guessed streak.
    assert cap.evaluate(first, second, {}, policy())[1] == ["memory-pressure"]
    second["psi"][kind]["avg60"] = long - 0.01
    assert not cap.evaluate(first, second, {"psi": 100}, policy())[1]


@pytest.mark.parametrize("kind,critical", [("full", 25), ("some", 50)])
def test_critical_native_short_trend_does_not_require_long_corroboration(
    kind: str, critical: float
) -> None:
    first, second = observation(), observation(1)
    for value in (first, second):
        trend(value, kind, critical, 0)
    assert cap.evaluate(first, second, {}, policy())[1] == ["memory-pressure"]
    second["psi"][kind]["avg10"] = critical - 0.01
    assert not cap.evaluate(first, second, {}, policy())[1]


@pytest.mark.parametrize(
    "mode", ["one-sample", "missing-long", "different-kind", "different-route"]
)
def test_unmatched_native_triggers_do_not_combine_into_stop(mode: str) -> None:
    first, second = observation(), observation(1)
    if mode == "one-sample":
        trend(second, "full", 5, 2)
    elif mode == "missing-long":
        for value in (first, second):
            trend(value, "full", 5, 1.99)
    elif mode == "different-kind":
        trend(first, "full", 5, 2)
        trend(second, "some", 20, 10)
    else:
        trend(first, "full", 25, 0)
        trend(second, "full", 5, 2)
    assert not cap.evaluate(first, second, {"psi": 200}, policy())[1]


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("low", "low-memory"),
        ("oom", "global-oom"),
        ("throttle", "zfs-throttle"),
        ("evict", "zfs-eviction-with-low-memory"),
    ],
)
def test_hard_faults_remain_immediate_even_without_material_psi(fault: str, expected: str) -> None:
    first, second = observation(), observation(1)
    if fault == "low":
        second["direct_memavailable_bytes"] = cap.GIB - 1
    elif fault == "oom":
        second["global_oom_kill"] += 1
    elif fault == "throttle":
        second["arc"]["memory_throttle_count"] += 1
    else:
        second["direct_memavailable_bytes"] = 2 * cap.GIB
        second["arc"]["evict_not_enough"] += 1
    assert cap.evaluate(first, second, {}, policy())[1] == [expected]


def test_low_memory_streak_and_combined_fault_are_not_soft_recovery_permission() -> None:
    first, second, third = (observation(index) for index in range(3))
    second["direct_memavailable_bytes"] = third["direct_memavailable_bytes"] = 7 * cap.GIB // 5
    streaks, reasons = cap.evaluate(first, second, {}, policy())
    assert not reasons
    assert cap.evaluate(second, third, streaks, policy())[1] == ["low-memory"]
    trend(first, "full", 5, 2)
    trend(second, "full", 5, 2)
    second["global_oom_kill"] += 1
    assert cap.evaluate(first, second, {}, policy())[1] == ["memory-pressure", "global-oom"]


@pytest.mark.parametrize("fault", ["gap", "backward", "bad-average", "subset", "delta-subset"])
def test_invalid_telemetry_cannot_be_reclassified_as_a_benign_warning(fault: str) -> None:
    first, second = observation(), observation(1)
    if fault == "gap":
        second["monotonic_ns"] += 5_000_000_000
    elif fault == "backward":
        second["psi"]["some"]["total"] -= 1
    elif fault == "bad-average":
        first["psi"]["some"]["avg10"] = float("nan")
    elif fault == "subset":
        second["psi"]["full"]["avg10"] = 2.0
    else:
        first["psi"]["some"]["total"] += 10
        second["psi"]["full"]["total"] += 2
        second["psi"]["some"]["total"] += 10
    with pytest.raises(ValueError):
        cap.evaluate(first, second, {}, policy())


def test_startup_admission_remains_stricter_for_both_profiles() -> None:
    samples = [observation(index) for index in range(3)]
    original = copy.deepcopy(samples)
    for profile in (cap.LEGACY_PSI, cap.NATIVE_TREND_PSI):
        assert len(cap.admit(samples, policy(profile))) == 3
        samples[1]["psi"]["some"]["avg10"] = 5.0
        with pytest.raises(ValueError, match="Startup PSI average limit"):
            cap.admit(samples, policy(profile))
        samples = copy.deepcopy(original)
