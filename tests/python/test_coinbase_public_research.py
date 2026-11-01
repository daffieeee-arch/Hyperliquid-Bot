"""Offline protocol, coverage, deadline and Parquet tests; no venue requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import duckdb
import pytest

from hyperliquid_bot.coinbase_public_research import (
    COINBASE_FEED_PRODUCT,
    COINBASE_PUBLIC_WEBSOCKET_URL,
    CoinbaseCaptureError,
    CoinbaseDataIntegrityError,
    CoinbasePublicResearchCollector,
    CoinbasePublicResearchConfig,
    CoinbaseSessionState,
    CoinbaseSinkError,
    WebSocketConnection,
    _decode,
    _duration,
    run_bounded_capture,
)
from hyperliquid_bot.raw_research import MessageDirection, RawResearchRecord

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "coinbase"
_SOURCE_COMMIT = "b" * 40
_IMAGE_DIGEST = "sha256:" + "c" * 64


def _fixture(name: str) -> bytes:
    return (_FIXTURES / f"{name}.json").read_bytes()


def _document(name: str) -> dict[str, object]:
    return _decode(_fixture(name))


def _state() -> CoinbaseSessionState:
    state = CoinbaseSessionState()
    state.normalize(_document("subscriptions"), 1)
    return state


class MemorySink:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.records: list[RawResearchRecord] = []
        self.fail_on = fail_on
        self.attempts = 0

    async def append(self, record: RawResearchRecord) -> None:
        self.attempts += 1
        if self.attempts == self.fail_on:
            raise RuntimeError("sink detail must not escape")
        self.records.append(record)

    async def aclose(self) -> None:
        return None


class Clock:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 1
        return self.value


class FakeConnection:
    def __init__(
        self,
        frames: Sequence[str | bytes],
        *,
        stop: asyncio.Event | None = None,
        terminal: Exception | None = None,
    ) -> None:
        self.frames = deque(frames)
        self.stop = stop
        self.terminal = terminal
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if self.frames:
            return self.frames.popleft()
        if self.terminal is not None:
            raise self.terminal
        if self.stop is not None:
            self.stop.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@asynccontextmanager
async def _context(connection: FakeConnection) -> AsyncIterator[WebSocketConnection]:
    try:
        yield connection
    finally:
        connection.closed = True


def _markers(sink: MemorySink, channel: str) -> list[dict[str, object]]:
    return [
        _decode(record.payload_bytes)
        for record in sink.records
        if record.direction is MessageDirection.LOCAL and record.channel == channel
    ]


def _all_frames() -> list[bytes]:
    return [
        _fixture(name)
        for name in ("subscriptions", "snapshot", "last_match", "match", "l2update", "heartbeat")
    ]


def test_fixed_public_product_and_non_auth_subscription() -> None:
    assert COINBASE_PUBLIC_WEBSOCKET_URL == "wss://ws-feed.exchange.coinbase.com"
    assert COINBASE_FEED_PRODUCT == "exchange_public_level2_batch_matches_heartbeat"
    assert _state().acknowledged


def test_l2_snapshot_and_absolute_zero_size_preserve_decimal_lexemes() -> None:
    state = _state()
    snapshot = state.normalize(_document("snapshot"), 2).payload
    assert snapshot["action"] == "snapshot"
    assert snapshot["levels"] == [
        {"side": "buy", "price": "100000.000000000000001", "size": "0.100000000000000001"},
        {"side": "sell", "price": "100001.00", "size": "2.000000000000000009"},
    ]
    update = state.normalize(_document("l2update"), 3).payload
    assert update["levels"] == [
        {"side": "buy", "price": "100000.000000000000001", "size": "0"},
        {"side": "sell", "price": "100001.00", "size": "1.000000000000000009"},
    ]
    assert update["event_time"] == "2026-11-01T12:00:00.123456789Z"
    assert update["raw_message_ordinal"] == 3
    assert (update["instrument_type"], update["base_asset"], update["quote_asset"]) == (
        "spot",
        "BTC",
        "USD",
    )


def test_match_anchor_is_not_a_live_trade_and_side_is_maker_side() -> None:
    state = _state()
    assert state.normalize(_document("last_match"), 2).payload["event"] == "trade_anchor"
    result = state.normalize(_document("match"), 3)
    assert result.payload["event"] == "trade"
    assert result.payload["trade_id"] == "9007199254740993"
    assert result.payload["sequence"] == "9007199254741993"
    assert result.payload["maker_side"] == "buy"
    assert result.payload["aggressor_side"] == "sell"
    assert result.quality_events == ()


def test_trade_jump_and_heartbeat_uncertainty_do_not_backfill_or_claim_complete() -> None:
    state = _state()
    state.normalize(_document("last_match"), 2)
    match = _document("match")
    match["trade_id"] = 9007199254740995
    result = state.normalize(match, 3)
    assert result.quality_events == (
        {
            "event": "trade_gap_detected",
            "first_missing_trade_id": "9007199254740993",
            "last_missing_trade_id": "9007199254740994",
            "backfilled": False,
        },
    )
    heartbeat = _document("heartbeat")
    heartbeat["last_trade_id"] = 9007199254740997
    result = state.normalize(heartbeat, 4)
    assert result.quality_events == (
        {
            "event": "trade_coverage_uncertain",
            "heartbeat_last_trade_id": "9007199254740997",
            "last_observed_trade_id": "9007199254740995",
            "backfilled": False,
        },
    )
    # Global sequence is not contiguous on a subset of exchange channels.
    heartbeat["sequence"] = 9007199254749999
    heartbeat["last_trade_id"] = 9007199254740997
    state.normalize(heartbeat, 5)
    heartbeat["last_trade_id"] = 9007199254740996
    with pytest.raises(CoinbaseDataIntegrityError, match="regressed"):
        state.normalize(heartbeat, 6)


@pytest.mark.parametrize("bad", [0, -1, 601, float("inf"), float("nan"), True, "1"])
def test_invalid_duration_fails_before_capture(bad: object) -> None:
    with pytest.raises(ValueError):
        _duration(bad)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 0, -1, True, 61])
def test_config_rejects_invalid_heartbeat_bounds(bad: float) -> None:
    with pytest.raises(ValueError):
        CoinbasePublicResearchConfig(heartbeat_timeout_seconds=bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("price", 100.1),
        ("size", "0"),
        ("price", "NaN"),
        ("price", "1e2"),
        ("price", "-1"),
        ("size", "-0"),
        ("side", ["buy"]),
        ("type", ["match"]),
        ("trade_id", True),
        ("sequence", 2.3),
        ("time", "2026-02-30T12:00:00Z"),
        ("product_id", "ETH-USD"),
    ],
)
def test_invalid_trade_fields_fail_closed(field: str, value: object) -> None:
    state = _state()
    document = _document("match")
    document[field] = value
    with pytest.raises(CoinbaseDataIntegrityError):
        state.normalize(document, 2)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"type":"heartbeat","type":"match"}',
        b'{"price":NaN}',
        b"[]",
        b"\xff",
        b"{",
    ],
)
def test_invalid_json_is_sanitized(payload: bytes) -> None:
    with pytest.raises(CoinbaseDataIntegrityError):
        _decode(payload)


def test_missing_duplicate_snapshot_and_wrong_acknowledgement_fail_closed() -> None:
    with pytest.raises(CoinbaseDataIntegrityError):
        CoinbaseSessionState().normalize(_document("snapshot"), 1)
    state = _state()
    with pytest.raises(CoinbaseDataIntegrityError, match="preceded"):
        state.normalize(_document("l2update"), 2)
    state.normalize(_document("snapshot"), 3)
    with pytest.raises(CoinbaseDataIntegrityError, match="duplicate"):
        state.normalize(_document("snapshot"), 4)
    ack = _document("subscriptions")
    cast(list[dict[str, object]], ack["channels"])[0]["name"] = "full"
    with pytest.raises(CoinbaseDataIntegrityError):
        CoinbaseSessionState().normalize(ack, 1)


@pytest.mark.asyncio
async def test_exact_payloads_source_clocks_and_single_session_lifecycle() -> None:
    stop = asyncio.Event()
    frames = _all_frames()
    connection = FakeConnection(frames, stop=stop)
    sink = MemorySink()
    collector = CoinbasePublicResearchCollector(
        sink,
        connection_factory=lambda: _context(connection),
        utc_ns=Clock(1000),
        monotonic_ns=Clock(2000),
        session_id_factory=lambda: "fixture-session",
    )
    await collector.capture_for(10, stop_event=stop)
    assert connection.closed
    subscription = json.loads(connection.sent[0])
    assert subscription == {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["level2_batch", "matches", "heartbeat"],
    }
    inbound = [r for r in sink.records if r.direction is MessageDirection.INBOUND]
    assert [r.payload_bytes for r in inbound] == frames
    assert {r.channel for r in inbound} == {COINBASE_FEED_PRODUCT}
    assert all(r.received_utc_ns >= 1000 and r.received_monotonic_ns >= 2000 for r in inbound)
    assert all(r.session_id == "fixture-session" for r in sink.records)
    assert [r.message_ordinal for r in sink.records] == list(range(1, len(sink.records) + 1))
    assert _markers(sink, "session")[-1] == {
        "event": "session_stopped",
        "reason": "operator_stop",
        "coverage": "unqualified",
        "complete": False,
    }
    with pytest.raises(ValueError, match="single-use"):
        await collector.capture_for(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [OSError("upstream private detail"), TimeoutError()])
async def test_terminal_transport_or_heartbeat_failure_is_not_retried(terminal: Exception) -> None:
    sink = MemorySink()
    connection = FakeConnection([_fixture("subscriptions")], terminal=terminal)
    collector = CoinbasePublicResearchCollector(
        sink, connection_factory=lambda: _context(connection)
    )
    with pytest.raises(CoinbaseCaptureError) as caught:
        await collector.capture_for(10)
    assert "private detail" not in str(caught.value)
    assert connection.closed
    assert _markers(sink, "data_quality")[-1]["event"] in {
        "transport_gap",
        "heartbeat_or_subscribe_timeout",
    }


@pytest.mark.asyncio
async def test_corrupt_inbound_bytes_are_preserved_before_integrity_failure() -> None:
    sink = MemorySink()
    connection = FakeConnection([_fixture("subscriptions"), b"\xff"])
    collector = CoinbasePublicResearchCollector(
        sink, connection_factory=lambda: _context(connection)
    )
    with pytest.raises(CoinbaseDataIntegrityError):
        await collector.capture_for(10)
    assert any(r.payload_bytes == b"\xff" for r in sink.records)
    assert _markers(sink, "data_quality")[-1]["event"] == "integrity_failure"


@pytest.mark.asyncio
async def test_sink_failure_is_terminal_without_retrying_append() -> None:
    sink = MemorySink(fail_on=3)
    connection = FakeConnection(_all_frames())
    collector = CoinbasePublicResearchCollector(
        sink, connection_factory=lambda: _context(connection)
    )
    with pytest.raises(CoinbaseSinkError, match="append failed"):
        await collector.capture_for(10)
    assert sink.attempts == 3
    assert connection.closed


@pytest.mark.asyncio
async def test_existing_stop_request_never_opens_transport() -> None:
    sink = MemorySink()
    stop = asyncio.Event()
    stop.set()

    def forbidden_connection() -> AbstractAsyncContextManager[WebSocketConnection]:
        raise AssertionError("stopped capture must not open transport")

    collector = CoinbasePublicResearchCollector(
        sink,
        connection_factory=forbidden_connection,
    )
    await collector.capture_for(1, stop_event=stop)
    assert _markers(sink, "session")[-1]["reason"] == "operator_stop"


@pytest.mark.asyncio
async def test_l2_traffic_cannot_hide_missing_heartbeats() -> None:
    sink = MemorySink()
    connection = FakeConnection(
        [
            _fixture("subscriptions"),
            _fixture("snapshot"),
            _fixture("l2update"),
            _fixture("l2update"),
        ]
    )
    collector = CoinbasePublicResearchCollector(
        sink,
        connection_factory=lambda: _context(connection),
        monotonic=Clock(0),
        config=CoinbasePublicResearchConfig(heartbeat_timeout_seconds=5),
    )
    with pytest.raises(CoinbaseCaptureError, match="timed out"):
        await collector.capture_for(10)
    assert _markers(sink, "data_quality")[-1]["event"] == "heartbeat_or_subscribe_timeout"
    assert connection.frames  # Deadline expires while more L2 frames are available.


@pytest.mark.asyncio
@pytest.mark.timing
async def test_duration_bounds_a_hung_connection_and_closes_it() -> None:
    sink = MemorySink()
    closed = asyncio.Event()

    @asynccontextmanager
    async def hung() -> AsyncIterator[WebSocketConnection]:
        try:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
            yield FakeConnection([])
        finally:
            closed.set()

    collector = CoinbasePublicResearchCollector(sink, connection_factory=hung)
    await asyncio.wait_for(collector.capture_for(1), timeout=1.5)
    assert closed.is_set()
    assert _markers(sink, "session")[-1]["reason"] == "duration_reached"


@pytest.mark.asyncio
async def test_create_only_corpus_manifest_parquet_hashes_and_query_views(tmp_path: Path) -> None:
    root = tmp_path / "coinbase-measurement"
    stop = asyncio.Event()
    frames = _all_frames()
    connection = FakeConnection(frames, stop=stop)
    report = await run_bounded_capture(
        output_root=root,
        duration_seconds=10,
        source_commit=_SOURCE_COMMIT,
        image_digest=_IMAGE_DIGEST,
        connection_factory=lambda: _context(connection),
        stop_event=stop,
    )
    assert report["status"] == "bounded_stopped"
    assert report["complete"] is False
    claim = json.loads((root / "capture-claim.json").read_text())
    assert claim["declared_source_commit"] == _SOURCE_COMMIT
    assert claim["declared_image_digest"] == _IMAGE_DIGEST
    assert claim["native_runtime_admitted"] is False
    assert claim["research_admission"] == "separate_corpus_not_registered_for_existing_studies"
    database = duckdb.connect(str(root / "catalog.duckdb"), read_only=True)
    try:
        raw = database.execute("""
            SELECT payload_bytes, payload_sha256 FROM raw_records
            WHERE direction = 'inbound' ORDER BY message_ordinal
        """).fetchall()
        assert [bytes(row[0]) for row in raw] == frames
        assert [row[1] for row in raw] == [hashlib.sha256(frame).hexdigest() for frame in frames]
        trade = database.execute("""
            SELECT trade_id, price, size, maker_side, aggressor_side, event_time,
                received_utc_ns, raw_message_ordinal FROM coinbase_spot_trades
        """).fetchone()
        assert trade is not None
        assert trade[:6] == (
            "9007199254740993",
            "100000.000000000000001",
            "0.100000000000000001",
            "buy",
            "sell",
            "2026-11-01T12:00:00.123456789Z",
        )
        source = database.execute(
            "SELECT received_utc_ns FROM raw_records WHERE message_ordinal = ?", [trade[7]]
        ).fetchone()
        assert source == (trade[6],)
        assert database.execute("SELECT count(*) FROM coinbase_spot_l2_events").fetchone() == (4,)
        assert database.execute("""
            SELECT size FROM coinbase_spot_l2_events
            WHERE action = 'update' AND side = 'buy'
        """).fetchone() == ("0",)
    finally:
        database.close()
    with pytest.raises(FileExistsError):
        await run_bounded_capture(
            output_root=root,
            duration_seconds=10,
            source_commit=_SOURCE_COMMIT,
            image_digest=_IMAGE_DIGEST,
            connection_factory=lambda: _context(connection),
        )


@pytest.mark.asyncio
async def test_failed_capture_retains_a_failed_report_and_inspectable_raw(tmp_path: Path) -> None:
    root = tmp_path / "failed-measurement"
    connection = FakeConnection([_fixture("subscriptions"), b"{invalid"])
    with pytest.raises(CoinbaseDataIntegrityError):
        await run_bounded_capture(
            output_root=root,
            duration_seconds=10,
            source_commit=_SOURCE_COMMIT,
            image_digest=_IMAGE_DIGEST,
            connection_factory=lambda: _context(connection),
        )
    report = json.loads((root / "capture-report.json").read_text())
    assert report["status"] == "failed"
    assert report["failure_class"] == "CoinbaseDataIntegrityError"
    assert report["parquet_files"] >= 1
    assert (root / "catalog.duckdb").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("source,image", [("main", _IMAGE_DIGEST), (_SOURCE_COMMIT, "latest")])
async def test_invalid_provenance_has_no_filesystem_or_transport_effect(
    tmp_path: Path, source: str, image: str
) -> None:
    root = tmp_path / "must-not-exist"
    with pytest.raises(ValueError):
        await run_bounded_capture(
            output_root=root, duration_seconds=1, source_commit=source, image_digest=image
        )
    assert not root.exists()
