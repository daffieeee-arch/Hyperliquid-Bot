"""Offline native-mode, reconnect and provenance counterexamples for fast L2."""

from __future__ import annotations

import asyncio
import copy
import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from hyperliquid_bot.capture_live_status import config_identity
from hyperliquid_bot.hyperliquid_l2_mode import (
    MAX_L2_MODE_PAYLOAD_BYTES,
    fast_book_error,
    fast_book_payload_error,
    l2_ack_mode_matches,
    l2_ack_payload_matches,
)
from hyperliquid_bot.hyperliquid_raw_research import (
    HyperliquidRawResearchCollector,
    HyperliquidRawResearchConfig,
    WebSocketConnection,
    _argument_parser,
    _classify_inbound,
    data1a_capture_claim,
    run_bounded_capture,
)
from hyperliquid_bot.hyperliquid_retained_instruments import (
    HyperliquidInstrumentConfigError,
    build_hyperliquid_retained_plan,
    official_subscription,
)
from hyperliquid_bot.raw_research import MessageDirection, RawResearchRecord
from hyperliquid_bot.reconstructable_paths import data1a_run_paths


def ack(channel: str = "l2Book", **fields: Any) -> dict[str, Any]:
    return {
        "channel": "subscriptionResponse",
        "data": {"method": "subscribe", "subscription": {"type": channel, "coin": "BTC", **fields}},
    }


def book(depth: int = 5) -> dict[str, Any]:
    return {
        "channel": "l2Book",
        "data": {
            "coin": "BTC",
            "time": 1234,
            "levels": [
                [{"px": str(100 - i), "sz": "0.1", "n": 1} for i in range(depth)],
                [{"px": str(101 + i), "sz": "0.2", "n": 2} for i in range(depth)],
            ],
        },
    }


def test_default_requests_and_config_fingerprint_remain_legacy() -> None:
    plan = build_hyperliquid_retained_plan()
    assert not plan.l2_fast
    assert plan.feed_name == "hyperliquid-public-btc-perp-trades-bbo-l2-ctx"
    assert official_subscription("BTC", "l2Book").payload_bytes == (
        b'{"method":"subscribe","subscription":{"type":"l2Book","coin":"BTC"}}'
    )
    claim = data1a_capture_claim(
        run_id="old-contract",
        duration_seconds=600,
        paths=data1a_run_paths(Path("/var/fixtures"), "old-contract"),
    )
    assert "l2_mode" not in claim
    expected = {
        "feed": claim["feed"],
        "websocket_url": claim["websocket_url"],
        "heartbeat_interval_seconds": 45.0,
        "receive_timeout_seconds": 60.0,
        "market_data_stale_seconds": 90.0,
        "started_coins": ["BTC"],
    }
    assert claim["config_identity"] == config_identity(expected)


def test_fast_is_one_explicit_mode_for_every_started_coin_and_other_channels_stay() -> None:
    plan = build_hyperliquid_retained_plan(
        l2_fast=True, addon_coins="ETH,SOL", enable_addons=True, include_candles=True
    )
    subscriptions = [json.loads(s.payload_bytes)["subscription"] for s in plan.subscriptions]
    for coin in ("BTC", "ETH", "SOL"):
        choices = [s for s in subscriptions if s["coin"] == coin]
        assert {s["type"] for s in choices} == {
            "trades",
            "bbo",
            "l2Book",
            "activeAssetCtx",
            "candle",
        }
        assert [s for s in choices if s["type"] == "l2Book"] == [
            {"type": "l2Book", "coin": coin, "fast": True}
        ]
        assert all("fast" not in s for s in choices if s["type"] != "l2Book")
    assert len(plan.expected_subscription_identities) == len(plan.subscriptions)
    assert plan.feed_name.endswith("-fast-l2-5-candles-1m")


@pytest.mark.parametrize("invalid", [1, 0, "true", None])
def test_mode_flag_requires_actual_boolean(invalid: Any) -> None:
    with pytest.raises(HyperliquidInstrumentConfigError, match="l2_fast"):
        build_hyperliquid_retained_plan(l2_fast=invalid)
    with pytest.raises(HyperliquidInstrumentConfigError, match="l2_fast"):
        official_subscription("BTC", "l2Book", l2_fast=invalid)


def test_fast_cannot_be_attached_to_another_channel() -> None:
    with pytest.raises(HyperliquidInstrumentConfigError, match="only to l2Book"):
        official_subscription("BTC", "trades", l2_fast=True)


def test_fast_claim_binds_exact_outbound_mode_without_rewriting_legacy_claim() -> None:
    paths = data1a_run_paths(Path("/var/fixtures"), "new-fast")
    slow = data1a_capture_claim(run_id="new-fast", duration_seconds=600, paths=paths)
    fast = data1a_capture_claim(
        run_id="new-fast",
        duration_seconds=600,
        paths=paths,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
    )
    assert fast["l2_mode"] == "fast-5"
    assert fast["l2_max_levels_per_side"] == 5
    assert fast["l2_mode_requires_session_ack"] is True
    assert fast["l2_subscription_payloads"] == [
        '{"method":"subscribe","subscription":{"type":"l2Book","coin":"BTC","fast":true}}'
    ]
    assert fast["config_identity"] != slow["config_identity"]
    assert fast["feed"] != slow["feed"]
    assert fast["credentialless"] is True and fast["signing"] is False
    assert not paths.run_dir.exists()


def test_official_nullable_ack_defaults_preserve_requested_mode() -> None:
    assert l2_ack_mode_matches(ack(fast=True, nSigFigs=None, mantissa=None), fast=True)
    assert l2_ack_mode_matches(ack(), fast=False)
    assert l2_ack_mode_matches(ack(fast=False), fast=False)
    assert not l2_ack_mode_matches(ack(fast=True), fast=False)
    assert not l2_ack_mode_matches(ack(), fast=True)


@pytest.mark.parametrize(
    "fields",
    [
        {"fast": False},
        {"fast": 1},
        {"fast": "true"},
        {"fast": None},
        {"fast": True, "nSigFigs": 4},
        {"fast": True, "mantissa": 2},
        {"fast": True, "unknown_mode": True},
    ],
)
def test_ack_mode_or_precision_cannot_silently_change(fields: dict[str, Any]) -> None:
    assert not l2_ack_mode_matches(ack(**fields), fast=True)


def test_fast_book_may_have_sparse_or_empty_sides() -> None:
    for depth in (0, 1, 3, 5):
        assert fast_book_error(book(depth), ("BTC",)) is None
    sparse = book()
    sparse["data"]["levels"][0] = []
    assert fast_book_error(sparse, ("BTC",)) is None


def test_native_integer_tokens_are_validated_without_string_coercion() -> None:
    valid = book()
    assert fast_book_payload_error(json.dumps(valid).encode(), ("BTC",)) is None
    valid["data"]["time"] = "1234"
    assert fast_book_payload_error(json.dumps(valid).encode(), ("BTC",)) is not None
    valid = book()
    valid["data"]["levels"][0][0]["n"] = "1"
    assert fast_book_payload_error(json.dumps(valid).encode(), ("BTC",)) is not None


def test_duplicate_native_mode_fields_and_oversized_l2_are_not_acknowledgements() -> None:
    duplicate = (
        b'{"channel":"subscriptionResponse","data":{"method":"subscribe",'
        b'"subscription":{"type":"l2Book","coin":"BTC","fast":false,"fast":true}}}'
    )
    assert not l2_ack_payload_matches(duplicate, fast=True)
    assert fast_book_payload_error(b" " * (MAX_L2_MODE_PAYLOAD_BYTES + 1), ("BTC",))


def test_deeply_nested_native_data_cannot_pass_fast_shape_check() -> None:
    payload = b'{"channel":"l2Book","data":' + b"[" * 5000 + b"0" + b"]" * 5000 + b"}"
    # Decoder recursion behavior differs across supported runtimes; either
    # classification must return safely and the fast source must reject it.
    channel, document, reason = _classify_inbound(payload)
    assert channel in {"unknown", "l2Book"}
    if channel == "unknown":
        assert document is None and reason is not None
    assert fast_book_payload_error(payload, ("BTC",)) is not None


@pytest.mark.parametrize(
    "mutation",
    [
        "slow_depth",
        "unknown_coin",
        "bad_time",
        "bad_decimal",
        "zero_size",
        "boolean_count",
        "duplicate_price",
        "crossed",
        "inverted_order",
    ],
)
def test_fast_book_rejects_mode_and_native_schema_drift(mutation: str) -> None:
    value = book()
    data = value["data"]
    if mutation == "slow_depth":
        value = book(6)
    elif mutation == "unknown_coin":
        data["coin"] = "ETH"
    elif mutation == "bad_time":
        data["time"] = True
    elif mutation == "bad_decimal":
        data["levels"][0][0]["px"] = "NaN"
    elif mutation == "zero_size":
        data["levels"][0][0]["sz"] = "0"
    elif mutation == "boolean_count":
        data["levels"][0][0]["n"] = True
    elif mutation == "duplicate_price":
        data["levels"][0][1]["px"] = "100"
    elif mutation == "crossed":
        data["levels"][0][0]["px"] = "102"
    else:
        data["levels"][1].reverse()
    assert fast_book_error(value, ("BTC",)) is not None


class Sink:
    def __init__(self) -> None:
        self.records: list[RawResearchRecord] = []

    async def append(self, record: RawResearchRecord) -> None:
        self.records.append(record)

    async def aclose(self) -> None:
        pass


class Connection:
    def __init__(self, messages: Sequence[dict[str, Any]], stop: asyncio.Event | None) -> None:
        self.messages = deque(json.dumps(m, separators=(",", ":")) for m in messages)
        self.stop = stop
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        await asyncio.sleep(0)
        if not self.messages:
            raise ConnectionError("synthetic reconnect")
        result = self.messages.popleft()
        if not self.messages and self.stop is not None:
            self.stop.set()
        return result


class Factory:
    def __init__(self, connections: list[Connection]) -> None:
        self.connections = deque(connections)

    @asynccontextmanager
    async def context(self) -> AsyncIterator[WebSocketConnection]:
        yield self.connections.popleft()

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        return self.context()


def events(sink: Sink) -> list[dict[str, Any]]:
    return [
        json.loads(record.payload_bytes)
        for record in sink.records
        if record.direction is MessageDirection.LOCAL
    ]


@pytest.mark.asyncio
async def test_fast_native_bytes_only_become_live_after_correct_mode_ack() -> None:
    stop = asyncio.Event()
    messages = [
        ack(c, **({"fast": True} if c == "l2Book" else {}))
        for c in ("trades", "bbo", "l2Book", "activeAssetCtx")
    ]
    messages.append(book())
    connection = Connection(messages, stop)
    sink = Sink()
    collector = HyperliquidRawResearchCollector(
        sink,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        connection_factory=Factory([connection]),
    )
    await collector.capture_for(5, stop_event=stop)
    assert sum(e["event"] == "subscriptions_active" for e in events(sink)) == 1
    assert ("l2Book", "BTC") in collector._feed_last_market_utc
    incoming = next(r for r in sink.records if r.channel == "l2Book")
    assert incoming.payload_bytes == json.dumps(messages[-1], separators=(",", ":")).encode()
    assert any(
        b'"fast":true' in r.payload_bytes
        for r in sink.records
        if r.direction is MessageDirection.OUTBOUND
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        ack(fast=False),
        ack(),
        ack(fast=1),
        book(),
        book(6),
        {"channel": "error", "data": "synthetic source rejection"},
        {"missing_channel": True},
    ],
)
async def test_bad_mode_or_unacknowledged_book_is_retained_and_not_live(
    bad: dict[str, Any],
) -> None:
    stop = asyncio.Event()
    sink = Sink()
    collector = HyperliquidRawResearchCollector(
        sink,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        connection_factory=Factory([Connection([bad], stop)]),
        config=HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
    )
    await collector.capture_for(5, stop_event=stop)
    local = events(sink)
    assert any(e["event"] in {"schema_error", "subscription_error"} for e in local)
    assert not any(e["event"] == "subscriptions_active" for e in local)
    assert ("l2Book", "BTC") not in collector._feed_last_market_utc
    assert any(
        r.payload_bytes == json.dumps(bad, separators=(",", ":")).encode()
        for r in sink.records
        if r.direction is MessageDirection.INBOUND
    )


@pytest.mark.asyncio
async def test_reconnect_must_reack_mode_before_any_new_fast_book_is_live() -> None:
    stop = asyncio.Event()
    sink = Sink()
    ids = iter(("first", "second"))
    connection_one = Connection([ack(fast=True), book()], None)
    connection_two = Connection([copy.deepcopy(book())], stop)
    collector = HyperliquidRawResearchCollector(
        sink,
        instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        connection_factory=Factory([connection_one, connection_two]),
        config=HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
        session_id_factory=lambda: next(ids),
    )
    await collector.capture_for(5, stop_event=stop)
    second = [
        json.loads(r.payload_bytes)
        for r in sink.records
        if r.session_id == "second" and r.direction is MessageDirection.LOCAL
    ]
    assert any(e["event"] == "schema_error" for e in second)
    assert not any(e["event"] == "market_data_recovered" for e in second)
    outbound = [
        r.payload_bytes
        for r in sink.records
        if r.session_id == "second" and r.direction is MessageDirection.OUTBOUND
    ]
    assert any(b'"fast":true' in payload for payload in outbound)


@pytest.mark.asyncio
async def test_mode_integrity_error_remains_counted_after_reconnect(tmp_path: Path) -> None:
    stop = asyncio.Event()
    first = Connection([ack(fast=False)], None)
    second = Connection([ack(fast=True), book()], stop)
    # Bounded capture's ordinary transport path reconnects with its default
    # delay; replace only that delay through an explicit collector fixture.
    from unittest.mock import patch

    with patch(
        "hyperliquid_bot.hyperliquid_raw_research.HyperliquidRawResearchConfig",
        return_value=HyperliquidRawResearchConfig(reconnect_delay_seconds=0),
    ):
        report = await run_bounded_capture(
            output_dir=tmp_path / "raw",
            database_path=tmp_path / "catalog.duckdb",
            duration_seconds=5,
            stop_event=stop,
            instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
            connection_factory=Factory([first, second]),
        )
    assert report["integrity_events"] == 1
    assert report["gaps"] == 1
    assert report["reconnects"] == 1


def test_cli_fast_mode_is_opt_in_and_not_an_implicit_default() -> None:
    parser = _argument_parser()
    assert parser.parse_args(["--duration-seconds", "600"]).l2_fast is False
    assert parser.parse_args(["--duration-seconds", "600", "--l2-fast"]).l2_fast is True
