"""Synthetic native transport, backpressure and raw-provenance counterexamples."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.connection import Connection
from websockets.exceptions import ConnectionClosedError, PayloadTooBig
from websockets.frames import Close, Frame, Opcode
from websockets.protocol import Protocol, Side

from hyperliquid_bot import hyperliquid_raw_research as native
from hyperliquid_bot.capture_live_status import config_identity
from hyperliquid_bot.hyperliquid_retained_instruments import build_hyperliquid_retained_plan
from hyperliquid_bot.parquet_research import ParquetResearchWriter, ParquetRotation
from hyperliquid_bot.raw_research import (
    MessageDirection,
    RawResearchRecord,
    capture_application_payload,
)
from hyperliquid_bot.reconstructable_paths import data1a_run_paths


class Sink:
    def __init__(self) -> None:
        self.records: list[RawResearchRecord] = []

    async def append(self, record: RawResearchRecord) -> None:
        self.records.append(record)

    async def aclose(self) -> None:
        pass


class ScriptedConnection:
    def __init__(self, frames: list[str | bytes | Exception], stop: asyncio.Event) -> None:
        self.frames = deque(frames)
        self.stop = stop

    async def send(self, message: str | bytes) -> None:
        pass

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        frame = self.frames.popleft()
        if not self.frames:
            self.stop.set()
        if isinstance(frame, Exception):
            raise frame
        return frame


class MemoryTransport(asyncio.Transport):
    """No socket: exercise the pinned protocol and async connection together."""

    def __init__(self) -> None:
        super().__init__()
        self.pauses = 0
        self.resumes = 0

    def set_write_buffer_limits(self, high: int | None = None, low: int | None = None) -> None:
        pass

    def pause_reading(self) -> None:
        self.pauses += 1

    def resume_reading(self) -> None:
        self.resumes += 1

    def write(self, data: Any) -> None:
        pass

    def close(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False


def native_connection() -> tuple[Connection, MemoryTransport]:
    protocol = Protocol(Side.CLIENT, max_size=native.FAST_TRANSPORT_MAX_MESSAGE_BYTES)
    connection = Connection(protocol, max_queue=native.FAST_TRANSPORT_MAX_QUEUE_FRAMES)
    transport = MemoryTransport()
    connection.connection_made(transport)
    return connection, transport


@pytest.mark.asyncio
@pytest.mark.parametrize("fast", [False, True])
async def test_actual_native_connect_kwargs_and_reconnect_selection(
    monkeypatch: pytest.MonkeyPatch, fast: bool
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def connect(url: str, **kwargs: Any) -> AsyncIterator[native.WebSocketConnection]:
        calls.append((url, kwargs))
        yield ScriptedConnection([], asyncio.Event())

    monkeypatch.setattr(native, "connect", connect)
    collector = native.HyperliquidRawResearchCollector(
        Sink(), instrument_plan=build_hyperliquid_retained_plan(l2_fast=fast)
    )
    for _ in range(2):
        async with collector._connection_factory():
            pass
    assert len(calls) == 2 and calls[0] == calls[1]
    url, kwargs = calls[0]
    assert url == native.HYPERLIQUID_MAINNET_WEBSOCKET_URL
    assert kwargs["max_size"] == (1024**2 if fast else 8 * 1024**2)
    assert kwargs["max_queue"] == (16 if fast else 1024)
    assert kwargs["compression"] == (None if fast else "deflate")
    assert kwargs["ping_interval"] is None
    assert kwargs["open_timeout"] == 10 and kwargs["close_timeout"] == 5


def test_fast_claim_binds_transport_limits_and_queue_semantics(tmp_path: Path) -> None:
    paths = data1a_run_paths(tmp_path, "transport-test")
    legacy = native.data1a_capture_claim(run_id="transport-test", duration_seconds=600, paths=paths)
    fast = native.data1a_capture_claim(
        run_id="transport-test",
        duration_seconds=600,
        paths=paths,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
    )
    fields = native.fast_transport_claim_fields()
    assert not any(key in legacy for key in fields)
    assert all(fast[key] == value for key, value in fields.items())
    assert fast["transport_queue_payload_high_water_bytes"] == 16 * 1024**2
    assert fast["transport_queue_limit_semantics"] == (
        "frame-high-water-backpressure-not-hard-byte-limit"
    )
    assert fast["transport_compression"] == "disabled"
    expected = {
        "feed": fast["feed"],
        "websocket_url": fast["websocket_url"],
        "heartbeat_interval_seconds": 45.0,
        "receive_timeout_seconds": 60.0,
        "market_data_stale_seconds": 90.0,
        "started_coins": ["BTC"],
        "l2_mode": "fast-5",
        "l2_max_levels_per_side": 5,
        "l2_subscription_payloads": fast["l2_subscription_payloads"],
        "l2_mode_requires_session_ack": True,
        **fields,
    }
    assert fast["config_identity"] == config_identity(expected)
    assert fast["config_identity"] != legacy["config_identity"]


@pytest.mark.asyncio
async def test_native_callback_can_overshoot_queue_high_water_without_losing_frames() -> None:
    connection, transport = native_connection()
    payload = b"x" * native.FAST_TRANSPORT_MAX_MESSAGE_BYTES
    count = native.FAST_TRANSPORT_MAX_QUEUE_FRAMES + 1
    connection.data_received(Frame(Opcode.BINARY, payload).serialize(mask=False) * count)
    assert transport.pauses == 1
    assert len(connection.recv_messages.frames) == count
    # A real callback can already contain more than the nominal 16 MiB budget.
    assert len(payload) * count > native.FAST_TRANSPORT_QUEUE_PAYLOAD_HIGH_WATER_BYTES
    for _ in range(count):
        assert await connection.recv() == payload
    assert len(connection.recv_messages.frames) == 0
    assert transport.resumes == 1
    connection.connection_lost(None)


@pytest.mark.asyncio
async def test_native_fragmented_message_limit_is_total_reassembled_bytes() -> None:
    connection, _transport = native_connection()
    half = b"x" * (native.FAST_TRANSPORT_MAX_MESSAGE_BYTES // 2)
    connection.data_received(
        Frame(Opcode.BINARY, half, fin=False).serialize(mask=False)
        + Frame(Opcode.CONT, half).serialize(mask=False)
    )
    assert await connection.recv() == half + half
    connection.data_received(
        Frame(Opcode.BINARY, half, fin=False).serialize(mask=False)
        + Frame(Opcode.CONT, half, fin=False).serialize(mask=False)
        + Frame(Opcode.CONT, b"!").serialize(mask=False)
    )
    assert isinstance(connection.protocol.parser_exc, PayloadTooBig)
    assert connection.protocol.close_sent is not None
    assert connection.protocol.close_sent.code == 1009
    connection.connection_lost(None)
    with pytest.raises(ConnectionClosedError) as error:
        await connection.recv()
    assert error.value.sent is not None and error.value.sent.code == 1009


@pytest.mark.asyncio
async def test_large_trade_payload_exact_bytes_clocks_and_live_accounting(tmp_path: Path) -> None:
    payload = '{"channel":"trades","data":[{"coin":"BTC","padding":"' + "€" * 40_000 + '"}]}'
    raw = payload.encode("utf-8")
    assert 64 * 1024 < len(raw) < native.FAST_TRANSPORT_MAX_MESSAGE_BYTES
    stop = asyncio.Event()

    @asynccontextmanager
    async def factory() -> AsyncIterator[native.WebSocketConnection]:
        yield ScriptedConnection([payload], stop)

    sink = Sink()
    collector = native.HyperliquidRawResearchCollector(
        sink,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        connection_factory=factory,
        utc_ns=lambda: 101,
        monotonic_ns=lambda: 202,
    )
    live = tmp_path / "capture-live.json"
    collector.set_live_status_path(live, "trade-bytes")
    await collector.capture_for(5, stop_event=stop)
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert len(inbound) == 1
    assert inbound[0].payload_bytes == raw
    assert inbound[0].payload_sha256 == hashlib.sha256(raw).hexdigest()
    assert inbound[0].received_utc_ns == 101 and inbound[0].received_monotonic_ns == 202
    status = json.loads(live.read_text())["source_status"]
    assert status["inbound_payload_bytes"] == len(raw)
    assert status["inbound_message_count"] == 1
    assert status["integrity_error_count"] == 0
    assert status["connection_state"] == "stopped"
    assert status["l2_session_ack"] is False
    assert [feed for feed in json.loads(live.read_text())["feeds"] if feed["state"] == "fresh"]


@pytest.mark.asyncio
async def test_oversize_disconnect_and_wrong_ack_count_separately(tmp_path: Path) -> None:
    stop = asyncio.Event()
    contexts = 0
    bad_ack = (
        '{"channel":"subscriptionResponse","data":{"method":"subscribe","subscription":'
        '{"type":"l2Book","coin":"BTC","fast":false}}}'
    )

    @asynccontextmanager
    async def factory() -> AsyncIterator[native.WebSocketConnection]:
        nonlocal contexts
        contexts += 1
        if contexts == 1:
            connection = ScriptedConnection(
                [ConnectionClosedError(None, Close(1009, "message too big"))], asyncio.Event()
            )
        else:
            connection = ScriptedConnection([bad_ack], stop)
        yield connection

    sink = Sink()
    collector = native.HyperliquidRawResearchCollector(
        sink,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        config=native.HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
        connection_factory=factory,
    )
    live = tmp_path / "live.json"
    collector.set_live_status_path(live, "errors")
    await collector.capture_for(5, stop_event=stop)
    status = json.loads(live.read_text())["source_status"]
    assert contexts == 2
    assert status["gap_count"] == 2
    assert status["reconnect_count"] == 1
    assert status["integrity_error_count"] == 1
    assert status["l2_session_ack"] is False
    assert not any(feed["state"] == "fresh" for feed in json.loads(live.read_text())["feeds"])
    markers = [json.loads(r.payload_bytes) for r in sink.records if r.channel == "data_quality"]
    assert any(marker.get("close_code_sent") == 1009 for marker in markers)
    assert any(r.payload_bytes == bad_ack.encode() for r in sink.records)


@pytest.mark.asyncio
async def test_live_mode_ack_belongs_only_to_the_current_connected_session(tmp_path: Path) -> None:
    stop = asyncio.Event()
    live = tmp_path / "live.json"
    snapshots: list[dict[str, Any]] = []
    ack = (
        '{"channel":"subscriptionResponse","data":{"method":"subscribe","subscription":'
        '{"type":"l2Book","coin":"BTC","fast":true}}}'
    )
    contexts = 0

    class FirstConnection:
        received_ack = False

        async def send(self, message: str | bytes) -> None:
            pass

        async def recv(self) -> str:
            if not self.received_ack:
                self.received_ack = True
                return ack
            collector.publish_live_status()
            snapshots.append(json.loads(live.read_text())["source_status"])
            raise ConnectionError("synthetic transport failure")

    @asynccontextmanager
    async def factory() -> AsyncIterator[native.WebSocketConnection]:
        nonlocal contexts
        contexts += 1
        if contexts == 1:
            yield FirstConnection()
        else:
            collector.publish_live_status()
            snapshots.append(json.loads(live.read_text())["source_status"])
            yield ScriptedConnection([b'{"channel":"pong"}'], stop)

    collector = native.HyperliquidRawResearchCollector(
        Sink(),
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        config=native.HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
        connection_factory=factory,
    )
    collector.set_live_status_path(live, "session-ack")
    await collector.capture_for(5, stop_event=stop)
    assert snapshots[0]["l2_session_ack"] is True
    assert snapshots[0]["connection_state"] == "connected"
    assert snapshots[1]["l2_session_ack"] is False
    assert snapshots[1]["connection_state"] == "connecting"
    assert snapshots[1]["gap_count"] == 1
    assert json.loads(live.read_text())["source_status"]["l2_session_ack"] is False


@pytest.mark.asyncio
async def test_writer_two_segments_apply_backpressure_with_payload_overshoot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ParquetResearchWriter(
        tmp_path / "raw", rotation=ParquetRotation(max_payload_bytes=16 * 1024**2)
    )
    # Hold publication without a disk/thread race. Production still owns the
    # unchanged two-segment bound, and one further append must wait.
    monkeypatch.setattr(writer, "_ensure_writer_locked", lambda: None)
    collector = native.HyperliquidRawResearchCollector(Sink())
    records: list[RawResearchRecord] = []
    for size in [1024**2 - 1, *([1024**2] * 16)]:
        captured = capture_application_payload(
            b"x" * size, utc_ns=lambda: 1, monotonic_ns=lambda: 1
        )
        await collector._append_captured(
            captured, session_id="raw-bound", channel="trades", direction=MessageDirection.INBOUND
        )
    assert isinstance(collector._sink, Sink)
    records = collector._sink.records
    for record in records * 2:
        await writer.append(record)
    assert len(writer._pending) == 2
    segment_bytes = [sum(len(r.payload_bytes) for r in segment) for segment, _ in writer._pending]
    assert segment_bytes == [17 * 1024**2 - 1] * 2
    blocked = asyncio.create_task(writer.append(records[0]))
    await asyncio.sleep(0)
    assert not blocked.done() and not writer._buffer
    blocked.cancel()
    await asyncio.gather(blocked, return_exceptions=True)
    monkeypatch.undo()
    await writer.aclose()
    assert writer.published_part_count == 2
