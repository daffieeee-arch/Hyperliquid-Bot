"""One preregistered historical calendar experiment; no network or order client.

Reuse the harness's trades, cashflows, costs, statistics and splits. The extra
paired-control gate precedes any financial read of the exposed historical test.
"""

from __future__ import annotations

import argparse
import bisect
import calendar
import hashlib
import json
import math
import os
import re
import resource
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from hyperliquid_bot.local_mode import require_local_paper_mode
from research.btc_flow import require
from research.btc_flow_io import PrivateOutput, _directory, read_bytes, sealed_parquet
from research.harness.benchmark import buy_and_hold
from research.harness.data import BarTable
from research.harness.evaluate import (
    MetricBlock,
    Trade,
    TradeSeries,
    collect_trades,
    decide_source,
    summarize,
    trade_series,
)
from research.harness.spec import (
    UNIT_SIZING,
    ConfigSpec,
    HypothesisSpec,
    load_document,
    spec_sha256,
    validate_spec,
)
from research.harness.splits import walk_forward

HOUR = 3_600_000
START = 1_577_836_800_000
END = 1_788_220_800_000
TEST_START = 1_735_689_600_000
ROWS = 58_440
STRESSES = (1.0, 1.5, 2.0)
STUDY = "btc-month-turn-calendar-proxy-v1"
SPEC_PATH = "docs/experiments/exp_btc_month_turn_proxy.spec.yaml"
SHA = re.compile(r"[0-9a-f]{64}")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def document(content: bytes) -> dict[str, object]:
    value = json.loads(content, object_pairs_hook=_unique)
    require(isinstance(value, dict), "JSON object required")
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class Part:
    path: Path
    sha256: str

    def read(self, maximum: int = 4 << 20) -> bytes:
        raw = read_bytes(self.path, maximum)
        require(digest(raw) == self.sha256, "approved source hash changed")
        return raw


def _parts(value: object) -> tuple[Part, ...]:
    require(isinstance(value, list), "part list required")
    output: list[Part] = []
    for item in cast(list[object], value):
        require(isinstance(item, dict), "part object required")
        row = cast(dict[str, object], item)
        require(set(row) == {"path", "sha256"}, "exact part fields required")
        path, expected = row["path"], row["sha256"]
        require(isinstance(path, str) and isinstance(expected, str), "part identity strings")
        path, expected = cast(str, path), cast(str, expected)
        require(
            Path(path).is_absolute()
            and ".." not in Path(path).parts
            and not any(char in path for char in "*?[]{}")
            and SHA.fullmatch(expected) is not None,
            "literal approved path/hash required",
        )
        output.append(Part(Path(path), expected))
    require(len({part.path for part in output}) == len(output), "duplicate source path")
    return tuple(output)


@dataclass(frozen=True, slots=True)
class Inputs:
    bars: tuple[Part, ...]
    funding: tuple[Part, ...]
    qualification: tuple[Part, ...]

    def verify(self) -> None:
        parts = (*self.bars, *self.funding, *self.qualification)
        total = 0
        for part in parts:
            total += len(part.read())
            require(total <= 64 << 20, "source byte cap")


def load_inputs(path: Path, expected: str) -> Inputs:
    raw = read_bytes(path, 1 << 20)
    require(digest(raw) == expected, "approved input manifest hash")
    data = document(raw)
    require(
        set(data)
        == {"format", "venue", "symbol", "start_ms", "end_ms", "bars", "funding", "qualification"}
        and type(data["format"]) is int
        and data["format"] == 1
        and data["venue"] == "binance-usdm"
        and data["symbol"] == "BTCUSDT"
        and type(data["start_ms"]) is int
        and data["start_ms"] == START
        and type(data["end_ms"]) is int
        and data["end_ms"] == END,
        "fixed complete Binance reference window required",
    )
    inputs = Inputs(_parts(data["bars"]), _parts(data["funding"]), _parts(data["qualification"]))
    names = tuple(
        f"BTCUSDT_{year:04}-{month:02}.parquet"
        for year in range(2020, 2027)
        for month in range(1, 13)
        if (year, month) <= (2026, 8)
    )
    require(
        tuple(part.path.name for part in inputs.bars) == names
        and tuple(part.path.name for part in inputs.funding) == names
        and len(inputs.qualification) == 3,
        "complete ordered monthly census required",
    )
    all_parts = (*inputs.bars, *inputs.funding, *inputs.qualification)
    require(len({part.path for part in all_parts}) == len(all_parts), "aliased input roles")
    inputs.verify()
    return inputs


def check_spec(path: Path) -> HypothesisSpec:
    spec = validate_spec(load_document(path))
    require(
        spec.hypothesis_id == STUDY
        and spec.alpha == 0.025
        and spec.direction == "long_only"
        and spec.signal_feature == "month_turn"
        and len(spec.configs) == 1
        and spec.configs[0].id == "calendar-long-96h"
        and spec.configs[0].threshold == 0.5
        and spec.configs[0].horizon_bars == 96
        and spec.costs.fee_bps == 5.0
        and spec.costs.slippage_bps == 1.0
        and spec.costs.spread_bps == 0.5
        and spec.costs.latency_bars == 1
        and spec.costs.funding_column == "funding_rate"
        and spec.sizing == UNIT_SIZING
        and spec.split.method == "expanding"
        and (spec.split.train_bars, spec.split.test_bars, spec.split.holdout_bars)
        == (8784, 8760, 14592)
        and (
            spec.sample.min_trades_validation,
            spec.sample.min_trades_holdout,
            spec.sample.min_folds,
        )
        == (36, 18, 4),
        "fixed preregistered calendar contract required",
    )
    return spec


def calendar_key(timestamp: int, *, control: bool) -> tuple[int, int] | None:
    require(timestamp % HOUR == HOUR - 1, "exact hourly close required")
    clock = datetime.fromtimestamp((timestamp + 1) // 1000, UTC)
    if clock.hour != 0:
        return None
    if control:
        return (clock.year, clock.month) if clock.day == 15 else None
    if clock.day != calendar.monthrange(clock.year, clock.month)[1]:
        return None
    target = clock + timedelta(days=1)
    return target.year, target.month


def calendar_pairs(
    timestamps: Sequence[int], start: int, end: int, config: ConfigSpec
) -> tuple[tuple[Trade, ...], tuple[Trade, ...]]:
    def trades(control: bool) -> tuple[Trade, ...]:
        return collect_trades(
            tuple(float(calendar_key(ts, control=control) is not None) for ts in timestamps),
            threshold=0.5,
            horizon_bars=config.horizon_bars,
            latency_bars=1,
            direction="long_only",
            start=start,
            end=end,
        )

    primary = trades(False)
    controls = {
        calendar_key(timestamps[trade.decision], control=True): trade for trade in trades(True)
    }
    paired: list[Trade] = []
    for trade in primary:
        key = calendar_key(timestamps[trade.decision], control=False)
        require(key in controls, "required calendar control outside known stage")
        paired.append(controls[key])
    return primary, tuple(paired)


@dataclass(frozen=True, slots=True)
class Funding:
    time: int
    rate: float


def funding_buckets(timestamps: Sequence[int], events: Sequence[Funding]) -> tuple[float, ...]:
    buckets: list[list[float]] = [[] for _ in timestamps]
    for event in events:
        index = bisect.bisect_left(timestamps, event.time)
        require(
            index < len(timestamps) and timestamps[index] - HOUR < event.time <= timestamps[index],
            "native funding event outside staged hourly coverage",
        )
        require(math.isfinite(event.rate), "finite funding rate required")
        buckets[index].append(event.rate)
    return tuple(math.fsum(values) for values in buckets)


@dataclass(frozen=True, slots=True)
class Stage:
    table: BarTable
    events: tuple[Funding, ...]


def clock_census(inputs: Inputs) -> dict[str, int]:
    closes: list[int] = []
    for part in inputs.bars:
        with sealed_parquet(part.path, part.sha256) as (connection, bound):
            rows = cast(
                list[tuple[str, int, int]],
                connection.execute(
                    "SELECT symbol, epoch_ms(open_time), epoch_ms(close_time) FROM read_parquet(?)",
                    [bound],
                ).fetchall(),
            )
        for symbol, opened, closed in rows:
            require(symbol == "BTCUSDT" and closed == opened + HOUR - 1, "bar clock/asset")
            closes.append(closed)
    require(closes == list(range(START + HOUR - 1, END, HOUR)), "complete unique hourly grid")
    times: list[int] = []
    for part in inputs.funding:
        with sealed_parquet(part.path, part.sha256) as (connection, bound):
            events = cast(
                list[tuple[str, int, int]],
                connection.execute(
                    "SELECT symbol, epoch_ms(funding_time), funding_interval_hours "
                    "FROM read_parquet(?)",
                    [bound],
                ).fetchall(),
            )
        for symbol, time, interval in events:
            require(
                symbol == "BTCUSDT" and interval == 8 and START <= time < END, "funding identity"
            )
            times.append(time)
    require(times == sorted(set(times)), "duplicate/regressing funding times")
    require(
        [time // (8 * HOUR) for time in times]
        == list(range(START // (8 * HOUR), END // (8 * HOUR))),
        "complete unique native eight-hour funding slots",
    )
    return {"hours": len(closes), "funding_events": len(times), "financial_prices_read": 0}


def _intersects(part: Part, start: int, end: int) -> bool:
    month = part.path.stem.removeprefix("BTCUSDT_")
    first = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first.timestamp() * 1000 < end and following.timestamp() * 1000 > start


def load_stage(inputs: Inputs, start: int, end: int) -> Stage:
    require(START <= start < end <= END and start % HOUR == end % HOUR == 0, "stage window")
    timestamps: list[int] = []
    prices: list[float] = []
    for part in inputs.bars:
        if not _intersects(part, start, end):
            continue
        with sealed_parquet(part.path, part.sha256) as (connection, bound):
            rows = cast(
                list[tuple[str, int, float]],
                connection.execute(
                    "SELECT symbol, epoch_ms(close_time), close FROM read_parquet(?) "
                    "WHERE epoch_ms(close_time)>=? AND epoch_ms(close_time)<?",
                    [bound, start, end],
                ).fetchall(),
            )
        for symbol, timestamp, price in rows:
            require(
                symbol == "BTCUSDT" and math.isfinite(price) and price > 0, "valid reference close"
            )
            timestamps.append(timestamp)
            prices.append(price)
    require(timestamps == list(range(start + HOUR - 1, end, HOUR)), "staged price continuity")
    events: list[Funding] = []
    for part in inputs.funding:
        if not _intersects(part, start, end):
            continue
        with sealed_parquet(part.path, part.sha256) as (connection, bound):
            rows_funding = cast(
                list[tuple[str, int, float]],
                connection.execute(
                    "SELECT symbol, epoch_ms(funding_time), funding_rate FROM read_parquet(?) "
                    "WHERE epoch_ms(funding_time)>=? AND epoch_ms(funding_time)<?",
                    [bound, start, end],
                ).fetchall(),
            )
        for symbol, timestamp, rate in rows_funding:
            require(symbol == "BTCUSDT" and math.isfinite(rate), "valid native funding")
            events.append(Funding(timestamp, rate))
    clock = tuple(timestamps)
    return Stage(
        BarTable(
            clock,
            tuple(prices),
            {
                "month_turn": tuple(
                    float(calendar_key(ts, control=False) is not None) for ts in clock
                )
            },
            {"available_ts": clock},
            funding_buckets(clock, events),
        ),
        tuple(events),
    )


def _series(stage: Stage, trades: tuple[Trade, ...]) -> TradeSeries:
    for trade in trades:
        for event in stage.events:
            bucket = bisect.bisect_left(stage.table.timestamps, event.time)
            require(
                (
                    stage.table.timestamps[trade.entry]
                    < event.time
                    <= stage.table.timestamps[trade.exit]
                )
                == (trade.entry < bucket <= trade.exit),
                "native funding exposure differs from bucket membership",
            )
    return trade_series(
        trades, stage.table.prices, funding=stage.table.funding, sizing=UNIT_SIZING, vol=None
    )


def metrics(
    primary: Sequence[TradeSeries], control: Sequence[TradeSeries], spec: HypothesisSpec
) -> dict[str, dict[str, MetricBlock]]:
    result: dict[str, dict[str, MetricBlock]] = {}
    require(len(primary) == len(control), "paired fold count")
    for stress in STRESSES:
        tom = [value for series in primary for value in series.net(spec.costs, stress)]
        mid = [value for series in control for value in series.net(spec.costs, stress)]
        require(len(tom) == len(mid), "paired observation count")
        result[f"{stress:.1f}"] = {
            "primary": summarize(tom),
            "control": summarize(mid),
            "paired_excess": summarize([a - b for a, b in zip(tom, mid, strict=True)]),
        }
    return result


def validation_passes(blocks: dict[str, dict[str, MetricBlock]], spec: HypothesisSpec) -> bool:
    return (
        set(blocks) == {"1.0", "1.5", "2.0"}
        and all(set(group) == {"primary", "control", "paired_excess"} for group in blocks.values())
        and all(
            block.trade_count >= spec.sample.min_trades_validation
            and block.mean_return is not None
            and math.isfinite(block.mean_return)
            and block.mean_return > 0
            and block.p_value is not None
            and math.isfinite(block.p_value)
            and 0 <= block.p_value
            and block.p_value <= spec.alpha
            for stress in blocks.values()
            for name, block in stress.items()
            if name in {"primary", "paired_excess"}
        )
    )


class CalendarSource:
    def __init__(self, spec: HypothesisSpec, loader: Callable[[int, int], Stage]) -> None:
        self.spec = spec
        self.length = ROWS
        self.loader = loader
        self.validation = loader(START, TEST_START)
        self.cache: dict[tuple[int, int], TradeSeries] = {}
        self.controls: list[TradeSeries] = []
        self.primary: list[TradeSeries] = []
        self.test: Stage | None = None
        self.test_control: TradeSeries | None = None
        self.test_primary: TradeSeries | None = None
        folds, _ = walk_forward(ROWS, spec.split)
        require(len(folds) >= spec.sample.min_folds, "validation fold floor")
        for fold in folds:
            tom, mid = calendar_pairs(
                self.validation.table.timestamps, fold.test_start, fold.test_end, spec.configs[0]
            )
            require(len(tom) == len(mid) == 11, "frozen validation calendar census")
            self.primary.append(_series(self.validation, tom))
            self.controls.append(_series(self.validation, mid))
            self.cache[(fold.test_start, fold.test_end)] = self.primary[-1]
        self.validation_metrics = metrics(self.primary, self.controls, spec)
        self.admitted_test = validation_passes(self.validation_metrics, spec)

    def window(self, config: ConfigSpec, start: int, end: int) -> TradeSeries:
        require(config == self.spec.configs[0], "single frozen configuration")
        if (start, end) in self.cache:
            return self.cache[(start, end)]
        require(
            self.admitted_test and (start, end) == (43848, ROWS) and self.test is None,
            "exposed historical test must be selection gated and read once",
        )
        self.test = self.loader(TEST_START, END)
        tom, mid = calendar_pairs(self.test.table.timestamps, 0, 14592, config)
        require(len(tom) == len(mid) == 19, "frozen historical-test calendar census")
        self.test_primary = _series(self.test, tom)
        self.test_control = _series(self.test, mid)
        return self.test_primary


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "forbidden"
                if key == "promotion_decision"
                else "historical_internal_" + item
                if key == "label" and isinstance(item, str) and not item.startswith("historical_")
                else _sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"\buntouched\s+(?:holdout|OOS)\b",
            "exposed historical test",
            value,
            flags=re.IGNORECASE,
        )
    return value


def evaluate(
    spec: HypothesisSpec,
    loader: Callable[[int, int], Stage],
    *,
    freeze_validation: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    source = CalendarSource(spec, loader)
    payload: dict[str, object] = {
        "status": "completed",
        "label": "historical_no_edge",
        "promotion_decision": "forbidden",
        "historical_exploratory": True,
        "future_oos_assigned": False,
        "hyperliquid_execution_qualified": False,
        "funding_valuation": "conditional_native_event_hourly_trade_close",
        "cost_model": "flat_13bps_roundtrip_on_unit_entry_notional; not_exit_notional_fee",
        "sizing_model": "unit_entry_notional; fixed_BTC_quantity_per_trade=1/own_entry_close",
        "validation": _sanitize(asdict_blocks(source.validation_metrics)),
        "historical_test": {"state": "sealed", "financial_read": False},
        "cash_benchmark": 0.0,
    }
    folds, _ = walk_forward(ROWS, spec.split)
    payload["validation_folds"] = [
        {
            "index": fold.index,
            "first_close_ms": source.validation.table.timestamps[fold.test_start],
            "last_close_ms": source.validation.table.timestamps[fold.test_end - 1],
            "pairs": len(series.gross),
        }
        for fold, series in zip(folds, source.primary, strict=True)
    ]
    validation_benchmark = buy_and_hold(
        spec.costs, source.validation.table, folds[0].test_start, folds[-1].test_end
    )
    payload["buy_and_hold"] = {
        "validation": None if validation_benchmark is None else asdict(validation_benchmark),
        "historical_test": {"state": "sealed"},
    }
    if freeze_validation is not None:
        freeze_validation(
            {
                "historical_exploratory": True,
                "promotion_decision": "forbidden",
                "admitted_test": source.admitted_test,
                "historical_test_financial_read": False,
                "validation": payload["validation"],
                "folds": payload["validation_folds"],
                "buy_and_hold": payload["buy_and_hold"],
            }
        )
    if not source.admitted_test:
        return payload
    decision = decide_source(source)
    payload["decision"] = _sanitize(asdict(decision))
    if source.test is not None:
        require(source.test_primary is not None and source.test_control is not None, "paired test")
        blocks = metrics(
            [cast(TradeSeries, source.test_primary)],
            [cast(TradeSeries, source.test_control)],
            spec,
        )
        excess_passes = all(
            block["paired_excess"].trade_count >= spec.sample.min_trades_holdout
            and block["paired_excess"].mean_return is not None
            and block["paired_excess"].mean_return > 0
            for block in blocks.values()
        )
        payload["historical_test"] = {
            "state": "evaluated_exposed_history",
            "financial_read": True,
            "metrics": asdict_blocks(blocks),
        }
        historical_benchmark = buy_and_hold(spec.costs, source.test.table, 0, 14592)
        payload["buy_and_hold"] = {
            "validation": None if validation_benchmark is None else asdict(validation_benchmark),
            "historical_test": None
            if historical_benchmark is None
            else asdict(historical_benchmark),
        }
        if decision.label == "passes_h1" and excess_passes:
            payload["label"] = "historical_interesting_but_unconfirmed"
    payload["benchmark_note"] = (
        "Buy-and-hold is descriptive; the matched-control gate is mandatory."
    )
    return payload


def asdict_blocks(blocks: dict[str, dict[str, MetricBlock]]) -> dict[str, object]:
    return {
        stress: {name: asdict(block) for name, block in group.items()}
        for stress, group in blocks.items()
    }


def persist_validation(output: PrivateOutput, evidence: dict[str, object]) -> None:
    """Commit validation and both directory entries before any test load."""
    output.json("validation.json", _sanitize(evidence))
    os.fsync(output.fd)
    parent = _directory(output.path.parent)
    try:
        output.assert_bound()
        os.fsync(parent)
        output.assert_bound()
    finally:
        os.close(parent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--review-go", required=True, type=Path)
    parser.add_argument("--review-go-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        require_local_paper_mode(os.environ.get("TRADING_MODE"))
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        cap = min(x for x in (soft, hard, 8 << 30) if x != resource.RLIM_INFINITY)
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        repo = Path(__file__).absolute().parents[2]
        git = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repo),
        ]
        require(
            subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
            == args.source_commit
            and not subprocess.check_output(
                [*git, "status", "--porcelain", "--untracked-files=all"]
            ),
            "clean reviewed source required",
        )
        subprocess.run(
            [*git, "merge-base", "--is-ancestor", args.source_commit, "origin/main"], check=True
        )
        spec_path = repo / SPEC_PATH
        spec_bytes = read_bytes(spec_path, 64 << 10)
        require(
            spec_bytes
            == subprocess.check_output([*git, "show", f"{args.source_commit}:{SPEC_PATH}"]),
            "committed study spec required",
        )
        spec = check_spec(spec_path)
        go_bytes = read_bytes(args.review_go, 16 << 10)
        require(digest(go_bytes) == args.review_go_sha256, "approved operator GO hash")
        go = document(go_bytes)
        require(
            set(go)
            == {
                "study",
                "source_commit",
                "inputs_sha256",
                "spec_sha256",
                "output",
                "review_sha256",
                "data_acceptance_sha256",
                "explicit_go",
            }
            and go["study"] == STUDY
            and go["source_commit"] == args.source_commit
            and go["inputs_sha256"] == args.inputs_sha256
            and go["spec_sha256"] == spec_sha256(load_document(spec_path))
            and go["output"] == str(args.output)
            and go["explicit_go"] is True
            and all(
                isinstance(go[key], str) and SHA.fullmatch(cast(str, go[key]))
                for key in ("review_sha256", "data_acceptance_sha256")
            ),
            "exact independently reviewed execution binding required",
        )
        inputs = load_inputs(args.inputs, args.inputs_sha256)
        inputs_bytes = read_bytes(args.inputs, 1 << 20)
        require(digest(inputs_bytes) == args.inputs_sha256, "stable admitted input manifest")
        with PrivateOutput(args.output) as output:
            output.write("spec.yaml", spec_bytes)
            output.write("inputs.json", inputs_bytes)
            output.json("operator-go.json", go)
            output.json(
                "input-lock.json",
                {
                    "inputs_sha256": args.inputs_sha256,
                    "source_commit": args.source_commit,
                    "spec_sha256": go["spec_sha256"],
                },
            )
            try:
                census = clock_census(inputs)
                payload = evaluate(
                    spec,
                    lambda start, end: load_stage(inputs, start, end),
                    freeze_validation=lambda evidence: persist_validation(
                        output,
                        {
                            **evidence,
                            "source_commit": args.source_commit,
                            "inputs_sha256": args.inputs_sha256,
                            "spec_sha256": go["spec_sha256"],
                            "operator_go_sha256": args.review_go_sha256,
                        },
                    ),
                )
                inputs.verify()
                require(
                    digest(read_bytes(args.inputs, 1 << 20)) == args.inputs_sha256
                    and read_bytes(spec_path, 64 << 10) == spec_bytes
                    and read_bytes(args.review_go, 16 << 10) == go_bytes,
                    "study inputs/spec/operator binding changed during evaluation",
                )
                payload.update(
                    {
                        "source_commit": args.source_commit,
                        "inputs_sha256": args.inputs_sha256,
                        "clock_census": census,
                        "mode": "PAPER",
                        "venue": "binance-usdm",
                        "source_environment": "DEV",
                        "image_digest": None,
                    }
                )
                output.json("result.json", _sanitize(payload))
            except Exception:
                output.json(
                    "result.json",
                    {
                        "status": "failed_closed",
                        "promotion_decision": "forbidden",
                        "historical_exploratory": True,
                    },
                )
                raise
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "label": payload["label"],
                    "promotion_decision": "forbidden",
                }
            )
        )
        return 0
    except Exception:
        print("Historical calendar study failed closed; private artifacts retained.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
