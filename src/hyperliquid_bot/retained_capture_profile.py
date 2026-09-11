"""Distinct retained-capture duration profiles.

DATA-2A / short-pilot smoke limits are not automatically valid for a 72-hour
retain. A 72h profile precomputes expected parts, disk, and free-space reserve
without changing writer integrity, queue, or reconnect semantics.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .parquet_research import ParquetRotation

ProfileName = Literal["data2a_short_pilot", "retained_72h"]

SMOKE_CAPTURE_SECONDS: Final = 600.0
JOINT_SMOKE_MAX_SECONDS: Final = 3600.0
RETAINED_72H_SECONDS: Final = 259_200
MAX_CAPTURE_SECONDS: Final = 7 * 24 * 60 * 60
RETAINED_72H_PROFILE_NAME: Final = "retained_72h"
DATA2A_SHORT_PILOT_PROFILE_NAME: Final = "data2a_short_pilot"

# Writer rotation is unchanged. Time-based publication is 60s, so a continuous
# feed yields one part per minute as a lower bound. Disk figures are operator
# budgets from existing WSL/VPS notes, not measured 72h rates.
_ROTATION = ParquetRotation()
SECONDS_PER_PART_LOWER_BOUND: Final = float(_ROTATION.max_interval_seconds)
FREE_SPACE_RESERVE_RATIO: Final = 0.20
FREE_SPACE_RESERVE_MIN_BYTES: Final = 50 * 1024 * 1024 * 1024

LaneId = Literal["data1a", "data1b", "data1e", "data1f"]


@dataclass(frozen=True, slots=True)
class LaneDiskBudget:
    """Conservative operator disk budget for one reconstructable lane."""

    lane_id: LaneId
    venue: str
    product: str
    expected_parquet_bytes: int
    notes: str


@dataclass(frozen=True, slots=True)
class RetainedCaptureProfile:
    """One named duration/disk profile. Smoke limits do not inherit to 72h."""

    name: ProfileName
    duration_seconds: float
    expected_parts_lower_bound: int
    free_space_reserve_bytes: int
    lanes: tuple[LaneDiskBudget, ...]
    heartbeat_is_not_market_data: bool = True
    twenty_four_seven: bool = False

    def __post_init__(self) -> None:
        if self.name == DATA2A_SHORT_PILOT_PROFILE_NAME:
            if self.duration_seconds > JOINT_SMOKE_MAX_SECONDS:
                raise ValueError("DATA-2A short-pilot duration cannot cover a 72h retain")
        elif self.name == RETAINED_72H_PROFILE_NAME:
            if self.duration_seconds != float(RETAINED_72H_SECONDS):
                raise ValueError("retained_72h duration must be exactly 259200 seconds")
        else:
            raise ValueError(f"unsupported retained-capture profile {self.name!r}")
        if self.expected_parts_lower_bound < 1:
            raise ValueError("expected_parts_lower_bound must be at least 1")
        if not self.heartbeat_is_not_market_data:
            raise ValueError("heartbeats must not be treated as market-data validity")

    @property
    def expected_disk_bytes(self) -> int:
        return sum(lane.expected_parquet_bytes for lane in self.lanes)

    @property
    def required_free_bytes(self) -> int:
        return self.expected_disk_bytes + self.free_space_reserve_bytes

    def as_claim_fields(self) -> dict[str, object]:
        return {
            "capture_profile": self.name,
            "duration_seconds": self.duration_seconds,
            "expected_parts_lower_bound": self.expected_parts_lower_bound,
            "expected_disk_bytes": self.expected_disk_bytes,
            "free_space_reserve_bytes": self.free_space_reserve_bytes,
            "heartbeat_is_not_market_data": True,
            "twenty_four_seven": False,
        }


def expected_parts_lower_bound(duration_seconds: float) -> int:
    """Lower-bound part count from the unchanged 60s rotation interval."""

    if type(duration_seconds) not in (int, float) or not math.isfinite(float(duration_seconds)):
        raise ValueError("duration_seconds must be a finite number")
    duration = float(duration_seconds)
    if duration < 1.0:
        raise ValueError("duration_seconds must be at least 1")
    return max(1, math.ceil(duration / SECONDS_PER_PART_LOWER_BOUND))


def _lane_budgets() -> tuple[LaneDiskBudget, ...]:
    gigabyte = 1024 * 1024 * 1024
    return (
        LaneDiskBudget(
            lane_id="data1a",
            venue="hyperliquid",
            product="BTC-PERP",
            expected_parquet_bytes=8 * gigabyte,
            notes=(
                "Official BTC trades/bbo/l2Book/activeAssetCtx. Operator budget "
                "from tens-of-KB/s WSL notes, rounded up. ETH/SOL add-ons are "
                "not included while deferred at start."
            ),
        ),
        LaneDiskBudget(
            lane_id="data1f",
            venue="binance",
            product="BTCUSDT",
            expected_parquet_bytes=40 * gigabyte,
            notes=(
                "Spot trade/bookTicker/depth@100ms plus USD-M /market and "
                "/public. Existing 72h operator budget: tens of GB."
            ),
        ),
        LaneDiskBudget(
            lane_id="data1e",
            venue="bitvavo",
            product="BTC-EUR",
            expected_parquet_bytes=20 * gigabyte,
            notes=(
                "MD Pro book+trades (no silent Standard fallback). At-least "
                "Kraken-class until a measured 72h rate exists."
            ),
        ),
        LaneDiskBudget(
            lane_id="data1b",
            venue="kraken",
            product="BTC-USD",
            expected_parquet_bytes=25 * gigabyte,
            notes=(
                "BTC/USD L2+trades at depth 100, plus L3/tape when WS keys are "
                "present. Not BTC/EUR. Low-tens of GB if L3 is enabled."
            ),
        ),
    )


def data2a_short_pilot_profile(
    *,
    duration_seconds: float = SMOKE_CAPTURE_SECONDS,
) -> RetainedCaptureProfile:
    """DATA-2A short-pilot / smoke. Must not be reused as a 72h retain."""

    duration = float(duration_seconds)
    if duration > JOINT_SMOKE_MAX_SECONDS:
        raise ValueError("DATA-2A short-pilot cannot exceed the 60-minute joint-smoke bound")
    scale = max(duration / float(RETAINED_72H_SECONDS), 1.0 / 432.0)
    megabyte = 1024 * 1024
    lanes = tuple(
        LaneDiskBudget(
            lane_id=lane.lane_id,
            venue=lane.venue,
            product=lane.product,
            expected_parquet_bytes=max(int(lane.expected_parquet_bytes * scale), 64 * megabyte),
            notes=lane.notes + " DATA-2A short-pilot disk is a scaled smoke budget.",
        )
        for lane in _lane_budgets()
    )
    return RetainedCaptureProfile(
        name=DATA2A_SHORT_PILOT_PROFILE_NAME,
        duration_seconds=duration,
        expected_parts_lower_bound=expected_parts_lower_bound(duration),
        free_space_reserve_bytes=2 * 1024 * 1024 * 1024,
        lanes=lanes,
    )


def retained_72h_profile() -> RetainedCaptureProfile:
    """Distinct 72-hour Phase A retain. Not derived from DATA-2A smoke limits."""

    duration = float(RETAINED_72H_SECONDS)
    expected_disk = sum(lane.expected_parquet_bytes for lane in _lane_budgets())
    reserve = max(
        FREE_SPACE_RESERVE_MIN_BYTES,
        math.ceil(expected_disk * FREE_SPACE_RESERVE_RATIO),
    )
    return RetainedCaptureProfile(
        name=RETAINED_72H_PROFILE_NAME,
        duration_seconds=duration,
        expected_parts_lower_bound=expected_parts_lower_bound(duration),
        free_space_reserve_bytes=reserve,
        lanes=_lane_budgets(),
    )


def profile_for_duration(duration_seconds: float) -> RetainedCaptureProfile:
    """Map an explicit duration onto smoke vs the distinct 72h retain profile."""

    duration = float(duration_seconds)
    if duration == float(RETAINED_72H_SECONDS):
        return retained_72h_profile()
    if duration <= JOINT_SMOKE_MAX_SECONDS:
        return data2a_short_pilot_profile(duration_seconds=duration)
    raise ValueError(
        "Phase A durations are the DATA-2A joint smoke (1-3600s) or the distinct "
        f"72h retain ({RETAINED_72H_SECONDS}s). Intermediate retained windows "
        "need an explicit later profile; smoke limits are not valid for 3 days."
    )


def classify_duration(duration_seconds: float) -> ProfileName:
    return profile_for_duration(duration_seconds).name


def free_space_bytes(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return int(usage.free)


def require_free_space(path: Path, profile: RetainedCaptureProfile) -> None:
    """Fail closed when the artifact filesystem cannot hold the profile + reserve."""

    free = free_space_bytes(path)
    if free < profile.required_free_bytes:
        raise ValueError(
            "insufficient free space for "
            f"{profile.name}: have {free} bytes, need {profile.required_free_bytes} "
            f"(expected {profile.expected_disk_bytes} plus "
            f"{profile.free_space_reserve_bytes} reserve)"
        )


def phase_a_preflight_report(
    *,
    artifact_root: Path | None = None,
    duration_seconds: float = RETAINED_72H_SECONDS,
) -> dict[str, object]:
    """Operator-printable Phase A disk/part preflight. No network, no secrets."""

    profile = profile_for_duration(duration_seconds)
    payload: dict[str, object] = {
        "schema": "phase-a-retained-preflight-v1",
        "profile": profile.name,
        "duration_seconds": profile.duration_seconds,
        "expected_parts_lower_bound_per_lane": profile.expected_parts_lower_bound,
        "expected_disk_bytes": profile.expected_disk_bytes,
        "free_space_reserve_bytes": profile.free_space_reserve_bytes,
        "required_free_bytes": profile.required_free_bytes,
        "heartbeat_is_not_market_data": True,
        "lanes": [
            {
                "lane_id": lane.lane_id,
                "venue": lane.venue,
                "product": lane.product,
                "expected_parquet_bytes": lane.expected_parquet_bytes,
                "notes": lane.notes,
            }
            for lane in profile.lanes
        ],
        "limitations": [
            "Disk figures are operator budgets, not measured 72h rates.",
            "DATA-2A short-pilot limits are not valid for a 72h retain.",
            "ETH/SOL Hyperliquid add-ons are deferred at Phase A start.",
            "Heartbeats and pongs are not market-data validity.",
        ],
    }
    if artifact_root is not None:
        root = artifact_root.expanduser()
        payload["artifact_root"] = str(root)
        if root.exists():
            free = free_space_bytes(root)
            payload["free_bytes"] = free
            payload["free_space_ok"] = free >= profile.required_free_bytes
        else:
            payload["free_bytes"] = None
            payload["free_space_ok"] = False
            payload["free_space_error"] = "artifact_root does not exist"
    return payload


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the Phase A retained-capture disk/part preflight."
    )
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=float(RETAINED_72H_SECONDS),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = phase_a_preflight_report(
        artifact_root=args.artifact_root,
        duration_seconds=float(args.duration_seconds),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def claim_profile_fields(duration_seconds: float) -> Mapping[str, object]:
    try:
        return profile_for_duration(duration_seconds).as_claim_fields()
    except ValueError:
        return {
            "capture_profile": "unscoped_retained",
            "duration_seconds": float(duration_seconds),
            "heartbeat_is_not_market_data": True,
            "twenty_four_seven": False,
        }


if __name__ == "__main__":
    raise SystemExit(main())
