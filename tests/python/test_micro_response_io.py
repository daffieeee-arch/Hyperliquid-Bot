from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from research import micro_flow_io as original
from research import micro_response_io as io

BASE = 1_788_220_800_000
S = 1_000_000_000


def row(
    at: int,
    channel: str = "bbo",
    *,
    age_ms: int = 0,
    session: str = "s",
    direction: str = "inbound",
    local: str | None = None,
    trades: list[dict[str, Any]] | None = None,
) -> tuple[Any, ...]:
    utc = (BASE + 300_000) * 1_000_000 + at * S
    body: dict[str, Any] = {
        "channel": channel,
        "data": {
            "coin": "BTC",
            "time": utc // 1_000_000 - age_ms,
            "bbo": [{"px": "10000", "sz": "2"}, {"px": "10001", "sz": "1"}],
            "levels": [[{"px": "10000", "sz": "2"}], [{"px": "10001", "sz": "1"}]],
        },
    }
    if trades is not None:
        body["data"] = trades
    if local:
        body = {"event": local}
        direction = "local"
    raw = json.dumps(body).encode()
    return (
        1,
        "hyperliquid",
        "BTC-PERP",
        channel,
        session,
        at + 1,
        utc,
        at * S,
        direction,
        "text",
        "utf-8",
        raw,
        hashlib.sha256(raw).hexdigest(),
    )


def source(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    monkeypatch.setattr(io, "native_records", lambda capture: iter(rows))
    monkeypatch.setattr(original, "native_records", lambda capture: iter(rows))
    return {"name": "fixture", "group": "development"}


@pytest.mark.parametrize(
    "age,accepted", [(-1001, False), (-1000, True), (1000, True), (2000, True), (2001, False)]
)
def test_native_timestamp_age_uses_milliseconds_and_original_bounds(
    monkeypatch: pytest.MonkeyPatch,
    age: int,
    accepted: bool,
) -> None:
    capture = source(monkeypatch, [row(0, age_ms=age)])
    items = [x for x in io.observations(capture) if x.event is not None]
    assert bool(items) is accepted
    if accepted:
        assert items[0].quote_record()["native_timestamp_age_ms"] == age


def test_streaming_windows_match_old_qualification_and_keep_raw_channel_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        row(0),
        row(1, "l2Book"),
        row(2, local="gap_detected"),
        row(3),
        row(4, local="reconnected"),
        row(5),
        row(6, age_ms=2001),
        row(7),
        row(8, session="new"),
        row(9, session="new"),
    ]
    capture = source(monkeypatch, rows)
    new = io.qualify(capture)
    old = [
        metadata
        for _, _, metadata in original.capture_windows(capture)
        if not metadata.get("quality_only")
    ]
    assert len(new["windows"]) == len(old)
    for a, b in zip(new["windows"], old, strict=True):
        assert {
            k: a[k]
            for k in (
                "start_mono_ns",
                "end_mono_ns",
                "start_utc_ns",
                "end_utc_ns",
                "source_events",
                "end_reason",
            )
        } == {
            k: b[k]
            for k in (
                "start_mono_ns",
                "end_mono_ns",
                "start_utc_ns",
                "end_utc_ns",
                "source_events",
                "end_reason",
            )
        }
    observed = [x for x in io.observations(capture) if x.event is not None]
    assert observed[0].channel == "bbo" and observed[1].channel == "l2Book"
    assert observed[0].source_id != observed[1].source_id
    assert new["all_receipt_channels"]["bbo"] == 9


def test_original_trade_dedup_keeps_exact_identity_without_lru_eviction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trade: dict[str, Any] = {
        "coin": "BTC",
        "time": BASE + 300_000,
        "tid": 1,
        "px": "10000",
        "sz": "1",
        "side": "B",
    }
    capture = source(monkeypatch, [row(0, "trades", trades=[trade, trade])])
    events = [x for x in io.observations(capture) if x.event is not None]
    assert len(events) == 1
    other = dict(trade, tid=2)
    source(monkeypatch, [row(0, "trades", trades=[trade, other])])
    monkeypatch.setattr(io, "MAX_TRADES", 1)
    with pytest.raises(Exception, match="deduplication count/byte"):
        list(io.observations(capture))


def test_metadata_byte_limit_refuses_instead_of_losing_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = source(monkeypatch, [row(0)])
    monkeypatch.setattr(io, "MAX_METADATA_BYTES", 1)
    with pytest.raises(Exception, match="metadata byte bound"):
        io.qualify(capture)


def test_wrong_original_input_hash_fails_before_metadata_reads(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    path.write_text('{"captures":[]}')
    with pytest.raises(Exception, match="pinned original"):
        io.load(path)


def test_original_coverage_pin_cannot_admit_a_sample_or_different_window() -> None:
    with pytest.raises(Exception, match="exact original"):
        io.verify_coverage([])


def funding_fixture(monkeypatch: pytest.MonkeyPatch, times: list[int]) -> dict[str, Any]:
    rows = [{"coin": "BTC", "time": t, "fundingRate": "0.00001"} for t in times]
    monkeypatch.setattr(io, "read_record", lambda source: json.dumps(rows).encode())
    return {
        "funding_sources": [
            {
                "venue": "HYPERLIQUID",
                "rows": len(rows),
                "request": {"coin": "BTC", "startTime": BASE, "endTime": BASE + 3 * 3_600_000},
            }
        ]
    }


def test_funding_census_requires_complete_native_slots_without_oracle_surrogate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = [BASE + i * 3_600_000 for i in range(3)]
    body = funding_fixture(monkeypatch, times)
    assert io.funding_times(body) == tuple(times)


@pytest.mark.parametrize("offsets", [[0, 2], [0, 1, 1], [0, 1, 3]])
def test_missing_duplicate_or_outside_funding_slot_refuses(
    monkeypatch: pytest.MonkeyPatch,
    offsets: list[int],
) -> None:
    body = funding_fixture(monkeypatch, [BASE + i * 3_600_000 for i in offsets])
    with pytest.raises(Exception, match="funding"):
        io.funding_times(body)
