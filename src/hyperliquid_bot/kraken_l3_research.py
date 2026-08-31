"""Bounded Kraken BTC/EUR public L2/trades and authenticated L3 capture."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

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

KRAKEN_PUBLIC_WEBSOCKET_URL: Final = "wss://ws.kraken.com/v2"
KRAKEN_L3_WEBSOCKET_URL: Final = "wss://ws-l3.kraken.com/v2"
KRAKEN_TOKEN_URL: Final = "https://api.kraken.com/0/private/GetWebSocketsToken"
KRAKEN_TOKEN_PATH: Final = "/0/private/GetWebSocketsToken"
KRAKEN_RESEARCH_VENUE: Final = "kraken"
KRAKEN_RESEARCH_PRODUCT: Final = "BTC/EUR"
MAX_CAPTURE_SECONDS: Final = 600.0

_PUBLIC_SUBSCRIPTIONS: Final = (
    (
        "trade",
        '{"method":"subscribe","params":{"channel":"trade","snapshot":false,"symbol":["BTC/EUR"]}}',
    ),
    (
        "book",
        '{"method":"subscribe","params":{"channel":"book","depth":10,'
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

    l2_depth: int = 10
    l3_depth: int = 10
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.l2_depth) is not int or self.l2_depth not in {10, 25, 100, 500, 1000}:
            raise ValueError("l2_depth is not supported by Kraken Spot WebSocket v2.")
        if type(self.l3_depth) is not int or self.l3_depth not in {10, 100, 1000}:
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
            prices = sorted(self._levels[side], reverse=reverse)[:10]
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
            )[:10]
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
        token_provider: KrakenTokenProvider,
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
        """Run both required connections; a terminal failure stops the other feed."""

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
    if l2_depth == 10:
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


def _require_bounded_duration(duration_seconds: object) -> None:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
