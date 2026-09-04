"""Offline DATA-1B tests for Kraken BTC/EUR authenticated L3 capture."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import traceback
import urllib.parse
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import duckdb
import pytest

from hyperliquid_bot.kraken_l3_research import (
    KRAKEN_RESEARCH_PRODUCT,
    KRAKEN_TOKEN_PATH,
    KRAKEN_TOKEN_URL,
    KRAKEN_WS_API_KEY_ENV,
    KRAKEN_WS_API_SECRET_ENV,
    MAX_CAPTURE_SECONDS,
    SMOKE_CAPTURE_SECONDS,
    KrakenApiCredentials,
    KrakenAuthenticationError,
    KrakenDataIntegrityError,
    KrakenL3ResearchCollector,
    KrakenL3ResearchConfig,
    KrakenRestTokenProvider,
    KrakenTransportError,
    KrakenWebSocketToken,
    WebSocketConnection,
    _L2BookState,
    _L3BookState,
    _argument_parser,
    _require_bounded_duration,
    _resolve_cli_mode,
    data1b_capture_claim,
    data1b_capture_health,
    load_optional_l3_token_provider,
    refuse_protected_trade_keys,
    run_reconstructable_capture,
)
from hyperliquid_bot.parquet_research import (
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import (
    MessageDirection,
    RawResearchRecord,
    RawResearchSink,
    capture_application_payload,
)
from hyperliquid_bot.reconstructable_paths import DATA1B_PATH_CONTRACT_ID, data1b_run_paths

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "kraken"


def _fixture_text(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _fixture_document(name: str) -> dict[str, object]:
    loaded = json.loads(
        (_FIXTURE_DIR / name).read_bytes(),
        parse_float=str,
        parse_int=str,
    )
    if type(loaded) is not dict:
        raise AssertionError("fixture root must be an object")
    return cast(dict[str, object], loaded)


def _ack(channel: str, *, success: bool = True, include_depth: bool = True) -> str:
    document: dict[str, object] = {
        "method": "subscribe",
        "success": success,
        "time_in": "2026-08-31T10:00:00.000001Z",
        "time_out": "2026-08-31T10:00:00.000002Z",
    }
    if success:
        result: dict[str, object] = {
            "channel": channel,
            "symbol": KRAKEN_RESEARCH_PRODUCT,
            "snapshot": channel != "trade",
        }
        if channel != "trade" and include_depth:
            result["depth"] = 10
        document["result"] = result
    else:
        document["error"] = "request rejected"
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def _status(connection_id: int) -> str:
    return json.dumps(
        {
            "channel": "status",
            "type": "update",
            "data": [
                {
                    "api_version": "v2",
                    "connection_id": connection_id,
                    "system": "online",
                    "version": "2.0.10",
                }
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


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


class RecordingParquetSink:
    def __init__(self, writer: ParquetResearchWriter) -> None:
        self._writer = writer
        self.records: list[RawResearchRecord] = []

    async def append(self, record: RawResearchRecord) -> None:
        await self._writer.append(record)
        self.records.append(record)

    async def aclose(self) -> None:
        await self._writer.aclose()


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
        receive_failure_text: str | None = None,
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._disconnect_when_empty = disconnect_when_empty
        self._receive_failure_text = receive_failure_text
        self.sent: list[str | bytes] = []
        self._never = asyncio.Event()

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            if self._receive_failure_text is not None:
                raise RuntimeError(self._receive_failure_text)
            if self._disconnect_when_empty:
                raise ConnectionError("scripted disconnect")
            await self._never.wait()
            raise AssertionError("unreachable")
        message = self._messages.popleft()
        if not self._messages and self._on_last is not None:
            self._on_last()
        return message


class FailingSendConnection(FakeConnection):
    def __init__(self, failure_text: str) -> None:
        super().__init__(())
        self._failure_text = failure_text

    async def send(self, message: str | bytes) -> None:
        del message
        raise RuntimeError(self._failure_text)


class StopOnSendConnection(FakeConnection):
    def __init__(self, stop_event: asyncio.Event) -> None:
        super().__init__(())
        self._stop_event = stop_event

    async def send(self, message: str | bytes) -> None:
        await super().send(message)
        self._stop_event.set()


@asynccontextmanager
async def _fake_context(connection: FakeConnection) -> AsyncIterator[WebSocketConnection]:
    yield connection


@asynccontextmanager
async def _failing_exit_context(
    connection: FakeConnection,
    failure_text: str,
) -> AsyncIterator[WebSocketConnection]:
    yield connection
    raise RuntimeError(failure_text)


class ScriptedConnectionFactory:
    def __init__(self, connections: Sequence[FakeConnection]) -> None:
        self._connections = deque(connections)
        self.calls = 0

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls += 1
        if not self._connections:
            raise AssertionError("collector requested an unexpected connection")
        return _fake_context(self._connections.popleft())


class ExitFailingConnectionFactory:
    def __init__(self, connection: FakeConnection, failure_text: str) -> None:
        self._connection = connection
        self._failure_text = failure_text

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        return _failing_exit_context(self._connection, self._failure_text)


class ReconnectFailingFactory:
    def __init__(self, first: FakeConnection, failure_text: str) -> None:
        self._first = first
        self._failure_text = failure_text
        self.calls = 0

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls += 1
        if self.calls == 1:
            return _fake_context(self._first)
        raise OSError(self._failure_text)


class SyntheticTokenProvider:
    def __init__(self, tokens: Sequence[str], *, failure_text: str | None = None) -> None:
        self._tokens = deque(tokens)
        self._failure_text = failure_text
        self.calls = 0

    async def get_token(self) -> KrakenWebSocketToken:
        self.calls += 1
        if self._failure_text is not None:
            raise RuntimeError(self._failure_text)
        if not self._tokens:
            raise AssertionError("collector requested an unexpected token")
        return KrakenWebSocketToken(self._tokens.popleft())


class SessionIds:
    def __init__(self) -> None:
        self.counts = {"public": 0, "l3": 0}

    def __call__(self, stream: str) -> str:
        self.counts[stream] += 1
        return f"{stream}-session-{self.counts[stream]}"


def _market_frames(records: Sequence[RawResearchRecord]) -> list[RawResearchRecord]:
    return [
        record
        for record in records
        if record.direction is MessageDirection.INBOUND
        and record.channel in {"trade", "book", "level3"}
    ]


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
        loaded = json.loads(record.payload_bytes)
        if type(loaded) is not dict:
            raise AssertionError("local record root must be an object")
        documents.append(cast(dict[str, object], loaded))
    return documents


def _all_record_bytes(records: Sequence[RawResearchRecord]) -> bytes:
    return b"\n".join(record.payload_bytes for record in records)


async def _capture_authentication_error(
    provider: KrakenRestTokenProvider,
) -> KrakenAuthenticationError:
    try:
        await provider.get_token()
    except KrakenAuthenticationError as error:
        return error
    raise AssertionError("expected Kraken authentication failure")


async def _capture_collector_authentication_error(
    collector: KrakenL3ResearchCollector,
) -> KrakenAuthenticationError:
    try:
        await collector.capture_for(5.0)
    except KrakenAuthenticationError as error:
        return error
    raise AssertionError("expected Kraken collector authentication failure")


async def _capture_transport_error(
    collector: KrakenL3ResearchCollector,
    stop_event: asyncio.Event,
) -> KrakenTransportError:
    try:
        await collector.capture_for(5.0, stop_event=stop_event)
    except KrakenTransportError as error:
        return error
    raise AssertionError("expected Kraken transport failure")


@pytest.mark.asyncio
async def test_token_request_uses_official_form_signature_and_redacted_objects() -> None:
    api_key = secrets.token_urlsafe(24)
    secret_bytes = secrets.token_bytes(48)
    api_secret = base64.b64encode(secret_bytes).decode("ascii")
    websocket_token = secrets.token_urlsafe(36)
    captured: dict[str, object] = {}

    def http_post(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        captured.update(
            url=url,
            body=body,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        return json.dumps(
            {"error": [], "result": {"token": websocket_token, "expires": 900}},
            separators=(",", ":"),
        ).encode("utf-8")

    credentials = KrakenApiCredentials(api_key=api_key, api_secret_base64=api_secret)
    provider = KrakenRestTokenProvider(
        credentials,
        http_post=http_post,
        nonce_factory=lambda: 1_788_105_600_000_000_001,
    )

    token = await provider.get_token()

    assert captured["url"] == KRAKEN_TOKEN_URL
    assert captured["body"] == b"nonce=1788105600000000001"
    body = captured["body"]
    assert type(body) is bytes
    headers = cast(dict[str, str], captured["headers"])
    expected_message = (
        KRAKEN_TOKEN_PATH.encode("ascii") + hashlib.sha256(b"1788105600000000001" + body).digest()
    )
    expected_signature = base64.b64encode(
        hmac.new(secret_bytes, expected_message, hashlib.sha512).digest()
    ).decode("ascii")
    assert headers == {
        "API-Key": api_key,
        "API-Sign": expected_signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert urllib.parse.parse_qs(body.decode("ascii")) == {"nonce": ["1788105600000000001"]}
    assert repr(credentials) == "KrakenApiCredentials(<redacted>)"
    assert repr(token) == "KrakenWebSocketToken(<redacted>)"
    assert api_key not in repr(credentials)
    assert api_secret not in repr(credentials)
    assert websocket_token not in repr(token)


@pytest.mark.asyncio
async def test_low_level_token_failure_has_no_secret_exception_chain() -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = base64.b64encode(secrets.token_bytes(48)).decode("ascii")
    leaked_by_fake = secrets.token_urlsafe(36)

    def failing_http_post(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        del url, body, headers, timeout_seconds, max_response_bytes
        raise RuntimeError(f"synthetic lower layer {api_key} {api_secret} {leaked_by_fake}")

    provider = KrakenRestTokenProvider(
        KrakenApiCredentials(api_key=api_key, api_secret_base64=api_secret),
        http_post=failing_http_post,
        nonce_factory=lambda: 1,
    )

    with pytest.raises(KrakenAuthenticationError) as captured:
        await provider.get_token()

    formatted = "".join(traceback.format_exception(captured.value))
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None
    for secret in (api_key, api_secret, leaked_by_fake):
        assert secret not in str(captured.value)
        assert secret not in formatted


@pytest.mark.asyncio
async def test_malformed_token_response_has_no_secret_context_or_traceback_locals() -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = base64.b64encode(secrets.token_bytes(48)).decode("ascii")
    temporary_token = secrets.token_urlsafe(36)
    malformed = b'{"error":[],"result":{"expires":900,"token":"' + temporary_token.encode("ascii")

    def malformed_http_post(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        del url, body, headers, timeout_seconds, max_response_bytes
        return malformed

    provider = KrakenRestTokenProvider(
        KrakenApiCredentials(api_key=api_key, api_secret_base64=api_secret),
        http_post=malformed_http_post,
        nonce_factory=lambda: 1,
    )

    captured = await _capture_authentication_error(provider)

    rendered = "".join(
        traceback.TracebackException.from_exception(
            captured,
            capture_locals=True,
        ).format()
    )
    assert captured.__context__ is None
    assert captured.__cause__ is None
    for material in (api_key, api_secret, temporary_token):
        assert material not in rendered


@pytest.mark.asyncio
async def test_exact_raw_parquet_roundtrip_and_string_preserving_kraken_views(
    tmp_path: Path,
) -> None:
    stop_event = asyncio.Event()
    tracker = CompletionTracker(stop_event, {"public", "l3"})
    websocket_token = secrets.token_urlsafe(36)
    public = FakeConnection(
        [
            _status(101),
            _ack("trade"),
            _ack("book"),
            _fixture_text("trade_update.json"),
            _fixture_text("book_snapshot.json"),
            _fixture_text("book_update.json"),
        ],
        on_last=lambda: tracker.mark("public"),
    )
    level3 = FakeConnection(
        [
            _status(202),
            _ack("level3"),
            _fixture_text("level3_snapshot.json"),
            _fixture_text("level3_update.json"),
        ],
        on_last=lambda: tracker.mark("l3"),
    )
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=8,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    collector = KrakenL3ResearchCollector(
        writer,
        SyntheticTokenProvider([websocket_token]),
        config=KrakenL3ResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=ScriptedConnectionFactory([public]),
        l3_connection_factory=ScriptedConnectionFactory([level3]),
        utc_ns=Counter(1_788_105_600_000_000_000),
        monotonic_ns=Counter(9_000_000_000),
        session_id_factory=SessionIds(),
    )

    try:
        await collector.capture_for(5.0, stop_event=stop_event)
    finally:
        await writer.aclose()

    assert 1 < len(writer.parquet_files) < 30
    assert writer.orphan_partial_files == ()
    assert websocket_token.encode("utf-8") not in b"".join(
        path.read_bytes() for path in writer.parquet_files
    )
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        raw_market = connection.execute(
            """
            SELECT channel, payload_bytes, payload_sha256, sha256(payload_bytes)
            FROM raw_records
            WHERE venue = 'kraken' AND direction = 'inbound'
            ORDER BY message_ordinal
            """
        ).fetchall()
        expected_payloads = {
            _fixture_text(name).encode("utf-8")
            for name in (
                "trade_update.json",
                "book_snapshot.json",
                "book_update.json",
                "level3_snapshot.json",
                "level3_update.json",
            )
        }
        assert len(raw_market) == 5
        assert {row[1] for row in raw_market} == expected_payloads
        for _, payload_bytes, stored_sha256, computed_sha256 in raw_market:
            assert stored_sha256 == hashlib.sha256(payload_bytes).hexdigest()
            assert computed_sha256 == stored_sha256

        assert connection.execute(
            """
            SELECT price, quantity, trade_id
            FROM kraken_spot_trades
            ORDER BY event_index
            """
        ).fetchall() == [
            ("60000.123456789012345678", "0.000000010000000000", "101"),
            ("60000.200000000000000000", "1.230000000000000001", "102"),
        ]
        assert connection.execute(
            """
            SELECT count(*), count(DISTINCT symbol)
            FROM kraken_spot_l2_events
            """
        ).fetchone() == (7, 1)
        l3_counts = connection.execute(
            """
            SELECT event, count(*)
            FROM kraken_spot_l3_order_events
            GROUP BY event
            ORDER BY event
            """
        ).fetchall()
        assert l3_counts == [("add", 2), ("delete", 2), ("modify", 2), ("snapshot", 5)]
        assert connection.execute(
            """
            SELECT event, previous_event, order_quantity, previous_order_quantity
            FROM kraken_spot_l3_order_lifecycle
            WHERE order_id = 'BID-ORDER-001'
            ORDER BY observation_index DESC
            LIMIT 1
            """
        ).fetchone() == ("modify", "snapshot", "0.60000000", "0.70000000")
        assert connection.execute(
            """
            SELECT side, side_index, wire_order, limit_price, order_quantity, event_source
            FROM kraken_spot_l3_order_events
            WHERE order_id = 'BID-ORDER-001' AND event = 'snapshot'
            """
        ).fetchone() == ("bid", 0, 0, "60000.1000", "0.70000000", "wire")
        assert connection.execute(
            """
            SELECT count(DISTINCT channel)
            FROM raw_records
            WHERE venue = 'kraken' AND direction = 'inbound'
            """
        ).fetchone() == (3,)
        stored_payloads = b"\n".join(
            row[0]
            for row in connection.execute(
                "SELECT payload_bytes FROM raw_records WHERE venue = 'kraken'"
            ).fetchall()
        )
        assert websocket_token.encode("utf-8") not in stored_payloads
        subscription_markers = [
            json.loads(row[0])
            for row in connection.execute(
                """
                SELECT decode(payload_bytes)
                FROM raw_records
                WHERE venue = 'kraken'
                  AND channel = 'subscription'
                  AND direction = 'local'
                """
            ).fetchall()
        ]
        assert any(
            marker.get("event") == "subscription_sent"
            and marker.get("subscription_type") == "level3"
            and marker.get("product") == "BTC/EUR"
            and marker.get("authenticated") is True
            for marker in subscription_markers
        )
        assert connection.execute(
            """
            SELECT count(*)
            FROM raw_records
            WHERE venue = 'kraken' AND direction = 'outbound'
            """
        ).fetchone() == (0,)
    finally:
        connection.close()
    assert websocket_token.encode("utf-8") not in database_path.read_bytes()

    status_connection = duckdb.connect(str(database_path), read_only=True)
    try:
        status_events = [
            json.loads(row[0])
            for row in status_connection.execute(
                """
                SELECT decode(payload_bytes)
                FROM raw_records
                WHERE venue = 'kraken'
                  AND channel = 'session'
                  AND direction = 'local'
                """
            ).fetchall()
            if json.loads(row[0]).get("event") == "venue_status"
        ]
    finally:
        status_connection.close()
    assert {
        (event.get("stream"), event.get("connection_id"), event.get("system"))
        for event in status_events
    } == {("public", "101", "online"), ("l3", "202", "online")}

    public_subscriptions = [json.loads(cast(str, payload)) for payload in public.sent]
    assert public_subscriptions == [
        {
            "method": "subscribe",
            "params": {
                "channel": "trade",
                "snapshot": False,
                "symbol": ["BTC/EUR"],
            },
        },
        {
            "method": "subscribe",
            "params": {
                "channel": "book",
                "depth": 10,
                "snapshot": True,
                "symbol": ["BTC/EUR"],
            },
        },
    ]
    outbound_l3 = cast(str, level3.sent[0])
    assert websocket_token in outbound_l3
    assert json.loads(outbound_l3)["params"]["channel"] == "level3"
    assert all(websocket_token.encode("utf-8") not in payload for payload in expected_payloads)


@pytest.mark.asyncio
async def test_callback_clocks_run_immediately_after_recv_returns() -> None:
    trace: list[str] = []
    stop_event = asyncio.Event()

    class TimingConnection:
        async def send(self, message: str | bytes) -> None:
            del message

        async def recv(self) -> str | bytes:
            trace.append("recv-return")
            return '{"channel":"heartbeat"}'

    def utc_ns() -> int:
        trace.append("utc-clock")
        return 101

    def monotonic_ns() -> int:
        trace.append("monotonic-clock")
        return 202

    collector = KrakenL3ResearchCollector(
        MemorySink(),
        SyntheticTokenProvider([secrets.token_urlsafe(24)]),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=ScriptedConnectionFactory([]),
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
    )

    captured = await collector._receive_or_stop(TimingConnection(), stop_event)

    assert captured is not None
    assert trace == ["recv-return", "utc-clock", "monotonic-clock"]
    assert captured.received_utc_ns == 101
    assert captured.received_monotonic_ns == 202


@pytest.mark.asyncio
async def test_l3_reconnect_gets_fresh_session_token_snapshot_and_gap_marker() -> None:
    stop_event = asyncio.Event()
    first_token = secrets.token_urlsafe(36)
    second_token = secrets.token_urlsafe(36)
    provider = SyntheticTokenProvider([first_token, second_token])
    public = FakeConnection([_ack("trade"), _ack("book"), _fixture_text("book_snapshot.json")])
    first_l3 = FakeConnection(
        [_ack("level3"), _fixture_text("level3_snapshot.json")],
        disconnect_when_empty=True,
    )
    second_l3 = FakeConnection(
        [_ack("level3"), _fixture_text("level3_snapshot.json")],
        on_last=stop_event.set,
    )
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        provider,
        config=KrakenL3ResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=ScriptedConnectionFactory([public]),
        l3_connection_factory=ScriptedConnectionFactory([first_l3, second_l3]),
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    assert provider.calls == 2
    assert first_token in cast(str, first_l3.sent[0])
    assert second_token in cast(str, second_l3.sent[0])
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )
    session_events = _marker_documents(sink.records, channel="session")
    assert any(
        event.get("event") == "disconnected" and event.get("stream") == "l3"
        for event in session_events
    )
    assert any(
        event.get("event") == "reconnected" and event.get("previous_session_id") == "l3-session-1"
        for event in session_events
    )
    quality_events = _marker_documents(sink.records, channel="data_quality")
    assert any(
        event.get("event") == "gap_detected" and event.get("stream") == "l3"
        for event in quality_events
    )
    auth_events = _marker_documents(sink.records, channel="authentication")
    assert [event.get("event") for event in auth_events].count(
        "authentication_boundary_started"
    ) == 2
    record_bytes = _all_record_bytes(sink.records)
    assert first_token.encode("utf-8") not in record_bytes
    assert second_token.encode("utf-8") not in record_bytes


@pytest.mark.asyncio
async def test_invalid_l3_auth_is_terminal_without_public_only_downgrade() -> None:
    websocket_token = secrets.token_urlsafe(36)
    public = FakeConnection([_ack("trade"), _ack("book"), _fixture_text("book_snapshot.json")])
    level3 = FakeConnection([_ack("level3", success=False)])
    public_factory = ScriptedConnectionFactory([public])
    l3_factory = ScriptedConnectionFactory([level3])
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([websocket_token]),
        config=KrakenL3ResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=public_factory,
        l3_connection_factory=l3_factory,
        session_id_factory=SessionIds(),
    )

    with pytest.raises(KrakenAuthenticationError, match="authentication failed"):
        await collector.capture_for(5.0)

    assert public_factory.calls == 1
    assert l3_factory.calls == 1
    assert not any(record.channel == "level3" for record in _market_frames(sink.records))
    assert websocket_token.encode("utf-8") not in _all_record_bytes(sink.records)
    assert any(
        event.get("event") == "authentication_failed"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_capture_stop_before_l3_ack_is_terminal_without_success_marker() -> None:
    stop_event = asyncio.Event()
    temporary_token = secrets.token_urlsafe(36)
    level3 = StopOnSendConnection(stop_event)
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([temporary_token]),
        l3_connection_factory=ScriptedConnectionFactory([level3]),
        public_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )

    with pytest.raises(KrakenAuthenticationError, match="authentication failed"):
        await collector._run_l3_stream(stop_event)

    assert temporary_token.encode("utf-8") not in _all_record_bytes(sink.records)
    assert not any(
        event.get("event") == "session_stopped"
        for event in _marker_documents(sink.records, channel="session")
    )
    assert any(
        event.get("event") == "authentication_failed"
        and event.get("reason") == "capture_stopped_before_authenticated_acknowledgement"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_successful_completion_requires_public_and_l3_book_snapshots() -> None:
    public_stop = asyncio.Event()
    public = FakeConnection(
        [_ack("trade"), _ack("book")],
        on_last=public_stop.set,
    )
    l3_stop = asyncio.Event()
    level3 = FakeConnection([_ack("level3")], on_last=l3_stop.set)
    temporary_token = secrets.token_urlsafe(36)
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([temporary_token]),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )

    with pytest.raises(KrakenDataIntegrityError, match="public book snapshot"):
        await collector._receive_public(public, "public-session-1", public_stop)
    with pytest.raises(KrakenDataIntegrityError, match="L3 book snapshot"):
        await collector._receive_l3(
            level3,
            "l3-session-1",
            KrakenWebSocketToken(temporary_token),
            l3_stop,
        )

    snapshot_failures = [
        event
        for event in _marker_documents(sink.records, channel="data_quality")
        if event.get("event") == "snapshot_missing"
    ]
    assert {event.get("stream") for event in snapshot_failures} == {"public", "l3"}


@pytest.mark.asyncio
async def test_token_provider_failure_is_absent_from_records_files_logs_and_exception(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = base64.b64encode(secrets.token_bytes(48)).decode("ascii")
    temporary_token = secrets.token_urlsafe(36)
    leak_text = f"{api_key} {api_secret} {temporary_token}"
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    sink = RecordingParquetSink(writer)
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([], failure_text=leak_text),
        public_connection_factory=ScriptedConnectionFactory(
            [FakeConnection([_ack("trade"), _ack("book"), _fixture_text("book_snapshot.json")])]
        ),
        l3_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )

    try:
        with pytest.raises(KrakenAuthenticationError) as captured:
            await collector.capture_for(5.0)
    finally:
        await sink.aclose()

    formatted = "".join(traceback.format_exception(captured.value))
    record_bytes = _all_record_bytes(sink.records)
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        persisted_bytes = b"\n".join(
            row[0]
            for row in connection.execute(
                "SELECT payload_bytes FROM raw_records WHERE venue = 'kraken'"
            ).fetchall()
        )
    finally:
        connection.close()
    file_bytes = (
        b"".join(path.read_bytes() for path in writer.parquet_files) + database_path.read_bytes()
    )
    for secret in (api_key, api_secret, temporary_token):
        assert secret not in formatted
        assert secret not in caplog.text
        assert secret.encode("utf-8") not in record_bytes
        assert secret.encode("utf-8") not in persisted_bytes
        assert secret.encode("utf-8") not in file_bytes
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_token_send_failure_is_absent_from_records_files_logs_and_exception(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    temporary_token = secrets.token_urlsafe(36)
    lower_failure = f"synthetic send failure {temporary_token}"
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    sink = RecordingParquetSink(writer)
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([temporary_token]),
        public_connection_factory=ScriptedConnectionFactory(
            [
                FakeConnection(
                    [
                        _status(303),
                        _ack("trade"),
                        _ack("book"),
                        _fixture_text("book_snapshot.json"),
                    ]
                )
            ]
        ),
        l3_connection_factory=ScriptedConnectionFactory([FailingSendConnection(lower_failure)]),
        session_id_factory=SessionIds(),
    )

    try:
        with pytest.raises(KrakenAuthenticationError) as captured:
            await collector.capture_for(5.0)
    finally:
        await sink.aclose()

    formatted = "".join(traceback.format_exception(captured.value))
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        persisted_bytes = b"\n".join(
            row[0]
            for row in connection.execute(
                "SELECT payload_bytes FROM raw_records WHERE venue = 'kraken'"
            ).fetchall()
        )
    finally:
        connection.close()
    file_bytes = (
        b"".join(path.read_bytes() for path in writer.parquet_files) + database_path.read_bytes()
    )
    for material in (temporary_token, lower_failure):
        assert material not in formatted
        assert material not in caplog.text
        assert material.encode("utf-8") not in _all_record_bytes(sink.records)
        assert material.encode("utf-8") not in persisted_bytes
        assert material.encode("utf-8") not in file_bytes
    assert captured.value.__context__ is None
    assert any(
        event.get("event") == "authentication_failed"
        and event.get("reason") == "subscription_send_failed"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_token_is_absent_after_unexpected_authenticated_context_exit(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop_event = asyncio.Event()
    temporary_token = secrets.token_urlsafe(36)
    lower_failure = f"synthetic context exit failure {temporary_token}"
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    sink = RecordingParquetSink(writer)
    public = FakeConnection(
        [
            _status(404),
            _ack("trade"),
            _ack("book"),
            _fixture_text("book_snapshot.json"),
        ]
    )
    level3 = FakeConnection(
        [_status(505), _ack("level3"), _fixture_text("level3_snapshot.json")],
        on_last=stop_event.set,
    )
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([temporary_token]),
        public_connection_factory=ScriptedConnectionFactory([public]),
        l3_connection_factory=ExitFailingConnectionFactory(level3, lower_failure),
        session_id_factory=SessionIds(),
    )

    try:
        captured = await _capture_transport_error(collector, stop_event)
    finally:
        await sink.aclose()

    rendered = "".join(
        traceback.TracebackException.from_exception(
            captured,
            capture_locals=True,
        ).format()
    )
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        persisted_bytes = b"\n".join(
            row[0]
            for row in connection.execute(
                "SELECT payload_bytes FROM raw_records WHERE venue = 'kraken'"
            ).fetchall()
        )
    finally:
        connection.close()
    file_bytes = (
        b"".join(path.read_bytes() for path in writer.parquet_files) + database_path.read_bytes()
    )
    for material in (temporary_token, lower_failure):
        assert material not in rendered
        assert material not in caplog.text
        assert material.encode("utf-8") not in _all_record_bytes(sink.records)
        assert material.encode("utf-8") not in persisted_bytes
        assert material.encode("utf-8") not in file_bytes
    assert captured.__context__ is None
    assert captured.__cause__ is None
    assert any(
        event.get("event") == "gap_detected" and event.get("stream") == "l3"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_inbound_token_echo_is_absent_from_all_persistence_and_exception_surfaces(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    temporary_token = secrets.token_urlsafe(36)
    echoed_payload = json.dumps(
        {"channel": "level3", "token": temporary_token},
        separators=(",", ":"),
    )
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    sink = RecordingParquetSink(writer)
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([temporary_token]),
        public_connection_factory=ScriptedConnectionFactory(
            [
                FakeConnection(
                    [
                        _status(606),
                        _ack("trade"),
                        _ack("book"),
                        _fixture_text("book_snapshot.json"),
                    ]
                )
            ]
        ),
        l3_connection_factory=ScriptedConnectionFactory(
            [FakeConnection([_status(707), _ack("level3"), echoed_payload])]
        ),
        session_id_factory=SessionIds(),
    )

    try:
        captured = await _capture_collector_authentication_error(collector)
    finally:
        await sink.aclose()

    rendered = "".join(
        traceback.TracebackException.from_exception(
            captured,
            capture_locals=True,
        ).format()
    )
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        persisted_bytes = b"\n".join(
            row[0]
            for row in connection.execute(
                "SELECT payload_bytes FROM raw_records WHERE venue = 'kraken'"
            ).fetchall()
        )
    finally:
        connection.close()
    file_bytes = (
        b"".join(path.read_bytes() for path in writer.parquet_files) + database_path.read_bytes()
    )
    assert temporary_token not in rendered
    assert temporary_token not in caplog.text
    assert temporary_token.encode("utf-8") not in _all_record_bytes(sink.records)
    assert temporary_token.encode("utf-8") not in persisted_bytes
    assert temporary_token.encode("utf-8") not in file_bytes
    assert captured.__context__ is None
    assert captured.__cause__ is None
    assert not any(record.channel == "level3" for record in _market_frames(sink.records))
    assert any(
        event.get("event") == "credential_echo_blocked"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_authenticated_ack_allows_omitted_depth_but_rejects_wrong_depth() -> None:
    temporary_token = secrets.token_urlsafe(36)
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([temporary_token]),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )

    without_depth = cast(
        dict[str, object],
        json.loads(_ack("level3", include_depth=False), parse_int=str),
    )
    assert (
        await collector._record_subscription_response(
            without_depth,
            "l3-session-1",
            stream="l3",
            authenticated=True,
            expected_channels=frozenset({"level3"}),
        )
        == "level3"
    )

    wrong_depth = cast(dict[str, object], json.loads(_ack("level3"), parse_int=str))
    wrong_result = cast(dict[str, object], wrong_depth["result"])
    wrong_result["depth"] = "100"
    with pytest.raises(KrakenDataIntegrityError, match="schema"):
        await collector._record_subscription_response(
            wrong_depth,
            "l3-session-1",
            stream="l3",
            authenticated=True,
            expected_channels=frozenset({"level3"}),
        )

    assert _market_frames(sink.records) == []
    assert temporary_token.encode("utf-8") not in _all_record_bytes(sink.records)


@pytest.mark.asyncio
async def test_checksum_failure_preserves_exact_market_frame_then_stops() -> None:
    websocket_token = secrets.token_urlsafe(36)
    bad_document = _fixture_document("level3_snapshot.json")
    data = cast(list[dict[str, object]], bad_document["data"])
    data[0]["checksum"] = "1"
    bad_payload = json.dumps(bad_document, separators=(",", ":"), sort_keys=False)
    level3 = FakeConnection([_ack("level3"), bad_payload])
    stop_event = asyncio.Event()
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([websocket_token]),
        l3_connection_factory=ScriptedConnectionFactory([level3]),
        public_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )

    with pytest.raises(KrakenDataIntegrityError, match="checksum"):
        await collector._receive_l3(
            level3,
            "l3-session-1",
            KrakenWebSocketToken(websocket_token),
            stop_event,
        )

    raw = _market_frames(sink.records)
    assert len(raw) == 1
    assert raw[0].payload_bytes == bad_payload.encode("utf-8")
    assert any(
        event.get("event") == "checksum_error"
        and event.get("raw_message_ordinal") == raw[0].message_ordinal
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_schema_failure_is_visible_and_preserves_recognized_market_frame() -> None:
    bad_payload = '{"channel":"book","type":"update","data":[]}'
    connection = FakeConnection([_ack("trade"), _ack("book"), bad_payload])
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([secrets.token_urlsafe(24)]),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )

    with pytest.raises(KrakenDataIntegrityError, match="schema"):
        await collector._receive_public(connection, "public-session-1", asyncio.Event())

    raw = _market_frames(sink.records)
    assert len(raw) == 1
    assert raw[0].payload_bytes == bad_payload.encode("utf-8")
    assert any(
        event.get("event") == "schema_error"
        and event.get("raw_message_ordinal") == raw[0].message_ordinal
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_failed_l3_reconnect_is_visible_terminal_and_sanitized() -> None:
    first_token = secrets.token_urlsafe(36)
    second_token = secrets.token_urlsafe(36)
    lower_failure = secrets.token_urlsafe(36)
    first = FakeConnection(
        [_ack("level3"), _fixture_text("level3_snapshot.json")],
        receive_failure_text=f"synthetic receive failure {first_token}",
    )
    factory = ReconnectFailingFactory(first, lower_failure)
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([first_token, second_token]),
        config=KrakenL3ResearchConfig(reconnect_delay_seconds=0.0),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=factory,
        session_id_factory=SessionIds(),
    )

    with pytest.raises(KrakenTransportError) as captured:
        await collector._run_l3_stream(asyncio.Event())

    formatted = "".join(traceback.format_exception(captured.value))
    assert captured.value.__context__ is None
    assert lower_failure not in formatted
    assert factory.calls == 2
    assert any(
        event.get("event") == "reconnect_failed" and event.get("stream") == "l3"
        for event in _marker_documents(sink.records, channel="session")
    )
    assert any(
        event.get("event") == "gap_detected"
        for event in _marker_documents(sink.records, channel="data_quality")
    )
    record_bytes = _all_record_bytes(sink.records)
    for secret in (first_token, second_token, lower_failure):
        assert secret.encode("utf-8") not in record_bytes


@pytest.mark.asyncio
async def test_oversize_schema_fails_before_raw_persistence() -> None:
    websocket_token = secrets.token_urlsafe(36)
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        SyntheticTokenProvider([websocket_token]),
        config=KrakenL3ResearchConfig(max_application_payload_bytes=32),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )
    oversized = capture_application_payload(b"{" + b"x" * 64 + b"}")

    with pytest.raises(KrakenDataIntegrityError, match="exceeded"):
        await collector._decode_inbound(
            oversized,
            "l3-session-1",
            "l3",
            token=KrakenWebSocketToken(websocket_token),
        )

    assert _market_frames(sink.records) == []
    assert websocket_token.encode("utf-8") not in _all_record_bytes(sink.records)


def test_l3_depth_truncation_is_local_scope_event_not_a_wire_delete() -> None:
    state = _L3BookState(1)
    document: dict[str, object] = {
        "channel": "level3",
        "type": "snapshot",
        "data": [
            {
                "symbol": "BTC/EUR",
                "bids": [
                    {
                        "order_id": "BEST-BID",
                        "limit_price": "2.0000",
                        "order_qty": "1.0000",
                        "timestamp": "2026-08-31T10:00:00.000000001Z",
                    },
                    {
                        "order_id": "OUT-OF-SCOPE-BID",
                        "limit_price": "1.0000",
                        "order_qty": "1.0000",
                        "timestamp": "2026-08-31T10:00:00.000000002Z",
                    },
                ],
                "asks": [
                    {
                        "order_id": "BEST-ASK",
                        "limit_price": "3.0000",
                        "order_qty": "1.0000",
                        "timestamp": "2026-08-31T10:00:00.000000001Z",
                    },
                    {
                        "order_id": "OUT-OF-SCOPE-ASK",
                        "limit_price": "4.0000",
                        "order_qty": "1.0000",
                        "timestamp": "2026-08-31T10:00:00.000000002Z",
                    },
                ],
                "checksum": "3946982796",
                "timestamp": "2026-08-31T10:00:00.000000003Z",
            }
        ],
    }

    normalized = state.normalize(document, 7)

    events = cast(list[dict[str, object]], normalized["events"])
    scope_events = [event for event in events if event["event"] == "scope_truncate"]
    assert {event["order_id"] for event in scope_events} == {
        "OUT-OF-SCOPE-BID",
        "OUT-OF-SCOPE-ASK",
    }
    assert all(event["event_source"] == "local_scope" for event in scope_events)
    assert all(event["wire_order"] is None for event in scope_events)


def test_official_kraken_l2_and_l3_checksum_vectors() -> None:
    l2_normalized = _L2BookState(10).normalize(
        _fixture_document("official_book_checksum_vector.json"),
        1,
    )
    l3_normalized = _L3BookState(10).normalize(
        _fixture_document("official_level3_checksum_vector.json"),
        2,
    )

    assert l2_normalized["checksum"] == "3310070434"
    assert l3_normalized["checksum"] == "1063832831"
    assert l3_normalized["message_timestamp"] is None


@pytest.mark.asyncio
async def test_terminal_failure_gracefully_stops_peer_without_cancelling_sink_boundary() -> None:
    class CoordinatedCollector(KrakenL3ResearchCollector):
        def __init__(self) -> None:
            super().__init__(
                MemorySink(),
                SyntheticTokenProvider([secrets.token_urlsafe(24)]),
                public_connection_factory=ScriptedConnectionFactory([]),
                l3_connection_factory=ScriptedConnectionFactory([]),
            )
            self.peer_started = asyncio.Event()
            self.peer_stopping = asyncio.Event()
            self.release_peer = asyncio.Event()
            self.peer_cancelled = False

        async def _run_public_stream(self, stop_event: asyncio.Event) -> None:
            self.peer_started.set()
            await stop_event.wait()
            self.peer_stopping.set()
            try:
                await self.release_peer.wait()
            except asyncio.CancelledError:
                self.peer_cancelled = True
                raise

        async def _run_l3_stream(self, stop_event: asyncio.Event) -> None:
            del stop_event
            await self.peer_started.wait()
            raise KrakenAuthenticationError("synthetic terminal failure")

    collector = CoordinatedCollector()
    capture = asyncio.create_task(collector.capture_for(5.0))

    await asyncio.wait_for(collector.peer_stopping.wait(), timeout=1.0)
    assert not capture.done()
    assert not collector.peer_cancelled
    collector.release_peer.set()
    with pytest.raises(KrakenAuthenticationError, match="synthetic terminal failure"):
        await capture
    assert not collector.peer_cancelled


@pytest.mark.parametrize("duration", [0.0, 604800.1, -1.0])
@pytest.mark.asyncio
async def test_capture_duration_is_strictly_bounded(duration: float) -> None:
    collector = KrakenL3ResearchCollector(
        MemorySink(),
        SyntheticTokenProvider([secrets.token_urlsafe(24)]),
        public_connection_factory=ScriptedConnectionFactory([]),
        l3_connection_factory=ScriptedConnectionFactory([]),
    )
    with pytest.raises(ValueError, match="between 1 and 604800"):
        await collector.capture_for(duration)


def test_retained_duration_raises_the_historical_smoke_cap() -> None:
    assert SMOKE_CAPTURE_SECONDS == 600.0
    assert MAX_CAPTURE_SECONDS == 7 * 24 * 60 * 60
    assert _require_bounded_duration(600.1) == 600.1
    assert _require_bounded_duration(86_400) == 86_400.0
    assert _require_bounded_duration(MAX_CAPTURE_SECONDS) == float(MAX_CAPTURE_SECONDS)
    with pytest.raises(ValueError, match="between 1 and 604800"):
        _require_bounded_duration(MAX_CAPTURE_SECONDS + 1)


def test_optional_l3_env_loader_refuses_wrong_key_types_without_printing_values() -> None:
    secret = "super-secret-value-must-never-be-printed"
    with pytest.raises(KrakenAuthenticationError, match="KRAKEN_API_SECRET") as error:
        refuse_protected_trade_keys({"KRAKEN_API_SECRET": secret})
    assert secret not in str(error.value)
    assert load_optional_l3_token_provider({}) is None
    with pytest.raises(KrakenAuthenticationError, match="KRAKEN_WS_API_SECRET"):
        load_optional_l3_token_provider({KRAKEN_WS_API_KEY_ENV: "ws-only-key"})
    provider = load_optional_l3_token_provider(
        {
            KRAKEN_WS_API_KEY_ENV: "ws-only-key-value",
            KRAKEN_WS_API_SECRET_ENV: base64.b64encode(b"ws-only-secret-bytes").decode("ascii"),
        }
    )
    assert provider is not None
    assert "ws-only-key-value" not in repr(provider)


@pytest.mark.asyncio
async def test_public_only_capture_succeeds_without_l3(tmp_path: Path) -> None:
    stop_event = asyncio.Event()
    public = FakeConnection(
        [_ack("trade"), _ack("book"), _fixture_text("book_snapshot.json")],
        on_last=stop_event.set,
    )
    sink = MemorySink()
    collector = KrakenL3ResearchCollector(
        sink,
        token_provider=None,
        public_connection_factory=ScriptedConnectionFactory([public]),
        l3_connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    channels = {record.channel for record in _market_frames(sink.records)}
    assert "book" in channels
    assert "level3" not in channels


@pytest.mark.asyncio
async def test_reconstructable_public_capture_writes_the_path_contract(tmp_path: Path) -> None:
    stop_event = asyncio.Event()

    def collector_factory(sink: RawResearchSink) -> KrakenL3ResearchCollector:
        return KrakenL3ResearchCollector(
            sink,
            token_provider=None,
            public_connection_factory=ScriptedConnectionFactory(
                [
                    FakeConnection(
                        [_ack("trade"), _ack("book"), _fixture_text("book_snapshot.json")],
                        on_last=stop_event.set,
                    )
                ]
            ),
            session_id_factory=SessionIds(),
        )

    report = await run_reconstructable_capture(
        artifact_root=tmp_path,
        run_id="sample-run",
        duration_seconds=86_400,
        token_provider=None,
        stop_event=stop_event,
        collector_factory=collector_factory,
    )
    paths = data1b_run_paths(tmp_path, "sample-run")
    assert report["path_contract"] == DATA1B_PATH_CONTRACT_ID
    assert report["status"] == "COMPLETED"
    assert report["authenticated_l3"] is False
    assert report["twenty_four_seven"] is False
    claim = json.loads(paths.capture_claim_path.read_text(encoding="utf-8"))
    health = json.loads(paths.capture_health_path.read_text(encoding="utf-8"))
    assert claim["retained"] is True
    assert claim["authenticated_l3"] is False
    assert claim["credentialless"] is True
    assert health["status"] == "COMPLETED"
    with pytest.raises(FileExistsError, match="refuses to reuse"):
        await run_reconstructable_capture(
            artifact_root=tmp_path,
            run_id="sample-run",
            duration_seconds=60,
            collector_factory=collector_factory,
        )


def test_data1b_claim_and_health_are_create_only_and_not_twenty_four_seven() -> None:
    paths = data1b_run_paths(Path("/var/reconstructable"), "sample-run")
    claim = data1b_capture_claim(
        run_id="sample-run",
        duration_seconds=86_400,
        paths=paths,
        include_l3=False,
    )
    health = data1b_capture_health(
        run_id="sample-run",
        duration_seconds=86_400,
        status="COMPLETED",
        report={
            "events": 0,
            "payload_bytes": 0,
            "parquet_files": 0,
            "parquet_bytes": 0,
            "gaps": 0,
            "reconnects": 0,
        },
        include_l3=False,
    )
    assert claim["schema"] == "data-1b-retained-capture-claim-v1"
    assert claim["path_contract"] == DATA1B_PATH_CONTRACT_ID
    assert claim["resume_policy"] == "never resume or overwrite an existing DATA-1B run directory"
    assert claim["retained"] is True
    assert health["twenty_four_seven"] is False
    assert health["authenticated_l3"] is False


def test_cli_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    parser = _argument_parser()
    reconstructable = parser.parse_args(
        ["--artifact-root", str(tmp_path), "--run-id", "sample-run", "--duration-seconds", "3600"]
    )
    assert _resolve_cli_mode(reconstructable) == "reconstructable"
    ad_hoc = parser.parse_args(
        [
            "--output-dir",
            str(tmp_path / "raw"),
            "--database",
            str(tmp_path / "research.duckdb"),
            "--duration-seconds",
            "60",
        ]
    )
    assert _resolve_cli_mode(ad_hoc) == "ad_hoc"
    mixed = parser.parse_args(
        [
            "--artifact-root",
            str(tmp_path),
            "--run-id",
            "sample-run",
            "--output-dir",
            str(tmp_path / "raw"),
            "--database",
            str(tmp_path / "research.duckdb"),
            "--duration-seconds",
            "60",
        ]
    )
    with pytest.raises(ValueError, match="not both"):
        _resolve_cli_mode(mixed)
