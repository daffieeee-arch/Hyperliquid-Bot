"""Replay a Hyperliquid BTC-PERP Parquet tape as normalized market events.

The SQL paths match the DATA-1A research views in
``hyperliquid_bot.parquet_research`` (trades, bbo, activeAssetCtx mark).
Partial files are ignored. Rows are ordered by receipt time, then message
ordinal, then event index. The same ``PaperEngine.on_event`` path consumes
this tape and a live public feed. A trade print re-sent after a reconnect
(same time, coin and trade id) is kept once, as the live collector does.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

import duckdb

from hyperliquid_bot.paper_engine.errors import PaperTapeError
from hyperliquid_bot.paper_engine.events import (
    BboEvent,
    MarketEvent,
    MarkEvent,
    TradeEvent,
    event_sort_key,
    utc_from_epoch_ms,
)

# The live trades collector's dedup window (HyperliquidTradesCollectorConfig
# .dedup_capacity). The replay keeps the same window so both paths drop the
# same re-sent prints.
TRADE_DEDUP_CAPACITY: Final = 10_000

_HYPERLIQUID_BUY: str = "B"
_HYPERLIQUID_SELL: str = "A"


def load_hyperliquid_parquet_tape(
    parquet_paths: Sequence[Path],
    *,
    instrument_id: str = "BTC-PERP",
    venue: str = "hyperliquid",
    product: str = "BTC-PERP",
) -> tuple[MarketEvent, ...]:
    """Read completed raw Parquet parts into a deterministic event tuple."""

    files = _parquet_files(parquet_paths)
    if type(instrument_id) is not str or not instrument_id:
        raise PaperTapeError("instrument_id must be non-empty text.")
    if venue != "hyperliquid" or product != "BTC-PERP":
        raise PaperTapeError("this replay reader is the Hyperliquid BTC-PERP raw tape.")
    relation = _read_parquet_sql(files)
    connection = duckdb.connect(":memory:")
    try:
        trades = _load_trades(
            connection,
            relation=relation,
            instrument_id=instrument_id,
            venue=venue,
            product=product,
        )
        bbos = _load_bbos(
            connection,
            relation=relation,
            instrument_id=instrument_id,
            venue=venue,
            product=product,
        )
        marks = _load_marks(
            connection,
            relation=relation,
            instrument_id=instrument_id,
            venue=venue,
            product=product,
        )
    finally:
        connection.close()
    events: list[MarketEvent] = [*trades, *bbos, *marks]
    if not events:
        raise PaperTapeError("parquet tape produced no trades, bbo, or mark events.")
    return tuple(sorted(events, key=event_sort_key))


def _parquet_files(parquet_paths: Sequence[Path]) -> tuple[Path, ...]:
    if not parquet_paths:
        raise PaperTapeError("at least one parquet path is required.")
    files: list[Path] = []
    for path in parquet_paths:
        if not isinstance(path, Path):
            raise PaperTapeError("parquet paths must be pathlib.Path values.")
        if path.is_dir():
            files.extend(
                sorted(
                    item
                    for item in path.glob("*.parquet")
                    if item.is_file() and not item.name.startswith(".")
                )
            )
            continue
        if path.is_file() and path.suffix == ".parquet" and not path.name.startswith("."):
            files.append(path.resolve())
            continue
        raise PaperTapeError(f"not a completed parquet part: {path}")
    if not files:
        raise PaperTapeError("no completed parquet parts were found.")
    return tuple(files)


def _read_parquet_sql(files: tuple[Path, ...]) -> str:
    rendered = ", ".join(f"'{_sql_string(path.resolve().as_posix())}'" for path in files)
    return f"read_parquet([{rendered}], union_by_name = true)"


def _sql_string(value: str) -> str:
    if "'" in value or "\x00" in value:
        raise PaperTapeError("parquet path contains a character that cannot be embedded in SQL.")
    return value


def _load_trades(
    connection: duckdb.DuckDBPyConnection,
    *,
    relation: str,
    instrument_id: str,
    venue: str,
    product: str,
) -> list[TradeEvent]:
    rows = connection.execute(
        f"""
        SELECT
            raw.message_ordinal,
            raw.received_utc_ns,
            CAST(trade.key AS BIGINT) AS event_index,
            json_extract_string(trade.value, '$.side') AS side,
            json_extract_string(trade.value, '$.px') AS price,
            json_extract_string(trade.value, '$.sz') AS size,
            json_extract_string(trade.value, '$.time') AS event_time_ms,
            json_extract_string(trade.value, '$.tid') AS trade_id,
            json_extract_string(trade.value, '$.coin') AS coin,
            json_extract_string(trade.value, '$.hash') AS trade_hash,
            CAST(json_extract(trade.value, '$.users') AS VARCHAR) AS users
        FROM {relation} AS raw,
             LATERAL json_each(decode(raw.payload_bytes), '$.data') AS trade
        WHERE raw.venue = 'hyperliquid'
          AND raw.product = ?
          AND raw.channel = 'trades'
          AND raw.direction = 'inbound'
        ORDER BY raw.received_utc_ns, raw.message_ordinal, event_index
        """,
        [product],
    ).fetchall()
    events: list[TradeEvent] = []
    # A reconnect re-sends recent prints. Like the live collector, keep one
    # copy per source identity (time, coin, tid) within the same LRU window.
    # The same identity with a different print is a corrupt tape.
    seen: OrderedDict[tuple[int, object, str], tuple[object, ...]] = OrderedDict()
    for row in rows:
        message_ordinal = _require_int(row[0], field_name="message_ordinal")
        received_utc_ns = _require_int(row[1], field_name="received_utc_ns")
        event_index = _require_int(row[2], field_name="event_index")
        side = _aggressor_side(_require_text(row[3], field_name="side"))
        price = _require_decimal(_require_text(row[4], field_name="price"), field_name="price")
        quantity = _require_decimal(_require_text(row[5], field_name="size"), field_name="size")
        event_time_ms = _require_int(row[6], field_name="event_time_ms")
        trade_id = _require_text(row[7], field_name="trade_id")
        identity = (event_time_ms, row[8], trade_id)
        fingerprint = (side, price, quantity, row[9], row[10])
        known = seen.get(identity)
        if known is not None:
            if known != fingerprint:
                raise PaperTapeError(f"trade id {trade_id} appears with different prints.")
            seen.move_to_end(identity)
            continue
        seen[identity] = fingerprint
        while len(seen) > TRADE_DEDUP_CAPACITY:
            seen.popitem(last=False)
        event_time = utc_from_epoch_ms(event_time_ms)
        events.append(
            TradeEvent(
                venue=venue,
                instrument_id=instrument_id,
                event_time_utc=event_time,
                received_utc_ns=received_utc_ns,
                source_event_id=f"trade-{trade_id}",
                price=price,
                quantity=quantity,
                aggressor_side=side,
                message_ordinal=message_ordinal,
                event_index=event_index,
            )
        )
    return events


def _load_bbos(
    connection: duckdb.DuckDBPyConnection,
    *,
    relation: str,
    instrument_id: str,
    venue: str,
    product: str,
) -> list[BboEvent]:
    rows = connection.execute(
        f"""
        SELECT
            message_ordinal,
            received_utc_ns,
            json_extract_string(decode(payload_bytes), '$.data.time') AS event_time_ms,
            json_extract_string(decode(payload_bytes), '$.data.bbo[0].px') AS bid_price,
            json_extract_string(decode(payload_bytes), '$.data.bbo[0].sz') AS bid_size,
            json_extract_string(decode(payload_bytes), '$.data.bbo[1].px') AS ask_price,
            json_extract_string(decode(payload_bytes), '$.data.bbo[1].sz') AS ask_size
        FROM {relation}
        WHERE venue = 'hyperliquid'
          AND product = ?
          AND channel = 'bbo'
          AND direction = 'inbound'
        ORDER BY received_utc_ns, message_ordinal
        """,
        [product],
    ).fetchall()
    events: list[BboEvent] = []
    for row in rows:
        message_ordinal = _require_int(row[0], field_name="message_ordinal")
        received_utc_ns = _require_int(row[1], field_name="received_utc_ns")
        event_time = utc_from_epoch_ms(_require_int(row[2], field_name="event_time_ms"))
        events.append(
            BboEvent(
                venue=venue,
                instrument_id=instrument_id,
                event_time_utc=event_time,
                received_utc_ns=received_utc_ns,
                source_event_id=f"bbo-{message_ordinal}",
                bid_price=_optional_decimal(row[3], field_name="bid_price"),
                bid_size=_optional_decimal(row[4], field_name="bid_size"),
                ask_price=_optional_decimal(row[5], field_name="ask_price"),
                ask_size=_optional_decimal(row[6], field_name="ask_size"),
                message_ordinal=message_ordinal,
                event_index=0,
            )
        )
    return events


def _load_marks(
    connection: duckdb.DuckDBPyConnection,
    *,
    relation: str,
    instrument_id: str,
    venue: str,
    product: str,
) -> list[MarkEvent]:
    rows = connection.execute(
        f"""
        SELECT
            message_ordinal,
            received_utc_ns,
            json_extract_string(decode(payload_bytes), '$.data.ctx.markPx') AS mark_price
        FROM {relation}
        WHERE venue = 'hyperliquid'
          AND product = ?
          AND channel = 'activeAssetCtx'
          AND direction = 'inbound'
        ORDER BY received_utc_ns, message_ordinal
        """,
        [product],
    ).fetchall()
    events: list[MarkEvent] = []
    for row in rows:
        message_ordinal = _require_int(row[0], field_name="message_ordinal")
        received_utc_ns = _require_int(row[1], field_name="received_utc_ns")
        mark_text = row[2]
        if mark_text is None or mark_text == "":
            continue
        events.append(
            MarkEvent(
                venue=venue,
                instrument_id=instrument_id,
                event_time_utc=_utc_from_ns(received_utc_ns),
                received_utc_ns=received_utc_ns,
                source_event_id=f"mark-{message_ordinal}",
                mark_price=_require_decimal(
                    _require_text(mark_text, field_name="mark_price"),
                    field_name="mark_price",
                ),
                message_ordinal=message_ordinal,
                event_index=0,
            )
        )
    return events


def _utc_from_ns(epoch_ns: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=epoch_ns // 1000)


def _require_int(value: object, *, field_name: str) -> int:
    if type(value) is bool or not isinstance(value, int):
        if type(value) is str and value.isascii() and value.lstrip("-").isdigit():
            return int(value)
        raise PaperTapeError(f"{field_name} is not an integer.")
    return value


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value:
        raise PaperTapeError(f"{field_name} is missing.")
    return value


def _optional_decimal(value: object, *, field_name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    return _require_decimal(_require_text(value, field_name=field_name), field_name=field_name)


def _require_decimal(text: str, *, field_name: str) -> Decimal:
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise PaperTapeError(f"{field_name} is not a decimal.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise PaperTapeError(f"{field_name} must be a finite non-negative decimal.")
    if parsed == 0 and field_name in {"price", "size", "mark_price", "bid_price", "ask_price"}:
        raise PaperTapeError(f"{field_name} must be positive.")
    return parsed


def _aggressor_side(side: str) -> str:
    if side == _HYPERLIQUID_BUY:
        return "BUY"
    if side == _HYPERLIQUID_SELL:
        return "SELL"
    raise PaperTapeError("trade side must be Hyperliquid B or A.")
