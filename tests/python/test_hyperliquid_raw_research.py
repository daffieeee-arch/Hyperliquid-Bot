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

import hyperliquid_bot.hyperliquid_raw_research as hyperliquid_raw_research_module
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
from hyperliquid_bot.hyperliquid_retained_instruments import build_hyperliquid_retained_plan
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
            heartbeat_interval_seconds=45.0,
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
            heartbeat_interval_seconds=45.0,
            reconnect_delay_seconds=0.0,
        ),
        connection_factory=factory,
        session_id_factory=lambda: next(session_ids),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    session_events = _local_events(sink.records, "session")
    assert any(
        event.get("event") == "disconnected"
        and event.get("reason") == "transport_error"
        and event.get("exception_class") == "ConnectionError"
        and event.get("transport_profile") == "hyperliquid_public"
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
    assert health["duration_seconds"] == 86_400.0
    assert float(health["elapsed_seconds"]) < float(health["duration_seconds"])
    assert health["elapsed_seconds"] != health["duration_seconds"]
    log_path = paths.run_dir / "capture-sample-run.log"
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    assert "data1a start" in log_text
    assert "requested_duration_seconds" in log_text
    assert "elapsed_seconds" in log_text
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
            "elapsed_seconds": 86_400.0,
        },
    )
    stored_claim = json.loads((fixture_dir / "capture-claim.json").read_text(encoding="utf-8"))
    stored_health = json.loads((fixture_dir / "capture-health.json").read_text(encoding="utf-8"))
    for key, value in stored_claim.items():
        assert claim[key] == value
    for key, value in stored_health.items():
        assert health[key] == value
    assert health["elapsed_seconds"] == 86_400.0
    assert health["duration_seconds"] == 86_400.0


def test_heartbeat_matches_known_good_client_idle_window() -> None:
    default = HyperliquidRawResearchConfig()
    assert default.heartbeat_interval_seconds == 45.0
    assert default.receive_timeout_seconds == 60.0
    with pytest.raises(ValueError, match=r"\[5, 60\)"):
        HyperliquidRawResearchConfig(heartbeat_interval_seconds=300.0)
    with pytest.raises(ValueError, match=r"\[5, 60\)"):
        HyperliquidRawResearchConfig(heartbeat_interval_seconds=60.0)
    with pytest.raises(ValueError, match=r"\[5, 60\)"):
        HyperliquidRawResearchConfig(heartbeat_interval_seconds=0.1)
    with pytest.raises(ValueError, match="receive_timeout"):
        HyperliquidRawResearchConfig(
            heartbeat_interval_seconds=45.0,
            receive_timeout_seconds=10.0,
        )


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


@pytest.mark.asyncio
async def test_enabled_addons_subscribe_eth_and_sol_without_dropping_btc() -> None:
    stop_event = asyncio.Event()
    inbound = [
        "Websocket connection established.",
        *[
            json.dumps(
                {
                    "channel": "subscriptionResponse",
                    "data": {
                        "method": "subscribe",
                        "subscription": {"type": channel, "coin": coin},
                    },
                },
                separators=(",", ":"),
            )
            for coin in ("BTC", "ETH", "SOL")
            for channel in ("trades", "bbo", "l2Book", "activeAssetCtx")
        ],
        _fixture_text("trades_frame.json"),
    ]
    connection = FakeConnection(inbound, stop_event=stop_event)
    collector = HyperliquidRawResearchCollector(
        MemorySink(),
        instrument_plan=build_hyperliquid_retained_plan(
            addon_coins="ETH,SOL",
            enable_addons=True,
        ),
        connection_factory=ScriptedConnectionFactory([connection]),
        session_id_factory=lambda: "session-addons",
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    sent = [str(item) for item in connection.sent]
    assert sent[0] == '{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}'
    assert any('"coin":"ETH"' in item and "trades" in item for item in sent)
    assert any('"coin":"SOL"' in item and "bbo" in item for item in sent)
    assert sum(1 for item in sent if '"coin":"BTC"' in item) == 4


def _btc_market_frames() -> list[str]:
    return [
        _fixture_text("trades_frame.json"),
        _fixture_text("bbo_frame.json"),
        _fixture_text("l2_book_frame.json"),
        _fixture_text("active_asset_ctx_frame.json"),
    ]


class _HangAfterMessages(FakeConnection):
    async def recv(self) -> str | bytes:
        if self._messages:
            return await super().recv()
        await asyncio.sleep(30)
        raise AssertionError("silence watch should have reconnected before this hang ended")


class _OnlyBbo(FakeConnection):
    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        return _fixture_text("bbo_frame.json")


@pytest.mark.asyncio
async def test_heartbeat_does_not_count_as_market_data_validity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hyperliquid_raw_research_module, "_MARKET_DATA_STALE_SECONDS", 0.05)

    stop_event = asyncio.Event()
    inbound = [
        "Websocket connection established.",
        *[
            _subscription_response(channel)
            for channel in ("trades", "bbo", "l2Book", "activeAssetCtx")
        ],
    ]
    recovered = FakeConnection(
        [
            "Websocket connection established.",
            *_btc_market_frames(),
        ],
        stop_event=stop_event,
    )
    factory = ScriptedConnectionFactory([_HangAfterMessages(inbound), recovered])
    sink = MemorySink()
    collector = HyperliquidRawResearchCollector(
        sink,
        config=HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
        connection_factory=factory,
        session_id_factory=lambda: "session-stale",
    )
    await collector.capture_for(2.0, stop_event=stop_event)
    quality = _local_events(sink.records, "data_quality")
    stale = [event for event in quality if event["event"] == "market_data_stale_despite_heartbeat"]
    assert stale
    assert stale[0]["coin"] == "BTC"
    assert stale[0]["market_channel"] in {"trades", "bbo", "l2Book", "activeAssetCtx"}
    assert any(event["event"] == "market_data_recovered" for event in quality)
    sessions = _local_events(sink.records, "session")
    assert any(event["event"] == "reconnected" for event in sessions)
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_one_required_channel_cannot_hide_another_silent_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hyperliquid_raw_research_module, "_MARKET_DATA_STALE_SECONDS", 0.05)
    stop_event = asyncio.Event()
    recovered = FakeConnection(_btc_market_frames(), stop_event=stop_event)
    factory = ScriptedConnectionFactory([_OnlyBbo([]), recovered])
    sink = MemorySink()
    collector = HyperliquidRawResearchCollector(
        sink,
        config=HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
        connection_factory=factory,
        session_id_factory=lambda: "session-split",
    )
    await collector.capture_for(2.0, stop_event=stop_event)
    quality = _local_events(sink.records, "data_quality")
    stale = [event for event in quality if event["event"] == "market_data_stale_despite_heartbeat"]
    assert stale
    assert {event["market_channel"] for event in stale} >= {"trades"}
    assert all(event["coin"] == "BTC" for event in stale)
    assert any(
        event["event"] == "market_data_recovered" and event["market_channel"] == "trades"
        for event in quality
    )
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_optional_addon_silence_does_not_reconnect_required_btc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hyperliquid_raw_research_module, "_MARKET_DATA_STALE_SECONDS", 0.05)
    stop_event = asyncio.Event()

    class _BtcKeepsFlowing(FakeConnection):
        def __init__(self) -> None:
            super().__init__([])
            self._index = 0

        async def recv(self) -> str | bytes:
            await asyncio.sleep(0.01)
            frames = _btc_market_frames()
            message = frames[self._index % len(frames)]
            self._index += 1
            if self._index >= 12:
                stop_event.set()
            return message

    factory = ScriptedConnectionFactory([_BtcKeepsFlowing()])
    sink = MemorySink()
    collector = HyperliquidRawResearchCollector(
        sink,
        config=HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
        instrument_plan=build_hyperliquid_retained_plan(addon_coins="ETH,SOL", enable_addons=True),
        connection_factory=factory,
        session_id_factory=lambda: "session-addon",
    )
    await collector.capture_for(2.0, stop_event=stop_event)
    quality = _local_events(sink.records, "data_quality")
    assert not any(event["event"] == "market_data_stale_despite_heartbeat" for event in quality)
    assert factory.calls == 1
