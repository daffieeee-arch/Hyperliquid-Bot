"""Deterministic synthetic tests for the PAPER HL/BN receipt-clock panel."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from hyperliquid_bot.panel_hl_binance import (
    DEFAULT_BUCKET_MS,
    NS_PER_MS,
    PANEL_MAX_GAP_FRACTION,
    PANEL_MIN_OVERLAP_BUCKETS,
    PANEL_PARQUET_NAME,
    PANEL_SUFFICIENCY_THRESHOLDS,
    PANEL_SUMMARY_NAME,
    PanelSufficiencyThresholds,
    PanelVerdictName,
    build_hl_binance_panel,
    main,
    require_bucket_ms,
    resolve_panel_inputs,
)
from hyperliquid_bot.parquet_research import ParquetResearchWriter, ParquetRotation
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
)
from hyperliquid_bot.reconstructable_paths import (
    DATA1A_PATH_CONTRACT_ID,
    DATA1F_PATH_CONTRACT_ID,
    data1a_run_paths,
    data1f_run_paths,
)

_HL_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "hyperliquid"
_BASE_UTC_NS = 1_788_105_600_000_000_000
_SECOND_NS = 1_000_000_000
_PRICE_COLUMNS = (
    "hl_last_trade_price",
    "hl_last_mid_price",
    "hl_last_bid_price",
    "hl_last_ask_price",
    "hl_bbo_mid_proxy",
    "bn_spot_last_price",
    "bn_spot_bid_price",
    "bn_spot_ask_price",
    "bn_spot_bbo_mid_proxy",
    "bn_usdm_last_agg_price",
    "bn_usdm_mark_price",
    "bn_usdm_index_price",
    "bn_usdm_funding_rate",
)
_FLOAT_TYPES = frozenset({"DOUBLE", "FLOAT", "REAL", "FLOAT4", "FLOAT8"})


def _hl_fixture(name: str) -> bytes:
    return (_HL_FIXTURE_DIR / name).read_bytes()


def _hl_record(
    ordinal: int,
    channel: str,
    payload: bytes,
    *,
    received_utc_ns: int,
    direction: MessageDirection = MessageDirection.INBOUND,
    frame_type: FrameType = FrameType.TEXT,
    payload_encoding: PayloadEncoding = PayloadEncoding.UTF8,
) -> RawResearchRecord:
    return RawResearchRecord(
        schema_version=RAW_RESEARCH_SCHEMA_VERSION,
        venue="hyperliquid",
        product="BTC-PERP",
        channel=channel,
        session_id="synthetic-panel-hl",
        message_ordinal=ordinal,
        received_utc_ns=received_utc_ns,
        received_monotonic_ns=9_000_000_000 + ordinal,
        direction=direction,
        frame_type=frame_type,
        payload_encoding=payload_encoding,
        payload_bytes=payload,
    )


def _bn_record(
    ordinal: int,
    *,
    product: str,
    channel: str,
    payload: bytes,
    received_utc_ns: int,
    direction: MessageDirection,
    frame_type: FrameType,
    payload_encoding: PayloadEncoding,
) -> RawResearchRecord:
    return RawResearchRecord(
        schema_version=RAW_RESEARCH_SCHEMA_VERSION,
        venue="binance",
        product=product,
        channel=channel,
        session_id="synthetic-panel-bn",
        message_ordinal=ordinal,
        received_utc_ns=received_utc_ns,
        received_monotonic_ns=8_000_000_000 + ordinal,
        direction=direction,
        frame_type=frame_type,
        payload_encoding=payload_encoding,
        payload_bytes=payload,
    )


def _bn_pair(
    raw_ordinal: int,
    marker_ordinal: int,
    *,
    product: str,
    source_channel: str,
    marker_channel: str,
    marker: dict[str, object],
    received_utc_ns: int,
) -> tuple[RawResearchRecord, RawResearchRecord]:
    inbound = _bn_record(
        raw_ordinal,
        product=product,
        channel=source_channel,
        payload=b'{"synthetic":true}',
        received_utc_ns=received_utc_ns,
        direction=MessageDirection.INBOUND,
        frame_type=FrameType.TEXT,
        payload_encoding=PayloadEncoding.UTF8,
    )
    document = {
        "raw_message_ordinal": raw_ordinal,
        "source_channel": source_channel,
        **marker,
    }
    local = _bn_record(
        marker_ordinal,
        product=product,
        channel=marker_channel,
        payload=json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        received_utc_ns=received_utc_ns,
        direction=MessageDirection.LOCAL,
        frame_type=FrameType.MARKER,
        payload_encoding=PayloadEncoding.UTF8_JSON,
    )
    return inbound, local


async def _publish(parquet_dir: Path, records: tuple[RawResearchRecord, ...]) -> None:
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=max(2, len(records)),
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    for record in records:
        await writer.append(record)
    await writer.aclose()


def _hl_bucket_records(
    stamps: tuple[int, ...],
    *,
    start_ordinal: int = 1,
) -> tuple[RawResearchRecord, ...]:
    records: list[RawResearchRecord] = []
    ordinal = start_ordinal
    for stamp in stamps:
        records.append(
            _hl_record(
                ordinal,
                "trades",
                _hl_fixture("trades_frame.json"),
                received_utc_ns=stamp,
            )
        )
        ordinal += 1
        records.append(
            _hl_record(ordinal, "bbo", _hl_fixture("bbo_frame.json"), received_utc_ns=stamp + 1)
        )
        ordinal += 1
        records.append(
            _hl_record(
                ordinal,
                "activeAssetCtx",
                _hl_fixture("active_asset_ctx_frame.json"),
                received_utc_ns=stamp + 2,
            )
        )
        ordinal += 1
    return tuple(records)


def _bn_bucket_records(
    stamps: tuple[int, ...],
    *,
    start_ordinal: int = 1,
) -> tuple[RawResearchRecord, ...]:
    records: list[RawResearchRecord] = []
    ordinal = start_ordinal
    for index, stamp in enumerate(stamps):
        price_suffix = str(index + 1)
        spot_trade, spot_trade_marker = _bn_pair(
            ordinal,
            ordinal + 1,
            product="BTCUSDT-SPOT",
            source_channel="spot_trade",
            marker_channel="normalized_spot_trade",
            marker={
                "symbol": "BTCUSDT",
                "trade_id": str(index + 1),
                "price": f"99999.12000000000000000{price_suffix}",
                "quantity": "0.001230000000000001",
            },
            received_utc_ns=stamp,
        )
        ordinal += 2
        records.extend((spot_trade, spot_trade_marker))
        spot_bbo, spot_bbo_marker = _bn_pair(
            ordinal,
            ordinal + 1,
            product="BTCUSDT-SPOT",
            source_channel="spot_book_ticker",
            marker_channel="normalized_spot_bbo",
            marker={
                "symbol": "BTCUSDT",
                "update_id": str(index + 1),
                "bid_price": "99999.110000000000000001",
                "bid_quantity": "1.2",
                "ask_price": "99999.130000000000000001",
                "ask_quantity": "0.8",
            },
            received_utc_ns=stamp + 1,
        )
        ordinal += 2
        records.extend((spot_bbo, spot_bbo_marker))
        usdm_agg, usdm_agg_marker = _bn_pair(
            ordinal,
            ordinal + 1,
            product="BTCUSDT-USDS-M-PERPETUAL",
            source_channel="usdm_agg_trade",
            marker_channel="normalized_usdm_context",
            marker={
                "context_type": "aggregate_trade",
                "symbol": "BTCUSDT",
                "price": f"100001.25000000000000000{price_suffix}",
                "quantity": "0.045",
            },
            received_utc_ns=stamp + 2,
        )
        ordinal += 2
        records.extend((usdm_agg, usdm_agg_marker))
        usdm_mark, usdm_mark_marker = _bn_pair(
            ordinal,
            ordinal + 1,
            product="BTCUSDT-USDS-M-PERPETUAL",
            source_channel="usdm_mark_price",
            marker_channel="normalized_usdm_context",
            marker={
                "context_type": "mark_price",
                "symbol": "BTCUSDT",
                "mark_price": "100000.880000000000000001",
                "index_price": "100000.700000000000000001",
                "funding_rate": "0.000100000000000001",
            },
            received_utc_ns=stamp + 3,
        )
        ordinal += 2
        records.extend((usdm_mark, usdm_mark_marker))
    return tuple(records)


def _dense_stamps(seconds: int = 5) -> tuple[int, ...]:
    return tuple(_BASE_UTC_NS + index * _SECOND_NS for index in range(seconds))


def _write_hl_claim(path: Path, *, retained: bool, run_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "path_contract": DATA1A_PATH_CONTRACT_ID,
                "product": "BTC-PERP",
                "retained": retained,
                "run_id": run_id,
                "schema": "data-1a-retained-capture-claim-v1",
                "venue": "hyperliquid",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_bn_claim(path: Path, *, retained: bool, run_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "path_contract": DATA1F_PATH_CONTRACT_ID,
                "product": "BTCUSDT",
                "retained": retained,
                "run_id": run_id,
                "schema": "data-1f-retained-capture-claim-v1",
                "venue": "binance",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _assert_no_edge(payload: object) -> None:
    rendered = json.dumps(payload, sort_keys=True)
    assert "edge" not in rendered.lower()


def _price_column_types(panel_path: Path) -> dict[str, str]:
    connection = duckdb.connect(":memory:")
    try:
        escaped = str(panel_path.resolve()).replace("'", "''")
        rows = connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}')").fetchall()
    finally:
        connection.close()
    return {str(name): str(sql_type).upper() for name, sql_type, *_ in rows}


@pytest.mark.asyncio
async def test_synthetic_overlap_writes_panel_ready_parquet(tmp_path: Path) -> None:
    stamps = _dense_stamps(5)
    hl_raw = tmp_path / "hl-raw"
    bn_raw = tmp_path / "bn-raw"
    output_dir = tmp_path / "out"
    await _publish(hl_raw, _hl_bucket_records(stamps))
    await _publish(bn_raw, _bn_bucket_records(stamps))

    result = build_hl_binance_panel(
        hl_parquet_dir=hl_raw,
        hl_database_path=tmp_path / "hl.duckdb",
        bn_parquet_dir=bn_raw,
        bn_database_path=tmp_path / "bn.duckdb",
        output_dir=output_dir,
    )

    assert result.trading_mode == "PAPER"
    assert result.verdict is PanelVerdictName.PANEL_READY
    assert result.sufficiency.enough_data is True
    assert result.sufficiency.reasons == ()
    assert result.input_mode == "ad_hoc"
    assert result.overlap_bucket_count == 5
    assert result.panel_row_count == 5
    assert result.overlap_ok_row_count == 5
    assert result.hl_gap_fraction == 0.0
    assert result.bn_gap_fraction == 0.0
    assert result.panel_parquet is not None
    panel_path = Path(result.panel_parquet)
    assert panel_path.name == PANEL_PARQUET_NAME
    assert panel_path.is_file()
    assert Path(result.summary_json or "").is_file()
    _assert_no_edge(result.to_json_dict())
    _assert_no_edge(json.loads(Path(result.summary_json or "").read_text(encoding="utf-8")))

    types = _price_column_types(panel_path)
    for column in _PRICE_COLUMNS:
        assert types[column] == "VARCHAR"
        assert types[column] not in _FLOAT_TYPES
    for sql_type in types.values():
        assert sql_type not in _FLOAT_TYPES

    connection = duckdb.connect(":memory:")
    try:
        escaped = str(panel_path.resolve()).replace("'", "''")
        row = connection.execute(
            f"SELECT * FROM read_parquet('{escaped}') ORDER BY bucket_utc_ns LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[0] == stamps[0] // (DEFAULT_BUCKET_MS * NS_PER_MS) * (DEFAULT_BUCKET_MS * NS_PER_MS)
    assert row[1] == "0.123456789012345678"
    assert row[2] == "104321.2350"
    assert row[3] == "104321.2300"
    assert row[4] == "104321.2400"
    assert row[5] == format((Decimal("104321.2300") + Decimal("104321.2400")) / Decimal(2), "f")
    assert row[12] == "99999.120000000000000001"
    assert row[19] == "100000.880000000000000001"
    assert row[21] == "0.000100000000000001"
    assert row[27] is True


@pytest.mark.asyncio
async def test_non_overlapping_spans_are_not_enough_data(tmp_path: Path) -> None:
    hl_raw = tmp_path / "hl-raw"
    bn_raw = tmp_path / "bn-raw"
    output_dir = tmp_path / "out"
    await _publish(hl_raw, _hl_bucket_records(_dense_stamps(3)))
    await _publish(
        bn_raw,
        _bn_bucket_records(tuple(stamp + 100 * _SECOND_NS for stamp in _dense_stamps(3))),
    )

    result = build_hl_binance_panel(
        hl_parquet_dir=hl_raw,
        hl_database_path=tmp_path / "hl.duckdb",
        bn_parquet_dir=bn_raw,
        bn_database_path=tmp_path / "bn.duckdb",
        output_dir=output_dir,
    )

    assert result.verdict is PanelVerdictName.NOT_ENOUGH_DATA
    assert result.panel_row_count == 0
    assert result.panel_parquet is None
    assert any("overlapping UTC" in reason for reason in result.sufficiency.reasons)
    assert not (output_dir / PANEL_PARQUET_NAME).exists()
    assert (output_dir / PANEL_SUMMARY_NAME).is_file()
    _assert_no_edge(result.to_json_dict())


@pytest.mark.asyncio
async def test_high_gap_fraction_is_not_enough_data(tmp_path: Path) -> None:
    hl_raw = tmp_path / "hl-raw"
    bn_raw = tmp_path / "bn-raw"
    output_dir = tmp_path / "out"
    stamps = (_BASE_UTC_NS, _BASE_UTC_NS + 10 * _SECOND_NS)
    await _publish(hl_raw, _hl_bucket_records(stamps))
    await _publish(bn_raw, _bn_bucket_records(stamps))

    result = build_hl_binance_panel(
        hl_parquet_dir=hl_raw,
        hl_database_path=tmp_path / "hl.duckdb",
        bn_parquet_dir=bn_raw,
        bn_database_path=tmp_path / "bn.duckdb",
        output_dir=output_dir,
    )

    assert result.verdict is PanelVerdictName.NOT_ENOUGH_DATA
    assert result.overlap_bucket_count == 11
    assert result.hl_incomplete_bucket_count == 9
    assert result.bn_incomplete_bucket_count == 9
    assert result.hl_gap_fraction == pytest.approx(9 / 11)
    assert result.bn_gap_fraction == pytest.approx(9 / 11)
    assert any("hl_gap_fraction" in reason for reason in result.sufficiency.reasons)
    assert any("bn_gap_fraction" in reason for reason in result.sufficiency.reasons)
    assert result.panel_parquet is None
    _assert_no_edge(result.to_json_dict())


@pytest.mark.asyncio
async def test_unretained_claim_is_not_enough_data(tmp_path: Path) -> None:
    hl_paths = data1a_run_paths(tmp_path, "hl-smoke")
    bn_paths = data1f_run_paths(tmp_path, "bn-smoke")
    output_dir = tmp_path / "out"
    hl_paths.raw_dir.mkdir(parents=True)
    bn_paths.raw_dir.mkdir(parents=True)
    stamps = _dense_stamps(5)
    await _publish(hl_paths.raw_dir, _hl_bucket_records(stamps))
    await _publish(bn_paths.raw_dir, _bn_bucket_records(stamps))
    _write_hl_claim(hl_paths.capture_claim_path, retained=False, run_id=hl_paths.run_id)
    _write_bn_claim(bn_paths.capture_claim_path, retained=True, run_id=bn_paths.run_id)

    result = build_hl_binance_panel(
        artifact_root=tmp_path,
        hl_run_id="hl-smoke",
        bn_run_id="bn-smoke",
        output_dir=output_dir,
    )

    assert result.verdict is PanelVerdictName.NOT_ENOUGH_DATA
    assert result.input_mode == "reconstructable"
    assert any("retained" in reason for reason in result.sufficiency.reasons)
    assert result.panel_parquet is None
    _assert_no_edge(result.to_json_dict())


@pytest.mark.asyncio
async def test_missing_binance_claim_is_not_enough_data(tmp_path: Path) -> None:
    hl_paths = data1a_run_paths(tmp_path, "hl-retained")
    bn_paths = data1f_run_paths(tmp_path, "bn-missing")
    output_dir = tmp_path / "out"
    hl_paths.raw_dir.mkdir(parents=True)
    bn_paths.raw_dir.mkdir(parents=True)
    stamps = _dense_stamps(5)
    await _publish(hl_paths.raw_dir, _hl_bucket_records(stamps))
    await _publish(bn_paths.raw_dir, _bn_bucket_records(stamps))
    _write_hl_claim(hl_paths.capture_claim_path, retained=True, run_id=hl_paths.run_id)

    result = build_hl_binance_panel(
        artifact_root=tmp_path,
        hl_run_id="hl-retained",
        bn_run_id="bn-missing",
        output_dir=output_dir,
    )

    assert result.verdict is PanelVerdictName.NOT_ENOUGH_DATA
    assert any("missing capture-claim.json" in reason for reason in result.sufficiency.reasons)
    _assert_no_edge(result.to_json_dict())


@pytest.mark.asyncio
async def test_reconstructable_retained_claims_write_panel_ready(tmp_path: Path) -> None:
    hl_paths = data1a_run_paths(tmp_path, "hl-retained")
    bn_paths = data1f_run_paths(tmp_path, "bn-retained")
    output_dir = tmp_path / "out"
    hl_paths.raw_dir.mkdir(parents=True)
    bn_paths.raw_dir.mkdir(parents=True)
    stamps = _dense_stamps(5)
    await _publish(hl_paths.raw_dir, _hl_bucket_records(stamps))
    await _publish(bn_paths.raw_dir, _bn_bucket_records(stamps))
    _write_hl_claim(hl_paths.capture_claim_path, retained=True, run_id=hl_paths.run_id)
    _write_bn_claim(bn_paths.capture_claim_path, retained=True, run_id=bn_paths.run_id)

    result = build_hl_binance_panel(
        artifact_root=tmp_path,
        hl_run_id="hl-retained",
        bn_run_id="bn-retained",
        output_dir=output_dir,
    )

    assert result.verdict is PanelVerdictName.PANEL_READY
    assert result.hl_path_contract == DATA1A_PATH_CONTRACT_ID
    assert result.bn_path_contract == DATA1F_PATH_CONTRACT_ID
    assert result.hl_run_id == "hl-retained"
    assert result.bn_run_id == "bn-retained"
    assert result.panel_parquet is not None
    _assert_no_edge(result.to_json_dict())


@pytest.mark.asyncio
async def test_cli_prints_panel_ready_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hl_raw = tmp_path / "hl-raw"
    bn_raw = tmp_path / "bn-raw"
    output_dir = tmp_path / "out"
    stamps = _dense_stamps(5)
    await _publish(hl_raw, _hl_bucket_records(stamps))
    await _publish(bn_raw, _bn_bucket_records(stamps))

    assert (
        main(
            [
                "--hl-parquet-dir",
                str(hl_raw),
                "--hl-database",
                str(tmp_path / "hl.duckdb"),
                "--bn-parquet-dir",
                str(bn_raw),
                "--bn-database",
                str(tmp_path / "bn.duckdb"),
                "--bucket-ms",
                "1000",
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["trading_mode"] == "PAPER"
    assert payload["verdict"] == "panel_ready"
    assert payload["bucket_ms"] == 1000
    _assert_no_edge(payload)


def test_thresholds_and_bucket_bounds_are_explicit() -> None:
    assert DEFAULT_BUCKET_MS == 1000
    assert PANEL_MAX_GAP_FRACTION == 0.05
    assert PANEL_MIN_OVERLAP_BUCKETS == 1
    assert PANEL_SUFFICIENCY_THRESHOLDS.max_gap_fraction == 0.05
    assert require_bucket_ms(1000) == 1000
    with pytest.raises(ValueError, match="bucket_ms"):
        require_bucket_ms(0)
    with pytest.raises(ValueError, match="max_gap_fraction"):
        PanelSufficiencyThresholds(max_gap_fraction=1.5, min_overlap_buckets=1)


def test_resolve_panel_inputs_rejects_mixed_modes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        resolve_panel_inputs(
            artifact_root=tmp_path,
            hl_run_id="hl",
            bn_run_id="bn",
            hl_parquet_dir=tmp_path / "raw",
            hl_database_path=tmp_path / "hl.duckdb",
            bn_parquet_dir=tmp_path / "bn-raw",
            bn_database_path=tmp_path / "bn.duckdb",
        )
