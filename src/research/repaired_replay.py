"""Bounded offline accounting replay for the recovered November research.

Inputs are explicit marked prices, funding coverage and execution targets.
This entrypoint consumes exported observations and never calls a venue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError, IntegrityError
from research.repaired_accounting import (
    Frame,
    Instrument,
    ReplaySpec,
    Target,
    select_scale,
    simulate,
)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise IntegrityError("repair_input", detail)


def mapping(value: object) -> dict[str, object]:
    require(isinstance(value, dict), "object required")
    assert isinstance(value, dict)
    require(all(isinstance(key, str) for key in value), "string keys required")
    return value


def sequence(value: object) -> list[object]:
    require(isinstance(value, list), "array required")
    assert isinstance(value, list)
    return value


def fields(row: dict[str, object], allowed: str) -> None:
    require(set(row) <= set(allowed.split()), "unknown schema field")


def text(value: object) -> str:
    require(isinstance(value, str) and bool(value), "nonempty string required")
    assert isinstance(value, str)
    return value


def number(value: object) -> float:
    require(type(value) in (int, float), "finite number required")
    assert isinstance(value, int | float)
    try:
        result = float(value)
    except OverflowError as error:
        raise IntegrityError("repair_input", "number outside finite range") from error
    require(math.isfinite(result), "finite number required")
    return result


def clock(value: object) -> int:
    require(type(value) is int and value >= 0, "nonnegative integer clock required")
    assert isinstance(value, int)
    return value


def values(value: object) -> dict[str, float]:
    return {key: number(item) for key, item in mapping(value).items()}


def _sources(body: dict[str, object], synthetic: bool) -> list[dict[str, str]]:
    sources = sequence(body.get("sources"))
    require(
        len(sources) <= 32 and (synthetic or bool(sources)), "bounded source identities required"
    )
    out: list[dict[str, str]] = []
    for item in sources:
        source = mapping(item)
        fields(source, "path sha256")
        path = Path(text(source.get("path")))
        expected = text(source.get("sha256"))
        require(bool(re.fullmatch(r"[a-f0-9]{64}", expected)), "SHA-256 required")
        actual = hashlib.sha256(read_bytes(path, 64 << 20)).hexdigest()
        require(actual == expected, "source hash mismatch")
        out.append({"path": str(path), "sha256": actual})
    return out


def _instruments(body: dict[str, object]) -> tuple[Instrument, ...]:
    out: list[Instrument] = []
    raw = sequence(body.get("instruments"))
    require(0 < len(raw) <= 100, "instrument count")
    for item in raw:
        row = mapping(item)
        fields(row, "symbol kind price_kind asset fee_bps slippage_bps")
        symbol = text(row.get("symbol"))
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,63}", symbol)), "symbol format")
        if "asset" in row:
            require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", text(row["asset"]))), "asset format")
        kind = text(row.get("kind"))
        require(kind in ("spot", "perp"), "spot or perp instrument required")
        price_kind = text(row.get("price_kind"))
        expected = {"spot": {"spot_close"}, "perp": {"perp_mark"}}
        require(price_kind in expected[kind], "actual leg prices required; premium proxies refused")
        assert kind in ("spot", "perp")
        out.append(
            Instrument(
                symbol,
                cast(Literal["spot", "perp"], kind),
                number(row.get("fee_bps")),
                number(row.get("slippage_bps")),
            )
        )
    return tuple(out)


def _carry_pairs(
    body: dict[str, object], spec: ReplaySpec, frames: list[Frame], targets: list[Target]
) -> None:
    pairs = sequence(body.get("pairs"))
    require(bool(pairs), "carry needs explicit spot/perp pairs")
    instruments = {item.symbol: item for item in spec.instruments}
    assets = {
        text(row.get("symbol")): text(row.get("asset"))
        for item in sequence(mapping(body.get("spec")).get("instruments"))
        for row in [mapping(item)]
    }
    symbols: set[str] = set()
    parsed: list[tuple[str, str]] = []
    for item in pairs:
        pair = mapping(item)
        fields(pair, "spot perp asset")
        spot, perp = text(pair.get("spot")), text(pair.get("perp"))
        require(spot not in symbols and perp not in symbols and spot != perp, "duplicate carry leg")
        require(spot in instruments and perp in instruments, "unknown carry leg")
        require(instruments[spot].kind == "spot" and instruments[perp].kind == "perp", "leg kind")
        asset = text(pair.get("asset"))
        require(assets[spot] == assets[perp] == asset, "carry underlying asset mismatch")
        symbols.update((spot, perp))
        parsed.append((spot, perp))
    require(symbols == set(instruments), "every carry instrument must belong to a pair")
    marks = {frame.timestamp_ms: frame.marks for frame in frames}
    for target in targets:
        require(target.replace_all, "carry requires a complete paired target")
        require(target.execution_ms in marks, "carry execution outside observed grid")
        for spot, perp in parsed:
            ws, wp = target.weights.get(spot, 0.0), target.weights.get(perp, 0.0)
            require(ws >= 0 and wp <= 0, "carry requires spot long and perp short")
            if ws == wp == 0:
                continue
            prices = marks[target.execution_ms]
            require(spot in prices and perp in prices, "both carry entry prices required")
            ps, pp = prices[spot], prices[perp]
            require(ps > 0 and pp > 0, "positive carry prices required")
            require(
                math.isclose(ws / ps, -wp / pp, rel_tol=1e-10, abs_tol=1e-12), "unequal hedge units"
            )


def _volume_windows(body: dict[str, object], spec: ReplaySpec, targets: list[Target]) -> None:
    windows = sequence(body.get("trade_windows"))
    expected: dict[tuple[int, str], str] = {}
    last_exit: dict[str, int] = {}
    for item in windows:
        row = mapping(item)
        fields(row, "symbol entry_ms exit_ms")
        symbol = text(row.get("symbol"))
        entry, exit_ms = clock(row.get("entry_ms")), clock(row.get("exit_ms"))
        require(spec.start_ms <= entry < exit_ms <= spec.end_ms, "trade crosses study boundary")
        require((entry - spec.start_ms) % spec.step_ms == 0, "entry off grid")
        require((exit_ms - spec.start_ms) % spec.step_ms == 0, "exit off grid")
        require(entry > last_exit.get(symbol, -1), "overlapping or ambiguous same-symbol trades")
        last_exit[symbol] = exit_ms
        expected[(entry, symbol)] = "entry"
        # The final frame closes every remaining position, including its fees.
        if exit_ms < spec.end_ms:
            expected[(exit_ms, symbol)] = "exit"
    observed: set[tuple[int, str]] = set()
    active: set[str] = set()
    for target in targets:
        require(not target.replace_all, "volume targets must preserve other open quantities")
        require(bool(target.weights), "empty volume execution")
        for symbol, weight in target.weights.items():
            event = (target.execution_ms, symbol)
            require(event in expected and event not in observed, "undeclared volume execution")
            observed.add(event)
            entering = expected[event] == "entry"
            require((weight != 0) == entering, "volume entry/exit target mismatch")
            if entering:
                require(symbol not in active, "overlapping volume execution")
                active.add(symbol)
            else:
                require(symbol in active, "volume exit precedes entry")
                active.remove(symbol)
        require(len(active) <= 10, "volume position count exceeds declared ten slots")
    require(observed == set(expected), "missing declared volume execution")


def replay_document(body: dict[str, object]) -> dict[str, object]:
    fields(
        body,
        "schema_version purpose study_id study_kind data_status source_commit sources "
        "minimum_latency_ms spec frames targets pairs trade_windows scale calibration",
    )
    require(
        type(body.get("schema_version")) is int and body["schema_version"] == 1, "schema version"
    )
    require(body.get("purpose") == "retrospective_repair", "retrospective repair purpose required")
    study = text(body.get("study_id"))
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", study)), "study identifier")
    kind = text(body.get("study_kind"))
    require(kind in ("positions", "volume", "hedged_carry"), "study kind")
    require("pairs" not in body or kind == "hedged_carry", "unexpected carry section")
    require("trade_windows" not in body or kind == "volume", "unexpected trade section")
    require(body.get("data_status") in ("synthetic", "development_exposed"), "data exposure status")
    synthetic = body["data_status"] == "synthetic"
    sources = _sources(body, synthetic)
    raw_spec = mapping(body.get("spec"))
    fields(raw_spec, "start_ms end_ms step_ms initial_equity instruments")
    spec = ReplaySpec(
        clock(raw_spec.get("start_ms")),
        clock(raw_spec.get("end_ms")),
        clock(raw_spec.get("step_ms")),
        number(raw_spec.get("initial_equity")),
        _instruments(raw_spec),
    )
    require(spec.step_ms > 0 and spec.end_ms > spec.start_ms, "positive replay clock interval")
    raw_frames = sequence(body.get("frames"))
    require(2 <= len(raw_frames) <= 100_000, "bounded observation count")
    frames: list[Frame] = []
    for item in raw_frames:
        row = mapping(item)
        fields(row, "timestamp_ms marks funding_rates")
        funding = {
            key: None if value is None else number(value)
            for key, value in mapping(row.get("funding_rates")).items()
        }
        frames.append(Frame(clock(row.get("timestamp_ms")), values(row.get("marks")), funding))
    raw_targets = sequence(body.get("targets"))
    require(len(raw_targets) <= len(frames), "bounded execution count")
    latency = clock(body.get("minimum_latency_ms"))
    require(latency > 0, "positive observation-to-execution latency required")
    targets: list[Target] = []
    for item in raw_targets:
        row = mapping(item)
        fields(row, "decision_ms execution_ms weights replace_all")
        decision, execution = clock(row.get("decision_ms")), clock(row.get("execution_ms"))
        require(execution - decision >= latency, "decision/execution latency")
        replace_all = row.get("replace_all", True)
        require(type(replace_all) is bool, "boolean replace_all required")
        assert isinstance(replace_all, bool)
        targets.append(Target(decision, execution, values(row.get("weights")), replace_all))
    if kind == "volume":
        _volume_windows(body, spec, targets)
    if kind == "hedged_carry":
        _carry_pairs(body, spec, frames, targets)
    calibration = body.get("calibration")
    scale = number(body.get("scale", 1.0))
    evaluations: object = None
    if calibration is None:
        result = simulate(spec, frames, targets, scale)
    else:
        require("scale" not in body, "choose fixed scale or historical calibration")
        raw = mapping(calibration)
        fields(raw, "stage candidates max_drawdown")
        require(raw.get("stage") == "development", "calibration restricted to development")
        candidates = sequence(raw.get("candidates"))
        require(0 < len(candidates) <= 32, "bounded scale candidate count")
        selection = select_scale(
            spec,
            frames,
            targets,
            [number(value) for value in candidates],
            number(raw.get("max_drawdown")),
        )
        scale, result, evaluations = selection.scale, selection.result, selection.evaluations
    # Recheck every external binding before admitting a result artifact.
    require(sources == _sources(body, synthetic), "source identity changed")
    assessment: Literal["accounting_only", "blocked_margin_model"] = (
        "blocked_margin_model" if kind == "hedged_carry" else "accounting_only"
    )
    return {
        "schema_version": 1,
        "study_id": study,
        "study_kind": kind,
        "data_status": body["data_status"],
        "status": "accounting_replay_completed",
        "promotion_decision": "forbidden",
        "economic_assessment": assessment,
        "scale": scale,
        "calibration_evaluations": evaluations,
        "risk_scope": "observed_grid_marked_equity",
        "sources": sources,
        "result": asdict(result),
        "limitations": [
            "Observed marks bound risk only at the admitted sampling times.",
            "Historical scale calibration provides no future drawdown guarantee.",
            "Fees/slippage are explicit assumptions; marks are not observed fills.",
            "Collateral, liquidation, financing and hedge transfer risks need admission.",
            "Recovered periods are exposed retrospective research.",
        ],
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        require(key not in out, "duplicate JSON key")
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        content = read_bytes(args.input.absolute(), 16 << 20)
        body = mapping(json.loads(content, object_pairs_hook=_unique_object))
        result = replay_document(body)
        result["input_sha256"] = hashlib.sha256(content).hexdigest()
        declared_commit = text(body.get("source_commit"))
        require(bool(re.fullmatch(r"[a-f0-9]{40}", declared_commit)), "source commit identity")
        result["declared_source_commit"] = declared_commit
        result["implementation_sha256"] = {
            name: hashlib.sha256(
                read_bytes(Path(__file__).with_name(name).absolute(), 1 << 20)
            ).hexdigest()
            for name in ("repaired_replay.py", "repaired_accounting.py")
        }
        with PrivateOutput(args.output.absolute()) as output:
            output.write("input.json", content)
            output.json("result.json", result)
            output.json(
                "completion.json",
                {"status": "complete", "input_sha256": result["input_sha256"]},
            )
        print("Accounting replay completed; artifacts at " + str(args.output))
        return 0
    except (HarnessError, OSError, ValueError) as error:
        print("Accounting replay refused: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
