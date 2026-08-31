"""Deterministic offline DATA-1C tests for public OKX BTC-USDT-SWAP capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import duckdb
import pytest
from websockets.exceptions import PayloadTooBig

from hyperliquid_bot.okx_public_research import (
    _BUSINESS_SUBSCRIPTIONS,
    OKX_EEA_BUSINESS_WEBSOCKET_URL,
    OKX_EEA_PUBLIC_WEBSOCKET_URL,
    OKX_RESEARCH_INDEX,
    OKX_RESEARCH_PRODUCT,
    OkxDataIntegrityError,
    OkxPublicResearchCollector,
    OkxPublicResearchConfig,
    OkxSinkError,
    WebSocketConnection,
    _BooksState,
    _normalize_market_frame,
)
from hyperliquid_bot.parquet_research import (
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import (
    MessageDirection,
    RawResearchRecord,
    capture_application_payload,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "okx"
_CHANNEL_REQUEST_IDS = {
    "bbo-tbt": "d1cbbo",
    "books": "d1cbooks",
    "funding-rate": "d1cfunding",
    "open-interest": "d1coi",
    "mark-price": "d1cmark",
    "index-tickers": "d1cindex",
    "trades-all": "d1ctrades",
}
_PUBLIC_CHANNELS = (
    "bbo-tbt",
    "books",
    "funding-rate",
    "open-interest",
    "mark-price",
    "index-tickers",
)
_MARKET_FIXTURES = {
    "trades-all": "trades_all_frame.json",
    "bbo-tbt": "bbo_tbt_frame.json",
    "books-snapshot": "books_snapshot.json",
    "books-update": "books_update.json",
    "funding-rate": "funding_rate_frame.json",
    "open-interest": "open_interest_frame.json",
    "mark-price": "mark_price_frame.json",
    "index-tickers": "index_tickers_frame.json",
}


def _fixture_text(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _document(payload: str | bytes) -> dict[str, object]:
    loaded = json.loads(
        payload,
        parse_float=str,
        parse_int=str,
    )
    if type(loaded) is not dict:
        raise AssertionError("fixture root must be an object")
    return cast(dict[str, object], loaded)


def _fixture_document(name: str) -> dict[str, object]:
    return _document((_FIXTURE_DIR / name).read_bytes())


def _copy_document(document: dict[str, object]) -> dict[str, object]:
    return _document(json.dumps(document, separators=(",", ":")))


def _book_update(
    *,
    prev_seq_id: int,
    seq_id: int,
    asks: list[list[str]] | None = None,
    bids: list[list[str]] | None = None,
) -> dict[str, object]:
    document = _fixture_document("books_update.json")
    data = cast(list[object], document["data"])
    item = cast(dict[str, object], data[0])
    item["prevSeqId"] = str(prev_seq_id)
    item["seqId"] = str(seq_id)
    item["asks"] = [] if asks is None else asks
    item["bids"] = [] if bids is None else bids
    return document


def _ack(channel: str, *, connection_id: str) -> str:
    instrument_id = OKX_RESEARCH_INDEX if channel == "index-tickers" else OKX_RESEARCH_PRODUCT
    return json.dumps(
        {
            "id": _CHANNEL_REQUEST_IDS[channel],
            "event": "subscribe",
            "arg": {"channel": channel, "instId": instrument_id},
            "connId": connection_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _all_acknowledgements(channels: Sequence[str], connection_id: str) -> list[str]:
    return [_ack(channel, connection_id=connection_id) for channel in channels]


class MemorySink:
    def __init__(self, *, fail_on_append: int | None = None) -> None:
        self.records: list[RawResearchRecord] = []
        self._fail_on_append = fail_on_append
        self.append_attempts = 0

    async def append(self, record: RawResearchRecord) -> None:
        self.append_attempts += 1
        if self.append_attempts == self._fail_on_append:
            raise RuntimeError("injected sink failure")
        self.records.append(record)

    async def aclose(self) -> None:
        return None


class Counter:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 1
        return self.value


class CompletionTracker:
    def __init__(self, stop_event: asyncio.Event, expected: set[str]) -> None:
        self._stop_event = stop_event
        self._expected = expected
        self.completed: set[str] = set()

    def mark(self, stream: str) -> None:
        self.completed.add(stream)
        if self.completed == self._expected:
            self._stop_event.set()


class FakeConnection:
    def __init__(
        self,
        messages: Sequence[str | bytes],
        *,
        on_last: Callable[[], None] | None = None,
        disconnect_when_empty: bool = False,
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._disconnect_when_empty = disconnect_when_empty
        self._never = asyncio.Event()
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            if self._disconnect_when_empty:
                raise ConnectionError("scripted disconnect")
            await self._never.wait()
            raise AssertionError("unreachable")
        message = self._messages.popleft()
        if not self._messages and self._on_last is not None:
            self._on_last()
        return message


@asynccontextmanager
async def _fake_context(connection: WebSocketConnection) -> AsyncIterator[WebSocketConnection]:
    yield connection


class ScriptedConnectionFactory:
    def __init__(self, connections: Sequence[WebSocketConnection]) -> None:
        self._connections = deque(connections)
        self.calls = 0

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls += 1
        if not self._connections:
            raise AssertionError("collector requested an unexpected connection")
        return _fake_context(self._connections.popleft())


class SessionIds:
    def __init__(self) -> None:
        self.counts = {"public": 0, "business": 0}

    def __call__(self, stream: str) -> str:
        self.counts[stream] += 1
        return f"{stream}-session-{self.counts[stream]}"


def _marker_documents(
    records: Sequence[RawResearchRecord],
    *,
    channel: str | None = None,
) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for record in records:
        if record.direction is not MessageDirection.LOCAL:
            continue
        if channel is not None and record.channel != channel:
            continue
        documents.append(_document(record.payload_bytes))
    return documents


def test_eea_endpoints_and_fixed_credentialless_scope() -> None:
    assert OKX_EEA_PUBLIC_WEBSOCKET_URL == "wss://wseea.okx.com:8443/ws/v5/public"
    assert OKX_EEA_BUSINESS_WEBSOCKET_URL == "wss://wseea.okx.com:8443/ws/v5/business"
    assert OKX_RESEARCH_PRODUCT == "BTC-USDT-SWAP"
    assert OKX_RESEARCH_INDEX == "BTC-USDT"


def test_books_accepts_snapshot_gap_skip_idle_and_documented_maintenance_reset() -> None:
    state = _BooksState()

    snapshot = state.normalize(_fixture_document("books_snapshot.json"), 10)
    assert snapshot["sequence_event"] == "snapshot"
    assert snapshot["prev_seq_id"] == "-1"
    assert snapshot["seq_id"] == "1000"
    assert snapshot["checksum_wire"] == "0"

    skipped = state.normalize(_fixture_document("books_update.json"), 11)
    assert skipped["sequence_event"] == "update"
    assert skipped["prev_seq_id"] == "1000"
    assert skipped["seq_id"] == "1007"

    no_update = state.normalize(_book_update(prev_seq_id=1007, seq_id=1007), 12)
    assert no_update["sequence_event"] == "no_update"
    assert no_update["events"] == []

    reset = state.normalize(
        _book_update(
            prev_seq_id=1007,
            seq_id=3,
            bids=[["60000.050000000000000001", "0.500000000000000001", "0", "1"]],
        ),
        13,
    )
    assert reset["sequence_event"] == "maintenance_reset"
    assert state.last_seq_id == 3

    after_reset = state.normalize(
        _book_update(
            prev_seq_id=3,
            seq_id=5,
            asks=[["60000.350000000000000001", "0.700000000000000001", "0", "2"]],
        ),
        14,
    )
    assert after_reset["sequence_event"] == "update"
    assert state.last_seq_id == 5


def test_books_limits_snapshot_depth_but_accepts_larger_incremental_change_batch() -> None:
    levels = [[f"60000.{index:03d}", "1", "0", "1"] for index in range(401)]

    oversized_snapshot = _fixture_document("books_snapshot.json")
    snapshot_item = cast(dict[str, object], cast(list[object], oversized_snapshot["data"])[0])
    snapshot_item["bids"] = levels
    with pytest.raises(OkxDataIntegrityError, match="side schema"):
        _BooksState().normalize(oversized_snapshot, 1)

    state = _BooksState()
    state.normalize(_fixture_document("books_snapshot.json"), 2)
    update = state.normalize(
        _book_update(prev_seq_id=1000, seq_id=1001, bids=levels),
        3,
    )

    assert update["sequence_event"] == "update"
    assert len(cast(list[object], update["events"])) == 401


@pytest.mark.parametrize(
    ("first_update", "second_update", "quality_event"),
    [
        (
            _book_update(prev_seq_id=1000, seq_id=1000, bids=[["60000", "1", "0", "1"]]),
            None,
            "sequence_error",
        ),
        (
            _fixture_document("books_update.json"),
            _fixture_document("books_update.json"),
            "sequence_gap",
        ),
        (_book_update(prev_seq_id=999, seq_id=1008), None, "sequence_gap"),
    ],
    ids=("non-empty-equal", "duplicate-frame", "previous-id-mismatch"),
)
def test_books_duplicate_or_out_of_order_updates_fail_closed_and_clear_state(
    first_update: dict[str, object],
    second_update: dict[str, object] | None,
    quality_event: str,
) -> None:
    state = _BooksState()
    state.normalize(_fixture_document("books_snapshot.json"), 1)
    if second_update is not None:
        state.normalize(_copy_document(first_update), 2)
        failing = second_update
    else:
        failing = first_update

    with pytest.raises(OkxDataIntegrityError) as captured:
        state.normalize(_copy_document(failing), 3)

    assert captured.value.quality_event == quality_event
    assert not state.has_snapshot
    assert state.last_seq_id is None


def test_books_requires_snapshot_and_rejects_second_snapshot_without_reconnect() -> None:
    state = _BooksState()
    with pytest.raises(OkxDataIntegrityError) as captured:
        state.normalize(_fixture_document("books_update.json"), 1)
    assert captured.value.quality_event == "snapshot_missing"
    assert not state.has_snapshot

    state.normalize(_fixture_document("books_snapshot.json"), 2)
    with pytest.raises(OkxDataIntegrityError, match="snapshot boundary") as second:
        state.normalize(_fixture_document("books_snapshot.json"), 3)
    assert second.value.quality_event == "sequence_error"
    assert not state.has_snapshot

    fresh_reconnect_state = _BooksState()
    fresh_reconnect_state.normalize(_fixture_document("books_snapshot.json"), 4)
    assert fresh_reconnect_state.has_snapshot


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(action="unexpected"),
        lambda document: cast(dict[str, object], document["arg"]).update(channel="books-rpi"),
        lambda document: cast(dict[str, object], document["arg"]).update(instId="ETH-USDT-SWAP"),
        lambda document: cast(dict[str, object], cast(list[object], document["data"])[0]).update(
            checksum="1"
        ),
        lambda document: cast(dict[str, object], cast(list[object], document["data"])[0]).pop(
            "asks"
        ),
    ],
    ids=(
        "action",
        "channel",
        "instrument",
        "checksum-is-not-an-integrity-signal",
        "missing-side",
    ),
)
def test_books_schema_and_identity_fail_closed(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    document = _fixture_document("books_snapshot.json")
    mutation(document)

    state = _BooksState()
    with pytest.raises(OkxDataIntegrityError):
        state.normalize(document, 1)
    assert not state.has_snapshot


def test_all_normalizers_preserve_decimal_text_without_float_conversion() -> None:
    cases = (
        ("trades-all", "trades_all_frame.json", None),
        ("bbo-tbt", "bbo_tbt_frame.json", None),
        ("books", "books_snapshot.json", _BooksState()),
        ("funding-rate", "funding_rate_frame.json", None),
        ("open-interest", "open_interest_frame.json", None),
        ("mark-price", "mark_price_frame.json", None),
        ("index-tickers", "index_tickers_frame.json", None),
    )
    rendered: list[str] = []
    for ordinal, (channel, fixture, state) in enumerate(cases, start=1):
        normalized = _normalize_market_frame(
            channel,
            _fixture_document(fixture),
            ordinal,
            state,
        )
        rendered.append(json.dumps(normalized, separators=(",", ":"), sort_keys=True))

    combined = "\n".join(rendered)
    for exact_decimal in (
        "60000.123456789012345678",
        "0.000000010000000001",
        "60000.200000000000000000",
        "1.200000000000000001",
        "0.000123456789012345",
        "25373011.000000000000000001",
        "60000.150000000000000001",
        "60000.140000000000000001",
    ):
        assert exact_decimal in combined


@pytest.mark.parametrize("extra_field", ["action", "prevSeqId", "checksum"])
def test_bbo_rejects_incremental_books_fields(extra_field: str) -> None:
    document = _fixture_document("bbo_tbt_frame.json")
    if extra_field == "action":
        document[extra_field] = "snapshot"
    else:
        data = cast(list[object], document["data"])
        cast(dict[str, object], data[0])[extra_field] = "0"

    with pytest.raises(OkxDataIntegrityError, match="bbo-tbt"):
        _normalize_market_frame("bbo-tbt", document, 1, None)


@pytest.mark.parametrize(
    ("channel", "fixture"),
    [
        ("trades-all", "trades_all_frame.json"),
        ("bbo-tbt", "bbo_tbt_frame.json"),
        ("funding-rate", "funding_rate_frame.json"),
        ("open-interest", "open_interest_frame.json"),
        ("mark-price", "mark_price_frame.json"),
        ("index-tickers", "index_tickers_frame.json"),
    ],
)
def test_non_book_channels_reject_wrong_instrument_identity(channel: str, fixture: str) -> None:
    document = _fixture_document(fixture)
    cast(dict[str, object], document["arg"])["instId"] = "ETH-USDT-SWAP"

    with pytest.raises(OkxDataIntegrityError, match="identity"):
        _normalize_market_frame(channel, document, 1, None)


@pytest.mark.asyncio
async def test_callback_clocks_run_immediately_after_recv_returns() -> None:
    trace: list[str] = []

    class TimingConnection:
        async def send(self, message: str | bytes) -> None:
            del message

        async def recv(self) -> str | bytes:
            trace.append("recv-return")
            asyncio.get_running_loop().call_soon(trace.append, "scheduler-turn")
            return _fixture_text("bbo_tbt_frame.json")

    def utc_ns() -> int:
        trace.append("utc-clock")
        return 101

    def monotonic_ns() -> int:
        trace.append("monotonic-clock")
        return 202

    collector = OkxPublicResearchCollector(
        MemorySink(),
        public_connection_factory=ScriptedConnectionFactory([]),
        business_connection_factory=ScriptedConnectionFactory([]),
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
    )

    captured = await collector._receive_or_stop(TimingConnection(), asyncio.Event(), 1.0)
    await asyncio.sleep(0)

    assert captured is not None
    assert trace == ["recv-return", "utc-clock", "monotonic-clock", "scheduler-turn"]
    assert captured.received_utc_ns == 101
    assert captured.received_monotonic_ns == 202


@pytest.mark.asyncio
async def test_exact_raw_parquet_roundtrip_and_okx_duckdb_views(tmp_path: Path) -> None:
    stop_event = asyncio.Event()
    tracker = CompletionTracker(stop_event, {"public", "business"})
    no_update_payload = json.dumps(
        _book_update(prev_seq_id=1007, seq_id=1007),
        separators=(",", ":"),
    )
    public = FakeConnection(
        [
            *_all_acknowledgements(_PUBLIC_CHANNELS, "public-connection"),
            _fixture_text("bbo_tbt_frame.json"),
            _fixture_text("books_snapshot.json"),
            _fixture_text("books_update.json"),
            no_update_payload,
            _fixture_text("funding_rate_frame.json"),
            _fixture_text("open_interest_frame.json"),
            _fixture_text("mark_price_frame.json"),
            _fixture_text("index_tickers_frame.json"),
        ],
        on_last=lambda: tracker.mark("public"),
    )
    business = FakeConnection(
        [
            _ack("trades-all", connection_id="business-connection"),
            _fixture_text("trades_all_frame.json"),
        ],
        on_last=lambda: tracker.mark("business"),
    )
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=9,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    collector = OkxPublicResearchCollector(
        writer,
        config=OkxPublicResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=ScriptedConnectionFactory([public]),
        business_connection_factory=ScriptedConnectionFactory([business]),
        utc_ns=Counter(1_788_105_600_000_000_000),
        monotonic_ns=Counter(9_000_000_000),
        session_id_factory=SessionIds(),
    )

    try:
        await collector.capture_for(5.0, stop_event=stop_event)
    finally:
        await writer.aclose()

    assert 1 < len(writer.parquet_files) < 20
    assert writer.orphan_partial_files == ()
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        expected_payloads = {
            _fixture_text(fixture).encode("utf-8") for fixture in _MARKET_FIXTURES.values()
        }
        expected_payloads.add(no_update_payload.encode("utf-8"))
        rows = connection.execute(
            """
            SELECT payload_bytes, payload_sha256, sha256(payload_bytes)
            FROM raw_records
            WHERE venue = 'okx'
              AND direction = 'inbound'
              AND channel != 'subscription'
            ORDER BY message_ordinal
            """
        ).fetchall()
        assert {row[0] for row in rows} == expected_payloads
        assert len(rows) == len(expected_payloads)
        for payload_bytes, stored_sha256, computed_sha256 in rows:
            assert stored_sha256 == hashlib.sha256(payload_bytes).hexdigest()
            assert computed_sha256 == stored_sha256

        for view_name in (
            "okx_swap_trades",
            "okx_swap_bbo",
            "okx_swap_l2_events",
            "okx_swap_derivative_context",
        ):
            assert connection.execute(f'SELECT count(*) FROM "{view_name}"').fetchone() is not None

        assert connection.execute(
            "SELECT price, quantity, trade_id FROM okx_swap_trades"
        ).fetchone() == (
            "60000.123456789012345678",
            "0.000000010000000001",
            "700000000001",
        )
        assert connection.execute(
            """
            SELECT price, quantity, order_count, sequence_event
            FROM okx_swap_l2_events
            WHERE side = 'ask' AND side_index = 0 AND message_type = 'snapshot'
            """
        ).fetchone() == (
            "60000.200000000000000000",
            "0.800000000000000001",
            "2",
            "snapshot",
        )
        assert connection.execute(
            """
            SELECT context_type, mark_price
            FROM okx_swap_derivative_context
            WHERE context_type = 'mark_price'
            """
        ).fetchone() == ("mark_price", "60000.150000000000000001")
        assert connection.execute(
            """
            SELECT prev_seq_id, seq_id, sequence_event, wire_order, action
            FROM okx_swap_l2_events
            WHERE sequence_event = 'no_update'
            """
        ).fetchone() == ("1007", "1007", "no_update", None, None)
        assert connection.execute(
            """
            SELECT count(*)
            FROM raw_records
            WHERE venue = 'okx' AND channel = 'liquidation-orders'
            """
        ).fetchone() == (0,)

        subscription_events = [
            _document(row[0])
            for row in connection.execute(
                """
                SELECT payload_bytes
                FROM raw_records
                WHERE venue = 'okx'
                  AND channel = 'subscription'
                  AND direction = 'local'
                """
            ).fetchall()
        ]
        assert (
            sum(event.get("event") == "subscription_acknowledged" for event in subscription_events)
            == 7
        )
        assert (
            sum(event.get("event") == "subscriptions_active" for event in subscription_events) == 2
        )
    finally:
        connection.close()

    sent = [json.loads(cast(str, payload)) for payload in [*public.sent, *business.sent]]
    sent_channels = [cast(dict[str, object], payload["args"][0])["channel"] for payload in sent]
    assert sent_channels == [*_PUBLIC_CHANNELS, "trades-all"]
    assert all(payload.get("op") == "subscribe" for payload in sent)
    assert all(
        type(payload.get("id")) is str
        and cast(str, payload["id"]).isascii()
        and cast(str, payload["id"]).isalnum()
        and len(cast(str, payload["id"])) <= 32
        for payload in sent
    )
    assert all("login" not in payload for payload in sent)


@pytest.mark.asyncio
async def test_reconnect_uses_new_session_empty_book_state_and_fresh_snapshot() -> None:
    stop_event = asyncio.Event()
    first_public = FakeConnection(
        [
            *_all_acknowledgements(_PUBLIC_CHANNELS, "public-one"),
            _fixture_text("books_snapshot.json"),
        ],
        disconnect_when_empty=True,
    )
    second_public = FakeConnection(
        [
            *_all_acknowledgements(_PUBLIC_CHANNELS, "public-two"),
            _fixture_text("books_snapshot.json"),
        ],
        on_last=stop_event.set,
    )
    business = FakeConnection(
        [
            _ack("trades-all", connection_id="business-one"),
            _fixture_text("trades_all_frame.json"),
        ]
    )
    public_factory = ScriptedConnectionFactory([first_public, second_public])
    business_factory = ScriptedConnectionFactory([business])
    sink = MemorySink()
    collector = OkxPublicResearchCollector(
        sink,
        config=OkxPublicResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=public_factory,
        business_connection_factory=business_factory,
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    assert public_factory.calls == 2
    assert business_factory.calls == 1
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )
    snapshots = [
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND and record.channel == "books"
    ]
    assert [record.session_id for record in snapshots] == [
        "public-session-1",
        "public-session-2",
    ]
    session_events = _marker_documents(sink.records, channel="session")
    assert any(
        event.get("event") == "reconnected"
        and event.get("previous_session_id") == "public-session-1"
        for event in session_events
    )
    quality_events = _marker_documents(sink.records, channel="data_quality")
    assert any(event.get("event") == "gap_detected" for event in quality_events)
    assert any(event.get("event") == "resnapshot_received" for event in quality_events)


@pytest.mark.asyncio
async def test_update_before_snapshot_after_reconnect_fails_closed() -> None:
    stop_event = asyncio.Event()
    first_public = FakeConnection(
        [
            *_all_acknowledgements(_PUBLIC_CHANNELS, "public-one"),
            _fixture_text("books_snapshot.json"),
        ],
        disconnect_when_empty=True,
    )
    second_public = FakeConnection(
        [
            *_all_acknowledgements(_PUBLIC_CHANNELS, "public-two"),
            _fixture_text("books_update.json"),
        ]
    )
    business = FakeConnection(
        [
            _ack("trades-all", connection_id="business-one"),
            _fixture_text("trades_all_frame.json"),
        ]
    )
    sink = MemorySink()
    collector = OkxPublicResearchCollector(
        sink,
        config=OkxPublicResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=ScriptedConnectionFactory([first_public, second_public]),
        business_connection_factory=ScriptedConnectionFactory([business]),
        session_id_factory=SessionIds(),
    )

    with pytest.raises(OkxDataIntegrityError) as captured:
        await collector.capture_for(5.0, stop_event=stop_event)

    assert captured.value.quality_event == "snapshot_missing"
    quality_events = _marker_documents(sink.records, channel="data_quality")
    assert any(
        event.get("event") == "snapshot_missing" and event.get("stream") == "public"
        for event in quality_events
    )


@pytest.mark.asyncio
async def test_schema_failure_preserves_recognized_raw_frame_and_is_visible() -> None:
    bad_frame = _fixture_document("books_snapshot.json")
    cast(dict[str, object], bad_frame["arg"])["instId"] = "ETH-USDT-SWAP"
    bad_bytes = json.dumps(bad_frame, separators=(",", ":")).encode("utf-8")
    sink = MemorySink()
    collector = OkxPublicResearchCollector(
        sink,
        public_connection_factory=ScriptedConnectionFactory([]),
        business_connection_factory=ScriptedConnectionFactory([]),
    )
    captured = capture_application_payload(bad_bytes)

    raw_ordinal, channel, document = await collector._record_inbound(
        captured,
        "public-session-1",
        "public",
    )
    assert channel == "books"
    with pytest.raises(OkxDataIntegrityError):
        try:
            _normalize_market_frame(channel, document, raw_ordinal, _BooksState())
        except OkxDataIntegrityError as error:
            await collector._quality_failure("public-session-1", "public", raw_ordinal, error)

    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert len(inbound) == 1
    assert inbound[0].payload_bytes == bad_bytes
    assert any(
        event.get("event") == "schema_error"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_malformed_subscription_ack_is_visible_and_fails_closed() -> None:
    sink = MemorySink()
    collector = OkxPublicResearchCollector(
        sink,
        public_connection_factory=ScriptedConnectionFactory([]),
        business_connection_factory=ScriptedConnectionFactory([]),
    )
    document = _document(_ack("books", connection_id="public-connection"))
    del document["connId"]

    with pytest.raises(OkxDataIntegrityError):
        await collector._record_subscription_response(
            document,
            "public-session-1",
            "public",
            12,
            {},
        )

    quality_events = _marker_documents(sink.records, channel="data_quality")
    assert any(
        event.get("event") == "schema_error" and event.get("raw_message_ordinal") == "12"
        for event in quality_events
    )


@pytest.mark.asyncio
async def test_complete_oversize_payload_is_preserved_then_fails_closed() -> None:
    sink = MemorySink()
    collector = OkxPublicResearchCollector(
        sink,
        config=OkxPublicResearchConfig(max_application_payload_bytes=32),
        public_connection_factory=ScriptedConnectionFactory([]),
        business_connection_factory=ScriptedConnectionFactory([]),
    )
    captured = capture_application_payload(_fixture_text("books_snapshot.json"))

    with pytest.raises(OkxDataIntegrityError) as error:
        await collector._record_inbound(captured, "public-session-1", "public")

    assert error.value.quality_event == "payload_oversize"
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert len(inbound) == 1
    assert inbound[0].payload_bytes == captured.payload_bytes
    assert inbound[0].payload_sha256 == hashlib.sha256(captured.payload_bytes).hexdigest()
    assert any(
        event.get("event") == "payload_oversize"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_heartbeat_timeout_is_visible_as_a_reconnect_boundary() -> None:
    sink = MemorySink()
    connection = FakeConnection(())
    collector = OkxPublicResearchCollector(
        sink,
        config=OkxPublicResearchConfig(
            heartbeat_idle_seconds=0.001,
            pong_timeout_seconds=0.001,
        ),
        public_connection_factory=ScriptedConnectionFactory([]),
        business_connection_factory=ScriptedConnectionFactory([]),
    )

    with pytest.raises(OSError, match="heartbeat timed out"):
        await collector._receive_session(
            connection,
            "business-session-1",
            "business",
            _BUSINESS_SUBSCRIPTIONS,
            asyncio.Event(),
            is_reconnect=False,
        )

    assert connection.sent == ["ping"]
    timeout_events = [
        event
        for event in _marker_documents(sink.records, channel="data_quality")
        if event.get("event") == "heartbeat_timeout"
    ]
    assert len(timeout_events) == 1
    assert timeout_events[0]["reason"] == ("application_pong_deadline_expired; reconnect_required")


@pytest.mark.asyncio
async def test_transport_truncation_is_terminal_without_partial_raw_record() -> None:
    class TruncatedConnection:
        def __init__(self) -> None:
            self.sent: list[str | bytes] = []

        async def send(self, message: str | bytes) -> None:
            self.sent.append(message)

        async def recv(self) -> str | bytes:
            raise PayloadTooBig(65_537, 65_536)

    sink = MemorySink()
    public_factory = ScriptedConnectionFactory([TruncatedConnection()])
    collector = OkxPublicResearchCollector(
        sink,
        public_connection_factory=public_factory,
        business_connection_factory=ScriptedConnectionFactory([FakeConnection(())]),
    )

    with pytest.raises(OkxDataIntegrityError) as error:
        await collector.capture_for(5.0)

    assert error.value.quality_event == "truncation_error"
    assert public_factory.calls == 1
    assert not any(record.direction is MessageDirection.INBOUND for record in sink.records)
    assert any(
        event.get("event") == "truncation_error"
        and event.get("reason") == "capture_stopped_fail_closed"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_sink_failure_is_terminal_without_reconnect() -> None:
    public_factory = ScriptedConnectionFactory([FakeConnection(())])
    business_factory = ScriptedConnectionFactory([FakeConnection(())])
    sink = MemorySink(fail_on_append=1)
    collector = OkxPublicResearchCollector(
        sink,
        public_connection_factory=public_factory,
        business_connection_factory=business_factory,
    )

    with pytest.raises(OkxSinkError):
        await collector.capture_for(5.0)

    assert sink.append_attempts == 1
    assert sink.records == []
    assert public_factory.calls <= 1
    assert business_factory.calls <= 1


@pytest.mark.parametrize(
    "config_kwargs",
    [
        {"heartbeat_idle_seconds": 0.0},
        {"heartbeat_idle_seconds": 30.0},
        {"pong_timeout_seconds": 0.0},
        {"reconnect_delay_seconds": -1.0},
        {"max_application_payload_bytes": 0},
    ],
)
def test_config_bounds(config_kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OkxPublicResearchConfig(**config_kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("duration", [0.0, 600.1, -1.0])
@pytest.mark.asyncio
async def test_capture_duration_is_strictly_bounded(duration: float) -> None:
    collector = OkxPublicResearchCollector(
        MemorySink(),
        public_connection_factory=ScriptedConnectionFactory([]),
        business_connection_factory=ScriptedConnectionFactory([]),
    )

    with pytest.raises(ValueError, match="between 1 and 600"):
        await collector.capture_for(duration)
