"""Run the preregistered BTC flow study from explicit hash-bound raw parts.

Never opens an archived DuckDB database or executes copied catalog SQL. Raw
financial fields are read only when this explicit research command is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb

from hyperliquid_bot.binance_spot_trades import BinanceTimestampUnit, decode_binance_spot_trade
from hyperliquid_bot.local_mode import require_local_paper_mode
from research.btc_flow import MINUTE, SECOND, FlowSource, Quote, SpotTrade, aggregate_flow, require
from research.harness.errors import HarnessError
from research.harness.evaluate import decide_source, summarize
from research.harness.spec import (
    HypothesisSpec,
    Json,
    load_document,
    spec_sha256,
    validate_spec,
    write_lock,
)

STUDY_ID = "btc-spot-flow-confirmed-hl-continuation-v1"
MAX_QUOTES = 1_000_000
MAX_TRADES = 25_000_000
MAX_PARTS = 10_000
MAX_SOURCE_BYTES = 100 << 30
DAY = 24 * 60 * MINUTE


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, object]:
    value = json.loads(
        raw, object_pairs_hook=_unique, parse_constant=lambda _: require(False, "nonfinite JSON")
    )
    require(isinstance(value, dict), "JSON object required")
    return cast(dict[str, object], value)


def _read(path: Path, maximum: int) -> bytes:
    require(path.is_absolute() and ".." not in path.parts, "absolute source path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, name in enumerate(path.parts[1:]):
            nxt = os.open(
                name,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | (0 if index == len(path.parts) - 2 else os.O_DIRECTORY),
                dir_fd=fd,
            )
            os.close(fd)
            fd = nxt
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_size <= maximum, "regular source byte limit"
        )
        content = bytearray()
        while block := os.read(fd, 1 << 20):
            content.extend(block)
            require(len(content) <= maximum, "source byte limit")
        after = os.fstat(fd)
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "source changed while reading",
        )
        return bytes(content)
    finally:
        os.close(fd)


def validate_manifest(document: dict[str, object]) -> tuple[int, int, str]:
    require(
        set(document)
        == {
            "format",
            "phase",
            "start_ns",
            "end_ns",
            "preregistration_commit",
            "preregistration_merged_ns",
            "parts",
        },
        "input manifest keys",
    )
    require(
        document["format"] == 1 and document["phase"] in ("exploratory", "prospective"),
        "input manifest version/phase",
    )
    start, end = document["start_ns"], document["end_ns"]
    require(type(start) is int and type(end) is int, "integer window clocks")
    start, end = cast(int, start), cast(int, end)
    require(
        start % MINUTE == end % MINUTE == 0 and 0 < end - start <= 3 * DAY, "bounded minute window"
    )
    if document["phase"] == "prospective":
        merged = document["preregistration_merged_ns"]
        require(type(merged) is int and merged > 0, "prospective merge clock")
        expected = ((cast(int, merged) + DAY + DAY - 1) // DAY) * DAY
        require(start == expected and end == start + 3 * DAY, "fresh fixed prospective window")
        commit = document["preregistration_commit"]
        require(
            isinstance(commit, str)
            and len(commit) == 40
            and all(c in "0123456789abcdef" for c in commit),
            "prospective merge identity",
        )
    else:
        require(
            document["preregistration_commit"] is None
            and document["preregistration_merged_ns"] is None,
            "exploratory data is not fresh OOS",
        )
    return start, end, cast(str, document["phase"])


def exact_part_path(value: str) -> Path:
    require(
        Path(value).is_absolute()
        and ".." not in Path(value).parts
        and not any(char in value for char in "*?[]{}"),
        "exact part paths required; globs refused",
    )
    return Path(value)


def checked_paths(document: dict[str, object]) -> tuple[list[str], dict[str, str]]:
    parts = document["parts"]
    require(isinstance(parts, list) and 0 < len(parts) <= MAX_PARTS, "raw part count")
    hashes: dict[str, str] = {}
    total = 0
    for item in cast(list[dict[str, object]], parts):
        require(set(item) == {"path", "sha256"}, "raw part fields")
        path, expected = item["path"], item["sha256"]
        require(
            isinstance(path, str) and path not in hashes and isinstance(expected, str),
            "raw part identity",
        )
        path, expected = cast(str, path), cast(str, expected)
        content = _read(exact_part_path(path), 512 << 20)
        total += len(content)
        require(total <= MAX_SOURCE_BYTES, "raw input total byte limit")
        digest = hashlib.sha256(content).hexdigest()
        require(digest == expected, "raw part hash mismatch")
        hashes[path] = digest
    return sorted(hashes), hashes


def receipt_freshness(received_ns: int, event_ns: int) -> None:
    # Event clocks validate freshness only; features still become available at
    # receipt. Stale frames invalidate the study, never disappear from its PnL.
    require(-SECOND <= received_ns - event_ns <= 5 * SECOND, "stale or future source event")


def load_source(
    spec: HypothesisSpec,
    start: int,
    end: int,
    paths: list[str],
) -> FlowSource:
    """Read original economic frames once; local normalization markers never count."""
    require(bool(paths) and paths == sorted(set(paths)), "closed raw part list")
    for path in paths:
        exact_part_path(path)
    connection = duckdb.connect(
        config={
            "threads": 2,
            "memory_limit": "2GB",
            "max_temp_directory_size": "0B",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
        }
    )
    quotes: list[Quote] = []
    hl_gaps: list[int] = []
    bn_gaps: list[int] = []

    def trades() -> Iterator[SpotTrade]:
        count = 0
        clocks: dict[tuple[str, str, str], tuple[int, int]] = {}
        connection.execute(
            "SELECT schema_version,venue,product,channel,session_id,message_ordinal,"
            "received_utc_ns,received_monotonic_ns,direction,payload_bytes,payload_sha256 "
            "FROM read_parquet(?) "
            "WHERE received_utc_ns>=? AND received_utc_ns<? AND "
            "((venue='hyperliquid' AND product='BTC-PERP' "
            "AND channel='bbo' AND direction='inbound') "
            "OR (venue='binance' AND product='BTCUSDT-SPOT' "
            "AND channel='spot_trade' AND direction='inbound') "
            "OR (venue IN ('hyperliquid','binance') "
            "AND channel='data_quality' AND direction='local')) "
            "ORDER BY session_id,message_ordinal",
            [paths, start, end],
        )
        while batch := connection.fetchmany(10_000):
            for (
                version,
                venue,
                product,
                channel,
                session,
                ordinal,
                when,
                monotonic,
                direction,
                payload,
                digest,
            ) in batch:
                require(
                    type(version) is int
                    and version == 1
                    and isinstance(payload, bytes)
                    and len(payload) <= 1 << 20,
                    "raw frame schema/bound",
                )
                require(hashlib.sha256(payload).hexdigest() == digest, "raw payload hash")
                record = _json(payload)
                if direction == "inbound":
                    key = (venue, session, channel)
                    require(type(when) is int and type(monotonic) is int, "source receipt clocks")
                    if key in clocks:
                        require(
                            when >= clocks[key][0] and monotonic >= clocks[key][1],
                            "receipt clock regression within inbound session/channel",
                        )
                    clocks[key] = (when, monotonic)
                require(
                    type(when) is int and isinstance(session, str) and type(ordinal) is int,
                    "raw metadata",
                )
                if channel == "data_quality":
                    event = record.get("event")
                    require(
                        isinstance(event, str)
                        and event
                        in {
                            "gap_detected",
                            "gap",
                            "liveness_gap",
                            "liveness_error",
                            "schema_error",
                            "truncation_error",
                            "sequence_error",
                            "snapshot_error",
                        },
                        "unknown or malformed quality marker",
                    )
                    (hl_gaps if venue == "hyperliquid" else bn_gaps).append(when)
                elif venue == "hyperliquid":
                    require(
                        record.get("channel") == "bbo" and isinstance(record.get("data"), dict),
                        "HL BBO payload",
                    )
                    data = cast(dict[str, object], record["data"])
                    coin = data.get("coin")
                    require(isinstance(coin, str) and bool(coin), "HL payload coin identity")
                    if coin != "BTC":
                        # Add-ons share the container product. Their prices are not this study.
                        continue
                    book = cast(list[dict[str, str]], data.get("bbo"))
                    require(
                        data.get("coin") == "BTC" and isinstance(book, list) and len(book) == 2,
                        "HL BBO identity",
                    )
                    require(
                        all(isinstance(v, str) for side in book for v in (side["px"], side["sz"])),
                        "HL decimal string fields",
                    )
                    require(type(data.get("time")) is int, "HL millisecond event clock")
                    receipt_freshness(when, cast(int, data["time"]) * 1_000_000)
                    require(
                        data.get("isSnapshot") is None or data.get("isSnapshot") is False,
                        "unexpected BBO snapshot flag",
                    )
                    quotes.append(
                        Quote(
                            when,
                            session,
                            ordinal,
                            Decimal(book[0]["px"]),
                            Decimal(book[1]["px"]),
                            Decimal(book[0]["sz"]),
                            Decimal(book[1]["sz"]),
                        )
                    )
                    require(len(quotes) <= MAX_QUOTES, "quote row limit")
                else:
                    require(
                        product == "BTCUSDT-SPOT" and direction == "inbound", "Spot family identity"
                    )
                    raw = record.get("data", record)
                    require(
                        isinstance(raw, dict)
                        and (raw.get("isSnapshot") is None or raw.get("isSnapshot") is False),
                        "Spot snapshots are not economic trades",
                    )
                    decoded = decode_binance_spot_trade(
                        raw, timestamp_unit=BinanceTimestampUnit.MICROSECONDS
                    )
                    require(decoded.symbol == "BTCUSDT", "Spot symbol")
                    receipt_freshness(when, decoded.trade_time * 1000)
                    count += 1
                    require(count <= MAX_TRADES, "Spot trade row limit")
                    yield SpotTrade(
                        when,
                        session,
                        decoded.trade_id,
                        decoded.price,
                        decoded.quantity,
                        decoded.buyer_was_maker,
                    )

    try:
        flow = aggregate_flow(trades())
    finally:
        connection.close()
    return FlowSource(
        spec,
        start,
        end,
        tuple(sorted(quotes, key=lambda q: (q.received_ns, q.ordinal))),
        flow,
        tuple(hl_gaps),
        tuple(bn_gaps),
    )


def study_spec(path: Path) -> tuple[dict[str, Json], HypothesisSpec]:
    document = load_document(path)
    spec = validate_spec(document)
    require(
        spec.hypothesis_id == STUDY_ID and spec.selection_method == "holm" and spec.alpha == 0.05,
        "fixed hypothesis",
    )
    require(
        sorted((c.threshold, c.horizon_bars) for c in spec.configs)
        == [(0.2, 1), (0.2, 5), (0.4, 1), (0.4, 5)],
        "fixed small grid",
    )
    require(
        (
            spec.costs.fee_bps,
            spec.costs.slippage_bps,
            spec.costs.spread_bps,
            spec.costs.latency_bars,
        )
        == (4.5, 1.0, 0.0, 1),
        "frozen quote cost assumptions",
    )
    require(
        (spec.split.train_bars, spec.split.test_bars, spec.split.holdout_bars) == (720, 720, 1440)
        and spec.split.method == "expanding",
        "fixed chronological split",
    )
    require(
        (spec.sample.min_trades_validation, spec.sample.min_trades_holdout, spec.sample.min_folds)
        == (30, 30, 3),
        "fixed sample floors",
    )
    return document, spec


def _output_directory(path: Path) -> None:
    require(path.is_absolute() and ".." not in path.parts, "absolute local output required")
    # No mutable durable NAS output: resolve every parent without symlinks and
    # require one of the supported local Linux filesystem types.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parent.parts[1:]:
            nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        info = Path(f"/proc/self/fdinfo/{fd}").read_text()
        mount = next(line.split()[1] for line in info.splitlines() if line.startswith("mnt_id:"))
        types = [
            line.partition(" - ")[2].split()[0]
            for line in Path("/proc/self/mountinfo").read_text().splitlines()
            if line.split()[0] == mount
        ]
        require(
            len(types) == 1 and types[0] in ("ext4", "xfs", "btrfs", "tmpfs"),
            "local research output filesystem",
        )
        os.mkdir(path.name, 0o700, dir_fd=fd)
    finally:
        os.close(fd)


def _result(path: Path, payload: dict[str, object]) -> None:
    fd = os.open(path / "result.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def run(
    spec_path: Path,
    manifest_path: Path,
    expected: str,
    output: Path,
    *,
    source_commit: str | None = None,
) -> dict[str, object]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    manifest_bytes = _read(manifest_path, 4 << 20)
    require(hashlib.sha256(manifest_bytes).hexdigest() == expected, "approved manifest hash")
    inputs = _json(manifest_bytes)
    start, end, phase = validate_manifest(inputs)
    spec_bytes = spec_path.read_bytes()
    document, spec = study_spec(spec_path)
    os.umask(0o077)
    _output_directory(output)
    # Metadata-only insufficient-span verdict: never lower floors or inspect prices.
    if end - start < 3 * DAY:
        insufficient: dict[str, object] = {
            "status": "not_enough_data",
            "reason": "72h fixed window required",
            "promotion_decision": "forbidden",
            "financial_frames_read": False,
            "source_manifest_sha256": expected,
            "source_commit": source_commit,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
        }
        _result(output, insufficient)
        return insufficient
    evaluation_started = False
    try:
        paths, hashes = checked_paths(inputs)
        source = load_source(spec, start, end, paths)
        evaluation_started = True
        decision = decide_source(source)
        payload: dict[str, object] = {
            "status": "completed",
            "source_commit": source_commit,
            "hypothesis_id": STUDY_ID,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
            "exploratory_only": phase == "exploratory",
            "decision": asdict(decision),
            "promotion_decision": "forbidden"
            if phase == "exploratory"
            else decision.promotion_decision,
            "execution_cost_model": (
                "observed two-sided quotes + both fill fees + adverse slippage; "
                "cost-vector stress1/1.5/2"
            ),
            "funding_model": (
                "no exposure inside hourly settlement guard; no missing settlement imputed"
            ),
            "flat_benchmark_net": 0.0,
            "source_manifest_sha256": expected,
            "source_part_sha256": hashes,
            "venue_orders_submitted": False,
            "deployment": False,
        }
        momentum = FlowSource(
            spec,
            start,
            end,
            source.quotes,
            source.flow,
            source.hl_gaps,
            source.bn_gaps,
            confirmed=False,
        )
        baseline: list[float] = []
        for fold in decision.folds:
            config = next(c for c in spec.configs if c.id == decision.primary_config_id)
            baseline.extend(
                momentum.window(config, fold.test_start, fold.test_end).net(spec.costs, 1.0)
            )
        payload["momentum_without_flow_validation_benchmark"] = asdict(summarize(baseline))
        if decision.selected_config_id is not None:
            selected = next(c for c in spec.configs if c.id == decision.selected_config_id)
            payload["momentum_without_flow_holdout_benchmark"] = asdict(
                summarize(
                    momentum.window(selected, decision.holdout_start, decision.holdout_end).net(
                        spec.costs, 1.0
                    )
                )
            )
        require(
            checked_paths(inputs)[1] == hashes and _read(manifest_path, 4 << 20) == manifest_bytes,
            "source mutation after study",
        )
        if phase == "exploratory":
            reported = cast(dict[str, object], payload["decision"])
            reported["label"] = "exploratory_" + decision.label
            reported["promotion_decision"] = "forbidden"
        local_spec = output / "spec.yaml"
        require(spec_path.read_bytes() == spec_bytes, "spec changed during study")
        local_spec.write_bytes(spec_bytes)
        write_lock(
            local_spec,
            cast(
                dict[str, Json], {"source_manifest_sha256": expected, "source_part_sha256": hashes}
            ),
        )
        _result(output, payload)
        return payload
    except HarnessError as error:
        failure: dict[str, object] = {
            "status": "failed_closed",
            "promotion_decision": "forbidden",
            "reason": "no valid complete study outcome; do not resume or reuse OOS",
            "holdout_scoring_may_have_been_attempted": evaluation_started,
            "failure_kind": error.failure_kind,
            "unknown_execution_outcomes_at_least": 1 if "BTC flow: unknown" in str(error) else 0,
            "source_manifest_sha256": expected,
            "source_commit": source_commit,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
        }
        _result(output, failure)
        return failure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        require_local_paper_mode(os.environ.get("TRADING_MODE"))
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
            == args.source_commit,
            "reviewed source commit",
        )
        require(
            not subprocess.check_output(
                [*git, "status", "--porcelain", "--untracked-files=all"], text=True
            ),
            "clean research source",
        )
        subprocess.run(
            [*git, "merge-base", "--is-ancestor", args.source_commit, "origin/main"],
            check=True,
            capture_output=True,
        )
        frozen_spec = subprocess.check_output(
            [*git, "show", args.source_commit + ":docs/experiments/exp_btc_spot_flow.spec.yaml"]
        )
        require(args.spec.read_bytes() == frozen_spec, "committed preregistration spec")
        inputs = _json(_read(args.inputs, 4 << 20))
        validate_manifest(inputs)
        if inputs["phase"] == "prospective":
            prereg = cast(str, inputs["preregistration_commit"])
            subprocess.run(
                [*git, "merge-base", "--is-ancestor", prereg, "origin/main"],
                check=True,
                capture_output=True,
            )
            require(
                subprocess.check_output(
                    [*git, "show", prereg + ":docs/experiments/exp_btc_spot_flow.spec.yaml"]
                )
                == frozen_spec,
                "prospective preregistration identity",
            )
            require(
                not subprocess.check_output(
                    [
                        *git,
                        "diff",
                        prereg,
                        args.source_commit,
                        "--",
                        "src/research",
                        "src/hyperliquid_bot/binance_spot_trades.py",
                        "src/hyperliquid_bot/paper_engine/precision.py",
                    ]
                ),
                "research code changed since preregistration",
            )
        result = run(
            args.spec,
            args.inputs,
            args.inputs_sha256,
            args.output,
            source_commit=args.source_commit,
        )
    except (
        HarnessError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
        duckdb.Error,
        ArithmeticError,
        subprocess.SubprocessError,
    ):
        print("BTC flow study failed closed; source/financial error details withheld.")
        return 2
    print(
        json.dumps({key: result[key] for key in ("status", "promotion_decision")}, sort_keys=True)
    )
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
