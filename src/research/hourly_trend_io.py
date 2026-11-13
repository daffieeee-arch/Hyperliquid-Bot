"""New hourly execution projection; original regime sources remain unchanged."""

from __future__ import annotations

import hashlib
from pathlib import Path

from research.btc_flow_io import read_bytes
from research.regime_io import BAD_DAYS, INPUT_SHA256, INTERVALS, Segment, Sources
from research.repaired_accounting import Frame
from research.two_models_io import (
    BTC,
    DAY,
    END,
    HOUR,
    MINUTE,
    START,
    check_index,
    daily_months,
    funding_censuses,
)
from research.volume_repair_io import json_value, require

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_hourly_persistent_trend_20261113.md"
STUDY_ID = "btc-hourly-persistent-trend-20261113-v1"


def load(path: Path) -> Sources:
    raw = read_bytes(path, 8 << 20)
    require(hashlib.sha256(raw).hexdigest() == INPUT_SHA256, "original pinned input bytes")
    body = json_value(raw)
    require(isinstance(body, dict), "source index object")
    assert isinstance(body, dict)
    check_index(body, verify_raw=False)
    funding, _ = funding_censuses(body)
    frames: list[dict[int, Frame]] = [{} for _ in INTERVALS]
    extremes: list[dict[int, tuple[float, float]]] = [{} for _ in INTERVALS]
    hours: dict[int, float] = {}
    monthly_rows = []
    for month, local, hourly, minute_extremes, _ in daily_months(body):
        hours.update(hourly)
        selected = 0
        for frame in local:
            instant = frame.timestamp_ms
            if not (instant % HOUR in (0, MINUTE, 2 * MINUTE) or instant in funding):
                continue
            for index, (start, end) in enumerate(INTERVALS):
                if start <= instant <= end:
                    previous = frames[index].get(instant)
                    # Adjacent months repeat their boundary. Only the earlier
                    # month owns its complete closing mark/execution reference.
                    if previous is None:
                        frames[index][instant] = frame
                        selected += 1
                    elif frame.execution_prices:
                        require(previous == frame, "consistent complete shared month boundary")
        for instant, bounds in minute_extremes.items():
            for index, (start, end) in enumerate(INTERVALS):
                if start < instant <= end:
                    require(instant not in extremes[index], "unique minute mark extrema")
                    extremes[index][instant] = bounds
        monthly_rows.append({"month": month, "retained_frames": selected})
    segments = []
    for index, (start, end) in enumerate(INTERVALS):
        ordered = tuple(frames[index][t] for t in sorted(frames[index]))
        require(
            bool(ordered) and ordered[0].timestamp_ms == start and ordered[-1].timestamp_ms == end,
            "complete source segment endpoints",
        )
        require(
            set(range(start, end + 1, HOUR)) <= set(frames[index]),
            "complete regular hourly valuations",
        )
        required_executions = {
            t + delay for t in range(start, end, HOUR) for delay in (MINUTE, 2 * MINUTE)
        } | {end}
        require(required_executions <= set(frames[index]), "every hourly +1m/+2m execution frame")
        require(
            all(
                BTC in frames[index][t].marks
                and (execution := frames[index][t].execution_prices) is not None
                and BTC in execution
                for t in required_executions
            ),
            "every required hourly trade-minute reference and separate mark",
        )
        require(
            {t for t in funding if start <= t <= end} <= set(frames[index]),
            "every exact settlement survives hourly source projection",
        )
        require(
            set(extremes[index]) == set(range(start + MINUTE, end + 1, MINUTE)),
            "complete held-eligible minute extrema",
        )
        require(
            all(
                BTC in frame.marks
                and (
                    frame.timestamp_ms % MINUTE != 0
                    or (frame.execution_prices is not None and BTC in frame.execution_prices)
                )
                for frame in ordered
                if frame.timestamp_ms > start
            ),
            "qualified segment marks and native execution references",
        )
        segments.append(Segment(start, end, ordered, extremes[index]))
    require(
        all(t in hours for t in range(START - 720 * HOUR, END + 1, HOUR)),
        "complete 30-day completed-hour history across month boundaries",
    )
    return Sources(
        tuple(segments),
        hours,
        {
            "input_sha256": INPUT_SHA256,
            "data_status": "exposed_retrospective_no_fresh_oos",
            "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
            "calendar_days": (END - START) // DAY,
            "unavailable_days": len(BAD_DAYS),
            "conditional_admitted_days": sum((b - a) // DAY for a, b in INTERVALS),
            "intervals": [{"start_ms": a, "end_ms": b} for a, b in INTERVALS],
            "hourly_observations": len(hours),
            "monthly_source_qualification": monthly_rows,
            "retained_frames": sum(len(segment.frames) for segment in segments),
            "hourly_decision_clocks": sum((b - a) // HOUR for a, b in INTERVALS),
            "required_plus1_plus2_references": 2 * sum((b - a) // HOUR for a, b in INTERVALS),
            "frame_clock_sha256": hashlib.sha256(
                b"".join(
                    f"{i}:{frame.timestamp_ms}\n".encode()
                    for i, segment in enumerate(segments)
                    for frame in segment.frames
                )
            ).hexdigest(),
            "execution_projection": "every_hour_plus1m_plus2m_and_exact_funding",
            "features_availability_assumed": True,
        },
    )
