"""Deterministic tests for the distinct 72h retained profile."""

from __future__ import annotations

import pytest

from hyperliquid_bot.retained_capture_profile import (
    DATA2A_SHORT_PILOT_PROFILE_NAME,
    JOINT_SMOKE_MAX_SECONDS,
    RETAINED_72H_SECONDS,
    data2a_short_pilot_profile,
    expected_parts_lower_bound,
    phase_a_preflight_report,
    profile_for_duration,
    retained_72h_profile,
)


def test_72h_profile_is_not_the_data2a_short_pilot() -> None:
    smoke = data2a_short_pilot_profile()
    retain = retained_72h_profile()
    assert smoke.name == DATA2A_SHORT_PILOT_PROFILE_NAME
    assert smoke.duration_seconds == 600.0
    assert retain.duration_seconds == float(RETAINED_72H_SECONDS)
    assert retain.name == "retained_72h"
    assert retain.expected_parts_lower_bound == expected_parts_lower_bound(259_200)
    assert retain.expected_parts_lower_bound == 4320
    assert retain.expected_disk_bytes > smoke.expected_disk_bytes
    assert retain.free_space_reserve_bytes >= 50 * 1024 * 1024 * 1024
    assert retain.heartbeat_is_not_market_data is True
    assert retain.twenty_four_seven is False


def test_short_pilot_cannot_cover_three_days() -> None:
    with pytest.raises(ValueError, match="60-minute"):
        data2a_short_pilot_profile(duration_seconds=RETAINED_72H_SECONDS)
    with pytest.raises(ValueError, match="not valid for 3 days"):
        profile_for_duration(86_400)
    assert profile_for_duration(JOINT_SMOKE_MAX_SECONDS).name == DATA2A_SHORT_PILOT_PROFILE_NAME
    assert profile_for_duration(float(RETAINED_72H_SECONDS)).name == "retained_72h"


def test_phase_a_preflight_lists_four_production_lanes_only() -> None:
    report = phase_a_preflight_report()
    assert report["profile"] == "retained_72h"
    lanes = report["lanes"]
    assert isinstance(lanes, list)
    lane_ids = [row["lane_id"] for row in lanes]
    assert lane_ids == ["data1a", "data1f", "data1e", "data1b"]
    products = [row["product"] for row in lanes]
    assert products == ["BTC-PERP", "BTCUSDT", "BTC-EUR", "BTC-USD"]
    assert "OKX" not in str(report)
    assert "Deribit" not in str(report)
    assert "Polymarket" not in str(report)
    assert report["heartbeat_is_not_market_data"] is True
