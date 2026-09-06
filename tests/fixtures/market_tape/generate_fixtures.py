"""Generate the cockpit market-tape Parquet fixtures.

The cockpit reads published ``part-*.parquet`` files exactly as the capture
writers publish them (DuckDB, ZSTD, raw_segment schema). These fixtures are
small deterministic runs per venue so the incremental reader can be tested
without touching a live retain. Regenerate with::

    PYTHONPATH=src python3 tests/fixtures/market_tape/generate_fixtures.py

Payload shapes mirror the research views in ``parquet_research.py``:
Hyperliquid inbound ``trades`` / ``bbo`` frames, and the ``normalized_*``
local markers the Binance, Bitvavo and Kraken collectors write.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb

from hyperliquid_bot.parquet_research import _CREATE_SEGMENT_TABLE, _INSERT_SEGMENT_ROW, _storage_row
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
)

ROOT = Path(__file__).resolve().parent / "artifact-root"
BASE_NS = 1_788_631_200_000_000_000  # 2026-09-05T18:00:00Z
STEP_NS = 1_000_000_000
WRITER_TAG = "fixture00001"


def _record(
    venue: str,
    product: str,
    channel: str,
    ordinal: int,
    payload: object,
    *,
    direction: MessageDirection = MessageDirection.INBOUND,
    frame_type: FrameType = FrameType.TEXT,
) -> RawResearchRecord:
    return RawResearchRecord(
        schema_version=RAW_RESEARCH_SCHEMA_VERSION,
        venue=venue,
        product=product,
        channel=channel,
        session_id="fixture-session",
        message_ordinal=ordinal,
        received_utc_ns=BASE_NS + ordinal * STEP_NS,
        received_monotonic_ns=ordinal * STEP_NS,
        direction=direction,
        frame_type=frame_type,
        payload_encoding=PayloadEncoding.UTF8_JSON,
        payload_bytes=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )


def _marker(venue: str, product: str, channel: str, ordinal: int, payload: object) -> RawResearchRecord:
    return _record(
        venue,
        product,
        channel,
        ordinal,
        payload,
        direction=MessageDirection.LOCAL,
        frame_type=FrameType.MARKER,
    )


def _write_part(raw_dir: Path, part_number: int, records: list[RawResearchRecord]) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    first = records[0].message_ordinal
    last = records[-1].message_ordinal
    stem = f"part-{part_number:06d}-{first:012d}-{last:012d}-{WRITER_TAG}"
    path = raw_dir / f"{stem}.parquet"
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(_CREATE_SEGMENT_TABLE)
        connection.executemany(_INSERT_SEGMENT_ROW, [_storage_row(record) for record in records])
        connection.execute(
            f"COPY raw_segment TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.close()


def _claim(run_dir: Path, schema: str, path_contract: str, run_id: str, venue: str, product: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "capture-claim.json").write_text(
        json.dumps(
            {
                "schema": schema,
                "path_contract": path_contract,
                "run_id": run_id,
                "state": "STARTED_FAIL_CLOSED",
                "retained": True,
                "twenty_four_seven": False,
                "credentialless": True,
                "signing": False,
                "venue": venue,
                "product": product,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def hyperliquid() -> None:
    run_id = "20260905t180000z-live-retained"
    run_dir = ROOT / "data-1a" / "hyperliquid" / "BTC-PERP" / run_id
    _claim(run_dir, "data-1a-retained-capture-claim-v1", "data-1a-hyperliquid-btc-perp-v1", run_id, "hyperliquid", "BTC-PERP")
    venue, product = "hyperliquid", "BTC-PERP"
    part1 = [
        _record(venue, product, "trades", 1, {"channel": "trades", "data": [
            {"coin": "BTC", "side": "B", "px": "109500.0", "sz": "0.0120", "time": 1788631801000, "tid": 1},
            {"coin": "BTC", "side": "A", "px": "109499.0", "sz": "0.0300", "time": 1788631801500, "tid": 2},
        ]}),
        _record(venue, product, "bbo", 2, {"channel": "bbo", "data": {"coin": "BTC", "time": 1788631802000, "bbo": [
            {"px": "109498.0", "sz": "1.2", "n": 3}, {"px": "109501.0", "sz": "0.8", "n": 2}]}}),
        _record(venue, product, "activeAssetCtx", 3, {"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": {
            "funding": "0.0000125", "openInterest": "12345.5", "oraclePx": "109500.5", "markPx": "109499.5", "midPx": "109499.5"}}}),
    ]
    part2 = [
        _record(venue, product, "trades", 4, {"channel": "trades", "data": [
            {"coin": "BTC", "side": "B", "px": "109510.0", "sz": "0.0050", "time": 1788631804000, "tid": 3},
        ]}),
        _record(venue, product, "bbo", 5, {"channel": "bbo", "data": {"coin": "BTC", "time": 1788631805000, "bbo": [
            {"px": "109509.0", "sz": "0.9", "n": 1}, {"px": "109511.0", "sz": "1.1", "n": 4}]}}),
    ]
    _write_part(run_dir / "raw", 1, part1)
    _write_part(run_dir / "raw", 2, part2)


def binance() -> None:
    run_id = "20260905t180100z-live-retained"
    run_dir = ROOT / "data-1f" / "binance" / "BTCUSDT" / run_id
    _claim(run_dir, "data-1f-retained-capture-claim-v1", "data-1f-binance-btcusdt-v1", run_id, "binance", "BTCUSDT")
    spot, usdm = "BTCUSDT-SPOT", "BTCUSDT-USDS-M-PERPETUAL"
    part1 = [
        _record("binance", spot, "btcusdt@trade", 1, {"e": "trade", "p": "109480.10", "q": "0.002"}),
        _marker("binance", spot, "normalized_spot_trade", 2, {
            "raw_message_ordinal": 1, "source_channel": "btcusdt@trade", "symbol": "BTCUSDT", "trade_id": "901",
            "price": "109480.10", "quantity": "0.00200", "exchange_event_time": "1788631801000", "trade_time": "1788631801000",
            "timestamp_unit": "ms", "buyer_was_maker": False, "aggressor_side": "buy"}),
        _record("binance", spot, "btcusdt@bookTicker", 3, {"u": 5, "s": "BTCUSDT", "b": "109479.90", "B": "2.1", "a": "109480.20", "A": "1.4"}),
        _marker("binance", spot, "normalized_spot_bbo", 4, {
            "raw_message_ordinal": 3, "source_channel": "btcusdt@bookTicker", "symbol": "BTCUSDT", "update_id": "5",
            "bid_price": "109479.90", "bid_quantity": "2.10000", "ask_price": "109480.20", "ask_quantity": "1.40000"}),
        _record("binance", usdm, "btcusdt@markPrice@1s", 5, {"e": "markPriceUpdate", "p": "109520.00", "i": "109500.00", "r": "0.0001"}),
        _marker("binance", usdm, "normalized_usdm_context", 6, {
            "raw_message_ordinal": 5, "source_channel": "usdm_mark_price", "context_type": "mark_price", "symbol": "BTCUSDT",
            "mark_price": "109520.00", "index_price": "109500.00", "funding_rate": "0.00010000", "event_time": "1788631806000"}),
    ]
    _write_part(run_dir / "raw", 1, part1)


def bitvavo() -> None:
    run_id = "20260905t180200z-live-retained"
    run_dir = ROOT / "data-1e" / "bitvavo" / "BTC-EUR" / run_id
    _claim(run_dir, "data-1e-retained-capture-claim-v1", "data-1e-bitvavo-btc-eur-v1", run_id, "bitvavo", "BTC-EUR")
    product = "BTC-EUR"
    part1 = [
        _record("bitvavo", product, "trades", 1, {"event": "trade", "market": "BTC-EUR", "price": "93810.5", "amount": "0.01"}),
        _marker("bitvavo", product, "normalized_trades", 2, {"raw_message_ordinal": 1, "market": "BTC-EUR", "events": [
            {"event_index": 0, "market": "BTC-EUR", "trade_id": "t-1", "price": "93810.5", "quantity": "0.01000000",
             "taker_side": "sell", "event_time_ms": "1788631801000", "event_time_ns": "1788631801000000000"}]}),
        _record("bitvavo", product, "ticker", 3, {"event": "ticker", "market": "BTC-EUR", "bestBid": "93800.1", "bestAsk": "93812.4"}),
        _marker("bitvavo", product, "normalized_ticker", 4, {
            "raw_message_ordinal": 3, "market": "BTC-EUR", "bid_price": "93800.1", "bid_quantity": "0.5",
            "ask_price": "93812.4", "ask_quantity": "0.25", "last_price": None}),
        _record("bitvavo", product, "ticker", 5, {"event": "ticker", "market": "BTC-EUR", "bestAsk": "93811.0"}),
        _marker("bitvavo", product, "normalized_ticker", 6, {
            "raw_message_ordinal": 5, "market": "BTC-EUR", "bid_price": None, "bid_quantity": None,
            "ask_price": "93811.0", "ask_quantity": "0.30", "last_price": None}),
    ]
    _write_part(run_dir / "raw", 1, part1)


def kraken() -> None:
    run_id = "20260905t180300z-live-retained"
    run_dir = ROOT / "data-1b" / "kraken" / "BTC-USD" / run_id
    _claim(run_dir, "data-1b-retained-capture-claim-v1", "data-1b-kraken-btc-usd-v1", run_id, "kraken", "BTC/USD")
    product = "BTC/USD"
    part1 = [
        _record("kraken", product, "book", 1, {"channel": "book", "type": "snapshot"}),
        _marker("kraken", product, "normalized_book", 2, {
            "event": "normalized_l2_frame", "raw_message_ordinal": 1, "message_type": "snapshot", "symbol": "BTC/USD",
            "message_timestamp": "2026-09-05T18:00:01.000000Z", "checksum": "1", "events": [
                {"data_index": 0, "wire_order": 0, "side": "bid", "side_index": 0, "action": "snapshot", "price": "109450.0", "qty": "0.5"},
                {"data_index": 0, "wire_order": 1, "side": "bid", "side_index": 0, "action": "snapshot", "price": "109449.0", "qty": "1.0"},
                {"data_index": 0, "wire_order": 2, "side": "ask", "side_index": 1, "action": "snapshot", "price": "109452.0", "qty": "0.7"},
                {"data_index": 0, "wire_order": 3, "side": "ask", "side_index": 1, "action": "snapshot", "price": "109453.0", "qty": "0.9"}]}),
        _record("kraken", product, "trade", 3, {"channel": "trade", "type": "update"}),
        _marker("kraken", product, "normalized_trade", 4, {"raw_message_ordinal": 3, "message_type": "update", "events": [
            {"event_index": 0, "symbol": "BTC/USD", "side": "buy", "price": "109452.0", "qty": "0.02", "order_type": "market",
             "trade_id": "77", "timestamp": "2026-09-05T18:00:03.000000Z"}]}),
        _record("kraken", product, "book", 5, {"channel": "book", "type": "update"}),
        _marker("kraken", product, "normalized_book", 6, {
            "event": "normalized_l2_frame", "raw_message_ordinal": 5, "message_type": "update", "symbol": "BTC/USD",
            "message_timestamp": "2026-09-05T18:00:05.000000Z", "checksum": "2", "events": [
                {"data_index": 0, "wire_order": 0, "side": "ask", "side_index": 1, "action": "update", "price": "109452.0", "qty": "0"},
                {"data_index": 0, "wire_order": 1, "side": "bid", "side_index": 0, "action": "update", "price": "109451.0", "qty": "0.3"}]}),
    ]
    _write_part(run_dir / "raw", 1, part1)


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    hyperliquid()
    binance()
    bitvavo()
    kraken()
    for path in sorted(ROOT.rglob("*")):
        if path.is_file():
            print(path.relative_to(ROOT), path.stat().st_size)


if __name__ == "__main__":
    main()
