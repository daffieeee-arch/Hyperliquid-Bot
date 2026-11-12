"""One frozen, offline retrospective reconstruction of the hourly volume rule."""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import re
import zipfile
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.repaired_accounting import (
    Frame,
    Instrument,
    ReplayResult,
    ReplaySpec,
    Target,
    simulate,
)
from research.volume_repair_io import (
    DECEMBER,
    END,
    HOUR,
    MINUTE,
    START,
    STUDY_ID,
    SYMBOLS,
    Funding,
    Hour,
    Source,
    document,
    funding,
    klines,
    load_index,
    prepare_index,
    require,
    sha256,
)


@dataclass(frozen=True, slots=True, order=True)
class Window:
    entry_ms: int
    exit_ms: int
    symbol: str
    direction: int


def candidates(
    symbol: str, hours: list[Hour], start: int = START, end: int = END
) -> tuple[list[Window], dict[str, int]]:
    """Use preceding contiguous hours, never a return or later price, for selection."""
    counts = {"spikes": 0, "busy": 0, "boundary": 0}
    require(
        all(b.open_ms - a.open_ms == HOUR for a, b in pairwise(hours)),
        "contiguous feature history",
    )
    median_window: list[Fraction] = []
    out: list[Window] = []
    busy_until = -1
    for index, bar in enumerate(hours):
        if index >= 720 and start <= bar.open_ms < end:
            require(len(median_window) == 720, "previous 720 hours required")
            twice_median = median_window[359] + median_window[360]
            if twice_median > 0 and bar.quote_volume > 0:
                signed_volume = 2 * bar.taker_quote - bar.quote_volume
                # Exact native rational cross-products preserve both inclusive
                # boundaries, without float cancellation or Decimal rounding.
                if (
                    2 * bar.quote_volume >= 5 * twice_median
                    and 5 * abs(signed_volume) >= bar.quote_volume
                ):
                    counts["spikes"] += 1
                    decision = bar.open_ms + HOUR
                    entry, exit_ms = decision + HOUR, decision + 5 * HOUR
                    if decision <= busy_until:
                        counts["busy"] += 1
                    elif exit_ms > end:
                        counts["boundary"] += 1
                    else:
                        out.append(Window(entry, exit_ms, symbol, 1 if signed_volume > 0 else -1))
                        busy_until = exit_ms
        if index >= 720:
            old = hours[index - 720].quote_volume
            median_window.pop(bisect.bisect_left(median_window, old))
        bisect.insort(median_window, bar.quote_volume)
    return out, counts


def select_windows(windows: list[Window]) -> tuple[list[Window], int]:
    active: dict[str, int] = {}
    accepted: list[Window] = []
    refused = 0
    for window in sorted(windows):
        active = {symbol: until for symbol, until in active.items() if until > window.entry_ms}
        require(window.symbol not in active, "same-symbol holding overlap")
        if len(active) == 10:
            refused += 1
            continue
        active[window.symbol] = window.exit_ms
        accepted.append(window)
    return accepted, refused


def targets(windows: list[Window], start: int = START, end: int = END) -> list[Target]:
    schedule: dict[int, dict[str, float]] = {}
    for window in windows:
        require(start <= window.entry_ms < window.exit_ms <= end, "complete holding window")
        for instant, weight in ((window.entry_ms, window.direction * 0.1), (window.exit_ms, 0.0)):
            if instant == end:
                continue  # The ledger performs the charged terminal close.
            weights = schedule.setdefault(instant, {})
            require(window.symbol not in weights, "ambiguous same-symbol timestamp")
            weights[window.symbol] = weight
    return [
        Target(max(start, instant - HOUR), instant, weights, False)
        for instant, weights in sorted(schedule.items())
    ]


def frames(
    hours: dict[str, list[Hour]],
    minute_marks: dict[str, list[Hour]],
    settlements: dict[str, list[Funding]],
    start: int = START,
    end: int = END,
) -> list[Frame]:
    require(set(hours) == set(minute_marks) == set(settlements), "same input universe")
    symbols = tuple(sorted(hours))
    prices = {
        symbol: {bar.open_ms + HOUR: bar.close for bar in series}
        for symbol, series in hours.items()
    }
    mark_series: dict[str, dict[int, float]] = {}
    for symbol, series in minute_marks.items():
        require(
            all(bar.open_ms == start + index * MINUTE for index, bar in enumerate(series))
            and len(series) >= (end - start) // MINUTE,
            "complete minute marks before as-of lookup",
        )
        mark_series[symbol] = {bar.open_ms + MINUTE: bar.close for bar in series}
    events: dict[int, dict[str, Funding]] = {}
    for symbol, payments in settlements.items():
        seen: set[int] = set()
        for payment in payments:
            require(payment.timestamp_ms not in seen, "duplicate settlement")
            seen.add(payment.timestamp_ms)
            if start <= payment.timestamp_ms <= end:
                events.setdefault(payment.timestamp_ms, {})[symbol] = payment
    clock = sorted(set(range(start, end + 1, MINUTE)) | set(events))
    output: list[Frame] = []
    for instant in clock:
        asof = instant // MINUTE * MINUTE
        marks: dict[str, float] = {}
        for symbol in symbols:
            if asof in mark_series[symbol]:
                require(0 <= instant - asof <= MINUTE, "causal bounded mark age")
                marks[symbol] = mark_series[symbol][asof]
        # Complete native census has been checked before explicit zero intervals exist.
        rates: dict[str, float | None] = dict.fromkeys(symbols, 0.0)
        for symbol, payment in events.get(instant, {}).items():
            marks[symbol] = payment.mark
            rates[symbol] = payment.rate
        references = {
            symbol: prices[symbol][instant] for symbol in symbols if instant in prices[symbol]
        }
        output.append(Frame(instant, marks, rates, references))
    return output


def load_observations(
    sources: dict[tuple[str, str], Source],
) -> tuple[dict[str, list[Hour]], dict[str, list[Hour]], dict[str, list[Funding]]]:
    hours: dict[str, list[Hour]] = {}
    marks: dict[str, list[Hour]] = {}
    payments: dict[str, list[Funding]] = {}
    for symbol in SYMBOLS:

        def source(role: str, symbol: str = symbol) -> Source:
            return sources[symbol, role]

        hours[symbol] = klines(
            source("hourly_dec"), source("hourly_dec_checksum"), DECEMBER, HOUR, 744
        )
        hours[symbol] += klines(
            source("hourly_jan"), source("hourly_jan_checksum"), START, HOUR, 744
        )
        marks[symbol] = klines(
            source("mark_jan"), source("mark_jan_checksum"), START, MINUTE, 44_640
        )
        payments[symbol] = funding(
            source("funding_native"), source("funding_native_checksum"), source("funding_rest")
        )
    return hours, marks, payments


def summary(result: ReplayResult) -> dict[str, object]:
    day = 24 * HOUR
    endpoints = [point for point in result.points if point.timestamp_ms % day == 0]
    if result.points[-1] != endpoints[-1]:
        endpoints.append(result.points[-1])
    daily = [
        {"end_ms": after.timestamp_ms, "net_return": after.equity / before.equity - 1}
        for before, after in pairwise(endpoints)
    ]
    return {
        "initial_equity_usdt": result.initial_equity,
        "final_equity_usdt": result.final_equity,
        "net_return": result.net_return,
        "sampled_marked_max_drawdown": result.max_drawdown,
        "funding_cash_flow_usdt": result.total_funding,
        "fee_usdt": math.fsum(trade.fee for trade in result.trades),
        "adverse_slippage_usdt": math.fsum(trade.slippage_cost for trade in result.trades),
        "execution_count": len(result.trades),
        "daily_returns_from_equity_endpoints": daily,
        "cash_benchmark_net_return": 0.0,
    }


def replay(index: dict[str, object]) -> dict[str, object]:
    sources = load_index(index)
    hours, marks, payments = load_observations(sources)
    all_windows: list[Window] = []
    counts: dict[str, object] = {}
    for symbol in SYMBOLS:
        windows, excluded = candidates(symbol, hours[symbol], START, END)
        all_windows.extend(windows)
        counts[symbol] = excluded
    accepted, capacity_refused = select_windows(all_windows)
    observations = frames(hours, marks, payments, START, END)
    clock = tuple(frame.timestamp_ms for frame in observations)
    instruments = tuple(Instrument(symbol, "perp", 5, 2) for symbol in SYMBOLS)
    spec = ReplaySpec(START, END, MINUTE, 10_000.0, instruments, clock)
    executions = targets(accepted, START, END)
    base = simulate(spec, observations, executions)
    stress = simulate(
        replace(
            spec,
            instruments=tuple(replace(item, fee_bps=10, slippage_bps=4) for item in instruments),
        ),
        observations,
        executions,
    )
    for source in sources.values():
        source.read()  # A result is admitted only if all original bytes still match.
    return {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "data_status": "development_exposed",
        "purpose": "retrospective_repair",
        "promotion_decision": "forbidden",
        "economic_assessment": "accounting_only",
        "original_primary_equivalent": False,
        "universe": list(SYMBOLS),
        "start_ms": START,
        "end_ms": END,
        "scale": 1,
        "counts": {
            "per_symbol": counts,
            "candidate_windows": len(all_windows),
            "capacity_refused": capacity_refused,
            "accepted_windows": len(accepted),
            "native_funding_events": sum(map(len, payments.values())),
        },
        "sources": [source.record() for source in sources.values()],
        "windows": [asdict(window) for window in accepted],
        "base": summary(base),
        "double_cost_stress_same_windows": summary(stress),
        "ledger": asdict(base),
        "limitations": [
            "Exposed retrospective month; no untouched OOS, selection or promotion.",
            "Survivorship-biased universe minus excluded ICP differs from original primary.",
            "Sampled minute marks omit intraminute risk; trade closes are execution proxies.",
            "Fees, spread, depth, partial fills and collateral/liquidation remain unvalidated.",
            "January-only delayed-rule accounting does not establish a Hyperliquid edge.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    prepare = subcommands.add_parser(
        "prepare-index", help="bind source bytes without financial decoding"
    )
    prepare.add_argument("--archive", type=Path, required=True)
    prepare.add_argument("--acquisition", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = subcommands.add_parser("run")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--expected-input-sha256", required=True)
    run.add_argument("--source-commit", required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare-index":
            index = prepare_index(args.archive.absolute(), args.acquisition.absolute())
            with PrivateOutput(args.output.absolute()) as output:
                output.json("input.json", index)
                output.json("completion.json", {"status": "index_prepared_no_financial_decoding"})
            print("Source index prepared without financial decoding: " + str(args.output))
            return 0
        require(bool(re.fullmatch("[0-9a-f]{40}", args.source_commit)), "source commit identity")
        content = read_bytes(args.input.absolute(), 1 << 20)
        require(sha256(content) == args.expected_input_sha256, "operator-pinned input index")
        result = replay(document(content))
        require(read_bytes(args.input.absolute(), 1 << 20) == content, "input index changed")
        result["declared_source_commit"] = args.source_commit
        result["input_sha256"] = sha256(content)
        result["implementation_sha256"] = {
            name: sha256(read_bytes(Path(__file__).with_name(name).absolute(), 1 << 20))
            for name in ("volume_repair.py", "volume_repair_io.py", "repaired_accounting.py")
        }
        with PrivateOutput(args.output.absolute()) as output:
            output.write("input.json", content)
            output.json("result.json", result)
            output.json("completion.json", {"status": "complete", "input_sha256": sha256(content)})
        print("Retrospective accounting repair completed: " + str(args.output))
        return 0
    except (HarnessError, OSError, ValueError, KeyError, csv.Error, zipfile.BadZipFile) as error:
        print("Volume repair refused: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
