"""Kraken BTC/EUR public L2/trades capture with optional authenticated L3.

The default retained path is public book + trades only at depth 100.
Authenticated L3 is optional (also depth 100 when enabled) and never a silent
fallback. CRC32 still covers only the best 10 price levels. Duration may be a
short smoke or a retained multi-day run. This is not a 24/7 service.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import os
import re
import signal
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Protocol, cast

import duckdb
from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

from .parquet_research import ParquetResearchWriter, ParquetRotation, create_research_catalog
from .raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    CapturedApplicationPayload,
    FrameType,
    MessageDirection,
    NanosecondClock,
    PayloadEncoding,
    RawResearchRecord,
    RawResearchSink,
    capture_application_payload,
)
from .reconstructable_paths import (
    DATA1B_PATH_CONTRACT_ID,
    DATA1B_PRODUCT,
    Data1BRunPaths,
    data1b_run_paths,
)

KRAKEN_PUBLIC_WEBSOCKET_URL: Final = "wss://ws.kraken.com/v2"
KRAKEN_L3_WEBSOCKET_URL: Final = "wss://ws-l3.kraken.com/v2"
KRAKEN_TOKEN_URL: Final = "https://api.kraken.com/0/private/GetWebSocketsToken"
KRAKEN_TOKEN_PATH: Final = "/0/private/GetWebSocketsToken"
KRAKEN_RESEARCH_VENUE: Final = "kraken"
KRAKEN_RESEARCH_PRODUCT: Final = "BTC/EUR"
SMOKE_CAPTURE_SECONDS: Final = 600.0
MAX_CAPTURE_SECONDS: Final = 7 * 24 * 60 * 60
DEFAULT_L2_DEPTH: Final = 100
DEFAULT_L3_DEPTH: Final = 100
SUPPORTED_L2_DEPTHS: Final = frozenset({10, 25, 100, 500, 1000})
SUPPORTED_L3_DEPTHS: Final = frozenset({10, 100, 1000})
CHECKSUM_PRICE_LEVELS: Final = 10
DATA1B_CLAIM_SCHEMA: Final = "data-1b-retained-capture-claim-v1"
DATA1B_HEALTH_SCHEMA: Final = "data-1b-retained-capture-health-v1"
KRAKEN_WS_API_KEY_ENV: Final = "KRAKEN_WS_API_KEY"
KRAKEN_WS_API_SECRET_ENV: Final = "KRAKEN_WS_API_SECRET"
KRAKEN_L3_OPTIONAL_ENV: Final = (KRAKEN_WS_API_KEY_ENV, KRAKEN_WS_API_SECRET_ENV)
PROTECTED_TRADE_KEY_ENV: Final = (
    "HYPERLIQUID_PK",
    "HYPERLIQUID_TESTNET_PK",
    "HYPERLIQUID_VAULT",
    "HYPERLIQUID_TESTNET_VAULT",
    "HYPERLIQUID_ACCOUNT_ADDRESS",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_SECRET",
    "BINANCE_API_KEY_TESTNET",
    "BINANCE_TESTNET_API_SECRET",
    "BITVAVO_API_KEY",
    "BITVAVO_API_SECRET",
    "BITVAVO_ACCESS_KEY",
    "BITVAVO_SECRET",
    "BITVAVO_SIGNING_KEY",
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "KRAKEN_PRIVATE_KEY",
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
)

_PUBLIC_SUBSCRIPTIONS: Final = (
    (
        "trade",
        '{"method":"subscribe","params":{"channel":"trade","snapshot":false,"symbol":["BTC/EUR"]}}',
    ),
    (
        "book",
        '{"method":"subscribe","params":{"channel":"book","depth":100,'
        '"snapshot":true,"symbol":["BTC/EUR"]}}',
    ),
)
_PUBLIC_CHANNELS: Final = frozenset({"trade", "book"})
_PLAIN_DECIMAL: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_RFC3339_NANO_UTC: Final = re.compile(
    r"(?P<second>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?Z\Z"
)
_MAX_TOKEN_RESPONSE_BYTES: Final = 64 * 1024

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.kraken_l3_research_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class KrakenCaptureError(RuntimeError):
    """Base class whose messages never include venue payload or credential text."""


class KrakenAuthenticationError(KrakenCaptureError):
    """The token boundary or authenticated subscription failed closed."""


class KrakenDataIntegrityError(KrakenCaptureError):
    """A market frame failed schema, ordering, size, or checksum validation."""


class KrakenTransportError(KrakenCaptureError):
    """A connection or reconnect could not establish the required feed set."""


class WebSocketConnection(Protocol):
    """Small Kraken transport surface implemented by websockets and offline fakes."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[str], str]
type TokenHttpPost = Callable[[str, bytes, dict[str, str], float, int], bytes]


class KrakenWebSocketToken:
    """Opaque short-lived token with deliberately redacted string representations."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if type(value) is not str or not value or value != value.strip():
            raise KrakenAuthenticationError("Kraken returned an invalid WebSocket token.")
        self._value = value

    def __repr__(self) -> str:
        return "KrakenWebSocketToken(<redacted>)"

    __str__ = __repr__

    def _for_subscription(self) -> str:
        return self._value

    def _appears_in(self, payload_bytes: bytes) -> bool:
        return self._value.encode("utf-8") in payload_bytes


class KrakenApiCredentials:
    """In-memory API material; construction and representations never expose values."""

    __slots__ = ("_api_key", "_decoded_secret")

    def __init__(self, *, api_key: str, api_secret_base64: str) -> None:
        if type(api_key) is not str or not api_key or api_key != api_key.strip():
            raise KrakenAuthenticationError("Kraken API credentials are invalid.")
        if type(api_secret_base64) is not str or not api_secret_base64:
            raise KrakenAuthenticationError("Kraken API credentials are invalid.")
        try:
            decoded_secret = base64.b64decode(api_secret_base64, validate=True)
        except (binascii.Error, ValueError):
            raise KrakenAuthenticationError("Kraken API credentials are invalid.") from None
        if not decoded_secret:
            raise KrakenAuthenticationError("Kraken API credentials are invalid.")
        self._api_key = api_key
        self._decoded_secret = decoded_secret

    def __repr__(self) -> str:
        return "KrakenApiCredentials(<redacted>)"

    __str__ = __repr__

    def _headers(self, *, nonce: str, body: bytes) -> dict[str, str]:
        encoded = nonce.encode("ascii") + body
        message = KRAKEN_TOKEN_PATH.encode("ascii") + hashlib.sha256(encoded).digest()
        signature = base64.b64encode(
            hmac.new(self._decoded_secret, message, hashlib.sha512).digest()
        ).decode("ascii")
        return {
            "API-Key": self._api_key,
            "API-Sign": signature,
            "Content-Type": "application/x-www-form-urlencoded",
        }


class KrakenTokenProvider(Protocol):
    """Issue one fresh token for one L3 connection boundary."""

    async def get_token(self) -> KrakenWebSocketToken: ...


class IncreasingNonce:
    """Thread-safe strictly increasing unsigned nonce for one dedicated API key."""

    def __init__(self) -> None:
        self._last = 0
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            candidate = max(time.time_ns() // 1_000_000, self._last + 1)
            if candidate >= 2**64:
                raise KrakenAuthenticationError("Kraken nonce generation failed.")
            self._last = candidate
            return candidate


class KrakenRestTokenProvider:
    """Obtain a token with stdlib form encoding and official HMAC-SHA512 signing."""

    def __init__(
        self,
        credentials: KrakenApiCredentials,
        *,
        http_post: TokenHttpPost | None = None,
        nonce_factory: Callable[[], int] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if type(credentials) is not KrakenApiCredentials:
            raise TypeError("credentials must be KrakenApiCredentials.")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._credentials = credentials
        self._http_post = http_post if http_post is not None else _stdlib_token_http_post
        self._nonce_factory = nonce_factory if nonce_factory is not None else IncreasingNonce()
        self._timeout_seconds = float(timeout_seconds)

    async def get_token(self) -> KrakenWebSocketToken:
        token: KrakenWebSocketToken | None = None
        token_request_failed = False
        try:
            token = await asyncio.to_thread(self._get_token_sync)
        except asyncio.CancelledError:
            raise
        except Exception:
            token_request_failed = True
        if token_request_failed:
            raise KrakenAuthenticationError("Kraken WebSocket token request failed.")
        if token is None:
            raise KrakenAuthenticationError("Kraken WebSocket token request failed.")
        return token

    def _get_token_sync(self) -> KrakenWebSocketToken:
        nonce_value = self._nonce_factory()
        if type(nonce_value) is not int or not 0 < nonce_value < 2**64:
            raise KrakenAuthenticationError("Kraken nonce generation failed.")
        nonce = str(nonce_value)
        body = urllib.parse.urlencode({"nonce": nonce}).encode("ascii")
        headers = self._credentials._headers(nonce=nonce, body=body)
        response_bytes = self._http_post(
            KRAKEN_TOKEN_URL,
            body,
            headers,
            self._timeout_seconds,
            _MAX_TOKEN_RESPONSE_BYTES,
        )
        return _parse_token_response(response_bytes)


@dataclass(frozen=True, slots=True)
class KrakenL3ResearchConfig:
    """Fixed BTC/EUR scope plus bounded transport and visible book depths."""

    l2_depth: int = DEFAULT_L2_DEPTH
    l3_depth: int = DEFAULT_L3_DEPTH
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.l2_depth) is not int or self.l2_depth not in SUPPORTED_L2_DEPTHS:
            raise ValueError("l2_depth is not supported by Kraken Spot WebSocket v2.")
        if type(self.l3_depth) is not int or self.l3_depth not in SUPPORTED_L3_DEPTHS:
            raise ValueError("l3_depth is not supported by Kraken level3.")
        if (
            type(self.reconnect_delay_seconds) not in (int, float)
            or self.reconnect_delay_seconds < 0
        ):
            raise ValueError("reconnect_delay_seconds must be non-negative.")
        if (
            type(self.max_application_payload_bytes) is not int
            or self.max_application_payload_bytes <= 0
        ):
            raise ValueError("max_application_payload_bytes must be a positive integer.")


@dataclass(slots=True)
class _L3Order:
    order_id: str
    side: str
    price_text: str
    price: Decimal
    quantity_text: str
    timestamp: str
    arrival_order: int


class _TradeState:
    def __init__(self) -> None:
        self._last_trade_id: int | None = None

    def normalize(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        message_type, data = _message_data(document, "trade")
        if message_type != "update":
            raise KrakenDataIntegrityError("Kraken trade schema validation failed.")
        events: list[dict[str, object]] = []
        next_last = self._last_trade_id
        for event_index, value in enumerate(data):
            item = _object(value, "trade data item")
            symbol = _required_text(item, "symbol")
            if symbol != KRAKEN_RESEARCH_PRODUCT:
                raise KrakenDataIntegrityError("Kraken trade schema validation failed.")
            trade_id_text = _unsigned_integer_text(item, "trade_id")
            trade_id = int(trade_id_text)
            if next_last is not None and trade_id <= next_last:
                raise KrakenDataIntegrityError("Kraken trade ordering validation failed.")
            next_last = trade_id
            side = _required_text(item, "side")
            order_type = _required_text(item, "ord_type")
            if side not in {"buy", "sell"} or order_type not in {"limit", "market"}:
                raise KrakenDataIntegrityError("Kraken trade schema validation failed.")
            events.append(
                {
                    "event_index": event_index,
                    "symbol": symbol,
                    "side": side,
                    "qty": _decimal_text(item, "qty", allow_zero=False)[0],
                    "price": _decimal_text(item, "price", allow_zero=False)[0],
                    "order_type": order_type,
                    "trade_id": trade_id_text,
                    "timestamp": _timestamp(item, "timestamp"),
                }
            )
        self._last_trade_id = next_last
        return {
            "event": "normalized_trade_frame",
            "raw_message_ordinal": raw_ordinal,
            "message_type": message_type,
            "events": events,
        }


class _L2BookState:
    def __init__(self, depth: int) -> None:
        self._depth = depth
        self._levels: dict[str, dict[Decimal, tuple[str, str]]] = {"bid": {}, "ask": {}}
        self._has_snapshot = False

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    def normalize(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        message_type, data = _message_data(document, "book")
        if len(data) != 1:
            raise KrakenDataIntegrityError("Kraken book schema validation failed.")
        item = _object(data[0], "book data item")
        if _required_text(item, "symbol") != KRAKEN_RESEARCH_PRODUCT:
            raise KrakenDataIntegrityError("Kraken book schema validation failed.")
        if message_type == "snapshot":
            self._levels = {"bid": {}, "ask": {}}
        elif not self._has_snapshot:
            raise KrakenDataIntegrityError("Kraken book update preceded its snapshot.")

        events: list[dict[str, object]] = []
        for wire_order, side, side_index, value in _wire_side_entries(item):
            level = _object(value, "book level")
            price_text, price = _decimal_text(level, "price", allow_zero=False)
            quantity_text, quantity = _decimal_text(level, "qty", allow_zero=True)
            if message_type == "snapshot" and quantity == 0:
                raise KrakenDataIntegrityError("Kraken book snapshot contained an empty level.")
            if quantity == 0:
                self._levels[side].pop(price, None)
            else:
                self._levels[side][price] = (price_text, quantity_text)
            events.append(
                {
                    "data_index": 0,
                    "wire_order": wire_order,
                    "side": side,
                    "side_index": side_index,
                    "price": price_text,
                    "qty": quantity_text,
                    "action": "snapshot" if message_type == "snapshot" else "update",
                }
            )

        self._truncate()
        expected = _uint32(item, "checksum")
        if self._checksum() != expected:
            raise KrakenDataIntegrityError("Kraken book checksum validation failed.")
        self._has_snapshot = True
        return {
            "event": "normalized_l2_frame",
            "raw_message_ordinal": raw_ordinal,
            "message_type": message_type,
            "symbol": KRAKEN_RESEARCH_PRODUCT,
            "message_timestamp": _timestamp(item, "timestamp"),
            "checksum": str(expected),
            "events": events,
        }

    def _truncate(self) -> None:
        for side, reverse in (("ask", False), ("bid", True)):
            prices = sorted(self._levels[side], reverse=reverse)
            for price in prices[self._depth :]:
                del self._levels[side][price]

    def _checksum(self) -> int:
        parts: list[str] = []
        for side, reverse in (("ask", False), ("bid", True)):
            prices = sorted(self._levels[side], reverse=reverse)[:CHECKSUM_PRICE_LEVELS]
            for price in prices:
                price_text, quantity_text = self._levels[side][price]
                parts.append(_checksum_component(price_text))
                parts.append(_checksum_component(quantity_text))
        return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF


class _L3BookState:
    def __init__(self, depth: int) -> None:
        self._depth = depth
        self._orders: dict[str, _L3Order] = {}
        self._has_snapshot = False
        self._arrival_order = 0

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    def normalize(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        message_type, data = _message_data(document, "level3")
        if len(data) != 1:
            raise KrakenDataIntegrityError("Kraken level3 schema validation failed.")
        item = _object(data[0], "level3 data item")
        if _required_text(item, "symbol") != KRAKEN_RESEARCH_PRODUCT:
            raise KrakenDataIntegrityError("Kraken level3 schema validation failed.")
        if message_type == "snapshot":
            self._orders.clear()
        elif not self._has_snapshot:
            raise KrakenDataIntegrityError("Kraken level3 update preceded its snapshot.")

        events: list[dict[str, object]] = []
        for wire_order, side, side_index, value in _wire_side_entries(item):
            order = _object(value, "level3 order")
            order_id = _required_text(order, "order_id")
            price_text, price = _decimal_text(order, "limit_price", allow_zero=False)
            quantity_text, quantity = _decimal_text(order, "order_qty", allow_zero=True)
            timestamp = _timestamp(order, "timestamp")
            event = "snapshot" if message_type == "snapshot" else _required_text(order, "event")
            allowed_events = (
                {"snapshot"}
                if message_type == "snapshot"
                else {
                    "add",
                    "modify",
                    "delete",
                }
            )
            if event not in allowed_events:
                raise KrakenDataIntegrityError("Kraken level3 schema validation failed.")
            self._apply_order(
                event=event,
                order_id=order_id,
                side=side,
                price_text=price_text,
                price=price,
                quantity_text=quantity_text,
                quantity=quantity,
                timestamp=timestamp,
            )
            events.append(
                {
                    "data_index": 0,
                    "wire_order": wire_order,
                    "side": side,
                    "side_index": side_index,
                    "event": event,
                    "event_source": "wire",
                    "order_id": order_id,
                    "limit_price": price_text,
                    "order_qty": quantity_text,
                    "timestamp": timestamp,
                }
            )

        events.extend(self._truncate_scope())
        expected = _uint32(item, "checksum")
        if self._checksum() != expected:
            raise KrakenDataIntegrityError("Kraken level3 checksum validation failed.")
        self._has_snapshot = True
        return {
            "event": "normalized_l3_frame",
            "raw_message_ordinal": raw_ordinal,
            "message_type": message_type,
            "symbol": KRAKEN_RESEARCH_PRODUCT,
            "message_timestamp": _optional_timestamp(item, "timestamp"),
            "checksum": str(expected),
            "events": events,
        }

    def _apply_order(
        self,
        *,
        event: str,
        order_id: str,
        side: str,
        price_text: str,
        price: Decimal,
        quantity_text: str,
        quantity: Decimal,
        timestamp: str,
    ) -> None:
        existing = self._orders.get(order_id)
        if event in {"snapshot", "add"}:
            if existing is not None or quantity <= 0:
                raise KrakenDataIntegrityError("Kraken level3 order lifecycle validation failed.")
            self._arrival_order += 1
            self._orders[order_id] = _L3Order(
                order_id=order_id,
                side=side,
                price_text=price_text,
                price=price,
                quantity_text=quantity_text,
                timestamp=timestamp,
                arrival_order=self._arrival_order,
            )
            return
        if existing is None or existing.side != side or existing.price != price:
            raise KrakenDataIntegrityError("Kraken level3 order lifecycle validation failed.")
        if event == "delete":
            del self._orders[order_id]
            return
        if quantity <= 0:
            raise KrakenDataIntegrityError("Kraken level3 order lifecycle validation failed.")
        existing.price_text = price_text
        existing.quantity_text = quantity_text
        existing.timestamp = timestamp

    def _truncate_scope(self) -> list[dict[str, object]]:
        removed: list[_L3Order] = []
        for side, reverse in (("ask", False), ("bid", True)):
            prices = sorted(
                {order.price for order in self._orders.values() if order.side == side},
                reverse=reverse,
            )
            out_of_scope = set(prices[self._depth :])
            for order in tuple(self._orders.values()):
                if order.side == side and order.price in out_of_scope:
                    removed.append(order)
                    del self._orders[order.order_id]
        return [
            {
                "data_index": 0,
                "wire_order": None,
                "side": order.side,
                "side_index": None,
                "event": "scope_truncate",
                "event_source": "local_scope",
                "order_id": order.order_id,
                "limit_price": order.price_text,
                "order_qty": order.quantity_text,
                "timestamp": order.timestamp,
            }
            for order in sorted(removed, key=lambda value: value.arrival_order)
        ]

    def _checksum(self) -> int:
        parts: list[str] = []
        for side, reverse in (("ask", False), ("bid", True)):
            prices = sorted(
                {order.price for order in self._orders.values() if order.side == side},
                reverse=reverse,
            )[:CHECKSUM_PRICE_LEVELS]
            for price in prices:
                orders = sorted(
                    (
                        order
                        for order in self._orders.values()
                        if order.side == side and order.price == price
                    ),
                    key=lambda order: (_timestamp_key(order.timestamp), order.arrival_order),
                )
                for order in orders:
                    parts.append(_checksum_component(order.price_text))
                    parts.append(_checksum_component(order.quantity_text))
        return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF


class KrakenL3ResearchCollector:
    """Capture the required public and authenticated Kraken feeds without execution."""

    def __init__(
        self,
        sink: RawResearchSink,
        token_provider: KrakenTokenProvider | None = None,
        *,
        config: KrakenL3ResearchConfig | None = None,
        public_connection_factory: ConnectionFactory | None = None,
        l3_connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        self._sink = sink
        self._token_provider = token_provider
        self._config = config if config is not None else KrakenL3ResearchConfig()
        self._public_connection_factory = (
            public_connection_factory
            if public_connection_factory is not None
            else _connection_factory(KRAKEN_PUBLIC_WEBSOCKET_URL, self._config)
        )
        self._l3_connection_factory = (
            l3_connection_factory
            if l3_connection_factory is not None
            else _connection_factory(KRAKEN_L3_WEBSOCKET_URL, self._config)
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._session_id_factory = (
            session_id_factory
            if session_id_factory is not None
            else lambda stream: f"{stream}-{uuid.uuid4().hex}"
        )
        self._message_ordinal = 0
        self._append_lock = asyncio.Lock()

    async def capture_for(
        self,
        duration_seconds: float,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run public L2+trades; authenticated L3 is optional and never a silent fallback."""

        _require_bounded_duration(duration_seconds)
        capture_stop = stop_event if stop_event is not None else asyncio.Event()
        timer = asyncio.create_task(
            self._stop_after(capture_stop, float(duration_seconds)),
            name="kraken-l3-research-duration",
        )
        public_task = asyncio.create_task(
            self._run_public_stream(capture_stop),
            name="kraken-public-research-stream",
        )
        if self._token_provider is None:
            try:
                await public_task
            finally:
                capture_stop.set()
                timer.cancel()
                if not public_task.done():
                    public_task.cancel()
                await asyncio.gather(timer, public_task, return_exceptions=True)
            return
        l3_task = asyncio.create_task(
            self._run_l3_stream(capture_stop),
            name="kraken-l3-research-stream",
        )
        try:
            done, pending = await asyncio.wait(
                (public_task, l3_task),
                return_when=asyncio.FIRST_EXCEPTION,
            )
            terminal = next(
                (task.exception() for task in done if task.exception() is not None),
                None,
            )
            if terminal is not None:
                capture_stop.set()
                # Let the peer leave through its stop boundary.  In particular, do not
                # cancel it while a shared sink is publishing an atomic Parquet part.
                await asyncio.gather(*pending, return_exceptions=True)
                raise terminal
            await asyncio.gather(*pending)
        finally:
            capture_stop.set()
            timer.cancel()
            for task in (public_task, l3_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(timer, public_task, l3_task, return_exceptions=True)

    async def _stop_after(self, stop_event: asyncio.Event, duration_seconds: float) -> None:
        await asyncio.sleep(duration_seconds)
        stop_event.set()

    async def _run_public_stream(self, stop_event: asyncio.Event) -> None:
        previous_session_id: str | None = None
        while not stop_event.is_set():
            session_id = self._session_id_factory("public")
            await self._session_started(session_id, "public", previous_session_id)
            connected = False
            payload_size_failure = False
            connection_failure = False
            unexpected_boundary_failure = False
            try:
                async with self._public_connection_factory() as connection:
                    connected = True
                    await self._connected(session_id, "public", previous_session_id)
                    for channel, payload in _public_subscriptions(self._config.l2_depth):
                        await connection.send(payload)
                        await self._append_marker(
                            session_id,
                            "subscription",
                            "subscription_sent",
                            stream="public",
                            subscription_type=channel,
                            product=KRAKEN_RESEARCH_PRODUCT,
                            authenticated=False,
                        )
                    await self._receive_public(connection, session_id, stop_event)
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        stream="public",
                        reason="capture_limit_reached",
                    )
                    return
            except asyncio.CancelledError:
                raise
            except PayloadTooBig:
                await self._terminal_quality(session_id, "public", "payload_size_error")
                payload_size_failure = True
            except KrakenCaptureError:
                raise
            except (WebSocketException, OSError):
                if not connected:
                    await self._connection_failed(session_id, "public", previous_session_id)
                    connection_failure = True
                else:
                    await self._disconnected(session_id, "public")
                    previous_session_id = session_id
            except Exception:
                unexpected_boundary_failure = True
            if payload_size_failure:
                raise KrakenDataIntegrityError("Kraken public payload exceeded its bound.")
            if connection_failure:
                raise KrakenTransportError("Kraken public connection failed.")
            if unexpected_boundary_failure:
                if connected:
                    await self._disconnected(session_id, "public")
                else:
                    await self._connection_failed(session_id, "public", previous_session_id)
                raise KrakenTransportError("Kraken public transport boundary failed.")
            await self._wait_to_reconnect(stop_event)

    async def _run_l3_stream(self, stop_event: asyncio.Event) -> None:
        if self._token_provider is None:
            raise KrakenAuthenticationError(
                "Kraken L3 was requested without a token provider; public-only is explicit."
            )
        previous_session_id: str | None = None
        while not stop_event.is_set():
            session_id = self._session_id_factory("l3")
            await self._session_started(session_id, "l3", previous_session_id)
            await self._append_marker(
                session_id,
                "authentication",
                "authentication_boundary_started",
                stream="l3",
                authenticated=True,
            )
            token: KrakenWebSocketToken | None = None
            token_failure = False
            try:
                token = await self._token_provider.get_token()
            except asyncio.CancelledError:
                raise
            except Exception:
                token_failure = True
            if type(token) is not KrakenWebSocketToken:
                token_failure = True
            if token_failure or token is None:
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "authentication_failed",
                    stream="l3",
                    reason="token_request_failed",
                )
                raise KrakenAuthenticationError("Kraken L3 authentication failed.")
            await self._append_marker(
                session_id,
                "authentication",
                "authentication_token_obtained",
                stream="l3",
                authenticated=True,
            )
            connected = False
            payload_size_failure = False
            connection_failure = False
            unexpected_boundary_failure = False
            authentication_failure = False
            try:
                async with self._l3_connection_factory() as connection:
                    connected = True
                    await self._connected(session_id, "l3", previous_session_id)
                    payload = _l3_subscription(token, self._config.l3_depth)
                    subscription_send_failed = False
                    try:
                        await connection.send(payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        subscription_send_failed = True
                    finally:
                        del payload
                    if subscription_send_failed:
                        await self._append_marker(
                            session_id,
                            "data_quality",
                            "authentication_failed",
                            stream="l3",
                            reason="subscription_send_failed",
                        )
                        raise KrakenAuthenticationError(
                            "Kraken L3 authenticated subscription failed."
                        )
                    await self._append_marker(
                        session_id,
                        "subscription",
                        "subscription_sent",
                        stream="l3",
                        subscription_type="level3",
                        product=KRAKEN_RESEARCH_PRODUCT,
                        authenticated=True,
                    )
                    await self._receive_l3(connection, session_id, token, stop_event)
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        stream="l3",
                        reason="capture_limit_reached",
                    )
                    return
            except asyncio.CancelledError:
                raise
            except PayloadTooBig:
                await self._terminal_quality(session_id, "l3", "payload_size_error")
                payload_size_failure = True
            except KrakenAuthenticationError:
                authentication_failure = True
            except KrakenCaptureError:
                raise
            except (WebSocketException, OSError):
                if not connected:
                    await self._connection_failed(session_id, "l3", previous_session_id)
                    connection_failure = True
                else:
                    await self._disconnected(session_id, "l3")
                    previous_session_id = session_id
            except Exception:
                unexpected_boundary_failure = True
            if payload_size_failure:
                raise KrakenDataIntegrityError("Kraken L3 payload exceeded its bound.")
            if authentication_failure:
                raise KrakenAuthenticationError("Kraken L3 authentication failed.")
            if connection_failure:
                raise KrakenTransportError("Kraken L3 connection failed.")
            if unexpected_boundary_failure:
                if connected:
                    await self._disconnected(session_id, "l3")
                else:
                    await self._connection_failed(session_id, "l3", previous_session_id)
                raise KrakenTransportError("Kraken L3 transport boundary failed.")
            await self._wait_to_reconnect(stop_event)

    async def _receive_public(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        acknowledged: set[str] = set()
        trade_state = _TradeState()
        book_state = _L2BookState(self._config.l2_depth)
        while not stop_event.is_set():
            captured = await self._receive_or_stop(connection, stop_event)
            if captured is None:
                break
            document = await self._decode_inbound(captured, session_id, "public", token=None)
            if _is_heartbeat(document):
                continue
            if document.get("channel") == "status":
                await self._record_status(document, session_id, "public")
                continue
            if _is_subscription_response(document):
                channel = await self._record_subscription_response(
                    document,
                    session_id,
                    stream="public",
                    authenticated=False,
                    expected_channels=_PUBLIC_CHANNELS,
                )
                acknowledged.add(channel)
                if acknowledged == _PUBLIC_CHANNELS:
                    await self._append_marker(
                        session_id,
                        "subscription",
                        "subscriptions_active",
                        stream="public",
                        product=KRAKEN_RESEARCH_PRODUCT,
                        authenticated=False,
                    )
                continue
            channel_value = document.get("channel")
            if type(channel_value) is not str:
                await self._schema_failure(session_id, "public", None)
            channel = cast(str, channel_value)
            if channel not in _PUBLIC_CHANNELS or channel not in acknowledged:
                await self._schema_failure(session_id, "public", None)
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel=channel,
            )
            try:
                normalized = (
                    trade_state.normalize(document, raw_ordinal)
                    if channel == "trade"
                    else book_state.normalize(document, raw_ordinal)
                )
            except KrakenDataIntegrityError as error:
                await self._integrity_failure(session_id, "public", raw_ordinal, error)
                raise
            await self._append_normalized(session_id, channel, normalized)
        if acknowledged != _PUBLIC_CHANNELS:
            await self._append_marker(
                session_id,
                "data_quality",
                "subscription_failed",
                stream="public",
                reason="capture_stopped_before_required_acknowledgements",
            )
            raise KrakenDataIntegrityError("Kraken public subscriptions were incomplete.")
        if not book_state.has_snapshot:
            await self._append_marker(
                session_id,
                "data_quality",
                "snapshot_missing",
                stream="public",
                reason="capture_stopped_before_l2_snapshot",
            )
            raise KrakenDataIntegrityError("Kraken public book snapshot was incomplete.")

    async def _receive_l3(
        self,
        connection: WebSocketConnection,
        session_id: str,
        token: KrakenWebSocketToken,
        stop_event: asyncio.Event,
    ) -> None:
        acknowledged = False
        book_state = _L3BookState(self._config.l3_depth)
        while not stop_event.is_set():
            captured = await self._receive_or_stop(connection, stop_event)
            if captured is None:
                break
            document = await self._decode_inbound(captured, session_id, "l3", token=token)
            if _is_heartbeat(document):
                continue
            if document.get("channel") == "status":
                await self._record_status(document, session_id, "l3")
                continue
            if _is_subscription_response(document):
                channel = await self._record_subscription_response(
                    document,
                    session_id,
                    stream="l3",
                    authenticated=True,
                    expected_channels=frozenset({"level3"}),
                )
                if channel != "level3":
                    raise KrakenAuthenticationError("Kraken L3 subscription failed.")
                acknowledged = True
                await self._append_marker(
                    session_id,
                    "subscription",
                    "subscriptions_active",
                    stream="l3",
                    product=KRAKEN_RESEARCH_PRODUCT,
                    authenticated=True,
                )
                continue
            if document.get("channel") != "level3" or not acknowledged:
                await self._schema_failure(session_id, "l3", None)
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="level3",
            )
            try:
                normalized = book_state.normalize(document, raw_ordinal)
            except KrakenDataIntegrityError as error:
                await self._integrity_failure(session_id, "l3", raw_ordinal, error)
                raise
            await self._append_normalized(session_id, "level3", normalized)
        if not acknowledged:
            await self._append_marker(
                session_id,
                "data_quality",
                "authentication_failed",
                stream="l3",
                reason="capture_stopped_before_authenticated_acknowledgement",
            )
            raise KrakenAuthenticationError("Kraken L3 subscription was not authenticated.")
        if not book_state.has_snapshot:
            await self._append_marker(
                session_id,
                "data_quality",
                "snapshot_missing",
                stream="l3",
                reason="capture_stopped_before_l3_snapshot",
            )
            raise KrakenDataIntegrityError("Kraken L3 book snapshot was incomplete.")

    async def _receive_or_stop(
        self,
        connection: WebSocketConnection,
        stop_event: asyncio.Event,
    ) -> CapturedApplicationPayload | None:
        receive_task = asyncio.create_task(connection.recv(), name="kraken-research-receive")
        stop_task = asyncio.create_task(stop_event.wait(), name="kraken-research-stop-wait")
        try:
            done, _ = await asyncio.wait(
                (receive_task, stop_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                frame: str | bytes | None = None
                unexpected_receive_failure = False
                try:
                    frame = receive_task.result()
                except asyncio.CancelledError:
                    raise
                except (PayloadTooBig, WebSocketException, OSError):
                    raise
                except Exception:
                    unexpected_receive_failure = True
                if unexpected_receive_failure or frame is None:
                    raise OSError("Kraken WebSocket receive failed.")
                return capture_application_payload(
                    frame,
                    utc_ns=self._utc_ns,
                    monotonic_ns=self._monotonic_ns,
                )
            return None
        finally:
            for task in (receive_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_task, stop_task, return_exceptions=True)

    async def _decode_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
        stream: str,
        *,
        token: KrakenWebSocketToken | None,
    ) -> dict[str, object]:
        if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
            await self._terminal_quality(session_id, stream, "payload_size_error")
            raise KrakenDataIntegrityError("Kraken payload exceeded its bound.")
        if token is not None and token._appears_in(captured.payload_bytes):
            await self._terminal_quality(session_id, stream, "credential_echo_blocked")
            raise KrakenAuthenticationError("Kraken L3 inbound credential echo was blocked.")
        try:
            loaded = json.loads(
                captured.payload_bytes,
                parse_float=str,
                parse_int=str,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            await self._schema_failure(session_id, stream, None)
        if type(loaded) is not dict:
            await self._schema_failure(session_id, stream, None)
        return cast(dict[str, object], loaded)

    async def _record_subscription_response(
        self,
        document: dict[str, object],
        session_id: str,
        *,
        stream: str,
        authenticated: bool,
        expected_channels: frozenset[str],
    ) -> str:
        success = document.get("success")
        result = document.get("result")
        if success is not True or type(result) is not dict:
            event = "authentication_failed" if authenticated else "subscription_failed"
            await self._append_marker(
                session_id,
                "data_quality",
                event,
                stream=stream,
                reason="subscription_rejected",
            )
            error_type = KrakenAuthenticationError if authenticated else KrakenDataIntegrityError
            raise error_type("Kraken subscription failed.")
        channel = result.get("channel")
        symbol = result.get("symbol")
        if channel not in expected_channels or symbol != KRAKEN_RESEARCH_PRODUCT:
            await self._schema_failure(session_id, stream, None)
        channel_text = cast(str, channel)
        expected_snapshot = channel_text != "trade"
        depth = result.get("depth")
        expected_depth = self._config.l2_depth if channel_text == "book" else self._config.l3_depth
        depth_invalid = depth is not None and (
            channel_text == "trade" or depth != str(expected_depth)
        )
        if result.get("snapshot") is not expected_snapshot or depth_invalid:
            await self._schema_failure(session_id, stream, None)
        await self._append_marker(
            session_id,
            "subscription",
            "subscription_acknowledged",
            stream=stream,
            subscription_type=channel_text,
            product=KRAKEN_RESEARCH_PRODUCT,
            authenticated=authenticated,
        )
        return channel_text

    async def _record_status(
        self,
        document: dict[str, object],
        session_id: str,
        stream: str,
    ) -> None:
        try:
            message_type, data = _message_data(document, "status")
            if message_type != "update" or len(data) != 1:
                raise KrakenDataIntegrityError("Kraken status schema validation failed.")
            status = _object(data[0], "status data item")
            system = _required_text(status, "system")
            api_version = _required_text(status, "api_version")
            service_version = _required_text(status, "version")
            connection_id = _unsigned_integer_text(status, "connection_id")
            if system not in {"online", "cancel_only", "maintenance", "post_only"}:
                raise KrakenDataIntegrityError("Kraken status schema validation failed.")
            if api_version != "v2":
                raise KrakenDataIntegrityError("Kraken status schema validation failed.")
        except KrakenDataIntegrityError:
            await self._schema_failure(session_id, stream, None)
            return
        await self._append_marker(
            session_id,
            "session",
            "venue_status",
            stream=stream,
            system=system,
            api_version=api_version,
            connection_id=connection_id,
            service_version=service_version,
        )

    async def _append_normalized(
        self,
        session_id: str,
        channel: str,
        normalized: dict[str, object],
    ) -> None:
        await self._append_local_payload(
            session_id,
            f"normalized_{channel}",
            normalized,
        )

    async def _append_captured(
        self,
        captured: CapturedApplicationPayload,
        *,
        session_id: str,
        channel: str,
    ) -> int:
        async with self._append_lock:
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=KRAKEN_RESEARCH_VENUE,
                product=KRAKEN_RESEARCH_PRODUCT,
                channel=channel,
                session_id=session_id,
                message_ordinal=self._message_ordinal,
                received_utc_ns=captured.received_utc_ns,
                received_monotonic_ns=captured.received_monotonic_ns,
                direction=MessageDirection.INBOUND,
                frame_type=captured.frame_type,
                payload_encoding=captured.payload_encoding,
                payload_bytes=captured.payload_bytes,
            )
            await self._sink.append(record)
            return record.message_ordinal

    async def _append_marker(
        self,
        session_id: str,
        channel: str,
        event: str,
        **fields: object,
    ) -> int:
        return await self._append_local_payload(
            session_id,
            channel,
            {"event": event, **fields},
        )

    async def _append_local_payload(
        self,
        session_id: str,
        channel: str,
        payload: dict[str, object],
    ) -> int:
        payload_bytes = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        async with self._append_lock:
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=KRAKEN_RESEARCH_VENUE,
                product=KRAKEN_RESEARCH_PRODUCT,
                channel=channel,
                session_id=session_id,
                message_ordinal=self._message_ordinal,
                received_utc_ns=self._utc_ns(),
                received_monotonic_ns=self._monotonic_ns(),
                direction=MessageDirection.LOCAL,
                frame_type=FrameType.MARKER,
                payload_encoding=PayloadEncoding.UTF8_JSON,
                payload_bytes=payload_bytes,
            )
            await self._sink.append(record)
            return record.message_ordinal

    async def _session_started(
        self,
        session_id: str,
        stream: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "session_started",
            stream=stream,
            reason="initial_connection" if previous_session_id is None else "reconnect_attempt",
        )

    async def _connected(
        self,
        session_id: str,
        stream: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(session_id, "session", "connected", stream=stream)
        if previous_session_id is not None:
            await self._append_marker(
                session_id,
                "session",
                "reconnected",
                stream=stream,
                previous_session_id=previous_session_id,
            )

    async def _disconnected(self, session_id: str, stream: str) -> None:
        await self._append_marker(
            session_id,
            "session",
            "disconnected",
            stream=stream,
            reason="transport_error",
        )
        await self._append_marker(
            session_id,
            "data_quality",
            "gap_detected",
            stream=stream,
            reason="transport_disconnect; missed stream history is not reconstructable",
        )

    async def _connection_failed(
        self,
        session_id: str,
        stream: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "reconnect_failed" if previous_session_id is not None else "connection_failed",
            stream=stream,
            reason="transport_error",
        )

    async def _terminal_quality(self, session_id: str, stream: str, event: str) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            stream=stream,
            reason="capture_stopped_fail_closed",
        )

    async def _schema_failure(
        self,
        session_id: str,
        stream: str,
        raw_ordinal: int | None,
    ) -> None:
        fields: dict[str, object] = {
            "stream": stream,
            "reason": "inbound_schema_invalid",
        }
        if raw_ordinal is not None:
            fields["raw_message_ordinal"] = raw_ordinal
        await self._append_marker(session_id, "data_quality", "schema_error", **fields)
        raise KrakenDataIntegrityError("Kraken inbound schema validation failed.")

    async def _integrity_failure(
        self,
        session_id: str,
        stream: str,
        raw_ordinal: int,
        error: KrakenDataIntegrityError,
    ) -> None:
        message = str(error)
        if "checksum" in message:
            event = "checksum_error"
        elif "ordering" in message:
            event = "ordering_error"
        else:
            event = "schema_error"
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            stream=stream,
            reason="market_data_integrity_failure",
            raw_message_ordinal=raw_ordinal,
        )

    async def _wait_to_reconnect(self, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=float(self._config.reconnect_delay_seconds),
            )
        except TimeoutError:
            pass


def _stdlib_token_http_post(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
    max_response_bytes: int,
) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    response_bytes: bytes | None = None
    request_failed = False
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise KrakenAuthenticationError("Kraken WebSocket token request failed.")
            response_bytes = cast(bytes, response.read(max_response_bytes + 1))
    except KrakenAuthenticationError:
        raise
    except Exception:
        request_failed = True
    if request_failed or response_bytes is None:
        raise KrakenAuthenticationError("Kraken WebSocket token request failed.")
    if len(response_bytes) > max_response_bytes:
        raise KrakenAuthenticationError("Kraken WebSocket token response exceeded its bound.")
    return response_bytes


def _parse_token_response(response_bytes: bytes) -> KrakenWebSocketToken:
    if type(response_bytes) is not bytes or len(response_bytes) > _MAX_TOKEN_RESPONSE_BYTES:
        raise KrakenAuthenticationError("Kraken WebSocket token response was invalid.")
    try:
        loaded = json.loads(response_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise KrakenAuthenticationError("Kraken WebSocket token response was invalid.") from None
    if type(loaded) is not dict or loaded.get("error") != []:
        raise KrakenAuthenticationError("Kraken rejected the WebSocket token request.")
    result = loaded.get("result")
    if type(result) is not dict or result.get("expires") != 900:
        raise KrakenAuthenticationError("Kraken WebSocket token response was invalid.")
    token = result.get("token")
    if type(token) is not str:
        raise KrakenAuthenticationError("Kraken WebSocket token response was invalid.")
    return KrakenWebSocketToken(token)


@asynccontextmanager
async def _websocket_connection(
    url: str,
    config: KrakenL3ResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        url,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=20.0,
        max_size=config.max_application_payload_bytes,
        max_queue=1024,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _connection_factory(url: str, config: KrakenL3ResearchConfig) -> ConnectionFactory:
    return lambda: _websocket_connection(url, config)


def _public_subscriptions(l2_depth: int) -> tuple[tuple[str, str], ...]:
    if l2_depth == DEFAULT_L2_DEPTH:
        return _PUBLIC_SUBSCRIPTIONS
    return (
        _PUBLIC_SUBSCRIPTIONS[0],
        (
            "book",
            json.dumps(
                {
                    "method": "subscribe",
                    "params": {
                        "channel": "book",
                        "depth": l2_depth,
                        "snapshot": True,
                        "symbol": [KRAKEN_RESEARCH_PRODUCT],
                    },
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )


def _l3_subscription(token: KrakenWebSocketToken, depth: int) -> str:
    return json.dumps(
        {
            "method": "subscribe",
            "params": {
                "channel": "level3",
                "depth": depth,
                "snapshot": True,
                "symbol": [KRAKEN_RESEARCH_PRODUCT],
                "token": token._for_subscription(),
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _is_subscription_response(document: dict[str, object]) -> bool:
    return document.get("method") == "subscribe"


def _is_heartbeat(document: dict[str, object]) -> bool:
    return document.get("channel") == "heartbeat" and set(document) == {"channel"}


def _message_data(
    document: dict[str, object],
    expected_channel: str,
) -> tuple[str, list[object]]:
    if document.get("channel") != expected_channel:
        raise KrakenDataIntegrityError("Kraken market data schema validation failed.")
    message_type = document.get("type")
    data = document.get("data")
    if message_type not in {"snapshot", "update"} or type(data) is not list or not data:
        raise KrakenDataIntegrityError("Kraken market data schema validation failed.")
    return message_type, cast(list[object], data)


def _wire_side_entries(item: dict[str, object]) -> list[tuple[int, str, int, object]]:
    entries: list[tuple[int, str, int, object]] = []
    saw_side = False
    wire_order = 0
    for key, value in item.items():
        if key not in {"bids", "asks"}:
            continue
        if type(value) is not list:
            raise KrakenDataIntegrityError("Kraken book side schema validation failed.")
        saw_side = True
        side = "bid" if key == "bids" else "ask"
        for side_index, entry in enumerate(value):
            entries.append((wire_order, side, side_index, entry))
            wire_order += 1
    if not saw_side:
        raise KrakenDataIntegrityError("Kraken book side schema validation failed.")
    return entries


def _object(value: object, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise KrakenDataIntegrityError(f"Kraken {label} schema validation failed.")
    return cast(dict[str, object], value)


def _required_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value:
        raise KrakenDataIntegrityError("Kraken text field schema validation failed.")
    return value


def _decimal_text(
    document: dict[str, object],
    field: str,
    *,
    allow_zero: bool,
) -> tuple[str, Decimal]:
    value = document.get(field)
    if type(value) is not str or _PLAIN_DECIMAL.fullmatch(value) is None:
        raise KrakenDataIntegrityError("Kraken decimal field schema validation failed.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise KrakenDataIntegrityError("Kraken decimal field schema validation failed.") from None
    if decimal < 0 or (not allow_zero and decimal == 0):
        raise KrakenDataIntegrityError("Kraken decimal field schema validation failed.")
    return value, decimal


def _unsigned_integer_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value.isascii() or not value.isdigit():
        raise KrakenDataIntegrityError("Kraken integer field schema validation failed.")
    return value


def _uint32(document: dict[str, object], field: str) -> int:
    value = int(_unsigned_integer_text(document, field))
    if not 0 <= value <= 0xFFFFFFFF:
        raise KrakenDataIntegrityError("Kraken checksum field schema validation failed.")
    return value


def _timestamp(document: dict[str, object], field: str) -> str:
    value = _required_text(document, field)
    if _RFC3339_NANO_UTC.fullmatch(value) is None:
        raise KrakenDataIntegrityError("Kraken timestamp field schema validation failed.")
    return value


def _optional_timestamp(document: dict[str, object], field: str) -> str | None:
    if field not in document:
        return None
    return _timestamp(document, field)


def _timestamp_key(value: str) -> tuple[str, str]:
    matched = _RFC3339_NANO_UTC.fullmatch(value)
    if matched is None:
        raise KrakenDataIntegrityError("Kraken timestamp field schema validation failed.")
    fraction = matched.group("fraction") or ""
    return matched.group("second"), fraction.ljust(9, "0")


def _checksum_component(value: str) -> str:
    component = value.replace(".", "").lstrip("0")
    if not component:
        raise KrakenDataIntegrityError("Kraken checksum input was empty.")
    return component


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError("non-standard JSON constant")


def _require_bounded_duration(duration_seconds: object) -> float:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not math.isfinite(duration) or not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
    return duration


def refuse_protected_trade_keys(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when trade/signing or generic Kraken key names are set."""

    source = os.environ if environ is None else environ
    for name in PROTECTED_TRADE_KEY_ENV:
        if source.get(name):
            raise KrakenAuthenticationError(
                f"Refuse: protected environment name {name} is set. Value not printed."
            )


def load_optional_l3_token_provider(
    environ: Mapping[str, str] | None = None,
) -> KrakenRestTokenProvider | None:
    """Enable L3 only when both WS env names are present. Never echo values."""

    source = os.environ if environ is None else environ
    refuse_protected_trade_keys(source)
    api_key = source.get(KRAKEN_WS_API_KEY_ENV)
    api_secret = source.get(KRAKEN_WS_API_SECRET_ENV)
    if api_key is None and api_secret is None:
        return None
    if api_key is None or api_secret is None:
        raise KrakenAuthenticationError(
            "Kraken L3 requires both KRAKEN_WS_API_KEY and KRAKEN_WS_API_SECRET. "
            "Values are not printed. Public L2+trades is the default retained path."
        )
    return KrakenRestTokenProvider(
        KrakenApiCredentials(api_key=api_key, api_secret_base64=api_secret)
    )


def build_capture_report(database_path: Path, parquet_dir: Path) -> dict[str, object]:
    """Return payload/file counts only; never emit captured payload contents."""

    connection = duckdb.connect(str(database_path.resolve()), read_only=True)
    try:
        channel_rows = connection.execute(
            """
            SELECT channel, direction, count(*), sum(octet_length(payload_bytes))
            FROM raw_records
            GROUP BY channel, direction
            ORDER BY channel, direction
            """
        ).fetchall()
        totals = connection.execute(
            "SELECT count(*), coalesce(sum(octet_length(payload_bytes)), 0) FROM raw_records"
        ).fetchone()
        gap_row = connection.execute(
            "SELECT count(*) FROM data_quality_events WHERE event = 'gap_detected'"
        ).fetchone()
        reconnect_row = connection.execute(
            "SELECT count(*) FROM sessions WHERE event = 'reconnected'"
        ).fetchone()
        if totals is None or gap_row is None or reconnect_row is None:
            raise RuntimeError("DuckDB did not return the requested capture aggregates.")
        total_events, total_payload_bytes = totals
    finally:
        connection.close()

    parquet_files = tuple(sorted(parquet_dir.resolve().glob("*.parquet")))
    parquet_bytes = sum(path.stat().st_size for path in parquet_files)
    raw_bytes = int(total_payload_bytes)
    return {
        "channels": [
            {
                "channel": str(channel),
                "direction": str(direction),
                "events": int(events),
                "payload_bytes": int(payload_bytes),
            }
            for channel, direction, events, payload_bytes in channel_rows
        ],
        "events": int(total_events),
        "payload_bytes": raw_bytes,
        "parquet_files": len(parquet_files),
        "parquet_bytes": parquet_bytes,
        "raw_payload_to_parquet_ratio": (raw_bytes / parquet_bytes if parquet_bytes else None),
        "gaps": int(gap_row[0]),
        "reconnects": int(reconnect_row[0]),
    }


async def run_bounded_capture(
    *,
    output_dir: Path,
    database_path: Path,
    duration_seconds: float,
    token_provider: KrakenTokenProvider | None = None,
    stop_event: asyncio.Event | None = None,
    config: KrakenL3ResearchConfig | None = None,
    collector_factory: Callable[[RawResearchSink], KrakenL3ResearchCollector] | None = None,
) -> dict[str, object]:
    """Run public (and optional L3) capture, close Parquet, and build the catalog."""

    duration = _require_bounded_duration(duration_seconds)
    writer = ParquetResearchWriter(output_dir, rotation=ParquetRotation())
    active = (
        collector_factory(writer)
        if collector_factory is not None
        else KrakenL3ResearchCollector(writer, token_provider, config=config)
    )
    try:
        await active.capture_for(duration, stop_event=stop_event)
    finally:
        await writer.aclose()
    create_research_catalog(output_dir, database_path)
    return build_capture_report(database_path, output_dir)


def data1b_capture_claim(
    *,
    run_id: str,
    duration_seconds: float,
    paths: Data1BRunPaths,
    include_l3: bool,
    config: KrakenL3ResearchConfig | None = None,
) -> dict[str, object]:
    """Create-only start claim for a reconstructable DATA-1B run."""

    duration = _require_bounded_duration(duration_seconds)
    depths = config if config is not None else KrakenL3ResearchConfig()
    return {
        "schema": DATA1B_CLAIM_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "path_contract": DATA1B_PATH_CONTRACT_ID,
        "run_id": run_id,
        "venue": KRAKEN_RESEARCH_VENUE,
        "product": DATA1B_PRODUCT,
        "wire_product": KRAKEN_RESEARCH_PRODUCT,
        "feed": (
            "kraken-public-btc-eur-book-trades-l3"
            if include_l3
            else "kraken-public-btc-eur-book-trades"
        ),
        "public_websocket_url": KRAKEN_PUBLIC_WEBSOCKET_URL,
        "l3_websocket_url": KRAKEN_L3_WEBSOCKET_URL if include_l3 else None,
        "credentialless": not include_l3,
        "signing": False,
        "authenticated_l3": include_l3,
        "l2_depth": depths.l2_depth,
        "l3_depth": depths.l3_depth if include_l3 else None,
        "checksum_price_levels": CHECKSUM_PRICE_LEVELS,
        "duration_seconds": duration,
        "smoke_duration_seconds": SMOKE_CAPTURE_SECONDS,
        "max_duration_seconds": MAX_CAPTURE_SECONDS,
        "retained": duration > SMOKE_CAPTURE_SECONDS,
        "twenty_four_seven": False,
        "resume_policy": "never resume or overwrite an existing DATA-1B run directory",
        "raw_dir": paths.raw_dir.as_posix(),
        "database_path": paths.database_path.as_posix(),
    }


def data1b_capture_health(
    *,
    run_id: str,
    duration_seconds: float,
    status: str,
    report: dict[str, object],
    include_l3: bool,
) -> dict[str, object]:
    """Create-only end health for a reconstructable DATA-1B run."""

    if status not in {"COMPLETED", "OPERATOR_STOP", "FAILED"}:
        raise ValueError("DATA-1B capture-health status is outside the documented bound.")
    duration = _require_bounded_duration(duration_seconds)
    return {
        "schema": DATA1B_HEALTH_SCHEMA,
        "kind": "capture-health",
        "path_contract": DATA1B_PATH_CONTRACT_ID,
        "run_id": run_id,
        "status": status,
        "duration_seconds": duration,
        "retained": duration > SMOKE_CAPTURE_SECONDS,
        "twenty_four_seven": False,
        "credentialless": not include_l3,
        "authenticated_l3": include_l3,
        "signing": False,
        "events": report.get("events"),
        "payload_bytes": report.get("payload_bytes"),
        "parquet_files": report.get("parquet_files"),
        "parquet_bytes": report.get("parquet_bytes"),
        "gaps": report.get("gaps"),
        "reconnects": report.get("reconnects"),
        "limitations": [
            "Published Parquet parts are reconstructable; a crash can lose the in-memory segment.",
            "This is not 24/7 service evidence or a trading edge.",
            "Default retained path is public L2 + trades at depth 100; optional L3 is never a silent fallback.",
            "Optional L3 keys enter only through KRAKEN_WS_API_KEY and KRAKEN_WS_API_SECRET.",
            "Generic KRAKEN_API_KEY / KRAKEN_API_SECRET names fail closed as the wrong key type.",
            "Kraken CRC32 still covers only the best 10 price levels even at subscribed depth 100.",
        ],
    }


def _write_create_only_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n")


async def run_reconstructable_capture(
    *,
    artifact_root: Path,
    run_id: str,
    duration_seconds: float,
    token_provider: KrakenTokenProvider | None = None,
    stop_event: asyncio.Event | None = None,
    operator_stop: Callable[[], bool] | None = None,
    config: KrakenL3ResearchConfig | None = None,
    collector_factory: Callable[[RawResearchSink], KrakenL3ResearchCollector] | None = None,
) -> dict[str, object]:
    """Write DATA-1B Parquet/DuckDB to the documented reconstructable path."""

    refuse_protected_trade_keys()
    include_l3 = token_provider is not None
    paths = data1b_run_paths(artifact_root, run_id)
    if paths.run_dir.exists():
        raise FileExistsError(f"DATA-1B refuses to reuse existing run directory: {paths.run_dir}")
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.raw_dir.mkdir(exist_ok=False)
    _write_create_only_json(
        paths.capture_claim_path,
        data1b_capture_claim(
            run_id=run_id,
            duration_seconds=duration_seconds,
            paths=paths,
            include_l3=include_l3,
            config=config,
        ),
    )
    report: dict[str, object] = {
        "events": 0,
        "payload_bytes": 0,
        "parquet_files": 0,
        "parquet_bytes": 0,
        "gaps": 0,
        "reconnects": 0,
    }
    status = "FAILED"
    try:
        report = await run_bounded_capture(
            output_dir=paths.raw_dir,
            database_path=paths.database_path,
            duration_seconds=duration_seconds,
            token_provider=token_provider,
            stop_event=stop_event,
            config=config,
            collector_factory=collector_factory,
        )
        if operator_stop is not None and operator_stop():
            status = "OPERATOR_STOP"
        else:
            status = "COMPLETED"
    finally:
        if not paths.capture_health_path.exists():
            _write_create_only_json(
                paths.capture_health_path,
                data1b_capture_health(
                    run_id=run_id,
                    duration_seconds=duration_seconds,
                    status=status,
                    report=report,
                    include_l3=include_l3,
                ),
            )
    return {
        **report,
        "run_id": run_id,
        "path_contract": DATA1B_PATH_CONTRACT_ID,
        "run_dir": str(paths.run_dir),
        "raw_dir": str(paths.raw_dir),
        "database_path": str(paths.database_path),
        "status": status,
        "authenticated_l3": include_l3,
        "twenty_four_seven": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Kraken BTC/EUR exact-raw research capture. Default is public L2 + trades "
            "at depth 100. Optional authenticated L3 uses KRAKEN_WS_API_KEY and "
            "KRAKEN_WS_API_SECRET and also defaults to depth 100. CRC32 still covers "
            "only the best 10 price levels. Duration may exceed the historical 600s "
            "smoke cap up to 7 days. This is not a 24/7 service. Do not start a "
            "multi-day retain from a Cloud Agent."
        )
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--duration-seconds", required=True, type=float)
    parser.add_argument(
        "--l2-depth",
        type=int,
        default=DEFAULT_L2_DEPTH,
        choices=sorted(SUPPORTED_L2_DEPTHS),
        help=(
            "Public book depth. Default 100. Existing Kraken depths 10/25/100/500/1000 "
            "remain valid. CRC32 still covers only the best 10 price levels."
        ),
    )
    parser.add_argument(
        "--l3-depth",
        type=int,
        default=DEFAULT_L3_DEPTH,
        choices=sorted(SUPPORTED_L3_DEPTHS),
        help=(
            "Optional authenticated level3 depth when L3 keys are set. Default 100. "
            "Existing Kraken depths 10/100/1000 remain valid. CRC32 still covers only "
            "the best 10 price levels."
        ),
    )
    return parser


def _resolve_cli_mode(args: argparse.Namespace) -> str:
    reconstructable = args.artifact_root is not None or args.run_id is not None
    ad_hoc = args.output_dir is not None or args.database is not None
    if reconstructable and ad_hoc:
        raise ValueError(
            "Use either --artifact-root/--run-id or --output-dir/--database, not both."
        )
    if reconstructable:
        if args.artifact_root is None or args.run_id is None:
            raise ValueError("Reconstructable capture requires both --artifact-root and --run-id.")
        return "reconstructable"
    if args.output_dir is None or args.database is None:
        raise ValueError(
            "Ad-hoc capture requires --output-dir and --database; "
            "preferred reconstructable mode uses --artifact-root and --run-id."
        )
    return "ad_hoc"


async def _run_from_args(args: argparse.Namespace) -> dict[str, object]:
    stop_event = asyncio.Event()
    operator_stopped = False

    def _request_operator_stop() -> None:
        nonlocal operator_stopped
        operator_stopped = True
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_operator_stop)
    except (NotImplementedError, RuntimeError):
        pass

    token_provider = load_optional_l3_token_provider()
    config = KrakenL3ResearchConfig(l2_depth=args.l2_depth, l3_depth=args.l3_depth)
    mode = _resolve_cli_mode(args)
    if mode == "reconstructable":
        return await run_reconstructable_capture(
            artifact_root=cast(Path, args.artifact_root),
            run_id=cast(str, args.run_id),
            duration_seconds=cast(float, args.duration_seconds),
            token_provider=token_provider,
            stop_event=stop_event,
            operator_stop=lambda: operator_stopped,
            config=config,
        )
    return await run_bounded_capture(
        output_dir=cast(Path, args.output_dir),
        database_path=cast(Path, args.database),
        duration_seconds=cast(float, args.duration_seconds),
        token_provider=token_provider,
        stop_event=stop_event,
        config=config,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = asyncio.run(_run_from_args(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
