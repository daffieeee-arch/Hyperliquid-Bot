"""Deterministic synthetic tests for the PAPER H1 lead-lag scaffold."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb
import pytest

from hyperliquid_bot.exp_h1_leadlag import (
    COMMIT_SHA_UNSET,
    COST_MULTIPLIERS,
    DEFAULT_BINANCE_IMPULSE_INSTRUMENT,
    DELTA_BUCKETS,
    EXPERIMENT_ID,
    FEATURE_SET,
    H1_SAMPLE_THRESHOLDS,
    MIN_TRADES_PER_HORIZON,
    MIN_USABLE_BUCKETS,
    PROMOTION_DECISION,
    SUMMARY_JSON_NAME,
    BinanceImpulseInstrument,
    H1VerdictName,
    PanelBucket,
    assign_h1_verdict,
    bn_impulse_price,
    evaluate_h1_leadlag,
    feature_set_for,
    main,
    require_binance_impulse_instrument,
    require_predeclared_deltas,
    require_utc_ns_range,
)
from hyperliquid_bot.panel_hl_binance import (
    DEFAULT_BUCKET_MS,
    NS_PER_MS,
    PANEL_PARQUET_NAME,
    PANEL_SUMMARY_NAME,
    PANEL_VERSION,
    build_hl_binance_panel,
)
from test_panel_hl_binance import _bn_bucket_records, _dense_stamps, _hl_bucket_records, _publish

_BASE_UTC_NS = 1_788_105_600_000_000_000
_SECOND_NS = 1_000_000_000


def _assert_no_edge_verdict(payload: object) -> None:
    if isinstance(payload, dict):
        if "verdict" in payload:
            assert payload["verdict"] != "edge"
            assert str(payload["verdict"]).lower() != "edge"
        for value in payload.values():
            _assert_no_edge_verdict(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_edge_verdict(item)


def _cost_keys() -> set[str]:
    return {format(item, "f") for item in COST_MULTIPLIERS}


def _assert_cost_grid(metrics_block: dict[str, object]) -> None:
    assert set(metrics_block) == {str(delta) for delta in DELTA_BUCKETS}
    for delta_block in metrics_block.values():
        assert isinstance(delta_block, dict)
        assert set(delta_block) == _cost_keys()


def _mapping(payload: object, key: str) -> dict[str, object]:
    assert isinstance(payload, dict)
    value = payload[key]
    assert isinstance(value, dict)
    return value


def _oos_metrics(payload: object) -> dict[str, object]:
    return _mapping(_mapping(payload, "metrics"), "oos")


def _write_panel_summary(
    path: Path,
    *,
    verdict: str,
    bucket_ms: int = DEFAULT_BUCKET_MS,
) -> None:
    path.write_text(
        json.dumps(
            {
                "bucket_ms": bucket_ms,
                "trading_mode": "PAPER",
                "verdict": verdict,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_panel_parquet(
    path: Path,
    *,
    buckets: int,
    usable: bool = True,
    follow_impulse: bool = False,
    usable_every: int = 1,
    include_spot: bool = True,
    include_usdm: bool = True,
) -> tuple[int, int]:
    rows: list[tuple[object, ...]] = []
    for index in range(buckets):
        stamp = _BASE_UTC_NS + index * _SECOND_NS
        row_usable = usable and (index % usable_every == 0)
        hl_mid = Decimal("100000") + (
            Decimal(index) * Decimal("250") if follow_impulse else Decimal(0)
        )
        bn_spot = Decimal("99900") + Decimal(index)
        bn_usdm_agg = Decimal("110000") + Decimal(index)
        bn_usdm_mark = Decimal("120000") + Decimal(index)
        hl_bid = hl_mid - Decimal("1")
        hl_ask = hl_mid + Decimal("1")
        hl_proxy = (hl_bid + hl_ask) / Decimal(2)
        rows.append(
            (
                stamp,
                format(hl_mid, "f"),
                format(hl_mid, "f"),
                format(hl_bid, "f"),
                format(hl_ask, "f"),
                format(hl_proxy, "f"),
                not row_usable,
                False,
                format(bn_spot, "f") if include_spot else None,
                format(bn_spot, "f") if include_spot else None,
                format(bn_usdm_agg, "f") if include_usdm else None,
                format(bn_usdm_mark, "f") if include_usdm else None,
                not row_usable,
                False,
                row_usable,
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE panel (
                bucket_utc_ns BIGINT NOT NULL,
                hl_last_trade_price VARCHAR,
                hl_last_mid_price VARCHAR,
                hl_last_bid_price VARCHAR,
                hl_last_ask_price VARCHAR,
                hl_bbo_mid_proxy VARCHAR,
                hl_incomplete BOOLEAN NOT NULL,
                hl_gap_detected BOOLEAN NOT NULL,
                bn_spot_last_price VARCHAR,
                bn_spot_bbo_mid_proxy VARCHAR,
                bn_usdm_last_agg_price VARCHAR,
                bn_usdm_mark_price VARCHAR,
                bn_incomplete BOOLEAN NOT NULL,
                bn_gap_detected BOOLEAN NOT NULL,
                overlap_ok BOOLEAN NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO panel VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
        escaped = str(path.resolve()).replace("'", "''")
        connection.execute(f"COPY panel TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        connection.close()
    if not rows:
        return 0, 0
    return int(cast(int, rows[0][0])), int(cast(int, rows[-1][0]))


def _write_ready_panel(
    directory: Path,
    *,
    buckets: int,
    follow_impulse: bool = False,
    usable: bool = True,
    usable_every: int = 1,
    include_spot: bool = True,
    include_usdm: bool = True,
) -> tuple[Path, Path, int, int]:
    parquet = directory / PANEL_PARQUET_NAME
    summary = directory / PANEL_SUMMARY_NAME
    start_ns, end_ns = _write_panel_parquet(
        parquet,
        buckets=buckets,
        usable=usable,
        follow_impulse=follow_impulse,
        usable_every=usable_every,
        include_spot=include_spot,
        include_usdm=include_usdm,
    )
    _write_panel_summary(summary, verdict="panel_ready")
    return parquet, summary, int(start_ns), int(end_ns)


def _identity_bucket(
    *,
    spot_last: Decimal | None,
    spot_bbo: Decimal | None,
    usdm_agg: Decimal | None,
    usdm_mark: Decimal | None,
) -> PanelBucket:
    return PanelBucket(
        bucket_utc_ns=_BASE_UTC_NS,
        hl_last_trade_price=Decimal("100000"),
        hl_last_mid_price=Decimal("100000"),
        hl_last_bid_price=Decimal("99999"),
        hl_last_ask_price=Decimal("100001"),
        hl_bbo_mid_proxy=Decimal("100000"),
        hl_incomplete=False,
        hl_gap_detected=False,
        bn_spot_last_price=spot_last,
        bn_spot_bbo_mid_proxy=spot_bbo,
        bn_usdm_last_agg_price=usdm_agg,
        bn_usdm_mark_price=usdm_mark,
        bn_incomplete=False,
        bn_gap_detected=False,
        overlap_ok=True,
    )


def _assert_instrument_recorded(payload: object, instrument: BinanceImpulseInstrument) -> None:
    assert isinstance(payload, dict)
    assert payload["feature_set"] == feature_set_for(instrument)
    parameters = payload["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["binance_impulse_instrument"] == instrument.value
    _assert_no_edge_verdict(payload)


def test_predeclared_constants_are_closed() -> None:
    assert DELTA_BUCKETS == (1, 5, 30)
    assert require_predeclared_deltas(DELTA_BUCKETS) == (1, 5, 30)
    assert set(H1VerdictName) == {H1VerdictName.NOISE, H1VerdictName.NOT_ENOUGH_DATA}
    assert "edge" not in {item.value for item in H1VerdictName}
    assert MIN_USABLE_BUCKETS == 16
    assert MIN_TRADES_PER_HORIZON == 8
    assert H1_SAMPLE_THRESHOLDS.min_usable_buckets == 16
    assert DEFAULT_BINANCE_IMPULSE_INSTRUMENT is BinanceImpulseInstrument.USDM_MARK
    assert FEATURE_SET == "bn_prior_bucket_return_sign_to_hl_forward_return/binance_usdm_mark"
    assert require_binance_impulse_instrument("binance_spot") is BinanceImpulseInstrument.SPOT
    with pytest.raises(ValueError, match="binance_impulse_instrument"):
        require_binance_impulse_instrument("spot_then_usdm")
    with pytest.raises(ValueError, match="at most 3"):
        require_predeclared_deltas((1, 5, 30, 60))
    with pytest.raises(ValueError, match="oos_start"):
        require_utc_ns_range(2, 1)


def test_bn_impulse_price_never_mixes_spot_and_usdm() -> None:
    usdm_only = _identity_bucket(
        spot_last=None,
        spot_bbo=None,
        usdm_agg=Decimal("110010"),
        usdm_mark=Decimal("120020"),
    )
    spot_only = _identity_bucket(
        spot_last=Decimal("99901"),
        spot_bbo=Decimal("99902"),
        usdm_agg=None,
        usdm_mark=None,
    )
    both = _identity_bucket(
        spot_last=Decimal("99901"),
        spot_bbo=Decimal("99902"),
        usdm_agg=Decimal("110010"),
        usdm_mark=Decimal("120020"),
    )
    spot_bbo_only = _identity_bucket(
        spot_last=None,
        spot_bbo=Decimal("99902"),
        usdm_agg=Decimal("110010"),
        usdm_mark=Decimal("120020"),
    )

    assert bn_impulse_price(usdm_only, BinanceImpulseInstrument.SPOT) is None
    assert bn_impulse_price(usdm_only, BinanceImpulseInstrument.USDM_AGG) == Decimal("110010")
    assert bn_impulse_price(usdm_only, BinanceImpulseInstrument.USDM_MARK) == Decimal("120020")

    assert bn_impulse_price(spot_only, BinanceImpulseInstrument.SPOT) == Decimal("99901")
    assert bn_impulse_price(spot_only, BinanceImpulseInstrument.USDM_AGG) is None
    assert bn_impulse_price(spot_only, BinanceImpulseInstrument.USDM_MARK) is None

    assert bn_impulse_price(both, BinanceImpulseInstrument.SPOT) == Decimal("99901")
    assert bn_impulse_price(both, BinanceImpulseInstrument.USDM_AGG) == Decimal("110010")
    assert bn_impulse_price(both, BinanceImpulseInstrument.USDM_MARK) == Decimal("120020")

    assert bn_impulse_price(spot_bbo_only, BinanceImpulseInstrument.SPOT) == Decimal("99902")
    assert bn_impulse_price(spot_bbo_only, BinanceImpulseInstrument.USDM_AGG) == Decimal("110010")
    assert bn_impulse_price(spot_bbo_only, BinanceImpulseInstrument.USDM_MARK) == Decimal("120020")


def test_assign_h1_verdict_refuses_edge() -> None:
    assert assign_h1_verdict("noise") is H1VerdictName.NOISE
    assert assign_h1_verdict("not_enough_data") is H1VerdictName.NOT_ENOUGH_DATA
    with pytest.raises(ValueError, match="must not assign verdict 'edge'"):
        assign_h1_verdict("edge")
    with pytest.raises(ValueError, match="must not assign verdict 'edge'"):
        assign_h1_verdict("EDGE")
    with pytest.raises(ValueError, match="noise or not_enough_data"):
        assign_h1_verdict("panel_ready")


def test_synthetic_panel_is_noise_even_when_returns_look_good(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(
        tmp_path / "panel",
        buckets=45,
        follow_impulse=True,
    )
    output_dir = tmp_path / "h1-out"

    result = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        output_dir=output_dir,
    )

    assert result.trading_mode == "PAPER"
    assert result.experiment_id == EXPERIMENT_ID
    assert result.verdict is H1VerdictName.NOISE
    assert result.promotion_decision == PROMOTION_DECISION
    assert result.commit_sha == COMMIT_SHA_UNSET
    assert result.usable_bucket_count == 45
    payload = result.to_json_dict()
    _assert_instrument_recorded(payload, DEFAULT_BINANCE_IMPULSE_INSTRUMENT)
    _assert_no_edge_verdict(payload)
    _assert_cost_grid(_oos_metrics(payload))
    written = json.loads((output_dir / SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    _assert_no_edge_verdict(written)
    written_oos = _oos_metrics(written)
    _assert_cost_grid(written_oos)
    assert written["verdict"] == "noise"
    positive_cells: list[dict[str, object]] = []
    for delta in written_oos.values():
        assert isinstance(delta, dict)
        for cell in delta.values():
            assert isinstance(cell, dict)
            mean = cell["mean_after_cost_hl_return"]
            assert isinstance(mean, str)
            if Decimal(mean) > 0:
                positive_cells.append(cell)
    assert positive_cells
    assert all(cell["verdict"] == "noise" for cell in positive_cells)


def test_tiny_panel_is_not_enough_data(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(tmp_path / "panel", buckets=6)

    result = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
    )

    assert result.verdict is H1VerdictName.NOT_ENOUGH_DATA
    payload = result.to_json_dict()
    _assert_no_edge_verdict(payload)
    _assert_cost_grid(_oos_metrics(payload))
    assert payload["verdict"] == "not_enough_data"
    assert any("usable_bucket_count" in reason for reason in result.reasons) or any(
        "trade_count" in reason for reason in result.reasons
    )


def test_gap_mask_without_enough_overlap_is_not_enough_data(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(
        tmp_path / "panel",
        buckets=45,
        usable_every=5,
    )

    result = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
    )

    assert result.verdict is H1VerdictName.NOT_ENOUGH_DATA
    assert result.usable_bucket_count < MIN_USABLE_BUCKETS
    assert any("after gap/incomplete mask" in reason for reason in result.reasons)
    _assert_no_edge_verdict(result.to_json_dict())


def test_missing_panel_is_not_enough_data(tmp_path: Path) -> None:
    result = evaluate_h1_leadlag(
        panel_parquet=tmp_path / "missing.parquet",
        panel_summary=tmp_path / "missing.json",
        oos_start_utc_ns=_BASE_UTC_NS,
        oos_end_utc_ns=_BASE_UTC_NS + 40 * _SECOND_NS,
    )

    assert result.verdict is H1VerdictName.NOT_ENOUGH_DATA
    assert any("missing" in reason for reason in result.reasons)
    _assert_no_edge_verdict(result.to_json_dict())


def test_panel_summary_not_enough_data_fails_closed(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(tmp_path / "panel", buckets=45)
    _write_panel_summary(summary, verdict="not_enough_data")

    result = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
    )

    assert result.verdict is H1VerdictName.NOT_ENOUGH_DATA
    assert result.panel_verdict == "not_enough_data"
    assert any("not panel_ready" in reason for reason in result.reasons)
    _assert_no_edge_verdict(result.to_json_dict())


def test_evaluate_rejects_mixed_inputs(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(tmp_path / "panel", buckets=45)
    with pytest.raises(ValueError, match="not both"):
        evaluate_h1_leadlag(
            panel_parquet=parquet,
            panel_summary=summary,
            artifact_root=tmp_path,
            hl_run_id="hl",
            bn_run_id="bn",
            oos_start_utc_ns=start_ns,
            oos_end_utc_ns=end_ns,
        )


def test_cli_prints_noise_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(
        tmp_path / "panel",
        buckets=45,
        follow_impulse=True,
    )
    output_dir = tmp_path / "out"

    assert (
        main(
            [
                "--panel-parquet",
                str(parquet),
                "--panel-summary",
                str(summary),
                "--oos-start-utc-ns",
                str(start_ns),
                "--oos-end-utc-ns",
                str(end_ns),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["trading_mode"] == "PAPER"
    assert payload["verdict"] == "noise"
    assert payload["promotion_decision"] == "forbidden"
    _assert_instrument_recorded(payload, DEFAULT_BINANCE_IMPULSE_INSTRUMENT)
    _assert_no_edge_verdict(payload)
    _assert_cost_grid(_oos_metrics(payload))


def test_cli_records_explicit_spot_instrument(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(
        tmp_path / "panel",
        buckets=45,
        follow_impulse=True,
    )

    assert (
        main(
            [
                "--panel-parquet",
                str(parquet),
                "--panel-summary",
                str(summary),
                "--oos-start-utc-ns",
                str(start_ns),
                "--oos-end-utc-ns",
                str(end_ns),
                "--binance-impulse-instrument",
                "binance_spot",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "noise"
    _assert_instrument_recorded(payload, BinanceImpulseInstrument.SPOT)


def test_registry_template_has_no_fake_metrics() -> None:
    template = json.loads(
        Path("docs/experiments/exp_h1_leadlag.registry.template.json").read_text(encoding="utf-8")
    )
    assert template["experiment_id"] == EXPERIMENT_ID
    assert template["metrics"] is None
    assert template["verdict"] is None
    assert template["promotion_decision"] == "forbidden"
    assert template["oos"]["start_utc_ns"] is None
    assert template["parameters"]["delta_buckets"] == [1, 5, 30]
    assert template["parameters"]["cost_multipliers"] == ["1.0", "1.5", "2.0"]
    assert template["parameters"]["binance_impulse_instrument"] == "binance_usdm_mark"
    assert template["feature_set"] == FEATURE_SET
    assert template["dataset"]["panel_version"] == PANEL_VERSION
    _assert_no_edge_verdict(template)


@pytest.mark.asyncio
async def test_runner_consumes_wpq1_synthetic_panel(tmp_path: Path) -> None:
    hl_raw = tmp_path / "hl-raw"
    bn_raw = tmp_path / "bn-raw"
    panel_dir = tmp_path / "panel-out"
    stamps = _dense_stamps(45)
    await _publish(hl_raw, _hl_bucket_records(stamps))
    await _publish(bn_raw, _bn_bucket_records(stamps))
    panel = build_hl_binance_panel(
        hl_parquet_dir=hl_raw,
        hl_database_path=tmp_path / "hl.duckdb",
        bn_parquet_dir=bn_raw,
        bn_database_path=tmp_path / "bn.duckdb",
        output_dir=panel_dir,
    )
    assert panel.panel_parquet is not None
    assert panel.summary_json is not None

    result = evaluate_h1_leadlag(
        panel_parquet=Path(panel.panel_parquet),
        panel_summary=Path(panel.summary_json),
        oos_start_utc_ns=int(panel.overlap_start_utc_ns or 0),
        oos_end_utc_ns=int(panel.overlap_end_utc_ns or 0),
    )

    assert result.verdict in {H1VerdictName.NOISE, H1VerdictName.NOT_ENOUGH_DATA}
    assert result.verdict is H1VerdictName.NOISE
    payload = result.to_json_dict()
    _assert_no_edge_verdict(payload)
    _assert_cost_grid(_oos_metrics(payload))
    assert _mapping(payload, "dataset")["panel_version"] == PANEL_VERSION
    _assert_instrument_recorded(payload, DEFAULT_BINANCE_IMPULSE_INSTRUMENT)
    assert NS_PER_MS * DEFAULT_BUCKET_MS == result.bucket_ns


def test_spot_empty_usdm_present_does_not_mix_families(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(
        tmp_path / "panel",
        buckets=45,
        follow_impulse=True,
        include_spot=False,
        include_usdm=True,
    )

    mixed_old_behavior = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        binance_impulse_instrument=BinanceImpulseInstrument.SPOT,
    )
    usdm_mark = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        binance_impulse_instrument=BinanceImpulseInstrument.USDM_MARK,
    )
    usdm_agg = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        binance_impulse_instrument=BinanceImpulseInstrument.USDM_AGG,
    )

    assert mixed_old_behavior.verdict is H1VerdictName.NOT_ENOUGH_DATA
    assert any("trade_count" in reason for reason in mixed_old_behavior.reasons)
    assert usdm_mark.verdict is H1VerdictName.NOISE
    assert usdm_agg.verdict is H1VerdictName.NOISE
    _assert_instrument_recorded(mixed_old_behavior.to_json_dict(), BinanceImpulseInstrument.SPOT)
    _assert_instrument_recorded(usdm_mark.to_json_dict(), BinanceImpulseInstrument.USDM_MARK)
    _assert_instrument_recorded(usdm_agg.to_json_dict(), BinanceImpulseInstrument.USDM_AGG)


def test_usdm_empty_spot_present_does_not_use_spot_in_usdm_modes(tmp_path: Path) -> None:
    parquet, summary, start_ns, end_ns = _write_ready_panel(
        tmp_path / "panel",
        buckets=45,
        follow_impulse=True,
        include_spot=True,
        include_usdm=False,
    )

    spot = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        binance_impulse_instrument=BinanceImpulseInstrument.SPOT,
    )
    usdm_mark = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        binance_impulse_instrument=BinanceImpulseInstrument.USDM_MARK,
    )
    usdm_agg = evaluate_h1_leadlag(
        panel_parquet=parquet,
        panel_summary=summary,
        oos_start_utc_ns=start_ns,
        oos_end_utc_ns=end_ns,
        binance_impulse_instrument=BinanceImpulseInstrument.USDM_AGG,
    )

    assert spot.verdict is H1VerdictName.NOISE
    assert usdm_mark.verdict is H1VerdictName.NOT_ENOUGH_DATA
    assert usdm_agg.verdict is H1VerdictName.NOT_ENOUGH_DATA
    assert any("trade_count" in reason for reason in usdm_mark.reasons)
    assert any("trade_count" in reason for reason in usdm_agg.reasons)
    _assert_instrument_recorded(spot.to_json_dict(), BinanceImpulseInstrument.SPOT)
    _assert_instrument_recorded(usdm_mark.to_json_dict(), BinanceImpulseInstrument.USDM_MARK)
    _assert_instrument_recorded(usdm_agg.to_json_dict(), BinanceImpulseInstrument.USDM_AGG)
