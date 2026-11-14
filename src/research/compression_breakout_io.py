"""Dense native-minute projection for the frozen compression experiment only."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.btc_flow_io import read_bytes
from research.compression_breakout import HourBox
from research.regime_io import BAD_DAYS, INPUT_SHA256, INTERVALS, Segment
from research.two_models_io import (
    BTC,
    DAY,
    END,
    HOUR,
    MINUTE,
    START,
    candles,
    check_index,
    daily_months,
    funding_censuses,
)
from research.volume_repair_io import json_value, require

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_compression_breakout_20261114.md"
STUDY_ID = "btc-compression-breakout-20261114-v1"


@dataclass(frozen=True)
class Sources:
    segments: tuple[Segment, ...]
    hourly: dict[int, HourBox]
    source_summary: dict[str, Any]


def load(path: Path) -> Sources:
    raw = read_bytes(path, 8 << 20)
    require(hashlib.sha256(raw).hexdigest() == INPUT_SHA256, "original pinned input bytes")
    body = json_value(raw)
    require(isinstance(body, dict), "source index object")
    assert isinstance(body, dict)
    check_index(body, verify_raw=False)
    funding, _ = funding_censuses(body)
    hourly = {}
    for row in body["daily_sources"]:
        if row["role"] != "hourly":
            continue
        for bar in candles(row, HOUR):
            t = bar.open_ms + HOUR
            require(t not in hourly, "unique native H1 identity")
            hourly[t] = HourBox(bar.low, bar.high, bar.close)
    require(
        all(t in hourly for t in range(START - 24 * HOUR, END + 1, HOUR)),
        "complete native 25-bar feature history and month continuity",
    )
    frames: list[dict[int, Any]] = [{} for _ in INTERVALS]
    extremes: list[dict[int, tuple[float, float]]] = [{} for _ in INTERVALS]
    months = []
    for month, local, _, minute_extremes, _ in daily_months(body):
        retained = 0
        for frame in local:
            for i, (start, end) in enumerate(INTERVALS):
                if not start <= frame.timestamp_ms <= end:
                    continue
                old = frames[i].get(frame.timestamp_ms)
                if old is None:
                    frames[i][frame.timestamp_ms] = frame
                    retained += 1
                elif frame.execution_prices:
                    require(frame == old, "consistent complete shared month boundary")
        for t, bounds in minute_extremes.items():
            for i, (start, end) in enumerate(INTERVALS):
                if start < t <= end:
                    require(t not in extremes[i], "unique native minute MARK extrema")
                    extremes[i][t] = bounds
        months.append({"month": month, "retained_frames": retained})
    segments = []
    digest = hashlib.sha256()
    for i, (start, end) in enumerate(INTERVALS):
        ordered = tuple(frames[i][t] for t in sorted(frames[i]))
        required = set(range(start, end + 1, MINUTE)) | {t for t in funding if start <= t <= end}
        require(
            set(frames[i]) == required, "all minute boundaries and exact funding; no clock drop"
        )
        require(
            set(extremes[i]) == set(range(start + MINUTE, end + 1, MINUTE)),
            "all held-minute adverse MARK extrema",
        )
        for frame in ordered:
            t = frame.timestamp_ms
            if t > start:
                require(
                    BTC in frame.marks
                    and (
                        t % MINUTE != 0
                        or (frame.execution_prices is not None and BTC in frame.execution_prices)
                    ),
                    "every required native minute execution reference and separate MARK",
                )
            digest.update(f"{i}:{t}\n".encode())
        segments.append(Segment(start, end, ordered, extremes[i]))
    return Sources(
        tuple(segments),
        hourly,
        {
            "input_sha256": INPUT_SHA256,
            "data_status": "exposed_retrospective_no_fresh_oos",
            "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
            "calendar_days": (END - START) // DAY,
            "unavailable_days": len(BAD_DAYS),
            "conditional_admitted_days": sum((b - a) // DAY for a, b in INTERVALS),
            "intervals": [{"start_ms": a, "end_ms": b} for a, b in INTERVALS],
            "hourly_ohlc_observations": len(hourly),
            "monthly_source_qualification": months,
            "retained_frames": sum(len(s.frames) for s in segments),
            "frame_clock_sha256": digest.hexdigest(),
            "execution_projection": "every_native_minute_plus_exact_funding",
            "features_availability_assumed": True,
        },
    )
