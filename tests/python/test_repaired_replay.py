"""Exercise the normal offline repair entrypoint with bounded synthetic exports."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from research.harness.errors import IntegrityError
from research.repaired_replay import main, replay_document


def scenario() -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "retrospective_repair",
        "study_id": "fixture-positions",
        "study_kind": "positions",
        "data_status": "synthetic",
        "source_commit": "a" * 40,
        "sources": [],
        "minimum_latency_ms": 1,
        "spec": {
            "start_ms": 0,
            "end_ms": 300,
            "step_ms": 100,
            "initial_equity": 1000.0,
            "instruments": [
                {
                    "symbol": "X",
                    "kind": "perp",
                    "price_kind": "perp_mark",
                    "fee_bps": 0,
                    "slippage_bps": 0,
                }
            ],
        },
        "frames": [
            {"timestamp_ms": t, "marks": {"X": p}, "funding_rates": {"X": 0.0}}
            for t, p in ((0, 100.0), (100, 100.0), (200, 110.0), (300, 100.0))
        ],
        "targets": [{"decision_ms": 0, "execution_ms": 100, "weights": {"X": 1.0}}],
    }


def result(body: dict[str, object]) -> dict[str, object]:
    value = replay_document(body)["result"]
    assert isinstance(value, dict)
    return value


def test_cli_preserves_input_identity_and_fixed_quantity_roundtrip(tmp_path: Path) -> None:
    source, output = tmp_path / "input.json", tmp_path / "output"
    content = (Path(__file__).parent / "fixtures/repaired-replay-roundtrip.json").read_bytes()
    source.write_bytes(content)
    assert main(["--input", str(source), "--output", str(output)]) == 0
    report = json.loads((output / "result.json").read_bytes())
    assert report["result"]["net_return"] == 0
    assert report["result"]["final_equity"] == 1000
    assert report["input_sha256"] == hashlib.sha256(content).hexdigest()
    assert report["promotion_decision"] == "forbidden"
    assert (output / "input.json").read_bytes() == content
    assert (output / "completion.json").is_file()
    assert len(report["implementation_sha256"]) == 2
    assert main(["--input", str(source), "--output", str(output)]) == 2
    assert json.loads((output / "result.json").read_bytes()) == report


def test_marked_risk_observes_open_position_loss() -> None:
    body = scenario()
    frames = body["frames"]
    assert isinstance(frames, list)
    frames[2]["marks"]["X"] = 50
    assert result(body)["max_drawdown"] == 0.5
    assert result(body)["net_return"] == 0


@pytest.mark.parametrize("missing", [None, "absent"])
def test_held_funding_is_never_inferred_as_zero(missing: object) -> None:
    body = scenario()
    frames = body["frames"]
    assert isinstance(frames, list)
    frames[2]["funding_rates"] = {} if missing == "absent" else {"X": missing}
    with pytest.raises(IntegrityError):
        result(body)


def test_frame_gap_and_same_clock_execution_refused() -> None:
    body = scenario()
    frames = body["frames"]
    assert isinstance(frames, list)
    del frames[2]
    with pytest.raises(IntegrityError):
        result(body)
    body = scenario()
    body["minimum_latency_ms"] = 0
    with pytest.raises(IntegrityError, match="latency"):
        result(body)


def test_sources_are_bound_before_any_result(tmp_path: Path) -> None:
    body = scenario()
    source = tmp_path / "observations.csv"
    source.write_bytes(b"approved observations")
    body["data_status"] = "development_exposed"
    body["sources"] = [
        {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    ]
    assert result(body)["net_return"] == 0
    source.write_bytes(b"changed observations")
    with pytest.raises(IntegrityError, match="hash"):
        result(body)


def test_duplicate_json_keys_and_boolean_clocks_refused(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_text('{"schema_version":1,"schema_version":2}')
    assert main(["--input", str(source), "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()
    body = scenario()
    body["minimum_latency_ms"] = True
    with pytest.raises(IntegrityError):
        result(body)


def carry_scenario() -> dict[str, object]:
    body = scenario()
    body["study_kind"] = "hedged_carry"
    spec = body["spec"]
    assert isinstance(spec, dict)
    spec["instruments"] = [
        {
            "symbol": symbol,
            "kind": kind,
            "price_kind": price,
            "asset": "BTC",
            "fee_bps": 0,
            "slippage_bps": 0,
        }
        for symbol, kind, price in (("S", "spot", "spot_close"), ("P", "perp", "perp_mark"))
    ]
    body["pairs"] = [{"asset": "BTC", "spot": "S", "perp": "P"}]
    body["frames"] = [
        {"timestamp_ms": t, "marks": {"S": 100.0, "P": p}, "funding_rates": {"P": 0.0}}
        for t, p in ((0, 100.0), (100, 100.0), (200, 100.5), (300, 101.0))
    ]
    body["targets"] = [{"decision_ms": 0, "execution_ms": 100, "weights": {"S": 0.5, "P": -0.5}}]
    return body


def test_carry_uses_actual_separate_legs_and_retains_margin_block() -> None:
    body = carry_scenario()
    report = replay_document(body)
    assert report["economic_assessment"] == "blocked_margin_model"
    assert result(body)["net_return"] == pytest.approx(-0.005)


@pytest.mark.parametrize("change", ["premium", "asset", "quantity"])
def test_carry_proxy_or_unmatched_hedge_refused(change: str) -> None:
    body = carry_scenario()
    spec = body["spec"]
    assert isinstance(spec, dict)
    instruments = spec["instruments"]
    assert isinstance(instruments, list)
    if change == "premium":
        instruments[1]["price_kind"] = "premium_index"
    elif change == "asset":
        instruments[1]["asset"] = "ETH"
    else:
        body["targets"] = [
            {"decision_ms": 0, "execution_ms": 100, "weights": {"S": 0.5, "P": -0.4}}
        ]
    with pytest.raises(IntegrityError):
        result(body)


def volume_scenario() -> dict[str, object]:
    body = scenario()
    body["study_kind"] = "volume"
    body["trade_windows"] = [{"symbol": "X", "entry_ms": 100, "exit_ms": 300}]
    body["targets"] = [
        {"decision_ms": 0, "execution_ms": 100, "weights": {"X": 0.1}, "replace_all": False}
    ]
    return body


def test_volume_terminal_trade_is_accounted_and_crossing_trade_refused() -> None:
    body = volume_scenario()
    assert result(body)["net_return"] == 0
    windows = body["trade_windows"]
    assert isinstance(windows, list)
    windows[0]["exit_ms"] = 400
    with pytest.raises(IntegrityError, match="boundary"):
        result(body)


def test_volume_planned_exit_must_be_present() -> None:
    body = volume_scenario()
    body["trade_windows"] = [{"symbol": "X", "entry_ms": 100, "exit_ms": 200}]
    with pytest.raises(IntegrityError, match="missing declared"):
        result(body)


def test_invalid_volume_grid_and_exit_order_refused() -> None:
    body = volume_scenario()
    spec = body["spec"]
    assert isinstance(spec, dict)
    spec["step_ms"] = 0
    with pytest.raises(IntegrityError):
        result(body)
    body = volume_scenario()
    body["trade_windows"] = [{"symbol": "X", "entry_ms": 100, "exit_ms": 200}]
    body["targets"] = [
        {"decision_ms": 100, "execution_ms": 200, "weights": {"X": 0}, "replace_all": False},
        {"decision_ms": 0, "execution_ms": 100, "weights": {"X": 0.1}, "replace_all": False},
    ]
    with pytest.raises(IntegrityError):
        result(body)


def test_perpetual_close_cannot_replace_funding_settlement_mark() -> None:
    body = scenario()
    spec = body["spec"]
    assert isinstance(spec, dict)
    instruments = spec["instruments"]
    assert isinstance(instruments, list)
    instruments[0]["price_kind"] = "perp_close"
    with pytest.raises(IntegrityError, match="actual leg prices"):
        result(body)


def test_fixed_and_calibrated_paths_both_run_and_report_observed_risk() -> None:
    body = scenario()
    frames = body["frames"]
    assert isinstance(frames, list)
    frames[2]["marks"]["X"] = 50
    fixed = copy.deepcopy(body)
    fixed["scale"] = 0.4
    assert result(fixed)["max_drawdown"] == pytest.approx(0.2)
    body["calibration"] = {
        "stage": "development",
        "candidates": [0.0, 0.4, 1.0],
        "max_drawdown": 0.25,
    }
    report = replay_document(body)
    assert report["scale"] == 0.4
    assert result(body)["max_drawdown"] == pytest.approx(0.2)


@pytest.mark.parametrize("section", ["top", "spec", "instrument", "frame", "target"])
def test_unexpected_credential_metadata_refused(section: str) -> None:
    body = scenario()
    row: object
    if section == "top":
        row = body
    elif section == "spec":
        row = body["spec"]
    elif section == "instrument":
        row = body["spec"]["instruments"][0]  # type: ignore[index]
    elif section == "frame":
        row = body["frames"][0]  # type: ignore[index]
    else:
        row = body["targets"][0]  # type: ignore[index]
    assert isinstance(row, dict)
    row["api_key"] = "synthetic-review-value"
    with pytest.raises(IntegrityError, match="unknown schema"):
        result(body)


def test_excessive_calibration_grid_refused() -> None:
    body = scenario()
    body["calibration"] = {
        "stage": "development",
        "candidates": list(range(33)),
        "max_drawdown": 0.25,
    }
    with pytest.raises(IntegrityError, match="candidate count"):
        result(body)
