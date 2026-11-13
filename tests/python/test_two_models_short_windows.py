"""The fixed short-window proof preserves account state without empty replays."""

from __future__ import annotations

import gzip
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from research import micro_flow as micro
from research import two_models_cli as cli
from research.btc_flow_io import PrivateOutput
from research.harness.errors import IntegrityError

D = Decimal
FUNDING = (0, 3_600_000)
BENCHMARK = micro.MicroConfig("momentum-benchmark", 30, 0.0, 0.02)
CONFIGURATIONS = (*micro.configs(), BENCHMARK)


def pressure_window(duration: int, *, phase_ns: int = 0) -> list[micro.MicroEvent]:
    rows = []
    for second in range(duration + 1):
        instant = phase_ns + second * micro.SECOND
        mid = D(100_000 + 2 * second)
        utc = 1_700_000_000 * micro.SECOND + instant
        venue = 600_000 + second * 1000
        rows.append(
            micro.MicroEvent(instant, utc, venue, "book", "synthetic", mid - 1, mid + 1, D(3), D(1))
        )
        rows.append(
            micro.MicroEvent(
                instant,
                utc,
                venue,
                "trade",
                "synthetic",
                trade_price=mid,
                trade_quantity=D("0.1"),
                trade_side=1,
            )
        )
        if second < duration:
            half = micro.SECOND // 2
            rows.append(
                micro.MicroEvent(
                    instant + half,
                    utc + half,
                    venue + 500,
                    "book",
                    "synthetic",
                    mid - 1,
                    mid + 1,
                    D(3),
                    D(1),
                )
            )
    return rows


@pytest.mark.parametrize("config", CONFIGURATIONS, ids=lambda config: config.name)
@pytest.mark.parametrize("cost", ("base", "stress"))
def test_333_second_proof_matches_every_registered_complete_policy(
    config: micro.MicroConfig,
    cost: str,
) -> None:
    rows = pressure_window(333)
    equity = 8765.43
    fast = cli.flat_short_window(rows, equity)
    full = micro.replay(
        config,
        rows,
        FUNDING,
        equity,
        fee_bps=4.5 if cost == "base" else 9.0,
        slippage_bps=1.0 if cost == "base" else 2.0,
        latency_ns=micro.SECOND // 2 if cost == "base" else micro.SECOND,
        end_mono_ns=rows[-1].mono_ns,
        benchmark=config.name == BENCHMARK.name,
    )
    assert full["status"] == fast["status"] == "complete"
    for name in (
        "initial_equity",
        "final_equity",
        "fee_cost",
        "slippage_cost",
        "total_funding",
        "same_quantity_gross_mid_pnl",
        "execution_spread_and_friction_cost",
        "observed_hours",
    ):
        assert fast[name] == pytest.approx(full[name])
    assert fast["open_position"] is full["open_position"] is None
    assert fast["trades"] == full["trades"] == []
    assert fast["fills"] == full["fills"] == []
    # The proof deliberately does not pretend it evaluated each feature decision.
    assert fast["counts"] == {"provably_precluded_short_windows": 1}
    assert "decisions" not in fast["counts"]
    assert full["counts"]["decisions"] > 0
    assert fast["ledger"] == []
    assert all(point["equity"] == equity for point in full["ledger"])


@pytest.mark.parametrize("phase_ns", [0, 5 * micro.SECOND + 123, 9 * micro.SECOND + 999_999_999])
def test_short_window_proof_does_not_depend_on_monotonic_grid_phase(phase_ns: int) -> None:
    rows = pressure_window(333, phase_ns=phase_ns)
    actual = micro.replay(micro.configs()[0], rows, FUNDING)
    assert actual["complete_trades"] == 0 and actual["status"] == "complete"
    assert cli.flat_short_window(rows, 10_000)["observed_hours"] == pytest.approx(333 / 3600)


def test_334_seconds_is_refused_and_a_real_base_entry_can_already_exist() -> None:
    rows = pressure_window(334)
    with pytest.raises(IntegrityError, match="too short"):
        cli.flat_short_window(rows, 10_000)
    full = micro.replay(micro.configs()[0], rows, FUNDING)
    assert full["status"] == "complete"
    assert full["complete_trades"] == 1
    assert full["fills"][0]["decision_mono_ns"] == 30 * micro.SECOND


def test_fast_proof_carries_a_previous_loss_and_preserves_coverage_in_totals() -> None:
    state = cli.Totals()
    state.equity = 9000.0
    rows = pressure_window(333)
    for _ in range(2):
        flat = cli.flat_short_window(rows, state.equity)
        state.add(flat, "development", "2026", is_micro=True)
    result = state.result()
    assert result["initial_equity"] == 10_000
    assert result["final_equity"] == 9000
    assert result["net_return"] == pytest.approx(-0.1)
    assert result["observed_hours"] == pytest.approx(2 * 333 / 3600)
    assert result["complete_trades"] == result["target_attainment_count"] == 0
    assert result["fee_cost"] == result["slippage_cost"] == result["total_funding"] == 0
    assert result["folds"]["development"]["initial_equity"] == 9000
    assert result["counts"]["provably_precluded_short_windows"] == 2
    assert "decisions" not in result["counts"]


def test_run_micro_keeps_admission_and_quality_without_eighteen_empty_case_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = pressure_window(333)
    admission = {
        "capture": "synthetic",
        "group": "development",
        "conditional_window": True,
        "counts": {"bbo": 667, "trades": 334},
        "source_events": len(rows),
        "end_reason": "native_book_unavailable",
    }
    quality = {
        "capture": "synthetic",
        "group": "development",
        "quality_only": True,
        "counts": {"stale_or_unavailable_native_book": 1},
        "conditional_windows": 1,
    }
    monkeypatch.setattr(
        cli,
        "capture_windows",
        lambda capture: iter(
            [("synthetic-w00001", rows, admission), ("synthetic-quality", [], quality)]
        ),
    )
    monkeypatch.setattr(cli, "funding_censuses", lambda index: ({}, FUNDING))

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("every registered policy is already provably flat in this window")

    monkeypatch.setattr(micro, "replay", unexpected)
    directory = tmp_path / "result"
    with PrivateOutput(directory) as output:
        result = cli.run_micro({"captures": [{"name": "synthetic"}]}, output)
    assert result["source_coverage"] == [quality]
    assert result["conditional_intact_windows"] is True
    assert result["development_selected_finalist"] is None
    assert result["unique_configurations"] == len(result["trials"]) == 8
    proof = json.loads(
        gzip.decompress((directory / "synthetic-w00001-flat-proof.json.gz").read_bytes())
    )
    assert proof["admission"] == admission
    assert len(proof["unchanged_accounts"]) == 9
    assert all(
        pair == {"base": 10_000.0, "stress": 10_000.0}
        for pair in proof["unchanged_accounts"].values()
    )
    assert json.loads((directory / "synthetic-quality.json").read_text()) == quality
    assert json.loads((directory / "synthetic-w00001-admission.json").read_text()) == admission
    assert len(list(directory.iterdir())) == 3
    for trial in [*result["trials"].values(), result["benchmarks"]["momentum"]]:
        for cost in ("base", "stress"):
            assert trial[cost]["complete_trades"] == 0
            assert trial[cost]["observed_hours"] == pytest.approx(333 / 3600)
            assert trial[cost]["target_attainment_count"] == 0
