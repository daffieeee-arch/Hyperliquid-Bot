"""Deterministic offline tests for the bounded Hyperliquid research collector."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import pytest

from hyperliquid_bot.hyperliquid_raw_research import (
    MAX_CAPTURE_SECONDS,
    SMOKE_CAPTURE_SECONDS,
    HyperliquidRawResearchCollector,
    HyperliquidRawResearchConfig,
    WebSocketConnection,
    _argument_parser,
    _require_bounded_duration,
    _resolve_cli_mode,
    data1a_capture_claim,
    data1a_capture_health,
    run_reconstructable_capture,
)
from hyperliquid_bot.raw_research import MessageDirection, RawResearchRecord
from hyperliquid_bot.reconstructable_paths import (
    DATA1A_PATH_CONTRACT_ID,
    data1a_run_paths,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "hyperliquid"


def _fixture_text(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _subscription_response(subscription_type: str) -> str:
    return json.dumps(
        {
            "channel": "subscriptionResponse",
            "data": {
                "method": "subscribe",
                "subscription": {"type": subscription_type, "coin": "BTC"},
            },
        },
        separators=(",", ":"),
    )


class MemorySink:
    def __init__(self, *, fail_on_append: int | None = None) -> None:
        self.records: list[RawResearchRecord] = []
        self.fail_on_append = fail_on_append
        self.append_attempts = 0

    async def append(self, record: RawResearchRecord) -> None:
        self.append_attempts += 1
        if self.append_attempts == self.fail_on_append:
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


class FakeConnection:
    def __init__(
        self,
        messages: Sequence[str | bytes],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._messages = deque(messages)
        self._stop_event = stop_event
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            raise ConnectionError("scripted disconnect")
        message = self._messages.popleft()
        if not self._messages and self._stop_event is not None:
            self._stop_event.set()
        return message


class TimingConnection:
    def __init__(self, trace: list[str]) -> None:
        self._trace = trace

    async def send(self, message: str | bytes) -> None:
        del message

    async def recv(self) -> str | bytes:
        self._trace.append("recv-return")
        return '{"channel":"bbo","data":{}}'


@asynccontextmanager
async def _fake_context(connection: FakeConnection) -> AsyncIterator[WebSocketConnection]:
    yield connection


class ScriptedConnectionFactory:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self._connections = deque(connections)
        self.calls = 0

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls += 1
        if not self._connections:
            raise AssertionError("collector requested an unexpected connection")
        return _fake_context(self._connections.popleft())


def _local_events(records: list[RawResearchRecord], channel: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for record in records:
        if record.direction is not MessageDirection.LOCAL or record.channel != channel:
            continue
        loaded = json.loads(record.payload_bytes)
        if type(loaded) is not dict:
            raise AssertionError("local marker must be a JSON object")
        events.append(cast(dict[str, object], loaded))
    return events


@pytest.mark.asyncio
async def test_receive_task_timestamps_at_recv_callback_entry() -> None:
    trace: list[str] = []

    def utc_ns() -> int:
        trace.append("utc-clock")
        return 101

    def monotonic_ns() -> int:
        trace.append("monotonic-clock")
        return 202

    collector = HyperliquidRawResearchCollector(
        MemorySink(),
        connection_factory=ScriptedConnectionFactory([]),
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
    )

    captured = await collector._receive_captured(TimingConnection(trace))

    assert trace == ["recv-return", "utc-clock", "monotonic-clock"]
    assert captured.received_utc_ns == 101
    assert captured.received_monotonic_ns == 202
    assert captured.payload_bytes == b'{"channel":"bbo","data":{}}'


@pytest.mark.asyncio
async def test_four_exact_subscriptions_and_market_payloads_are_captured() -> None:
    stop_event = asyncio.Event()
    inbound_messages = [
        "Websocket connection established.",
        *[
            _subscription_response(subscription_type)
            for subscription_type in ("trades", "bbo", "l2Book", "activeAssetCtx")
        ],
        _fixture_text("trades_frame.json"),
        _fixture_text("bbo_frame.json"),
        _fixture_text("l2_book_frame.json"),
        _fixture_text("active_asset_ctx_frame.json"),
    ]
    connection = FakeConnection(inbound_messages, stop_event=stop_event)
    factory = ScriptedConnectionFactory([connection])
    sink = MemorySink()
    utc_ns = Counter(1_788_105_600_000_000_000)
    monotonic_ns = Counter(9_000_000_000)
    collector = HyperliquidRawResearchCollector(
        sink,
        config=HyperliquidRawResearchConfig(
            heartbeat_interval_seconds=300.0,
            reconnect_delay_seconds=0.0,
        ),
        connection_factory=factory,
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
        session_id_factory=lambda: "session-one",
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    expected_subscriptions = [
        '{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}',
        '{"method":"subscribe","subscription":{"type":"bbo","coin":"BTC"}}',
        '{"method":"subscribe","subscription":{"type":"l2Book","coin":"BTC"}}',
        '{"method":"subscribe","subscription":{"type":"activeAssetCtx","coin":"BTC"}}',
    ]
    assert connection.sent == expected_subscriptions
    outbound_payloads = [
        record.payload_bytes.decode("utf-8")
        for record in sink.records
        if record.direction is MessageDirection.OUTBOUND and record.channel == "subscription"
    ]
    assert outbound_payloads == expected_subscriptions

    inbound_by_channel = {
        record.channel: record.payload_bytes
        for record in sink.records
        if record.direction is MessageDirection.INBOUND
        and record.channel in {"trades", "bbo", "l2Book", "activeAssetCtx"}
    }
    assert inbound_by_channel == {
        "trades": _fixture_text("trades_frame.json").encode("utf-8"),
        "bbo": _fixture_text("bbo_frame.json").encode("utf-8"),
        "l2Book": _fixture_text("l2_book_frame.json").encode("utf-8"),
        "activeAssetCtx": _fixture_text("active_asset_ctx_frame.json").encode("utf-8"),
    }
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )
    assert {record.session_id for record in sink.records} == {"session-one"}
    subscription_events = _local_events(sink.records, "subscription")
    assert [event["event"] for event in subscription_events].count("subscription_sent") == 4
    assert [event["event"] for event in subscription_events].count("subscription_acknowledged") == 4
    assert [event["event"] for event in subscription_events].count("subscriptions_active") == 1
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_disconnect_reconnect_gap_and_malformed_payload_are_visible() -> None:
    stop_event = asyncio.Event()
    first = FakeConnection([_fixture_text("trades_frame.json")])
    malformed = b"\x00not-json\xff"
    second = FakeConnection(
        [malformed, _fixture_text("bbo_frame.json")],
        stop_event=stop_event,
    )
    factory = ScriptedConnectionFactory([first, second])
    session_ids = iter(("session-one", "session-two"))
    sink = MemorySink()
    collector = HyperliquidRawResearchCollector(
        sink,
        config=HyperliquidRawResearchConfig(
            heartbeat_interval_seconds=300.0,
            reconnect_delay_seconds=0.0,
        ),
        connection_factory=factory,
        session_id_factory=lambda: next(session_ids),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    session_events = _local_events(sink.records, "session")
    assert any(
        event.get("event") == "disconnected" and event.get("reason") == "transport_error"
        for event in session_events
    )
    assert any(
        event.get("event") == "reconnected" and event.get("previous_session_id") == "session-one"
        for event in session_events
    )
    assert any(event.get("event") == "session_stopped" for event in session_events)

    quality_events = _local_events(sink.records, "data_quality")
    assert any(event.get("event") == "gap_detected" for event in quality_events)
    assert any(event.get("event") == "unclassified_payload" for event in quality_events)
    malformed_records = [
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND and record.channel == "unknown"
    ]
    assert len(malformed_records) == 1
    assert malformed_records[0].payload_bytes == malformed
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_sink_failure_stops_without_silent_reconnect() -> None:
    stop_event = asyncio.Event()
    connection = FakeConnection([_fixture_text("trades_frame.json")], stop_event=stop_event)
    factory = ScriptedConnectionFactory([connection])
    sink = MemorySink(fail_on_append=3)
    collector = HyperliquidRawResearchCollector(
        sink,
        connection_factory=factory,
        session_id_factory=lambda: "session-one",
    )

    with pytest.raises(RuntimeError, match="injected sink failure"):
        await collector.capture_for(5.0, stop_event=stop_event)

    assert factory.calls == 1
    assert sink.append_attempts == 3


@pytest.mark.parametrize("duration", [0.0, -1.0, MAX_CAPTURE_SECONDS + 0.1])
@pytest.mark.asyncio
async def test_capture_duration_is_strictly_bounded(duration: float) -> None:
    collector = HyperliquidRawResearchCollector(
        MemorySink(),
        connection_factory=ScriptedConnectionFactory([]),
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


@pytest.mark.asyncio
async def test_reconstructable_capture_writes_the_path_contract(tmp_path: Path) -> None:
    stop_event = asyncio.Event()
    inbound = [
        "Websocket connection established.",
        *[
            _subscription_response(channel)
            for channel in ("trades", "bbo", "l2Book", "activeAssetCtx")
        ],
        _fixture_text("trades_frame.json"),
    ]
    factory = ScriptedConnectionFactory([FakeConnection(inbound, stop_event=stop_event)])
    report = await run_reconstructable_capture(
        artifact_root=tmp_path,
        run_id="sample-run",
        duration_seconds=86_400,
        stop_event=stop_event,
        connection_factory=factory,
    )
    paths = data1a_run_paths(tmp_path, "sample-run")
    assert report["path_contract"] == DATA1A_PATH_CONTRACT_ID
    assert report["status"] == "COMPLETED"
    assert report["twenty_four_seven"] is False
    assert paths.capture_claim_path.is_file()
    assert paths.capture_health_path.is_file()
    assert paths.database_path.is_file()
    assert list(paths.raw_dir.glob(paths.parquet_glob))
    claim = json.loads(paths.capture_claim_path.read_text(encoding="utf-8"))
    health = json.loads(paths.capture_health_path.read_text(encoding="utf-8"))
    assert claim["retained"] is True
    assert claim["duration_seconds"] == 86_400.0
    assert claim["twenty_four_seven"] is False
    assert health["status"] == "COMPLETED"
    assert health["path_contract"] == DATA1A_PATH_CONTRACT_ID
    with pytest.raises(FileExistsError, match="refuses to reuse"):
        await run_reconstructable_capture(
            artifact_root=tmp_path,
            run_id="sample-run",
            duration_seconds=60,
            connection_factory=ScriptedConnectionFactory([]),
        )


def test_data1a_claim_and_health_match_committed_sample_contract() -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures" / "data_1a_retained" / "sample-run"
    paths = data1a_run_paths(Path("/var/reconstructable"), "sample-run")
    claim = data1a_capture_claim(run_id="sample-run", duration_seconds=86_400, paths=paths)
    health = data1a_capture_health(
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
    )
    stored_claim = json.loads((fixture_dir / "capture-claim.json").read_text(encoding="utf-8"))
    stored_health = json.loads((fixture_dir / "capture-health.json").read_text(encoding="utf-8"))
    for key, value in stored_claim.items():
        assert claim[key] == value
    for key, value in stored_health.items():
        assert health[key] == value


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
