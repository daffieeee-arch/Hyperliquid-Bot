"""Deterministic tests for the capture-process bandwidth policy."""

from __future__ import annotations

import pytest

from hyperliquid_bot.capture_bandwidth_policy import (
    CAPTURE_DOWNLOAD_CAP_MBIT,
    CAPTURE_UPLOAD_CAP_MBIT,
    CaptureBandwidthPolicy,
    CaptureBandwidthPolicyError,
    bandwidth_preflight,
    effective_upload_cap_mbit,
    report_rate_conflict,
    require_hard_limiter_for_72h,
    trickle_argv,
)


def test_caps_are_100_down_and_5_up() -> None:
    policy = CaptureBandwidthPolicy()
    assert policy.download_cap_mbit == CAPTURE_DOWNLOAD_CAP_MBIT == 100.0
    assert policy.upload_cap_mbit == CAPTURE_UPLOAD_CAP_MBIT == 5.0
    assert policy.download_kib_per_sec == 12800
    assert policy.upload_kib_per_sec == 640
    assert trickle_argv(policy) == ("trickle", "-s", "-d", "12800", "-u", "640")


def test_upload_cap_is_also_ten_percent_of_measured() -> None:
    assert effective_upload_cap_mbit(80.0) == 5.0
    assert effective_upload_cap_mbit(20.0) == 2.0
    with pytest.raises(CaptureBandwidthPolicyError, match="10%"):
        CaptureBandwidthPolicy(upload_cap_mbit=5.0, measured_upload_mbit=20.0)


def test_policy_refuses_to_raise_the_budget() -> None:
    with pytest.raises(CaptureBandwidthPolicyError, match="100"):
        CaptureBandwidthPolicy(download_cap_mbit=101.0)
    with pytest.raises(CaptureBandwidthPolicyError, match="5"):
        CaptureBandwidthPolicy(upload_cap_mbit=6.0)


def test_conflict_is_reported_instead_of_silent_drop() -> None:
    with pytest.raises(CaptureBandwidthPolicyError, match="conflict"):
        report_rate_conflict(observed_download_mbit=120.0, observed_upload_mbit=1.0)
    report_rate_conflict(observed_download_mbit=40.0, observed_upload_mbit=1.0)


def test_72h_requires_hard_limiter_not_a_monitor() -> None:
    missing = bandwidth_preflight(attested=False)
    if missing["limiter_kind"] == "missing":
        with pytest.raises(CaptureBandwidthPolicyError, match="hard limiter"):
            require_hard_limiter_for_72h(missing)
    attested = bandwidth_preflight(attested=True)
    require_hard_limiter_for_72h(attested)
    assert attested["monitor_is_not_limiter"] is True
    assert attested["whole_house_or_wsl_shaper"] is False
    with pytest.raises(CaptureBandwidthPolicyError, match="deferred"):
        require_hard_limiter_for_72h(bandwidth_preflight(attested=True, hl_addons_enabled=True))
