"""Bounded offline Polymarket BTC/crypto prediction-market research capture."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Protocol, cast

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

POLYMARKET_MARKET_WEBSOCKET_URL: Final = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_RESEARCH_VENUE: Final = "polymarket"
POLYMARKET_RESEARCH_PRODUCT: Final = "BTC-CRYPTO-RESEARCH"
MAX_CAPTURE_SECONDS: Final = 180.0
MAX_MARKET_BATCH_EVENTS: Final = 64

_PING_TEXT: Final = "PING"
_PONG_BYTES: Final = b"PONG"
_MARKET_BATCH_CHANNEL: Final = "market_batch"
_TOKEN_ID: Final = re.compile(r"[0-9]+\Z")
_CONDITION_ID: Final = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_INTEGER_TEXT: Final = re.compile(r"0|[1-9][0-9]*\Z")
_DECIMAL_TEXT: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_CAPTURE_SHUTDOWN_GRACE_SECONDS: Final = 0.25


class PolymarketCaptureError(RuntimeError):
    """Bounded public-data error that never includes a venue payload."""


class PolymarketDataIntegrityError(PolymarketCaptureError):
    """An inbound schema, identity, snapshot, or local-book rule failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event


class PolymarketTransportError(PolymarketCaptureError):
    """The injected public transport failed or exhausted its reconnect bound."""


class PolymarketSinkError(PolymarketCaptureError):
    """The shared exact-raw sink failed and capture stopped without retry."""


class PolymarketPayloadTruncated(OSError):
    """Transport-neutral signal that a complete application frame was not delivered."""


class _IntegerLexeme(str):
    """Distinguish an unquoted JSON integer from an official decimal string."""


class _DecimalLexeme(str):
    """Distinguish an unquoted JSON decimal from an official decimal string."""


class WebSocketConnection(Protocol):
    """Small transport surface supplied explicitly by an offline fake or later gate."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[], str]
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PolymarketOutcomeSpec:
    """One explicitly mapped outcome token; no outcome complement is inferred."""

    outcome: str
    token_id: str
    tick_size: str
    min_order_size: str

    def __post_init__(self) -> None:
        _validate_plain_text(self.outcome, "outcome")
        _validate_token_id(self.token_id)
        _validate_positive_decimal_text(self.tick_size, "tick_size")
        _validate_positive_decimal_text(self.min_order_size, "min_order_size")


@dataclass(frozen=True, slots=True)
class PolymarketMarketSpec:
    """Sanitized point-in-time metadata supplied outside the capture transport."""

    event_id: str
    market_id: str
    condition_id: str
    slug: str
    question: str
    end_time: str
    active: bool
    closed: bool
    enable_order_book: bool
    accepting_orders: bool
    neg_risk: bool
    outcomes: tuple[PolymarketOutcomeSpec, PolymarketOutcomeSpec]

    def __post_init__(self) -> None:
        for field_name in ("event_id", "market_id", "slug", "question", "end_time"):
            _validate_plain_text(cast(str, getattr(self, field_name)), field_name)
        if not _CONDITION_ID.fullmatch(self.condition_id):
            raise ValueError("condition_id must be a 32-byte hexadecimal identifier.")
        for field_name in ("active", "closed", "enable_order_book", "accepting_orders", "neg_risk"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be a boolean.")
        if (
            not self.active
            or self.closed
            or not self.enable_order_book
            or not self.accepting_orders
        ):
            raise ValueError(
                "market must be active, open, order-book enabled, and accepting orders."
            )
        if type(self.outcomes) is not tuple or len(self.outcomes) != 2:
            raise ValueError("outcomes must contain exactly two mapped outcome tokens.")
        if any(type(outcome) is not PolymarketOutcomeSpec for outcome in self.outcomes):
            raise TypeError("outcomes must contain PolymarketOutcomeSpec values.")
        if self.outcomes[0].outcome == self.outcomes[1].outcome:
            raise ValueError("outcome labels must be distinct.")
        if self.outcomes[0].token_id == self.outcomes[1].token_id:
            raise ValueError("outcome token IDs must be distinct.")


@dataclass(frozen=True, slots=True)
class PolymarketPublicResearchConfig:
    """One explicitly mapped binary condition and its transport controls."""

    markets: tuple[PolymarketMarketSpec, ...]
    reconnect_delay_seconds: float = 1.0
    heartbeat_interval_seconds: float = 10.0
    pong_timeout_seconds: float = 5.0
    send_timeout_seconds: float = 5.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    max_reconnects: int = 1

    def __post_init__(self) -> None:
        if type(self.markets) is not tuple or len(self.markets) != 1:
            raise ValueError("markets must contain exactly one binary condition.")
        if any(type(market) is not PolymarketMarketSpec for market in self.markets):
            raise TypeError("markets must contain PolymarketMarketSpec values.")
        for field_name, lower_exclusive, upper_inclusive in (
            ("heartbeat_interval_seconds", 0.0, 10.0),
            ("pong_timeout_seconds", 0.0, 30.0),
            ("send_timeout_seconds", 0.0, 30.0),
        ):
            _validate_finite_float(
                getattr(self, field_name),
                field_name,
                lower_exclusive=lower_exclusive,
                upper_inclusive=upper_inclusive,
            )
        if float(self.pong_timeout_seconds) > float(self.heartbeat_interval_seconds):
            raise ValueError("pong_timeout_seconds must not exceed heartbeat_interval_seconds.")
        _validate_finite_float(
            self.reconnect_delay_seconds,
            "reconnect_delay_seconds",
            lower_inclusive=0.0,
            upper_inclusive=30.0,
        )
        if (
            type(self.max_application_payload_bytes) is not int
            or self.max_application_payload_bytes <= 0
        ):
            raise ValueError("max_application_payload_bytes must be a positive integer.")
        if type(self.max_reconnects) is not int or not 0 <= self.max_reconnects <= 1:
            raise ValueError("max_reconnects must be zero or one.")

    @property
    def token_ids(self) -> tuple[str, ...]:
        """Return both exact token strings in their explicit outcome-mapping order."""

        return tuple(outcome.token_id for outcome in self.markets[0].outcomes)


@dataclass(slots=True)
class _BookState:
    condition_id: str
    tick_size: str
    bids: dict[Decimal, str]
    asks: dict[Decimal, str]
    has_snapshot: bool = False
    snapshot_count: int = 0


class _SessionState:
    """Session-local L2 state; no undocumented continuity primitive is invented."""

    def __init__(self, config: PolymarketPublicResearchConfig) -> None:
        self.books = {
            outcome.token_id: _BookState(market.condition_id, outcome.tick_size, {}, {})
            for market in config.markets
            for outcome in market.outcomes
        }
        self.market_by_condition = {market.condition_id: market for market in config.markets}
        self.condition_by_token = {
            outcome.token_id: market.condition_id
            for market in config.markets
            for outcome in market.outcomes
        }

    @property
    def all_snapshots_received(self) -> bool:
        return all(book.has_snapshot for book in self.books.values())

    def staged_copy(self) -> _SessionState:
        """Copy mutable book state for frame-atomic validation and normalization."""

        staged = object.__new__(_SessionState)
        staged.books = {
            token_id: _BookState(
                condition_id=book.condition_id,
                tick_size=book.tick_size,
                bids=dict(book.bids),
                asks=dict(book.asks),
                has_snapshot=book.has_snapshot,
                snapshot_count=book.snapshot_count,
            )
            for token_id, book in self.books.items()
        }
        staged.market_by_condition = self.market_by_condition
        staged.condition_by_token = self.condition_by_token
        return staged

    def stage_frame(
        self,
        documents: tuple[dict[str, object], ...],
        raw_ordinal: int,
        *,
        is_batch: bool,
        source_frame_channel: str,
    ) -> tuple[_SessionState, tuple[dict[str, object], ...], tuple[str, ...]]:
        """Validate one whole wire frame on copied state before any derived write."""

        if not documents:
            raise PolymarketDataIntegrityError("Polymarket event array must not be empty.")
        event_types = tuple(_required_text(document, "event_type") for document in documents)
        if is_batch:
            batch_limit = (
                MAX_MARKET_BATCH_EVENTS if self.all_snapshots_received else len(self.books)
            )
            if len(documents) > batch_limit:
                raise PolymarketDataIntegrityError("Polymarket event array exceeded its bound.")
        if is_batch and not self.all_snapshots_received:
            if any(event_type != "book" for event_type in event_types):
                raise PolymarketDataIntegrityError(
                    "Polymarket initialization accepts only book snapshots.",
                    quality_event="update_before_snapshot",
                )
            seen_assets: set[str] = set()
            for document in documents:
                condition_id = _condition_identity(document, self.market_by_condition)
                token_id = _required_text(document, "asset_id")
                book = self._book_for(condition_id, token_id)
                if token_id in seen_assets or book.has_snapshot:
                    raise PolymarketDataIntegrityError(
                        "Polymarket initialization repeated an outcome asset."
                    )
                seen_assets.add(token_id)

        staged = self.staged_copy()
        normalized_rows: list[dict[str, object]] = []
        for frame_wire_order, document in enumerate(documents):
            for normalized in staged.normalize(document, raw_ordinal):
                normalized["source_frame_channel"] = source_frame_channel
                normalized["frame_wire_order"] = frame_wire_order
                normalized_rows.append(normalized)
        return staged, tuple(normalized_rows), event_types

    def normalize(
        self, document: dict[str, object], raw_ordinal: int
    ) -> tuple[dict[str, object], ...]:
        event_type = _required_text(document, "event_type")
        if event_type == "book":
            return (self._book(document, raw_ordinal),)
        if event_type == "price_change":
            return self._price_change(document, raw_ordinal)
        if event_type == "best_bid_ask":
            return (self._best_bid_ask(document, raw_ordinal),)
        if event_type == "last_trade_price":
            return (self._last_trade(document, raw_ordinal),)
        if event_type == "tick_size_change":
            return (self._tick_size(document, raw_ordinal),)
        if event_type == "market_resolved":
            condition_id = self._validate_market_resolved(document)
            if condition_id in self.market_by_condition:
                raise PolymarketDataIntegrityError(
                    "A configured Polymarket market resolved during capture.",
                    quality_event="market_closed",
                )
            return ()
        if event_type == "new_market":
            self._validate_new_market(document)
            return ()
        raise PolymarketDataIntegrityError("Polymarket event type was not requested or supported.")

    def _book(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        condition_id, token_id, book = self._identity(document)
        timestamp_ms = _timestamp_text(document)
        book_hash = _required_text(document, "hash")
        events: list[dict[str, object]] = []
        new_bids = _book_side(document, "bids", "BUY", events)
        new_asks = _book_side(document, "asks", "SELL", events)
        _validate_uncrossed_book(new_bids, new_asks)
        message_type = "resnapshot" if book.has_snapshot else "snapshot"
        book.bids = new_bids
        book.asks = new_asks
        book.has_snapshot = True
        book.snapshot_count += 1
        return {
            "event": "normalized_l2_frame",
            "source_channel": "book",
            "raw_message_ordinal": raw_ordinal,
            "message_type": message_type,
            "market": condition_id,
            "asset_id": token_id,
            "timestamp_ms": timestamp_ms,
            "opaque_hash": book_hash,
            "sequence_available": False,
            "checksum_available": False,
            "events": events,
        }

    def _price_change(
        self,
        document: dict[str, object],
        raw_ordinal: int,
    ) -> tuple[dict[str, object], ...]:
        condition_id = _condition_identity(document, self.market_by_condition)
        timestamp_ms = _optional_timestamp_text(document)
        changes = _required_list(document, "price_changes")
        if not changes:
            raise PolymarketDataIntegrityError("price_changes must not be empty.")
        seen: set[tuple[str, str, Decimal]] = set()
        l2_by_token: dict[str, list[dict[str, object]]] = {}
        bbo_rows: list[dict[str, object]] = []
        for wire_order, value in enumerate(changes):
            change = _required_object(value, "price_changes entry")
            token_id = _required_text(change, "asset_id")
            book = self._book_for(condition_id, token_id)
            if not book.has_snapshot:
                raise PolymarketDataIntegrityError(
                    "Polymarket price change arrived before its token snapshot.",
                    quality_event="update_before_snapshot",
                )
            price = _price_text(change, "price")
            price_key = Decimal(price)
            size = _nonnegative_decimal_text(change, "size")
            side = _side_text(change)
            opaque_hash = _optional_text(change, "hash")
            identity = (token_id, side, price_key)
            if identity in seen:
                raise PolymarketDataIntegrityError("Duplicate price level appeared in one update.")
            seen.add(identity)
            levels = book.bids if side == "BUY" else book.asks
            action = "delete" if Decimal(size) == 0 else "upsert"
            if action == "delete":
                levels.pop(price_key, None)
            else:
                levels[price_key] = size
            l2_by_token.setdefault(token_id, []).append(
                {
                    "wire_order": wire_order,
                    "side": side,
                    "action": action,
                    "price": price,
                    "size": size,
                    "opaque_hash": opaque_hash,
                }
            )
            best_bid = _optional_price_text(change, "best_bid")
            best_ask = _optional_price_text(change, "best_ask")
            if best_bid is not None or best_ask is not None:
                _validate_bbo_matches_book(book, best_bid, best_ask)
                bbo_rows.append(
                    _normalized_bbo(
                        raw_ordinal=raw_ordinal,
                        source_channel="price_change",
                        condition_id=condition_id,
                        token_id=token_id,
                        timestamp_ms=timestamp_ms,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        spread=None,
                    )
                )
        for token_id in l2_by_token:
            changed_book = self.books[token_id]
            _validate_uncrossed_book(changed_book.bids, changed_book.asks)
        normalized: list[dict[str, object]] = [
            {
                "event": "normalized_l2_frame",
                "source_channel": "price_change",
                "raw_message_ordinal": raw_ordinal,
                "message_type": "update",
                "market": condition_id,
                "asset_id": token_id,
                "timestamp_ms": timestamp_ms,
                "opaque_hash": None,
                "sequence_available": False,
                "checksum_available": False,
                "events": events,
            }
            for token_id, events in l2_by_token.items()
        ]
        normalized.extend(bbo_rows)
        return tuple(normalized)

    def _best_bid_ask(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        condition_id, token_id, book = self._identity(document)
        if not book.has_snapshot:
            raise PolymarketDataIntegrityError(
                "Polymarket BBO arrived before its token snapshot.",
                quality_event="update_before_snapshot",
            )
        best_bid = _price_text(document, "best_bid")
        best_ask = _price_text(document, "best_ask")
        _validate_bbo_matches_book(book, best_bid, best_ask)
        return _normalized_bbo(
            raw_ordinal=raw_ordinal,
            source_channel="best_bid_ask",
            condition_id=condition_id,
            token_id=token_id,
            timestamp_ms=_timestamp_text(document),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=_nonnegative_decimal_text(document, "spread"),
        )

    def _last_trade(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        condition_id, token_id, book = self._identity(document)
        if not book.has_snapshot:
            raise PolymarketDataIntegrityError(
                "Polymarket last-trade update arrived before its token snapshot.",
                quality_event="update_before_snapshot",
            )
        size = _nonnegative_decimal_text(document, "size")
        fee_rate_bps = _optional_nonnegative_decimal_text(document, "fee_rate_bps")
        transaction_hash = _optional_text(document, "transaction_hash")
        return {
            "event": "normalized_last_trade_price",
            "source_channel": "last_trade_price",
            "raw_message_ordinal": raw_ordinal,
            "market": condition_id,
            "asset_id": token_id,
            "price": _price_text(document, "price"),
            "size": size,
            "fee_rate_bps": fee_rate_bps,
            "side": _side_text(document),
            "timestamp_ms": _timestamp_text(document),
            "transaction_hash": transaction_hash,
            "complete_trade_tape": False,
        }

    def _tick_size(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        condition_id, token_id, book = self._identity(document)
        if not book.has_snapshot:
            raise PolymarketDataIntegrityError(
                "Polymarket tick-size update arrived before its token snapshot.",
                quality_event="update_before_snapshot",
            )
        old_tick_size = _positive_decimal_text(document, "old_tick_size")
        new_tick_size = _positive_decimal_text(document, "new_tick_size")
        if Decimal(old_tick_size) != Decimal(book.tick_size):
            raise PolymarketDataIntegrityError(
                "Polymarket tick-size change did not match session-local state.",
                quality_event="tick_size_mismatch",
            )
        book.tick_size = new_tick_size
        return {
            "event": "normalized_market_metadata",
            "record_type": "tick_size_change",
            "source_channel": "tick_size_change",
            "raw_message_ordinal": raw_ordinal,
            "market": condition_id,
            "asset_id": token_id,
            "old_tick_size": old_tick_size,
            "new_tick_size": new_tick_size,
            "timestamp_ms": _timestamp_text(document),
        }

    def _identity(self, document: dict[str, object]) -> tuple[str, str, _BookState]:
        condition_id = _condition_identity(document, self.market_by_condition)
        token_id = _required_text(document, "asset_id")
        return condition_id, token_id, self._book_for(condition_id, token_id)

    def _book_for(self, condition_id: str, token_id: str) -> _BookState:
        book = self.books.get(token_id)
        if book is None or self.condition_by_token.get(token_id) != condition_id:
            raise PolymarketDataIntegrityError("Polymarket market/token identity did not match.")
        return book

    def _validate_new_market(self, document: dict[str, object]) -> None:
        for field_name in ("id", "question", "slug"):
            _required_text(document, field_name)
        condition_id = _required_text(document, "market")
        if not _CONDITION_ID.fullmatch(condition_id):
            raise PolymarketDataIntegrityError("new_market condition ID was invalid.")
        _timestamp_text(document)
        assets = _required_list(document, "assets_ids")
        outcomes = _required_list(document, "outcomes")
        if len(assets) != 2 or len(outcomes) != 2:
            raise PolymarketDataIntegrityError("new_market must describe two mapped outcomes.")
        for asset in assets:
            if type(asset) is not str or not _TOKEN_ID.fullmatch(asset):
                raise PolymarketDataIntegrityError("new_market asset ID was invalid.")
        for outcome in outcomes:
            if type(outcome) is not str or not outcome or outcome != outcome.strip():
                raise PolymarketDataIntegrityError("new_market outcome label was invalid.")

    def _validate_market_resolved(self, document: dict[str, object]) -> str:
        _required_text(document, "id")
        condition_id = _required_text(document, "market")
        if not _CONDITION_ID.fullmatch(condition_id):
            raise PolymarketDataIntegrityError("market_resolved condition ID was invalid.")
        winning_asset_id = _required_text(document, "winning_asset_id")
        if not _TOKEN_ID.fullmatch(winning_asset_id):
            raise PolymarketDataIntegrityError("market_resolved winning asset ID was invalid.")
        _required_text(document, "winning_outcome")
        _timestamp_text(document)
        assets = _required_list(document, "assets_ids")
        if len(assets) != 2 or any(
            type(asset) is not str or not _TOKEN_ID.fullmatch(asset) for asset in assets
        ):
            raise PolymarketDataIntegrityError("market_resolved asset mapping was invalid.")
        if winning_asset_id not in assets:
            raise PolymarketDataIntegrityError(
                "market_resolved winner was absent from its asset mapping."
            )
        return condition_id


class PolymarketPublicResearchCapture:
    """Capture exact public market frames through a mandatory injected transport."""

    def __init__(
        self,
        sink: RawResearchSink,
        config: PolymarketPublicResearchConfig,
        *,
        connection_factory: ConnectionFactory,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        monotonic: MonotonicClock = time.monotonic,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable.")
        self._sink = sink
        self._config = config
        self._connection_factory = connection_factory
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._monotonic = monotonic
        self._session_id_factory = session_id_factory or (lambda: uuid.uuid4().hex)
        self._message_ordinal = 0
        self._sink_failed = False

    async def capture_for(
        self,
        duration_seconds: float,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run at most one reconnect within the hard 180-second research bound."""

        _require_bounded_duration(duration_seconds)
        if stop_event is not None and stop_event.is_set():
            raise PolymarketDataIntegrityError(
                "Polymarket capture was stopped before a session could start.",
                quality_event="coverage_incomplete",
            )
        capture_stop = asyncio.Event()
        stop_reason = ["capture_limit_reached"]
        duration = float(duration_seconds)
        deadline = asyncio.get_running_loop().time() + duration
        graceful_stop_after = max(
            0.0,
            duration - min(_CAPTURE_SHUTDOWN_GRACE_SECONDS, duration / 2),
        )
        timer = asyncio.create_task(
            self._stop_after(capture_stop, graceful_stop_after, stop_reason),
            name="polymarket-public-research-duration",
        )
        external_stop_task = (
            asyncio.create_task(
                self._forward_external_stop(stop_event, capture_stop, stop_reason),
                name="polymarket-public-research-external-stop",
            )
            if stop_event is not None
            else None
        )
        helper_tasks = [timer]
        if external_stop_task is not None:
            helper_tasks.append(external_stop_task)
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    await self._run(capture_stop, stop_reason)
            except TimeoutError:
                raise PolymarketTransportError(
                    "Polymarket capture exceeded its hard duration bound."
                ) from None
        finally:
            capture_stop.set()
            for task in helper_tasks:
                task.cancel()
            await asyncio.gather(*helper_tasks, return_exceptions=True)

    async def _stop_after(
        self,
        stop_event: asyncio.Event,
        duration_seconds: float,
        stop_reason: list[str],
    ) -> None:
        await asyncio.sleep(duration_seconds)
        if not stop_event.is_set():
            stop_reason[0] = "capture_limit_reached"
            stop_event.set()

    async def _forward_external_stop(
        self,
        external_stop: asyncio.Event,
        capture_stop: asyncio.Event,
        stop_reason: list[str],
    ) -> None:
        await external_stop.wait()
        if not capture_stop.is_set():
            stop_reason[0] = "external_stop_requested"
            capture_stop.set()

    async def _run(self, stop_event: asyncio.Event, stop_reason: list[str]) -> None:
        previous_session_id: str | None = None
        reconnects = 0
        while not stop_event.is_set():
            session_id = self._session_id_factory()
            await self._append_marker(
                session_id,
                "session",
                "session_started",
                previous_session_id=previous_session_id,
                authenticated=False,
            )
            await self._append_metadata(session_id)
            connected = False
            try:
                async with self._connection_factory() as connection:
                    connected = True
                    await self._append_marker(
                        session_id,
                        "session",
                        "reconnected" if previous_session_id is not None else "connected",
                        previous_session_id=previous_session_id,
                        authenticated=False,
                    )
                    await self._send_subscription(connection, session_id)
                    await self._receive_session(connection, session_id, stop_event)
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        reason=stop_reason[0],
                    )
                    return
            except asyncio.CancelledError:
                raise
            except PolymarketSinkError:
                raise
            except PolymarketDataIntegrityError:
                raise
            except PolymarketPayloadTruncated:
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "truncation_error",
                    reason="complete_application_frame_was_not_delivered",
                )
                raise PolymarketDataIntegrityError(
                    "Polymarket application payload was truncated.",
                    quality_event="truncation_error",
                ) from None
            except (OSError, TimeoutError):
                if not connected:
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "connection_failed",
                        reason="public_transport_unavailable",
                    )
                    raise PolymarketTransportError("Polymarket public connection failed.") from None
                await self._append_marker(
                    session_id,
                    "session",
                    "disconnected",
                    reason="public_transport_interrupted",
                )
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "gap",
                    reason="disconnect_interval_is_not_observable",
                )
                if reconnects >= self._config.max_reconnects:
                    raise PolymarketTransportError(
                        "Polymarket public reconnect bound was exhausted."
                    ) from None
                reconnects += 1
                previous_session_id = session_id
                if self._config.reconnect_delay_seconds:
                    try:
                        async with asyncio.timeout(float(self._config.reconnect_delay_seconds)):
                            await stop_event.wait()
                    except TimeoutError:
                        pass
                if stop_event.is_set():
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "reconnect_incomplete",
                        reason="capture_stopped_before_fresh_session_snapshot",
                    )
                    raise PolymarketTransportError(
                        "Polymarket capture stopped at an incomplete reconnect boundary."
                    ) from None
            except Exception:
                raise PolymarketTransportError("Polymarket public transport failed.") from None

    async def _send_subscription(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        payload_text = json.dumps(
            {
                "assets_ids": list(self._config.token_ids),
                "custom_feature_enabled": False,
                "initial_dump": True,
                "level": 2,
                "type": "market",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        captured = capture_application_payload(
            payload_text,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        try:
            async with asyncio.timeout(float(self._config.send_timeout_seconds)):
                await connection.send(payload_text)
        except TimeoutError:
            raise OSError("Polymarket public subscription send timed out.") from None
        raw_ordinal = await self._append_captured(
            captured,
            session_id=session_id,
            channel="subscription",
            direction=MessageDirection.OUTBOUND,
            payload_encoding=PayloadEncoding.UTF8_JSON,
        )
        await self._append_marker(
            session_id,
            "subscription",
            "subscription_sent",
            raw_message_ordinal=raw_ordinal,
            authenticated=False,
            token_count=len(self._config.token_ids),
            acknowledgement_available=False,
        )

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        state = _SessionState(self._config)
        next_heartbeat_at = self._monotonic() + float(self._config.heartbeat_interval_seconds)
        pong_deadline: float | None = None
        subscriptions_active_marked = False
        while not stop_event.is_set():
            now = self._monotonic()
            if pong_deadline is not None and now >= pong_deadline:
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "heartbeat_timeout",
                    reason="application_pong_deadline_expired",
                )
                raise OSError("Polymarket application heartbeat timed out.")
            if pong_deadline is None and now >= next_heartbeat_at:
                await self._send_heartbeat(connection, session_id)
                sent_at = self._monotonic()
                pong_deadline = sent_at + float(self._config.pong_timeout_seconds)
                next_heartbeat_at = sent_at + float(self._config.heartbeat_interval_seconds)
                continue
            next_deadline = next_heartbeat_at if pong_deadline is None else pong_deadline
            timeout = max(0.0, next_deadline - now)
            try:
                captured = await self._receive_or_stop(connection, stop_event, timeout)
            except TimeoutError:
                now = self._monotonic()
                if pong_deadline is not None and now >= pong_deadline:
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "heartbeat_timeout",
                        reason="application_pong_deadline_expired",
                    )
                    raise OSError("Polymarket application heartbeat timed out.") from None
                if now < next_heartbeat_at:
                    continue
                await self._send_heartbeat(connection, session_id)
                sent_at = self._monotonic()
                pong_deadline = sent_at + float(self._config.pong_timeout_seconds)
                next_heartbeat_at = sent_at + float(self._config.heartbeat_interval_seconds)
                continue
            if captured is None:
                break
            if captured.payload_bytes == _PONG_BYTES:
                if captured.frame_type is not FrameType.TEXT:
                    raw_ordinal = await self._append_captured(
                        captured,
                        session_id=session_id,
                        channel="heartbeat",
                        direction=MessageDirection.INBOUND,
                    )
                    await self._quality_failure(
                        session_id,
                        raw_ordinal,
                        PolymarketDataIntegrityError(
                            "Polymarket heartbeat used a non-text frame.",
                            quality_event="frame_type_error",
                        ),
                    )
                await self._append_captured(
                    captured,
                    session_id=session_id,
                    channel="heartbeat",
                    direction=MessageDirection.INBOUND,
                )
                pong_deadline = None
                continue
            raw_ordinal, documents, is_batch, source_frame_channel = await self._record_inbound(
                captured, session_id
            )
            try:
                staged_state, normalized_rows, event_types = state.stage_frame(
                    documents,
                    raw_ordinal,
                    is_batch=is_batch,
                    source_frame_channel=source_frame_channel,
                )
            except PolymarketDataIntegrityError as error:
                await self._quality_failure(session_id, raw_ordinal, error)
                raise
            for normalized in normalized_rows:
                await self._append_local_payload(
                    session_id,
                    _normalized_channel(normalized),
                    normalized,
                )
            state = staged_state
            if state.all_snapshots_received and not subscriptions_active_marked:
                await self._append_marker(
                    session_id,
                    "subscription",
                    "subscriptions_active",
                    authenticated=False,
                    evidence="fresh_book_snapshot_for_every_token",
                    raw_message_ordinal=raw_ordinal,
                )
                subscriptions_active_marked = True
            for frame_wire_order, event_type in enumerate(event_types):
                if event_type == "book":
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "snapshot_received",
                        raw_message_ordinal=raw_ordinal,
                        frame_wire_order=frame_wire_order,
                        reason="full_aggregated_l2_book",
                    )
        missing_snapshots = sum(not book.has_snapshot for book in state.books.values())
        if missing_snapshots:
            await self._append_marker(
                session_id,
                "data_quality",
                "coverage_incomplete",
                reason="required_session_evidence_missing",
                missing_snapshot_count=missing_snapshots,
            )
            raise PolymarketDataIntegrityError(
                "Polymarket session ended before required evidence was observed.",
                quality_event="coverage_incomplete",
            )

    async def _receive_or_stop(
        self,
        connection: WebSocketConnection,
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> CapturedApplicationPayload | None:
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="polymarket-public-research-receive",
        )
        stop_task = asyncio.create_task(stop_event.wait(), name="polymarket-public-stop")
        try:
            done, _ = await asyncio.wait(
                (receive_task, stop_task),
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                try:
                    return receive_task.result()
                except asyncio.CancelledError:
                    raise
                except PolymarketPayloadTruncated:
                    raise
                except Exception:
                    raise OSError("Polymarket WebSocket receive failed.") from None
            if stop_task in done:
                return None
            raise TimeoutError
        finally:
            for task in (receive_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_task, stop_task, return_exceptions=True)

    async def _receive_captured(
        self,
        connection: WebSocketConnection,
    ) -> CapturedApplicationPayload:
        frame = await connection.recv()
        return capture_application_payload(
            frame,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )

    async def _send_heartbeat(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        captured = capture_application_payload(
            _PING_TEXT,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        try:
            async with asyncio.timeout(float(self._config.send_timeout_seconds)):
                await connection.send(_PING_TEXT)
        except TimeoutError:
            raise OSError("Polymarket heartbeat send timed out.") from None
        await self._append_captured(
            captured,
            session_id=session_id,
            channel="heartbeat",
            direction=MessageDirection.OUTBOUND,
        )

    async def _record_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
    ) -> tuple[int, tuple[dict[str, object], ...], bool, str]:
        if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(
                session_id,
                raw_ordinal,
                PolymarketDataIntegrityError(
                    "Polymarket application payload exceeded its bound.",
                    quality_event="payload_oversize",
                ),
            )
        if captured.frame_type is not FrameType.TEXT:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(
                session_id,
                raw_ordinal,
                PolymarketDataIntegrityError(
                    "Polymarket market data must use a text frame.",
                    quality_event="frame_type_error",
                ),
            )
        try:
            decoded = _decode_json_value(captured.payload_bytes)
        except PolymarketDataIntegrityError as error:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(session_id, raw_ordinal, error)
            raise AssertionError("unreachable invalid payload") from None
        documents: tuple[dict[str, object], ...]
        if type(decoded) is dict:
            document = cast(dict[str, object], decoded)
            try:
                channel = _required_text(document, "event_type")
            except PolymarketDataIntegrityError as error:
                raw_ordinal = await self._append_captured(
                    captured,
                    session_id=session_id,
                    channel="unknown",
                    direction=MessageDirection.INBOUND,
                    payload_encoding=PayloadEncoding.UTF8_JSON,
                )
                await self._quality_failure(session_id, raw_ordinal, error)
                raise AssertionError("unreachable invalid payload") from None
            documents = (document,)
            is_batch = False
        elif type(decoded) is list:
            channel = _MARKET_BATCH_CHANNEL
            documents = ()
            is_batch = True
        else:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
                payload_encoding=PayloadEncoding.UTF8_JSON,
            )
            await self._quality_failure(
                session_id,
                raw_ordinal,
                PolymarketDataIntegrityError(
                    "Polymarket payload must be one event object or an event-object array."
                ),
            )
            raise AssertionError("unreachable invalid payload") from None
        raw_ordinal = await self._append_captured(
            captured,
            session_id=session_id,
            channel=channel,
            direction=MessageDirection.INBOUND,
            payload_encoding=PayloadEncoding.UTF8_JSON,
        )
        if is_batch:
            try:
                values = cast(list[object], decoded)
                if not values:
                    raise PolymarketDataIntegrityError("Polymarket event array must not be empty.")
                if len(values) > MAX_MARKET_BATCH_EVENTS:
                    raise PolymarketDataIntegrityError(
                        "Polymarket event array exceeded its absolute bound."
                    )
                documents = tuple(
                    _required_object(value, "Polymarket event array element") for value in values
                )
            except PolymarketDataIntegrityError as error:
                await self._quality_failure(session_id, raw_ordinal, error)
                raise AssertionError("unreachable invalid payload") from None
        return raw_ordinal, documents, is_batch, channel

    async def _append_metadata(self, session_id: str) -> None:
        for market in self._config.markets:
            for outcome_index, outcome in enumerate(market.outcomes):
                await self._append_local_payload(
                    session_id,
                    "normalized_market_metadata",
                    {
                        "event": "normalized_market_metadata",
                        "record_type": "configured_market",
                        "source_channel": "local_config",
                        "raw_message_ordinal": None,
                        "event_id": market.event_id,
                        "market_id": market.market_id,
                        "market": market.condition_id,
                        "slug": market.slug,
                        "question": market.question,
                        "end_time": market.end_time,
                        "active": market.active,
                        "closed": market.closed,
                        "enable_order_book": market.enable_order_book,
                        "accepting_orders": market.accepting_orders,
                        "neg_risk": market.neg_risk,
                        "outcome_index": outcome_index,
                        "outcome": outcome.outcome,
                        "asset_id": outcome.token_id,
                        "tick_size": outcome.tick_size,
                        "min_order_size": outcome.min_order_size,
                        "metadata_origin": "sanitized_injected_config",
                    },
                )

    async def _append_captured(
        self,
        captured: CapturedApplicationPayload,
        *,
        session_id: str,
        channel: str,
        direction: MessageDirection,
        payload_encoding: PayloadEncoding | None = None,
    ) -> int:
        ordinal = self._next_ordinal()
        record = RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue=POLYMARKET_RESEARCH_VENUE,
            product=POLYMARKET_RESEARCH_PRODUCT,
            channel=channel,
            session_id=session_id,
            message_ordinal=ordinal,
            received_utc_ns=captured.received_utc_ns,
            received_monotonic_ns=captured.received_monotonic_ns,
            direction=direction,
            frame_type=captured.frame_type,
            payload_encoding=(
                captured.payload_encoding if payload_encoding is None else payload_encoding
            ),
            payload_bytes=captured.payload_bytes,
        )
        await self._append_record(record)
        return ordinal

    async def _append_local_payload(
        self,
        session_id: str,
        channel: str,
        payload: dict[str, object],
    ) -> int:
        payload_bytes = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        ordinal = self._next_ordinal()
        await self._append_record(
            RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=POLYMARKET_RESEARCH_VENUE,
                product=POLYMARKET_RESEARCH_PRODUCT,
                channel=channel,
                session_id=session_id,
                message_ordinal=ordinal,
                received_utc_ns=self._utc_ns(),
                received_monotonic_ns=self._monotonic_ns(),
                direction=MessageDirection.LOCAL,
                frame_type=FrameType.MARKER,
                payload_encoding=PayloadEncoding.UTF8_JSON,
                payload_bytes=payload_bytes,
            )
        )
        return ordinal

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

    async def _quality_failure(
        self,
        session_id: str,
        raw_ordinal: int,
        error: PolymarketDataIntegrityError,
    ) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            error.quality_event,
            reason="fail_closed_public_market_integrity_boundary",
            raw_message_ordinal=raw_ordinal,
        )
        raise error

    async def _append_record(self, record: RawResearchRecord) -> None:
        if self._sink_failed:
            raise PolymarketSinkError("Polymarket raw sink is terminal after its first failure.")
        try:
            await self._sink.append(record)
        except Exception:
            self._sink_failed = True
            raise PolymarketSinkError("Polymarket raw sink failed.") from None

    def _next_ordinal(self) -> int:
        self._message_ordinal += 1
        return self._message_ordinal


def _decode_json_value(payload: bytes) -> object:
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            parse_int=_IntegerLexeme,
            parse_float=_DecimalLexeme,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PolymarketDataIntegrityError,
        RecursionError,
    ):
        raise PolymarketDataIntegrityError("Polymarket payload was not strict JSON.") from None
    return value


def _reject_json_constant(value: str) -> object:
    raise PolymarketDataIntegrityError(f"Non-finite JSON constant {value!r} is forbidden.")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PolymarketDataIntegrityError("Duplicate JSON object key is forbidden.")
        result[key] = value
    return result


def _condition_identity(
    document: dict[str, object],
    markets: dict[str, PolymarketMarketSpec],
) -> str:
    condition_id = _required_text(document, "market")
    if condition_id not in markets:
        raise PolymarketDataIntegrityError("Polymarket condition identity was not configured.")
    return condition_id


def _book_side(
    document: dict[str, object],
    key: str,
    side: str,
    events: list[dict[str, object]],
) -> dict[Decimal, str]:
    levels = _required_list(document, key)
    normalized: dict[Decimal, str] = {}
    for side_index, value in enumerate(levels):
        level = _required_object(value, f"{key} entry")
        price = _price_text(level, "price")
        price_key = Decimal(price)
        size = _nonnegative_decimal_text(level, "size")
        if Decimal(size) == 0:
            raise PolymarketDataIntegrityError("Snapshot levels must have positive size.")
        if price_key in normalized:
            raise PolymarketDataIntegrityError("Snapshot contained a duplicate price level.")
        normalized[price_key] = size
        events.append(
            {
                "wire_order": len(events),
                "side": side,
                "side_index": side_index,
                "action": "snapshot",
                "price": price,
                "size": size,
            }
        )
    return normalized


def _normalized_bbo(
    *,
    raw_ordinal: int,
    source_channel: str,
    condition_id: str,
    token_id: str,
    timestamp_ms: str | None,
    best_bid: str | None,
    best_ask: str | None,
    spread: str | None,
) -> dict[str, object]:
    if best_bid is None and best_ask is None:
        raise PolymarketDataIntegrityError("BBO update carried neither side.")
    if best_bid is not None and best_ask is not None and Decimal(best_bid) > Decimal(best_ask):
        raise PolymarketDataIntegrityError("Polymarket BBO was crossed.")
    if (
        spread is not None
        and best_bid is not None
        and best_ask is not None
        and Decimal(spread) != Decimal(best_ask) - Decimal(best_bid)
    ):
        raise PolymarketDataIntegrityError("Polymarket BBO spread was inconsistent.")
    return {
        "event": "normalized_bbo",
        "source_channel": source_channel,
        "raw_message_ordinal": raw_ordinal,
        "market": condition_id,
        "asset_id": token_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "timestamp_ms": timestamp_ms,
    }


def _normalized_channel(payload: dict[str, object]) -> str:
    event = payload.get("event")
    if event == "normalized_l2_frame":
        return "normalized_l2"
    if event == "normalized_bbo":
        return "normalized_bbo"
    if event == "normalized_last_trade_price":
        return "normalized_last_trade_price"
    if event == "normalized_market_metadata":
        return "normalized_market_metadata"
    raise AssertionError("Unsupported local normalization event.")


def _validate_bbo_matches_book(
    book: _BookState,
    best_bid: str | None,
    best_ask: str | None,
) -> None:
    if best_bid is not None and (not book.bids or Decimal(best_bid) != max(book.bids)):
        raise PolymarketDataIntegrityError(
            "Polymarket source BBO did not match session-local L2 state.",
            quality_event="book_state_mismatch",
        )
    if best_ask is not None and (not book.asks or Decimal(best_ask) != min(book.asks)):
        raise PolymarketDataIntegrityError(
            "Polymarket source BBO did not match session-local L2 state.",
            quality_event="book_state_mismatch",
        )


def _validate_uncrossed_book(bids: dict[Decimal, str], asks: dict[Decimal, str]) -> None:
    if bids and asks and max(bids) > min(asks):
        raise PolymarketDataIntegrityError("Polymarket aggregated L2 book was crossed.")


def _required_object(value: object, field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise PolymarketDataIntegrityError(f"{field_name} must be an object.")
    return cast(dict[str, object], value)


def _required_list(document: dict[str, object], key: str) -> list[object]:
    value = document.get(key)
    if type(value) is not list:
        raise PolymarketDataIntegrityError(f"{key} must be an array.")
    return cast(list[object], value)


def _required_text(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if type(value) is not str or not value or value != value.strip():
        raise PolymarketDataIntegrityError(f"{key} must be non-empty text.")
    return value


def _optional_text(document: dict[str, object], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise PolymarketDataIntegrityError(f"{key} must be null or non-empty text.")
    return value


def _timestamp_text(document: dict[str, object]) -> str:
    timestamp = _required_text(document, "timestamp")
    if not _INTEGER_TEXT.fullmatch(timestamp):
        raise PolymarketDataIntegrityError("timestamp must be an epoch-millisecond string.")
    return timestamp


def _optional_timestamp_text(document: dict[str, object]) -> str | None:
    timestamp = document.get("timestamp")
    if timestamp is None:
        return None
    if type(timestamp) is not str or not timestamp or timestamp != timestamp.strip():
        raise PolymarketDataIntegrityError("timestamp must be null or an epoch-millisecond string.")
    if not _INTEGER_TEXT.fullmatch(timestamp):
        raise PolymarketDataIntegrityError("timestamp must be null or an epoch-millisecond string.")
    return timestamp


def _side_text(document: dict[str, object]) -> str:
    side = _required_text(document, "side")
    if side not in {"BUY", "SELL"}:
        raise PolymarketDataIntegrityError("side must be BUY or SELL.")
    return side


def _price_text(document: dict[str, object], key: str) -> str:
    value = _required_text(document, key)
    decimal = _decimal(value, key)
    if not Decimal(0) <= decimal <= Decimal(1):
        raise PolymarketDataIntegrityError(f"{key} must be between zero and one.")
    return value


def _optional_price_text(document: dict[str, object], key: str) -> str | None:
    value = document.get(key)
    if value is None or value == "":
        return None
    if type(value) is not str or not value or value != value.strip():
        raise PolymarketDataIntegrityError(f"{key} must be null or decimal text.")
    decimal = _decimal(value, key)
    if not Decimal(0) <= decimal <= Decimal(1):
        raise PolymarketDataIntegrityError(f"{key} must be between zero and one.")
    return value


def _positive_decimal_text(document: dict[str, object], key: str) -> str:
    value = _required_text(document, key)
    if _decimal(value, key) <= 0:
        raise PolymarketDataIntegrityError(f"{key} must be positive.")
    return value


def _nonnegative_decimal_text(document: dict[str, object], key: str) -> str:
    value = _required_text(document, key)
    if _decimal(value, key) < 0:
        raise PolymarketDataIntegrityError(f"{key} must be non-negative.")
    return value


def _optional_nonnegative_decimal_text(document: dict[str, object], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise PolymarketDataIntegrityError(f"{key} must be null or decimal text.")
    if _decimal(value, key) < 0:
        raise PolymarketDataIntegrityError(f"{key} must be non-negative.")
    return value


def _decimal(value: str, field_name: str) -> Decimal:
    if not _DECIMAL_TEXT.fullmatch(value):
        raise PolymarketDataIntegrityError(f"{field_name} must be canonical decimal text.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise PolymarketDataIntegrityError(f"{field_name} was not decimal text.") from None
    if not decimal.is_finite():
        raise PolymarketDataIntegrityError(f"{field_name} must be finite.")
    return decimal


def _validate_plain_text(value: str, field_name: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty text without outer whitespace.")


def _validate_token_id(value: str) -> None:
    if type(value) is not str or not _TOKEN_ID.fullmatch(value):
        raise ValueError("token_id must be unsigned decimal text.")


def _validate_positive_decimal_text(value: str, field_name: str) -> None:
    if type(value) is not str or not _DECIMAL_TEXT.fullmatch(value):
        raise ValueError(f"{field_name} must be canonical decimal text.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field_name} must be decimal text.") from None
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError(f"{field_name} must be a positive finite decimal string.")


def _validate_finite_float(
    value: object,
    field_name: str,
    *,
    lower_inclusive: float | None = None,
    lower_exclusive: float | None = None,
    upper_inclusive: float,
) -> None:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a finite number.")
    numeric = float(cast(int | float, value))
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number.")
    if lower_inclusive is not None and numeric < lower_inclusive:
        raise ValueError(f"{field_name} is below its lower bound.")
    if lower_exclusive is not None and numeric <= lower_exclusive:
        raise ValueError(f"{field_name} must be positive.")
    if numeric > upper_inclusive:
        raise ValueError(f"{field_name} exceeds its upper bound.")


def _require_bounded_duration(duration_seconds: float) -> None:
    if (
        type(duration_seconds) not in (int, float)
        or not math.isfinite(float(duration_seconds))
        or not 0 < float(duration_seconds) <= MAX_CAPTURE_SECONDS
    ):
        raise ValueError("duration_seconds must be finite, positive, and at most 180 seconds.")
