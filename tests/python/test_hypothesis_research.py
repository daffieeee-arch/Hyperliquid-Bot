"""Deterministic tests for the PAPER retained-series hypothesis entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from hyperliquid_bot.hypothesis_research import (
    CANDIDATE_MAX_GAP_FRACTION,
    CANDIDATE_MIN_BBO_COUNT,
    CANDIDATE_MIN_MID_COUNT,
    CANDIDATE_MIN_SPAN_HOURS,
    CANDIDATE_MIN_TRADE_COUNT,
    CANDIDATE_SUFFICIENCY_THRESHOLDS,
    NS_PER_HOUR,
    BaselineSlot,
    BaselineSlotResult,
    HypothesisVerdictName,
    SufficiencyThresholds,
    evaluate_retained_series,
    main,
    resolve_series_input,
)
from hyperliquid_bot.parquet_research import ParquetResearchWriter, ParquetRotation
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
)
from hyperliquid_bot.reconstructable_paths import DATA1A_PATH_CONTRACT_ID, data1a_run_paths

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "hyperliquid"

# Synthetic-only gate for the scaffold happy path. Not a candidate threshold and
# not market evidence. The committed fixtures cannot meet even these values
# without spreading clocks across hours in a generated Parquet set.
_SYNTHETIC_TEST_THRESHOLDS = SufficiencyThresholds(
    min_span_hours=2.0,
    min_trade_count=2,
    min_bbo_count=2,
    min_mid_count=2,
    max_gap_fraction=0.50,
)


def _fixture(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _record(
    ordinal: int,
    channel: str,
    payload: bytes,
    *,
    received_utc_ns: int,
    direction: MessageDirection = MessageDirection.INBOUND,
    frame_type: FrameType = FrameType.TEXT,
    payload_encoding: PayloadEncoding = PayloadEncoding.UTF8,
    schema_version: int = RAW_RESEARCH_SCHEMA_VERSION,
) -> RawResearchRecord:
    return RawResearchRecord(
        schema_version=schema_version,
        venue="hyperliquid",
        product="BTC-PERP",
        channel=channel,
        session_id="synthetic-hypothesis-session",
        message_ordinal=ordinal,
        received_utc_ns=received_utc_ns,
        received_monotonic_ns=9_000_000_000 + ordinal,
        direction=direction,
        frame_type=frame_type,
        payload_encoding=payload_encoding,
        payload_bytes=payload,
    )


def _marker(
    ordinal: int, channel: str, payload: bytes, *, received_utc_ns: int
) -> RawResearchRecord:
    return _record(
        ordinal,
        channel,
        payload,
        received_utc_ns=received_utc_ns,
        direction=MessageDirection.LOCAL,
        frame_type=FrameType.MARKER,
        payload_encoding=PayloadEncoding.UTF8_JSON,
    )


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


def _fixture_smoke_records(
    base_utc_ns: int = 1_788_105_600_000_000_000,
) -> tuple[RawResearchRecord, ...]:
    return (
        _record(1, "trades", _fixture("trades_frame.json"), received_utc_ns=base_utc_ns + 1),
        _record(2, "bbo", _fixture("bbo_frame.json"), received_utc_ns=base_utc_ns + 2),
        _record(
            3,
            "activeAssetCtx",
            _fixture("active_asset_ctx_frame.json"),
            received_utc_ns=base_utc_ns + 3,
        ),
        _marker(4, "session", b'{"event":"connected"}', received_utc_ns=base_utc_ns + 4),
        _marker(
            5,
            "data_quality",
            b'{"event":"gap_detected","reason":"synthetic smoke disconnect"}',
            received_utc_ns=base_utc_ns + 5,
        ),
    )


def _synthetic_multi_hour_records() -> tuple[RawResearchRecord, ...]:
    """Three UTC hours of reused wire fixtures. Synthetic clocks, not market data."""

    base = 1_788_105_600_000_000_000
    records: list[RawResearchRecord] = [
        _marker(1, "session", b'{"event":"connected"}', received_utc_ns=base),
    ]
    ordinal = 2
    for hour in range(3):
        stamp = base + hour * NS_PER_HOUR
        records.append(
            _record(ordinal, "trades", _fixture("trades_frame.json"), received_utc_ns=stamp)
        )
        ordinal += 1
        records.append(_record(ordinal, "bbo", _fixture("bbo_frame.json"), received_utc_ns=stamp))
        ordinal += 1
        records.append(
            _record(
                ordinal,
                "activeAssetCtx",
                _fixture("active_asset_ctx_frame.json"),
                received_utc_ns=stamp,
            )
        )
        ordinal += 1
    return tuple(records)


def _assert_no_edge_claim(payload: object) -> None:
    rendered = json.dumps(payload, sort_keys=True)
    assert '"edge"' not in rendered
    assert "profitable" not in rendered.lower()
    assert "winner" not in rendered.lower()


def test_candidate_thresholds_are_explicit_multi_day_constants() -> None:
    assert CANDIDATE_MIN_SPAN_HOURS == 72.0
    assert CANDIDATE_MIN_TRADE_COUNT == 10_000
    assert CANDIDATE_MIN_BBO_COUNT == 5_000
    assert CANDIDATE_MIN_MID_COUNT == 500
    assert CANDIDATE_MAX_GAP_FRACTION == 0.05
    assert CANDIDATE_SUFFICIENCY_THRESHOLDS.min_span_hours == 72.0
    assert CANDIDATE_MIN_SPAN_HOURS >= 48.0


def test_invalid_thresholds_fail_closed() -> None:
    with pytest.raises(ValueError, match="min_span_hours"):
        SufficiencyThresholds(
            min_span_hours=0,
            min_trade_count=1,
            min_bbo_count=1,
            min_mid_count=1,
            max_gap_fraction=0.1,
        )
    with pytest.raises(ValueError, match="max_gap_fraction"):
        SufficiencyThresholds(
            min_span_hours=1.0,
            min_trade_count=1,
            min_bbo_count=1,
            min_mid_count=1,
            max_gap_fraction=1.5,
        )


def test_baseline_result_rejects_reserved_edge_verdict() -> None:
    with pytest.raises(ValueError, match="must not assign verdict 'edge'"):
        BaselineSlotResult(
            slot=BaselineSlot.MOMENTUM,
            ran=True,
            verdict=HypothesisVerdictName.EDGE,
            reason="forbidden",
            diagnostics=(),
        )


@pytest.mark.asyncio
async def test_committed_fixtures_fail_closed_as_not_enough_data(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    await _publish(parquet_dir, _fixture_smoke_records())

    result = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
    )

    assert result.trading_mode == "PAPER"
    assert result.verdict is HypothesisVerdictName.NOT_ENOUGH_DATA
    assert result.sufficiency.enough_data is False
    assert result.baseline.ran is False
    assert result.sanity.trade_count == 2
    assert result.sanity.bbo_count == 1
    assert result.sanity.mid_count == 1
    assert result.sanity.gap_detected_count == 1
    assert result.sanity.span_duration_hours < 1.0
    assert result.sanity.schema_versions == (RAW_RESEARCH_SCHEMA_VERSION,)
    assert any("span_duration_hours" in reason for reason in result.sufficiency.reasons)
    assert any("trade_count" in reason for reason in result.sufficiency.reasons)
    assert "skipped" in result.baseline.reason
    _assert_no_edge_claim(result.to_json_dict())


@pytest.mark.asyncio
async def test_synthetic_series_runs_momentum_scaffold_as_noise(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    await _publish(parquet_dir, _synthetic_multi_hour_records())

    result = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
    )

    assert result.verdict is HypothesisVerdictName.NOISE
    assert result.sufficiency.enough_data is True
    assert result.sufficiency.reasons == ()
    assert result.baseline.ran is True
    assert result.baseline.slot is BaselineSlot.MOMENTUM
    assert result.baseline.verdict is HypothesisVerdictName.NOISE
    assert result.sanity.span_duration_hours == 2.0
    assert result.sanity.trade_count == 6
    assert result.sanity.bbo_count == 3
    assert result.sanity.mid_count == 3
    assert result.sanity.incomplete_hour_count == 0
    diagnostics = dict(result.baseline.diagnostics)
    assert diagnostics["lookback_hours"] == 1
    assert diagnostics["mid_return_pairs"] == 2
    assert diagnostics["trade_return_pairs"] == 4
    assert "scaffold" in result.baseline.reason
    _assert_no_edge_claim(result.to_json_dict())

    candidate = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
    )
    assert candidate.verdict is HypothesisVerdictName.NOT_ENOUGH_DATA
    assert candidate.baseline.ran is False
    assert candidate.sufficiency.enough_data is False


@pytest.mark.asyncio
async def test_basis_slot_fails_closed_without_binance_series(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    await _publish(parquet_dir, _synthetic_multi_hour_records())

    result = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
        baseline=BaselineSlot.BASIS,
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
    )

    assert result.verdict is HypothesisVerdictName.NOT_ENOUGH_DATA
    assert result.baseline.ran is False
    assert result.baseline.slot is BaselineSlot.BASIS
    assert any("Binance" in reason for reason in result.sufficiency.reasons)
    _assert_no_edge_claim(result.to_json_dict())


@pytest.mark.asyncio
async def test_basis_slot_with_binance_dir_still_does_not_fit(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    binance_dir = tmp_path / "binance"
    database_path = tmp_path / "research.duckdb"
    await _publish(parquet_dir, _synthetic_multi_hour_records())
    binance_dir.mkdir()
    (binance_dir / "part-000001-placeholder.parquet").write_bytes(b"PAR1not-a-real-series")

    result = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
        baseline=BaselineSlot.BASIS,
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
        binance_parquet_dir=binance_dir,
    )

    assert result.verdict is HypothesisVerdictName.NOISE
    assert result.sufficiency.enough_data is True
    assert result.baseline.ran is False
    assert result.baseline.slot is BaselineSlot.BASIS
    assert dict(result.baseline.diagnostics)["binance_parquet_files"] == 1
    assert "no basis fit is implemented" in result.baseline.reason
    _assert_no_edge_claim(result.to_json_dict())


@pytest.mark.asyncio
async def test_gap_fraction_fails_closed_on_sparse_hours(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    base = 1_788_105_600_000_000_000
    records = (
        _record(1, "trades", _fixture("trades_frame.json"), received_utc_ns=base),
        _record(2, "bbo", _fixture("bbo_frame.json"), received_utc_ns=base + 1),
        _record(
            3,
            "activeAssetCtx",
            _fixture("active_asset_ctx_frame.json"),
            received_utc_ns=base + 2,
        ),
        _marker(
            4, "session", b'{"event":"session_stopped"}', received_utc_ns=base + 3 * NS_PER_HOUR
        ),
    )
    await _publish(parquet_dir, records)

    result = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
    )

    assert result.verdict is HypothesisVerdictName.NOT_ENOUGH_DATA
    assert result.sanity.span_duration_hours == 3.0
    assert result.sanity.incomplete_hour_count == 3
    assert result.sanity.gap_fraction == 0.75
    assert result.baseline.ran is False
    assert any("gap_fraction" in reason for reason in result.sufficiency.reasons)


@pytest.mark.asyncio
async def test_unsupported_schema_version_fails_closed(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    parquet_dir.mkdir()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE raw_segment (
                schema_version INTEGER NOT NULL,
                venue VARCHAR NOT NULL,
                product VARCHAR NOT NULL,
                channel VARCHAR NOT NULL,
                session_id VARCHAR NOT NULL,
                message_ordinal BIGINT NOT NULL,
                received_utc_ns BIGINT NOT NULL,
                received_monotonic_ns BIGINT NOT NULL,
                direction VARCHAR NOT NULL,
                frame_type VARCHAR NOT NULL,
                payload_encoding VARCHAR NOT NULL,
                payload_bytes BLOB NOT NULL,
                payload_sha256 VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO raw_segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                2,
                "hyperliquid",
                "BTC-PERP",
                "trades",
                "foreign-schema",
                1,
                1_788_105_600_000_000_000,
                9_000_000_001,
                "inbound",
                "text",
                "utf-8",
                b"{}",
                "00",
            ],
        )
        escaped = str((parquet_dir / "part-foreign-schema.parquet").resolve()).replace("'", "''")
        connection.execute(f"COPY raw_segment TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        connection.close()

    result = evaluate_retained_series(
        parquet_dir=parquet_dir,
        database_path=database_path,
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
    )

    assert result.verdict is HypothesisVerdictName.NOT_ENOUGH_DATA
    assert result.sanity.schema_versions == (2,)
    assert any("schema_versions" in reason for reason in result.sufficiency.reasons)
    assert result.baseline.ran is False


@pytest.mark.asyncio
async def test_cli_prints_fail_closed_json_for_fixture_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    await _publish(parquet_dir, _fixture_smoke_records())

    assert (
        main(
            [
                "--parquet-dir",
                str(parquet_dir),
                "--database",
                str(database_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["trading_mode"] == "PAPER"
    assert payload["input_mode"] == "ad_hoc"
    assert payload["verdict"] == "not_enough_data"
    assert payload["baseline"]["ran"] is False
    assert payload["sanity"]["trade_count"] == 2
    _assert_no_edge_claim(payload)


def test_evaluate_retained_series_rejects_non_paths(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"pathlib\.Path"):
        evaluate_retained_series(
            parquet_dir="raw",  # type: ignore[arg-type]
            database_path=tmp_path / "research.duckdb",
        )


def _write_capture_claim(path: Path, *, retained: bool, run_id: str) -> None:
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


@pytest.mark.asyncio
async def test_reconstructable_layout_runs_momentum_scaffold(tmp_path: Path) -> None:
    paths = data1a_run_paths(tmp_path, "synthetic-run")
    paths.raw_dir.mkdir(parents=True)
    await _publish(paths.raw_dir, _synthetic_multi_hour_records())
    _write_capture_claim(paths.capture_claim_path, retained=True, run_id=paths.run_id)

    result = evaluate_retained_series(
        artifact_root=tmp_path,
        run_id="synthetic-run",
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
    )

    assert result.input_mode == "reconstructable"
    assert result.path_contract == DATA1A_PATH_CONTRACT_ID
    assert result.run_id == "synthetic-run"
    assert result.verdict is HypothesisVerdictName.NOISE
    assert result.baseline.ran is True
    _assert_no_edge_claim(result.to_json_dict())


@pytest.mark.asyncio
async def test_reconstructable_smoke_claim_fails_closed(tmp_path: Path) -> None:
    paths = data1a_run_paths(tmp_path, "smoke-run")
    paths.raw_dir.mkdir(parents=True)
    await _publish(paths.raw_dir, _synthetic_multi_hour_records())
    _write_capture_claim(paths.capture_claim_path, retained=False, run_id=paths.run_id)

    result = evaluate_retained_series(
        artifact_root=tmp_path,
        run_id="smoke-run",
        thresholds=_SYNTHETIC_TEST_THRESHOLDS,
    )

    assert result.verdict is HypothesisVerdictName.NOT_ENOUGH_DATA
    assert result.baseline.ran is False
    assert any("retained" in reason for reason in result.sufficiency.reasons)


def test_resolve_series_input_rejects_mixed_modes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        resolve_series_input(
            parquet_dir=tmp_path / "raw",
            database_path=tmp_path / "research.duckdb",
            artifact_root=tmp_path,
            run_id="mixed",
        )


@pytest.mark.asyncio
async def test_cli_reconstructable_prints_fail_closed_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = data1a_run_paths(tmp_path, "cli-run")
    paths.raw_dir.mkdir(parents=True)
    await _publish(paths.raw_dir, _fixture_smoke_records())
    _write_capture_claim(paths.capture_claim_path, retained=True, run_id=paths.run_id)

    assert main(["--artifact-root", str(tmp_path), "--run-id", "cli-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["input_mode"] == "reconstructable"
    assert payload["path_contract"] == DATA1A_PATH_CONTRACT_ID
    assert payload["verdict"] == "not_enough_data"
    _assert_no_edge_claim(payload)
