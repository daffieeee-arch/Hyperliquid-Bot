"""Synthetic price-only oracle arithmetic and strictly admitted source boundaries."""

from __future__ import annotations

import csv
import gzip
import io
import zipfile
from decimal import Decimal, Inexact, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from research import clairvoyant_opportunity as model
from research import clairvoyant_opportunity_cli as cli
from research import clairvoyant_opportunity_io as source
from research.btc_flow_io import PrivateOutput
from research.harness.errors import IntegrityError
from research.two_models_io import BTC, DAY, MINUTE, START
from research.volume_repair_io import KLINE_HEADER


def find(
    cases: list[dict[str, Any]], grid: int = 1, cost: str = "zero", weight: str = "1"
) -> dict[str, Any]:
    return next(
        c
        for c in cases
        if c["grid_minutes"] == grid
        and c["cost"] == cost
        and c["reference_notional_fraction"] == weight
    )


def test_signed_exact_fees_fill_notional_and_tie_ownership() -> None:
    long = model.direction(Fraction(100), Fraction(110), 1, "base")
    short = model.direction(Fraction(100), Fraction(110), -1, "base")
    assert long.quantity == Fraction(1, 100) and short.quantity == -Fraction(1, 100)
    assert long.entry_fill == Fraction("100.02") and long.exit_fill == Fraction("109.978")
    assert short.entry_fill == Fraction("99.98") and short.exit_fill == Fraction("110.022")
    assert long.net == Fraction("0.098635009") and short.net == Fraction("-0.101365009")
    assert long.net == long.gross - long.fees - long.friction
    assert long.fees != short.fees and model.choose(long, short) == 1
    assert (
        model.choose(
            model.direction(Fraction(110), Fraction(100), 1, "stress"),
            model.direction(Fraction(110), Fraction(100), -1, "stress"),
        )
        == -1
    )
    equal = model.direction(Fraction(100), Fraction(100), 1, "zero")
    assert equal.net == 0 and model.choose(equal, equal) == 0


def test_fixed_reference_day_sizing_is_not_intraday_compounding() -> None:
    day = source.NativeDay(START, tuple(["100", "200", "100"] + ["100"] * 1438))
    result = model.score_day(day)
    unit = find(result["cases"])
    half = find(result["cases"], weight="0.5")
    assert Decimal(unit["normalized_price_pnl"]["net"]) == Decimal(
        "1.5"
    )  # Compounding would produce2.
    assert Decimal(half["normalized_price_pnl"]["net"]) == Decimal(".75")
    assert unit["counts"] == {"long": 1, "short": 1, "flat": 1438}
    assert result["label"] == "CLAIRVOYANT_NONTRADABLE" and result["tradable"] is False
    assert len(result["cases"]) == 24 and len(result["interval_digests"]) == 12
    assert {c["opportunities"] for c in result["cases"]} == {1440, 288, 96, 24}
    assert all(
        c["counts"]["long"] + c["counts"]["short"] + c["counts"]["flat"] == c["opportunities"]
        for c in result["cases"]
    )
    assert (
        find(result["cases"], grid=5)["completed_roundtrips"] == 0
    )  # No intragrid clairvoyant high/low.


def test_full1440_heterogeneous_denominators_are_bounded_and_half_scaling_exact() -> None:
    day = source.NativeDay(START, tuple(str(400000000 + i) for i in range(1441)))
    result = model.score_day(day)
    raw = source.encoded(result)
    assert len(raw) < 150000 and len(gzip.compress(raw, mtime=0)) < 50000
    for grid in model.GRIDS:
        for cost in model.COSTS:
            one = find(result["cases"], grid, cost, "1")
            half = find(result["cases"], grid, cost, "0.5")
            assert one["counts"] == half["counts"]
            for key in model.FIELDS:
                assert Fraction(half["normalized_price_pnl"][key]) * 2 == Fraction(
                    one["normalized_price_pnl"][key]
                )
                assert len(one["normalized_price_pnl"][key]) < 100
            for key in ("net_lower", "net_upper"):
                assert Fraction(half[key]) * 2 == Fraction(one[key])
            assert (
                Fraction(one["net_lower"])
                <= Fraction(one["normalized_price_pnl"]["net"])
                <= Fraction(one["net_upper"])
            )


def test_directed_rounding_brackets_exact_small_sum_and_threshold_uncertainty() -> None:
    total = model.Totals()
    exact = Fraction(0)
    for p0 in range(101, 121):
        value = model.direction(Fraction(p0), Fraction(p0 + 1), 1, "zero")
        total.add(value, 1)
        exact += value.net
    assert Fraction(total.lower) <= exact <= Fraction(total.upper)
    assert model.threshold(Decimal(".2"), Decimal(".2")) == ("PASS_PRICE_REFERENCE_THRESHOLD", True)
    assert model.threshold(Decimal(".1999"), Decimal(".19999")) == (
        "BELOW_PRICE_REFERENCE_THRESHOLD",
        False,
    )
    assert model.threshold(
        Decimal(".19999999999999999999999999999999999999999999999999"),
        Decimal(".20000000000000000000000000000000000000000000000001"),
    ) == ("NUMERIC_THRESHOLD_UNCERTAIN", None)
    lo = model.half(Decimal(".39999999999999999999999999999999999999999999999999"), Decimal(".5"))
    hi = model.half(Decimal(".40000000000000000000000000000000000000000000000001"), Decimal(".5"))
    assert model.threshold(lo, hi)[1] is None


def test_statistics_keep_uncertain_days_and_even_median_not_a_winner() -> None:
    def row(value: str, lower: str, upper: str, hit: bool | None) -> dict[str, Any]:
        return {
            "normalized_price_pnl": {"net": value},
            "net_lower": lower,
            "net_upper": upper,
            "threshold_hit": hit,
        }

    result = model.statistics(
        [
            row("0", "0", "0", False),
            row(".2", ".19999", ".20001", None),
            row(".3", ".3", ".3", True),
            row(".5", ".5", ".5", True),
        ]
    )
    assert result["calendar_days"] == 4 and result["threshold_day_count_range"] == [2, 3]
    assert Decimal(result["median"]["estimate"]) == Decimal(".25")
    assert result["threshold_uncertain_days"] == 1
    assert (
        Decimal(result["mean"]["lower"])
        <= Decimal(result["mean"]["estimate"])
        <= Decimal(result["mean"]["upper"])
    )


def native(tmp_path: Path, *, fault: str | None = None) -> dict[str, Any]:
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow([next(iter(v)) for v in KLINE_HEADER])
    for i in range(3):
        stamp = START + i * MINUTE
        row = [
            stamp,
            "100",
            "101",
            "99",
            "100.0000",
            "1",
            stamp + MINUTE - 1,
            "100",
            1,
            "0.5",
            "50",
            "0",
        ]
        if i == 1:
            if fault == "gap":
                continue
            if fault == "duplicate":
                row[0] = START
            if fault == "negative":
                row[4] = "-1"
            if fault == "nonfinite":
                row[4] = "NaN"
            if fault == "clock":
                row[6] = stamp + MINUTE
        writer.writerow(row)
    p = tmp_path / "BTCUSDT-1m-2024-01.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(p.stem + ".csv", text.getvalue())
    raw = p.read_bytes()
    check = Path(str(p) + ".CHECKSUM")
    check.write_text(f"{source.sha(raw)} {p.name}\n")
    return {
        "month": "2024-01",
        "role": "trade_minute",
        "symbol": BTC,
        "path": str(p),
        "bytes": len(raw),
        "sha256": source.sha(raw),
        "checksum_path": str(check),
        "checksum_bytes": check.stat().st_size,
        "checksum_sha256": source.sha(check.read_bytes()),
    }


def test_strict_source_reuses_native_close_text_not_first_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(source, "month_clock", lambda month: (START, START + 3 * MINUTE))
    row = native(tmp_path)
    assert source.month_closes(row) == [
        (START + MINUTE, "100.0000"),
        (START + 2 * MINUTE, "100.0000"),
        (START + 3 * MINUTE, "100.0000"),
    ]
    Path(row["path"]).write_bytes(b"corrupted")
    with pytest.raises(Exception, match="identity"):
        source.month_closes(row)


@pytest.mark.parametrize("fault", ["gap", "duplicate", "negative", "nonfinite", "clock"])
def test_source_bad_grid_values_cannot_be_dropped_or_repaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    monkeypatch.setattr(source, "month_clock", lambda month: (START, START + 3 * MINUTE))
    with pytest.raises(IntegrityError):
        source.month_closes(native(tmp_path, fault=fault))


def test_utc_endpoints_each_day1441_shared_midnight_and_all_days_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source, "END", START + 2 * DAY)
    rows = [{"part": i} for i in range(33)]

    def closes(row: dict[str, Any]) -> list[tuple[int, str]]:
        if row["part"] == 0:
            return [(START, "99")]
        if row["part"] == 1:
            return [(t, str(t)) for t in range(START + MINUTE, START + 2 * DAY + 1, MINUTE)]
        return []

    monkeypatch.setattr(source, "month_closes", closes)
    days = list(source.days(rows))
    assert len(days) == 2 and all(len(d.prices) == 1441 for d in days)
    assert days[0].prices[0] == "99" and days[0].prices[-1] == days[1].prices[0]
    assert days[-1].start_ms + DAY == START + 2 * DAY
    admission = source.qualify(rows)
    assert admission["distinct_minute_boundaries"] == 2881 and admission["marks_required"] is False
    assert admission["calendar_days"] == 2


def test_pinned_source_index_and_private_createonly_outputs(tmp_path: Path) -> None:
    p = tmp_path / "index.json"
    p.write_text("[]")
    with pytest.raises(Exception, match="registered33"):
        source.load_index(p)
    with PrivateOutput(tmp_path / "out") as private:
        output = cli.Evidence(private)
        output.json("once.json", {"label": source.LABEL})
        with pytest.raises(FileExistsError):
            output.json("once.json", {})
        with pytest.raises(Exception, match="bounded"):
            output.write("large.bin", b"x" * (64 << 20 | 1))


@pytest.mark.parametrize("mode", ["LIVE", "TESTNET", "SHADOW", "PAPER "])
def test_paper_gate_precedes_source_output_and_financial_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "identity", lambda commit: pytest.fail("source read reached"))
    with pytest.raises(ValueError, match="PAPER"):
        cli.main(
            [
                "--index",
                str(tmp_path / "index"),
                "--output",
                str(tmp_path / "out"),
                "--source-commit",
                "a" * 40,
                "--expected-plan-sha256",
                "b" * 64,
                "--expected-amendment-sha256",
                "c" * 64,
            ]
        )
    assert not (tmp_path / "out").exists()


def test_exact_half_does_not_inherit_ambient_inexact_flag_or_precision() -> None:
    value = Decimal("3.2345678901234567890123456789012345678901234567891")
    with localcontext(prec=3) as ambient:
        ambient.divide(Decimal(1), Decimal(3))
        assert ambient.flags[Inexact]
        result = model.half(value, Decimal(".5"))
        assert Fraction(result) * 2 == Fraction(value)
        assert len(result.as_tuple().digits) == 51
        assert ambient.flags[Inexact]  # Caller context is not mutated.


def test_qualification_only_cli_never_invokes_financial_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    code = {
        str(source.PLAN.relative_to(source.ROOT)): "b" * 64,
        str(source.AMENDMENT.relative_to(source.ROOT)): "c" * 64,
    }
    monkeypatch.setattr(cli, "identity", lambda commit: code)
    monkeypatch.setattr(cli, "load_index", lambda path: [])
    monkeypatch.setattr(cli, "qualify", lambda rows: {"calendar_days": 974})
    monkeypatch.setattr(cli, "verify_sources", lambda *args: None)
    monkeypatch.setattr(cli, "calculate", lambda *args: pytest.fail("financial labels reached"))
    result = cli.main(
        [
            "--index",
            str(tmp_path / "unused"),
            "--output",
            str(tmp_path / "run"),
            "--source-commit",
            "a" * 40,
            "--expected-plan-sha256",
            "b" * 64,
            "--expected-amendment-sha256",
            "c" * 64,
            "--qualify-only",
        ]
    )
    assert result == 0 and not (tmp_path / "run/result.json").exists()
    assert b"SOURCE_ONLY_COMPLETE" in (tmp_path / "run/completion.json").read_bytes()


def test_all24case_day_pipeline_and_failures_keep_explicit_denominators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    native = [source.NativeDay(START + i * DAY, tuple(["100"] * 1441)) for i in range(2)]
    admission = {"daily_source_hashes": [{"source_hash": d.source_hash()} for d in native]}
    monkeypatch.setattr(cli, "days", lambda rows: iter(native))
    monkeypatch.setattr(cli, "END", START + 2 * DAY)
    with PrivateOutput(tmp_path / "good") as private:
        result = cli.calculate([], admission, cli.Evidence(private))
    assert (
        result["case_count"] == 24 and len(result["cases"]) == 24 and result["calendar_days"] == 2
    )
    assert result["selector"] is None and result["new_alpha_configurations"] == 0
    assert result["programme_unique_configurations"] == 36
    assert all(
        r["calendar_days"] == 2 and r["threshold_day_count_range"] == [0, 0]
        for r in result["cases"].values()
    )

    def failure(day: source.NativeDay) -> dict[str, Any]:
        raise ArithmeticError("synthetic unsupported arithmetic failure")

    monkeypatch.setattr(model, "score_day", failure)
    with PrivateOutput(tmp_path / "failed") as private, pytest.raises(ArithmeticError):
        cli.calculate([], admission, cli.Evidence(private))
    files = list((tmp_path / "failed").glob("failed-day-*.json"))
    assert len(files) == 1 and b"FAILED_ALL24CASES_UNCERTIFIED" in files[0].read_bytes()
    assert b"no_flat_or_zero_substitution" in files[0].read_bytes()


def test_exact_half_uses_own_exponent_range_as_well_as_precision() -> None:
    with localcontext(prec=3) as ambient:
        ambient.Emin = -1
        ambient.Emax = 1
        value = Decimal("1.234567890123456789e-100")
        assert Fraction(model.half(value, Decimal(".5"))) * 2 == Fraction(value)
