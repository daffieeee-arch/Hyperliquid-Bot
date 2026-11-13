"""Native receipt validation and explicit conditional research-window admission."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.btc_flow_io import sealed_parquet
from research.micro_flow import MicroEvent
from research.two_models_io import read_record
from research.volume_repair_io import json_value, require

SECOND = 1_000_000_000
MAX_NATIVE_AGE_MS = 2000
MAX_FUTURE_MS = 1000
COLUMNS = (
    "schema_version",
    "venue",
    "product",
    "channel",
    "session_id",
    "message_ordinal",
    "received_utc_ns",
    "received_monotonic_ns",
    "direction",
    "frame_type",
    "payload_encoding",
    "payload_bytes",
    "payload_sha256",
)


def native_records(capture: dict[str, Any]) -> Iterator[tuple[Any, ...]]:
    ordinal = 0
    previous_mono = previous_utc = -1
    for part in capture["parts"]:
        require(0 < part["bytes"] <= 4 << 20, "bounded native raw part")
        with sealed_parquet(Path(part["path"]), part["sha256"]) as (connection, bound):
            schema = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [bound]
            ).fetchall()
            require({row[0] for row in schema} == set(COLUMNS), "exact native receipt columns")
            query = (
                "SELECT " + ",".join(COLUMNS) + ",file_row_number "
                "FROM read_parquet(?,file_row_number=true) ORDER BY file_row_number"
            )
            cursor = connection.execute(query, [bound])
            part_row = 0
            while rows := cursor.fetchmany(1024):
                for original in rows:
                    require(original[-1] == part_row, "native physical row order")
                    part_row += 1
                    row = original[:-1]
                    (
                        version,
                        venue,
                        product,
                        _,
                        session,
                        sequence,
                        utc,
                        mono,
                        _,
                        _,
                        encoding,
                        payload,
                        digest,
                    ) = row
                    require(
                        version == 1 and venue == "hyperliquid" and product == "BTC-PERP",
                        "native receipt identity",
                    )
                    require(
                        type(sequence) is int and sequence == ordinal + 1,
                        "complete receipt ordinals",
                    )
                    require(
                        type(utc) is int
                        and utc >= 0
                        and utc >= previous_utc
                        and type(mono) is int
                        and mono >= 0
                        and mono >= previous_mono,
                        "ordered native receipt clocks",
                    )
                    require(isinstance(session, str) and bool(session), "native receipt session")
                    require(encoding in ("utf-8", "utf-8-json"), "native text encoding")
                    require(
                        isinstance(payload, bytes) and len(payload) <= 1 << 20,
                        "bounded original payload",
                    )
                    require(hashlib.sha256(payload).hexdigest() == digest, "native payload SHA-256")
                    ordinal, previous_utc, previous_mono = sequence, utc, mono
                    yield row
    for metadata in capture["metadata"]:
        read_record(metadata)


def positive(value: Any) -> Decimal:
    require(isinstance(value, str), "native decimal string")
    number = Decimal(value)
    require(number.is_finite() and number > 0, "positive native price/size")
    return number


def book_event(
    channel: str,
    data: dict[str, Any],
    mono: int,
    utc: int,
    session: str,
) -> MicroEvent | None:
    require(type(data.get("time")) is int and data["time"] >= 0, "native book timestamp")
    age = utc // 1_000_000 - data["time"]
    if not -MAX_FUTURE_MS <= age <= MAX_NATIVE_AGE_MS:
        return None
    if channel == "bbo":
        levels = data.get("bbo")
        require(isinstance(levels, list) and len(levels) == 2, "native BBO sides")
        assert isinstance(levels, list)
        bid, ask = levels
    else:
        levels = data.get("levels")
        require(isinstance(levels, list) and len(levels) == 2, "native full L2 sides")
        assert isinstance(levels, list)
        bid, ask = (side[0] if side else None for side in levels)
    if bid is None or ask is None:
        return None
    require(isinstance(bid, dict) and isinstance(ask, dict), "native best levels")
    bid_px, ask_px = positive(bid["px"]), positive(ask["px"])
    bid_sz, ask_sz = positive(bid["sz"]), positive(ask["sz"])
    require(bid_px <= ask_px, "uncrossed native book")
    return MicroEvent(mono, utc, data["time"], "book", session, bid_px, ask_px, bid_sz, ask_sz)


def capture_windows(
    capture: dict[str, Any],
) -> Iterator[tuple[str, list[MicroEvent], dict[str, Any]]]:
    """Window ends come from source qualification, never subsequent returns.

    These are scheduled *research* boundaries around unavailable data, not a
    claim that an online strategy knows an impending disconnect or queue lag.
    The caller must report conditional coverage and retain every exclusion.
    """
    events: list[MicroEvent] = []
    counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    first_clock: tuple[int, int] | None = None
    last_clock = (0, 0)
    session = ""
    blackout = False
    index = 0
    seen_trades: set[tuple[int, str, int]] = set()

    def take(reason: str) -> tuple[str, list[MicroEvent], dict[str, Any]] | None:
        nonlocal events, counts, index
        totals.update(counts)
        if not events:
            counts = Counter()
            return None
        index += 1
        value = (
            f"{capture['name']}-w{index:05d}",
            events,
            {
                "capture": capture["name"],
                "group": capture["group"],
                "end_reason": reason,
                "counts": dict(counts),
                "conditional_window": True,
                "source_events": len(events),
                "start_mono_ns": events[0].mono_ns,
                "end_mono_ns": events[-1].mono_ns,
                "start_utc_ns": events[0].utc_ns,
                "end_utc_ns": events[-1].utc_ns,
            },
        )
        events, counts = [], Counter()
        return value

    for row in native_records(capture):
        _, _, _, channel, current_session, _, utc, mono, direction, _, _, payload, _ = row
        if first_clock is None:
            first_clock = (mono, utc)
        last_clock = (mono, utc)
        if current_session != session:
            if value := take("session_boundary"):
                yield value
            session, blackout = current_session, False
        body = json_value(payload)
        require(isinstance(body, dict), "native JSON object frame")
        assert isinstance(body, dict)
        if direction == "local":
            kind = body.get("event")
            if kind in {"gap_detected", "market_data_stale_despite_heartbeat", "disconnected"}:
                counts["gap_or_stale_markers"] += 1
                if value := take(str(kind)):
                    yield value
                blackout = True
            elif kind in {"session_started", "reconnected", "market_data_recovered"}:
                blackout = False
            continue
        if direction != "inbound" or blackout or channel not in {"bbo", "l2Book", "trades"}:
            continue
        require(body.get("channel") == channel, "native channel binding")
        data = body.get("data")
        if channel in {"bbo", "l2Book"}:
            require(isinstance(data, dict), "native book object")
            assert isinstance(data, dict)
            if data.get("coin") != "BTC":
                counts["other_coin_frames"] += 1
                continue
            event = book_event(channel, data, mono, utc, session)
            if event is None:
                counts["stale_or_unavailable_native_book"] += 1
                if value := take("native_book_unavailable"):
                    yield value
                continue
            events.append(event)
            counts[channel] += 1
        else:
            require(isinstance(data, list), "native public trade batch")
            assert isinstance(data, list)
            for trade in data:
                require(isinstance(trade, dict), "native public trade")
                if trade.get("coin") != "BTC":
                    counts["other_coin_trades"] += 1
                    continue
                require(
                    type(trade.get("time")) is int and type(trade.get("tid")) is int,
                    "native trade identity",
                )
                instant = trade["time"]
                age = utc // 1_000_000 - instant
                if not -MAX_FUTURE_MS <= age <= MAX_NATIVE_AGE_MS:
                    counts["stale_or_snapshot_trades"] += 1
                    continue
                key = (instant, "BTC", trade["tid"])
                if key in seen_trades:
                    counts["duplicate_native_trades"] += 1
                    continue
                seen_trades.add(key)
                require(trade.get("side") in {"A", "B"}, "native B/A trade sign")
                events.append(
                    MicroEvent(
                        mono,
                        utc,
                        instant,
                        "trade",
                        session,
                        trade_price=positive(trade["px"]),
                        trade_quantity=positive(trade["sz"]),
                        trade_side=1 if trade["side"] == "B" else -1,
                    )
                )
                counts["trades"] += 1
    if value := take("closed_capture_end"):
        yield value

    require(first_clock is not None, "nonempty raw capture")
    assert first_clock is not None
    yield (
        f"{capture['name']}-quality",
        [],
        {
            "capture": capture["name"],
            "group": capture["group"],
            "quality_only": True,
            "counts": dict(totals),
            "start_mono_ns": first_clock[0],
            "end_mono_ns": last_clock[0],
            "start_utc_ns": first_clock[1],
            "end_utc_ns": last_clock[1],
            "conditional_windows": index,
        },
    )
