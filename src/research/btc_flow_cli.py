"""Run the preregistered BTC flow study from explicit hash-bound raw parts.

Never opens an archived DuckDB database or executes copied catalog SQL. Raw
financial fields are read only when this explicit research command is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from collections.abc import Iterator
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import duckdb

from hyperliquid_bot.binance_spot_trades import BinanceTimestampUnit, decode_binance_spot_trade
from hyperliquid_bot.local_mode import require_local_paper_mode
from research.btc_flow import (
    MINUTE,
    SECOND,
    CaptureWindow,
    FlowSource,
    Quote,
    SpotTrade,
    aggregate_flow,
    require,
)
from research.btc_flow_capture import qualify_capture
from research.btc_flow_io import PrivateOutput, sealed_parquet
from research.btc_flow_io import read_bytes as _read
from research.harness.errors import HarnessError
from research.harness.evaluate import TradeSeries, decide_source, summarize
from research.harness.spec import (
    ConfigSpec,
    HypothesisSpec,
    Json,
    load_document,
    spec_sha256,
    validate_spec,
)
from research.hist_etl.hyperliquid import slot_of

STUDY_ID = "btc-spot-flow-confirmed-hl-continuation-v1"
MAX_QUOTES = 2_000_000
MAX_TRADES = 25_000_000
MAX_PARTS = 70_000
MAX_SOURCE_BYTES = 100 << 30
MAX_MANIFEST_BYTES = 32 << 20
MAX_PROCESS_BYTES = 8 << 30
DAY = 24 * 60 * MINUTE
HOUR_MS = 3_600_000
FUNDING_MODEL = "hl-fundingHistory-api-clock-v1"


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
            "funding",
            "capture",
        },
        "input manifest keys",
    )
    require(
        type(document["format"]) is int
        and document["format"] == 3
        and document["phase"] in ("exploratory", "prospective"),
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
        require(set(item) == {"path", "sha256", "publication_sha256"}, "raw part fields")
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


def funding_timeline(
    reference: object, start: int, end: int
) -> tuple[tuple[int, ...], dict[str, str]]:
    """Qualify native timing metadata only; never interpret rates or premiums.

    Provenance clocks are assertions requiring independent acceptance before
    the manifest is approved. Hashes bind them; they do not authenticate them.
    """
    require(isinstance(reference, dict), "funding snapshot required")
    proof_ref = cast(dict[str, object], reference)
    require(set(proof_ref) == {"path", "sha256"}, "funding proof reference")

    def bound(ref: dict[str, object], maximum: int) -> tuple[dict[str, object], str, str]:
        path, expected = ref["path"], ref["sha256"]
        require(isinstance(path, str) and isinstance(expected, str), "funding file identity")
        path, expected = cast(str, path), cast(str, expected)
        raw = _read(exact_part_path(path), maximum)
        require(hashlib.sha256(raw).hexdigest() == expected, "funding file hash mismatch")
        return _json(raw), path, expected

    proof, path, digest = bound(proof_ref, 16 << 10)
    require(
        set(proof)
        == {
            "format",
            "model",
            "endpoint",
            "coin",
            "snapshot_cutoff_ns",
            "frozen_utc_ns",
            "known_holes",
            "sources",
        }
        and type(proof["format"]) is int
        and proof["format"] == 1
        and proof["model"] == FUNDING_MODEL
        and proof["endpoint"] == "https://api.hyperliquid.xyz/info"
        and proof["coin"] == "BTC"
        and proof["known_holes"] == [],
        "funding provenance/model without hole waivers required",
    )
    cutoff, frozen = proof["snapshot_cutoff_ns"], proof["frozen_utc_ns"]
    require(
        type(cutoff) is int
        and type(frozen) is int
        and end + 60 * MINUTE <= cutoff <= frozen <= time.time_ns(),
        "funding snapshot must be frozen after end plus padding and before scoring",
    )
    sources = proof["sources"]
    require(isinstance(sources, list) and 1 <= len(sources) <= 2, "funding month count")
    first = (start - 60 * MINUTE) // (HOUR_MS * 1_000_000)
    last = -(-(end + 60 * MINUTE) // (HOUR_MS * 1_000_000))
    required = set(range(first, last))
    events: dict[int, int] = {}
    hashes = {path: digest}
    for item in cast(list[object], sources):
        require(isinstance(item, dict), "funding source reference")
        ref = cast(dict[str, object], item)
        require(set(ref) == {"path", "sha256"}, "funding source keys")
        raw, source_path, source_hash = bound(ref, 1 << 20)
        require(source_path not in hashes, "duplicate funding source")
        hashes[source_path] = source_hash
        require(
            set(raw) == {"source", "coin", "start_ms", "end_ms", "rows"}
            and raw["source"] == "hyperliquid-info-fundingHistory"
            and raw["coin"] == "BTC",
            "native fundingHistory BTC snapshot required",
        )
        lo, hi, rows = raw["start_ms"], raw["end_ms"], raw["rows"]
        require(
            type(lo) is int
            and type(hi) is int
            and lo % HOUR_MS == hi % HOUR_MS == 0
            and lo < hi <= cast(int, cutoff) // 1_000_000
            and isinstance(rows, list)
            and 0 < len(rows) <= 800,
            "native funding window/row bound",
        )
        previous: int | None = None
        used = False
        for row in cast(list[dict[str, object]], rows):
            require(
                isinstance(row, dict)
                and set(row) == {"coin", "fundingRate", "premium", "time"}
                and row["coin"] == "BTC"
                and type(row["time"]) is int
                and isinstance(row["fundingRate"], str)
                and isinstance(row["premium"], str),
                "native funding row metadata schema",
            )
            when = cast(int, row["time"])
            slot = slot_of(when, HOUR_MS)
            require(
                (previous is None or previous < when)
                and cast(int, lo) // HOUR_MS <= slot < cast(int, hi) // HOUR_MS
                and when * 1_000_000 <= cast(int, cutoff),
                "native funding row ordering/window/cutoff",
            )
            previous = when
            if slot in required:
                require(slot not in events, "duplicate funding hour slot")
                events[slot] = when
                used = True
        require(used, "irrelevant funding month refused")
    require(set(events) == required, "complete funding hour slots including padding required")
    return tuple(sorted(events.values())), hashes


def receipt_freshness(received_ns: int, event_ns: int) -> None:
    # Event clocks validate freshness only; features still become available at
    # receipt. Stale frames invalidate the study, never disappear from its PnL.
    require(-SECOND <= received_ns - event_ns <= 5 * SECOND, "stale or future source event")


def _raw_rows(
    hashes: dict[str, str], start: int, end: int
) -> Iterator[tuple[str, tuple[Any, ...]]]:
    for path, expected in sorted(hashes.items()):
        with sealed_parquet(exact_part_path(path), expected) as (connection, bound):
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
                [[bound], start, end],
            )
            while batch := connection.fetchmany(10_000):
                for row in batch:
                    yield path, row


def load_source(
    spec: HypothesisSpec,
    start: int,
    end: int,
    hashes: dict[str, str],
    contexts: dict[tuple[str, str], CaptureWindow],
    *,
    funding_events_ms: tuple[int, ...] | None = None,
) -> FlowSource:
    """Read original economic frames once; local normalization markers never count."""
    require(0 < len(hashes) <= MAX_PARTS, "closed hash-bound raw part list")
    clocks: dict[tuple[str, str, str, str], list[int]] = {}
    quotes: list[Quote] = []
    hl_gaps: list[int] = []
    bn_gaps: list[int] = []
    sessions: dict[tuple[str, str], CaptureWindow] = {}
    bn_first_trade: dict[str, int] = {}

    def trades() -> Iterator[SpotTrade]:
        count = 0
        for part, row in _raw_rows(hashes, start, end):
            (
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
            ) = row
            require(
                type(version) is int
                and version == 1
                and isinstance(payload, bytes)
                and len(payload) <= 1 << 20,
                "raw frame schema/bound",
            )
            require(hashlib.sha256(payload).hexdigest() == digest, "raw payload hash")
            require((part, venue) in contexts, "raw frame has no qualified capture context")
            context = contexts[(part, venue)]
            record = _json(payload)
            if direction == "inbound":
                key = (part, venue, session, channel)
                require(
                    type(when) is int and type(monotonic) is int and type(ordinal) is int,
                    "source receipt clocks/ordinal",
                )
                if key in clocks:
                    old = clocks[key]
                    require(
                        ordinal > old[1] and when >= old[3] and monotonic >= old[5],
                        "receipt clock/ordinal regression within part/session/channel",
                    )
                    clocks[key] = [old[0], ordinal, old[2], when, old[4], monotonic]
                else:
                    clocks[key] = [ordinal, ordinal, when, when, monotonic, monotonic]
                require(
                    when >= context.prelaunch_utc_ns
                    and monotonic >= context.prelaunch_monotonic_ns,
                    "raw clocks predate capture planning",
                )
                binding = (venue, session)
                require(
                    binding not in sessions or sessions[binding] == context,
                    "ambiguous session capture binding",
                )
                sessions[binding] = context
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
                require(coin in ("BTC", "ETH", "SOL"), "HL payload coin identity")
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
                bn_first_trade[session] = min(bn_first_trade.get(session, when), when)
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

    flow = aggregate_flow(trades())
    previous: dict[tuple[str, str, str], list[int]] = {}
    # Parts may be presented in path order unrelated to source ordinal. Check
    # only staged-window metadata ranges, preserving per-session semantics.
    for key, values in sorted(clocks.items(), key=lambda item: (*item[0][1:], item[1][0])):
        stream = key[1:]
        if stream in previous:
            old = previous[stream]
            require(
                values[0] > old[1] and values[2] >= old[3] and values[4] >= old[5],
                "cross-part receipt clock/ordinal regression",
            )
        previous[stream] = values
    return FlowSource(
        spec,
        start,
        end,
        tuple(sorted(quotes, key=lambda q: (q.received_ns, q.ordinal))),
        flow,
        tuple(hl_gaps),
        tuple(bn_gaps),
        funding_events_ms=funding_events_ms,
        hl_windows={
            session: context
            for (venue, session), context in sessions.items()
            if venue == "hyperliquid"
        },
        bn_windows={
            session: context for (venue, session), context in sessions.items() if venue == "binance"
        },
        bn_first_trade=bn_first_trade,
    )


class StagedFlowSource:
    """Conditional scenario source: financial OOS decoding happens on demand.

    The existing harness requests the final window only after validation selects
    a configuration. Validation and its benchmarks share only the first 48h;
    OOS quality/clock errors cannot enter that read. This source does not qualify
    funding and is not an executable economic-research permission.
    """

    def __init__(
        self,
        spec: HypothesisSpec,
        start: int,
        end: int,
        hashes: dict[str, str],
        contexts: dict[tuple[str, str], CaptureWindow],
        *,
        funding_events_ms: tuple[int, ...] | None = None,
    ) -> None:
        require(end - start == 3 * DAY, "fixed staged 72h axis")
        self.spec, self.start, self.end = spec, start, end
        self.boundary = end - spec.split.holdout_bars * MINUTE
        require(self.boundary == start + 2 * DAY, "fixed 48h decode boundary")
        self.hashes = dict(hashes)
        self.contexts = dict(contexts)
        self.funding_events_ms = funding_events_ms
        self.holdout_decoding_started = False
        self._sources = {
            (False, True): load_source(
                spec,
                start,
                self.boundary,
                self.hashes,
                self.contexts,
                funding_events_ms=funding_events_ms,
            )
        }

    @property
    def length(self) -> int:
        return (self.end - self.start) // MINUTE

    def _window(self, config: ConfigSpec, start: int, end: int, *, confirmed: bool) -> TradeSeries:
        require(0 <= start < end <= self.length, "staged window outside axis")
        split = (self.boundary - self.start) // MINUTE
        require(end <= split or start >= split, "staged window crosses sealed OOS")
        holdout = start >= split
        if (holdout, True) not in self._sources:
            self.holdout_decoding_started = True
            # One previously decoded minute is the fixed feature warm-up. It is
            # reread only after selection, together with the reserved OOS.
            self._sources[(True, True)] = load_source(
                self.spec,
                self.boundary - MINUTE,
                self.end,
                self.hashes,
                self.contexts,
                funding_events_ms=self.funding_events_ms,
            )
        if (holdout, confirmed) not in self._sources:
            original = self._sources[(holdout, True)]
            self._sources[(holdout, confirmed)] = FlowSource(
                self.spec,
                original.start_ns,
                original.end_ns,
                original.quotes,
                original.flow,
                original.hl_gaps,
                original.bn_gaps,
                confirmed=confirmed,
                funding_events_ms=self.funding_events_ms,
                hl_windows=original.hl_windows,
                bn_windows=original.bn_windows,
                bn_first_trade=original.bn_first_trade,
            )
        source = self._sources[(holdout, confirmed)]
        offset = (source.start_ns - self.start) // MINUTE
        return source.window(config, start - offset, end - offset)

    def window(self, config: ConfigSpec, start: int, end: int) -> TradeSeries:
        return self._window(config, start, end, confirmed=True)

    def benchmark_window(self, config: ConfigSpec, start: int, end: int) -> TradeSeries:
        return self._window(config, start, end, confirmed=False)


def study_spec(path: Path) -> tuple[dict[str, Json], HypothesisSpec]:
    document = load_document(path)
    spec = validate_spec(document)
    require(
        spec.hypothesis_id == STUDY_ID and spec.selection_method == "holm" and spec.alpha == 0.05,
        "fixed hypothesis",
    )
    require(FUNDING_MODEL in spec.dataset_version, "fixed conditional funding model")
    require("public-collector-window-plan-v1" in spec.dataset_version, "fixed capture-window model")
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


def _result(output: PrivateOutput, payload: dict[str, object]) -> None:
    output.json("result.json", payload)


def _run(
    spec_path: Path,
    manifest_path: Path,
    expected: str,
    output: PrivateOutput,
    *,
    source_commit: str | None = None,
) -> dict[str, object]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    manifest_bytes = _read(manifest_path, MAX_MANIFEST_BYTES)
    require(hashlib.sha256(manifest_bytes).hexdigest() == expected, "approved manifest hash")
    inputs = _json(manifest_bytes)
    start, end, phase = validate_manifest(inputs)
    spec_bytes = spec_path.read_bytes()
    document, spec = study_spec(spec_path)
    os.umask(0o077)
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
    try:
        funding_events, funding_hashes = funding_timeline(inputs["funding"], start, end)
    except (HarnessError, ValueError, TypeError, KeyError, OSError, ArithmeticError):
        unqualified: dict[str, object] = {
            "status": "funding_unqualified",
            "reason": "complete independently accepted frozen funding API-clock timeline required",
            "promotion_decision": "forbidden",
            "financial_frames_read": False,
            "funding_model": FUNDING_MODEL,
            "onchain_cashflow_qualified": False,
            "source_manifest_sha256": expected,
            "source_commit": source_commit,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
        }
        _result(output, unqualified)
        return unqualified
    try:
        contexts, capture_hashes = qualify_capture(inputs["capture"], inputs["parts"], _json)
    except (HarnessError, ValueError, TypeError, KeyError, OSError, ArithmeticError):
        capture_unqualified: dict[str, object] = {
            "status": "capture_unqualified",
            "reason": "accepted complete native publication/prelaunch context required",
            "promotion_decision": "forbidden",
            "financial_frames_read": False,
            "source_manifest_sha256": expected,
            "source_commit": source_commit,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
        }
        _result(output, capture_unqualified)
        return capture_unqualified
    source: StagedFlowSource | None = None
    try:
        _, hashes = checked_paths(inputs)
        source = StagedFlowSource(
            spec, start, end, hashes, contexts, funding_events_ms=funding_events
        )
        decision = decide_source(source)
        payload: dict[str, object] = {
            "status": "completed",
            "source_commit": source_commit,
            "hypothesis_id": STUDY_ID,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
            "exploratory_only": phase == "exploratory",
            "decision": asdict(decision),
            "promotion_decision": "forbidden",
            "execution_cost_model": (
                "observed two-sided quotes + both fill fees + adverse slippage; "
                "cost-vector stress1/1.5/2"
            ),
            "funding_model": FUNDING_MODEL,
            "model_conditional": True,
            "onchain_cashflow_qualified": False,
            "funding_event_count_including_padding": len(funding_events),
            "funding_source_sha256": funding_hashes,
            "capture_source_sha256": capture_hashes,
            "flat_benchmark_net": 0.0,
            "source_manifest_sha256": expected,
            "source_part_sha256": hashes,
            "venue_orders_submitted": False,
            "deployment": False,
        }
        baseline: list[float] = []
        for fold in decision.folds:
            config = next(c for c in spec.configs if c.id == decision.primary_config_id)
            baseline.extend(
                source.benchmark_window(config, fold.test_start, fold.test_end).net(spec.costs, 1.0)
            )
        payload["momentum_without_flow_validation_benchmark"] = asdict(summarize(baseline))
        if decision.selected_config_id is not None:
            selected = next(c for c in spec.configs if c.id == decision.selected_config_id)
            payload["momentum_without_flow_holdout_benchmark"] = asdict(
                summarize(
                    source.benchmark_window(
                        selected, decision.holdout_start, decision.holdout_end
                    ).net(spec.costs, 1.0)
                )
            )
        require(
            checked_paths(inputs)[1] == hashes
            and funding_timeline(inputs["funding"], start, end) == (funding_events, funding_hashes)
            and qualify_capture(inputs["capture"], inputs["parts"], _json)
            == (contexts, capture_hashes)
            and _read(manifest_path, MAX_MANIFEST_BYTES) == manifest_bytes,
            "source mutation after study",
        )
        reported = cast(dict[str, object], payload["decision"])
        reported["label"] = (
            "model_conditional_"
            + ("exploratory_" if phase == "exploratory" else "")
            + decision.label
        )
        reported["promotion_decision"] = "forbidden"
        require(spec_path.read_bytes() == spec_bytes, "spec changed during study")
        output.write("spec.yaml", spec_bytes)
        output.json(
            "spec.yaml.lock.json",
            {
                "spec_sha256": spec_sha256(document),
                "canonical_spec": document,
                "data_fingerprint": {
                    "source_manifest_sha256": expected,
                    "source_part_sha256": hashes,
                    "funding_source_sha256": funding_hashes,
                    "capture_source_sha256": capture_hashes,
                },
            },
        )
        _result(output, payload)
        return payload
    except HarnessError as error:
        failure: dict[str, object] = {
            "status": "failed_closed",
            "promotion_decision": "forbidden",
            "reason": "no valid complete study outcome; do not resume or reuse OOS",
            "holdout_scoring_may_have_been_attempted": (
                source is not None and source.holdout_decoding_started
            ),
            "failure_kind": error.failure_kind,
            "unknown_execution_outcomes_at_least": 1 if "BTC flow: unknown" in str(error) else 0,
            "source_manifest_sha256": expected,
            "source_commit": source_commit,
            "spec_sha256": spec_sha256(document),
            "phase": phase,
            "funding_model": FUNDING_MODEL,
            "model_conditional": True,
            "onchain_cashflow_qualified": False,
        }
        _result(output, failure)
        return failure


def run(
    spec_path: Path,
    manifest_path: Path,
    expected: str,
    output: Path,
    *,
    source_commit: str | None = None,
) -> dict[str, object]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    os.umask(0o077)
    with PrivateOutput(output) as writer:
        return _run(spec_path, manifest_path, expected, writer, source_commit=source_commit)


def bound_address_space() -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = min(value for value in (soft, hard, MAX_PROCESS_BYTES) if value != resource.RLIM_INFINITY)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


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
        bound_address_space()
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
        inputs = _json(_read(args.inputs, MAX_MANIFEST_BYTES))
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
        MemoryError,
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
