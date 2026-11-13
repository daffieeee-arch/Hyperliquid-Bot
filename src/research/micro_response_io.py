"""Streaming source-only admission for the fixed native micro response study."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

from research.btc_flow_io import read_bytes
from research.micro_flow import MicroEvent
from research.micro_flow_io import book_event, native_records, positive
from research.two_models_io import check_index, read_record
from research.volume_repair_io import json_value, require

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_micro_response_cost_20261113.md"
STUDY_ID = "micro-response-cost-20261113-v1"
INPUT_SHA256 = "ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518"
COVERAGE_SHA256 = "d7e6dd8b3abb9990a12471b713697a66cf4af1caa3341532fcd507907ff4a320"
MAX_TRADES = 3_000_000
MAX_TRADE_BYTES = 384 << 20
MAX_WINDOWS = 10_000
MAX_METADATA_BYTES = 8 << 20
GAPS = {"gap_detected", "market_data_stale_despite_heartbeat", "disconnected"}
RECOVERY = {"session_started", "reconnected", "market_data_recovered"}


@dataclass(frozen=True, slots=True)
class Observation:
    window: int
    mono_ns: int
    utc_ns: int
    channel: str
    source_id: str
    payload_sha256: str
    last_inbound_ns: int | None
    event: MicroEvent | None = None
    end_reason: str | None = None
    level_counts: tuple[int, int] | None = None

    def quote_record(self) -> dict[str, Any]:
        require(self.event is not None and self.event.kind == "book", "observed book identity")
        assert self.event is not None
        return {
            "mono_ns": self.mono_ns,
            "utc_ns": self.utc_ns,
            "venue_ms": self.event.venue_ms,
            "channel": self.channel,
            "source_id": self.source_id,
            "payload_sha256": self.payload_sha256,
            "native_timestamp_age_ms": self.utc_ns // 1_000_000 - self.event.venue_ms,
            "level_counts": self.level_counts,
            "bid": str(self.event.bid),
            "ask": str(self.event.ask),
            "bid_size": str(self.event.bid_size),
            "ask_size": str(self.event.ask_size),
        }


def make_observation(
    row: tuple[Any, ...],
    last_inbound: int | None,
    window: int,
    event: MicroEvent | None = None,
    reason: str | None = None,
    levels: tuple[int, int] | None = None,
    index: int = 0,
) -> Observation:
    return Observation(
        window,
        row[7],
        row[6],
        row[3],
        f"{row[4]}:{row[5]}:{index}",
        row[12],
        last_inbound,
        event,
        reason,
        levels,
    )


def observations(capture: dict[str, Any]) -> Iterator[Observation]:
    """Keep the old conditional windows, with clocks/channel identity retained.

    Receipt-only records advance liveness and diagnostic clocks, never features
    or quote freshness. Boundaries end at the preceding accepted market event.
    """
    session = ""
    blackout = active = False
    window = 0
    last_inbound: int | None = None
    seen: set[tuple[int, int]] = set()
    for row in native_records(capture):
        _, _, _, channel, current, ordinal, utc, mono, direction, _, _, payload, digest = row
        if direction == "inbound":
            last_inbound = mono
        source = f"{current}:{ordinal}"

        record = partial(make_observation, row, last_inbound)

        if current != session:
            if active:
                yield record(window, reason="session_boundary")
            active, blackout, session = False, False, current
        body = json_value(payload)
        require(isinstance(body, dict), "native JSON object frame")
        assert isinstance(body, dict)
        if direction == "local":
            kind = body.get("event")
            if kind in GAPS:
                if active:
                    yield record(window, reason=str(kind))
                active, blackout = False, True
            elif kind in RECOVERY:
                blackout = False
        elif direction == "inbound" and not blackout and channel in {"bbo", "l2Book", "trades"}:
            require(body.get("channel") == channel, "native channel binding")
            data = body.get("data")
            if channel in {"bbo", "l2Book"}:
                require(isinstance(data, dict), "native book object")
                assert isinstance(data, dict)
                if data.get("coin") == "BTC":
                    event = book_event(channel, data, mono, utc, session)
                    if event is None:
                        if active:
                            yield record(window, reason="native_book_unavailable")
                        active = False
                    else:
                        if not active:
                            window += 1
                            active = True
                        levels = (1, 1) if channel == "bbo" else tuple(map(len, data["levels"]))
                        require(len(levels) == 2, "native two-sided observation")
                        yield record(window, event, levels=(levels[0], levels[1]))
            else:
                require(isinstance(data, list), "native trade batch")
                assert isinstance(data, list)
                for index, trade in enumerate(data):
                    require(isinstance(trade, dict), "native trade object")
                    if trade.get("coin") != "BTC":
                        continue
                    require(
                        type(trade.get("time")) is int and type(trade.get("tid")) is int,
                        "native trade identity",
                    )
                    stamp = trade["time"]
                    if not -1000 <= utc // 1_000_000 - stamp <= 2000:
                        continue
                    key = (stamp, trade["tid"])
                    if key in seen:
                        continue
                    seen.add(key)
                    require(
                        len(seen) <= MAX_TRADES and len(seen) * 128 <= MAX_TRADE_BYTES,
                        "deduplication count/byte bound; no eviction permitted",
                    )
                    require(trade.get("side") in {"A", "B"}, "native trade side")
                    event = MicroEvent(
                        mono,
                        utc,
                        stamp,
                        "trade",
                        session,
                        trade_price=positive(trade["px"]),
                        trade_quantity=positive(trade["sz"]),
                        trade_side=1 if trade["side"] == "B" else -1,
                    )
                    if not active:
                        window += 1
                        active = True
                    yield record(window, event, index=index)
        # This is after market events: equal-clock decisions see the same first
        # accepted event as the original replay, not a future same-packet trade.
        yield record(window if active else 0)
    if active:
        yield Observation(
            window,
            mono,
            utc,
            channel,
            source,
            digest,
            last_inbound,
            end_reason="closed_capture_end",
        )


def qualify(capture: dict[str, Any]) -> dict[str, Any]:
    """A first source-only pass; no pressure, prices-as-labels or financial math."""
    windows: dict[int, dict[str, Any]] = {}
    channels: Counter[str] = Counter()
    receipts: Counter[str] = Counter()
    for item in observations(capture):
        if item.event is None and item.end_reason is None:
            receipts[item.channel] += 1
        if item.event is not None:
            event = item.event
            if item.window not in windows:
                require(len(windows) < MAX_WINDOWS, "window metadata count bound")
                windows[item.window] = {
                    "id": item.window,
                    "start_mono_ns": item.mono_ns,
                    "start_utc_ns": item.utc_ns,
                    "first_venue_ms": event.venue_ms,
                    "source_events": 0,
                }
            window = windows[item.window]
            window.update(
                end_mono_ns=item.mono_ns, end_utc_ns=item.utc_ns, last_venue_ms=event.venue_ms
            )
            window["source_events"] += 1
            channels[item.channel] += 1
        if item.end_reason:
            windows[item.window]["end_reason"] = item.end_reason
    require(bool(windows), "nonempty source-qualified capture")
    result = {
        "capture": capture["name"],
        "group": capture["group"],
        "windows": list(windows.values()),
        "channels": dict(channels),
        "all_receipt_channels": dict(receipts),
        "source_mode": "original_pinned_legacy_slow_request_not_fast",
        "observation_ns": sum(w["end_mono_ns"] - w["start_mono_ns"] for w in windows.values()),
        "conditional_windows": True,
    }
    require(len(json.dumps(result).encode()) <= MAX_METADATA_BYTES, "window metadata byte bound")
    return result


def verify_coverage(captures: list[dict[str, Any]]) -> str:
    rows = [
        [
            capture["capture"],
            capture["group"],
            w["id"],
            w["source_events"],
            w["start_mono_ns"],
            w["end_mono_ns"],
            w["start_utc_ns"],
            w["end_utc_ns"],
            w["end_reason"],
        ]
        for capture in captures
        for w in capture["windows"]
    ]
    digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
    require(
        len(rows) == 3159 and digest == COVERAGE_SHA256,
        "exact original 3159 conditional windows before labels",
    )
    return digest


def load(path: Path) -> dict[str, Any]:
    raw = read_bytes(path, 8 << 20)
    require(hashlib.sha256(raw).hexdigest() == INPUT_SHA256, "pinned original eleven-capture index")
    body = json_value(raw)
    require(isinstance(body, dict), "source index object")
    assert isinstance(body, dict)
    check_index(body, verify_raw=False)
    require(sum(len(c["parts"]) for c in body["captures"]) == 9323, "all original source parts")
    return body


def verify_sources(body: dict[str, Any]) -> None:
    check_index(body, verify_raw=False)
    for capture in body["captures"]:
        for part in capture["parts"]:
            read_record(part, 4 << 20)
    for row in body["funding_sources"]:
        read_record(row)


def funding_times(body: dict[str, Any]) -> tuple[int, ...]:
    """Only native HL event times/rates; no invented oracle or funding cash."""
    events: dict[int, Decimal] = {}
    hour = 3_600_000
    for source in body["funding_sources"]:
        if source["venue"] != "HYPERLIQUID":
            continue
        rows = json_value(read_record(source))
        request = source["request"]
        require(isinstance(rows, list) and 0 < len(rows) < 500, "uncapped HL funding census")
        assert isinstance(rows, list)
        require(
            request["coin"] == "BTC" and len(rows) == source["rows"], "funding request identity"
        )
        times = []
        for row in rows:
            stamp = row["time"]
            rate = Decimal(row["fundingRate"])
            require(
                row["coin"] == "BTC"
                and type(stamp) is int
                and request["startTime"] <= stamp <= request["endTime"]
                and rate.is_finite()
                and (stamp not in events or events[stamp] == rate),
                "native funding time/rate identity",
            )
            events[stamp] = rate
            times.append(stamp)
        expected = set(
            range(
                (request["startTime"] + hour - 1) // hour, (request["endTime"] + hour - 1) // hour
            )
        )
        require(
            times == sorted(set(times)) and {t // hour for t in times} == expected,
            "complete native hourly funding slots",
        )
        require(
            all(abs(t - round(t / hour) * hour) < 60_000 for t in times),
            "actual funding calendar clock bound",
        )
    require(bool(events), "native funding census required")
    return tuple(sorted(events))


def funding_ranges(body: dict[str, Any]) -> tuple[tuple[int, int], ...]:
    """Keep qualified requests separate; the caller validates funding_times first."""
    return tuple(
        (row["request"]["startTime"], row["request"]["endTime"])
        for row in body["funding_sources"]
        if row["venue"] == "HYPERLIQUID"
    )
