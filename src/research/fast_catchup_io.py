"""Strict immutable native inputs and bounded source-only availability projections.

This module has no strategy, network or executable-source acquisition. Its local
projection is an explicitly hypothetical availability view, never a repaired tape.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import heapq
import json
import os
import stat
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, BinaryIO

import duckdb

from research.btc_flow_io import PrivateOutput, _directory, read_bytes
from research.fast_catchup import SECOND, Event, Level, text
from research.micro_flow_io import COLUMNS
from research.two_models_io import read_record
from research.volume_repair_io import json_value, require

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_fast_catchup_20261114.md"
PLAN_SHA = "024b5080277fa5ebef06e5d5b42f31aaed73181cde3717c36c6a459946677296"
AMENDMENT = ROOT / "docs/experiments/exp_fast_catchup_20261114_engineering.md"
STUDY = "hl-bn-cost-target-catchup-20261114-v1"
CAMPAIGN = "hl-cont-20261113203809-be480ab9-0000"
MAX_ROWS = 20_000_000
MAX_PROJECTION = 4 << 30
MAX_PENDING = 32 << 20
REQUIRED = {"usdm_agg_trade", "usdm_book_ticker"}
NORMALIZED = {
    "spot_trade": "normalized_spot_trade",
    "spot_book_ticker": "normalized_spot_bbo",
    "spot_depth": "normalized_spot_depth",
    "spot_depth_snapshot": "normalized_spot_depth",
    "usdm_agg_trade": "normalized_usdm_context",
    "usdm_book_ticker": "normalized_usdm_context",
    "usdm_mark_price": "normalized_usdm_context",
    "usdm_force_order": "normalized_usdm_context",
    "usdm_open_interest": "normalized_usdm_context",
}


@dataclass(frozen=True, slots=True)
class RawLink:
    session: str
    product: str
    channel: str
    event: Event | None
    disposition: str
    budget_bytes: int


def obj(value: Any) -> dict[str, Any]:
    require(isinstance(value, dict), "JSON object required")
    return dict(value)


def integer(value: Any) -> int:
    require(type(value) is int and value >= 0, "nonnegative native integer")
    return int(value)


def number(value: Any, *, zero: bool = False) -> Decimal:
    require(isinstance(value, str) and 0 < len(value) <= 80, "bounded native decimal string")
    require("e" not in value.lower(), "fixed-point native decimal")
    result = Decimal(value)
    require(result.is_finite() and (result >= 0 if zero else result > 0), "native decimal range")
    return result


def load(path: Path, expected_sha: str) -> dict[str, Any]:
    require(
        len(expected_sha) == 64 and set(expected_sha) <= set("0123456789abcdef"),
        "explicit qualified input-index SHA required",
    )
    raw = read_bytes(path, 8 << 20)
    require(hashlib.sha256(raw).hexdigest() == expected_sha, "exact qualified input index")
    index = obj(json_value(raw))
    require(
        set(index)
        == {
            "schema",
            "study_id",
            "campaign",
            "plan_sha256",
            "data_status",
            "source_acceptance",
            "source_acceptance_passed",
            "captures",
            "clock_offset_ns",
            "precision",
            "funding",
        },
        "strict source index fields",
    )
    require(
        index["schema"] == "fast-catchup-source-index-v1"
        and index["study_id"] == STUDY
        and index["campaign"] == CAMPAIGN
        and index["plan_sha256"] == PLAN_SHA
        and index["data_status"] == "exposed_retrospective_no_fresh_oos",
        "fixed registered source selection",
    )
    receipt = obj(json_value(read_record(obj(index["source_acceptance"]))))
    require(
        index["source_acceptance_passed"] is True
        and str(receipt.get("status", "")).startswith("PASS")
        and receipt.get("campaign") == CAMPAIGN
        and receipt.get("native_required_market_fields_valid") is True
        and receipt.get("financial_labels_or_model_scoring") is False,
        "independent complete native source acceptance, not a pilot or success flag alone",
    )
    captures = obj(index["captures"])
    require(set(captures) == {"hyperliquid", "binance"}, "exact fixed two native lanes")
    paths: set[str] = set()
    for capture in captures.values():
        capture = obj(capture)
        require(set(capture) == {"parts", "metadata"}, "strict capture fields")
        require(
            isinstance(capture["parts"], list)
            and 0 < len(capture["parts"]) <= 5000
            and isinstance(capture["metadata"], list)
            and len(capture["metadata"]) == 12,
            "complete WORK parts and both copies' twelve metadata bindings",
        )
        for ref in capture["parts"] + capture["metadata"]:
            ref = obj(ref)
            require(set(ref) == {"path", "bytes", "sha256"}, "strict source reference")
            require(
                isinstance(ref["path"], str) and ref["path"] not in paths,
                "absolute source identities cannot alias across copies",
            )
            paths.add(ref["path"])
        for ref in capture["metadata"]:
            read_record(ref)
    # An independent receipt binds the COMPLETE choice, not merely a campaign name.
    require(receipt.get("captures") == captures, "acceptance must bind exact parts/metadata choice")
    require(
        type(index["clock_offset_ns"]) is int and index["clock_offset_ns"] > 0,
        "fixed prelaunch UTC-minus-monotonic offset",
    )
    require(
        index["precision"]
        in (
            {"sz_decimals": 5, "mode": "capture_metadata"},
            {"sz_decimals": 5, "mode": "explicit_epoch_assumption"},
        ),
        "explicit BTC precision provenance",
    )
    require(set(obj(index["funding"])) == {"source", "start_ms", "end_ms"}, "funding bindings")
    return index


def funding(index: dict[str, Any], start: int, end: int) -> tuple[int, ...]:
    record = obj(index["funding"])
    begin, finish = integer(record["start_ms"]), integer(record["end_ms"])
    offset = index["clock_offset_ns"]
    require(
        begin <= (offset + start) // 1_000_000 - 3_600_000
        and finish >= (offset + end) // 1_000_000 + 3_600_000,
        "complete padded funding request",
    )
    rows = json_value(read_record(obj(record["source"]), 1 << 20))
    require(isinstance(rows, list) and 0 < len(rows) < 500, "nonempty uncapped funding census")
    assert isinstance(rows, list)
    times: list[int] = []
    for row in rows:
        row = obj(row)
        require(row.get("coin") == "BTC", "native funding BTC identity")
        instant = integer(row.get("time"))
        rate = row.get("fundingRate")
        require(
            isinstance(rate, str) and len(rate) <= 80 and Decimal(rate).is_finite(),
            "original finite funding rate",
        )
        require(
            begin <= instant <= finish and (not times or times[-1] < instant),
            "ordered unique native funding events inside request",
        )
        times.append(instant)
    for hour in range((begin + 3_599_999) // 3_600_000, finish // 3_600_000):
        require(
            sum(hour * 3_600_000 <= t < (hour + 1) * 3_600_000 for t in times) == 1,
            "every full UTC request hour requires one native event",
        )
    return tuple(times)


@contextmanager
def sealed_part(part: dict[str, Any]) -> Iterator[tuple[duckdb.DuckDBPyConnection, str]]:
    """Seal the single approved 4MiB read; no second path race or external SQL."""
    content = read_record(part, 4 << 20)
    fd = os.memfd_create("fast-approved-part", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            require(written > 0, "complete immutable native part write")
            view = view[written:]
        del view, content
        fcntl.fcntl(fd, 1033, 0x000F)
        require(fcntl.fcntl(fd, 1034) == 0x000F, "immutable native part seals")
        bound = f"/proc/self/fd/{fd}"
        connection = duckdb.connect(
            config={
                "threads": 1,
                "memory_limit": "64MB",
                "max_temp_directory_size": "0B",
                "enable_external_file_cache": "false",
                "autoload_known_extensions": "false",
                "autoinstall_known_extensions": "false",
            }
        )
        try:
            connection.execute("SET allowed_paths = ?", [[bound]])
            connection.execute("SET enable_external_access = false")
            yield connection, bound
        finally:
            connection.close()
    finally:
        os.close(fd)


def records(capture: dict[str, Any], lane: str) -> Iterator[tuple[Any, ...]]:
    previous = 0
    for part in capture["parts"]:
        require(
            type(part["bytes"]) is int and 0 < part["bytes"] <= 4 << 20,
            "bounded sealed native source part",
        )
        with sealed_part(part) as (connection, bound):
            connection.execute("SET threads=1")
            connection.execute("SET memory_limit='64MB'")
            schema = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [bound]
            ).fetchall()
            require({r[0] for r in schema} == set(COLUMNS), "exact native receipt columns")
            cursor = connection.execute(
                "SELECT " + ",".join(COLUMNS) + ",file_row_number "
                "FROM read_parquet(?,file_row_number=true) ORDER BY file_row_number",
                [bound],
            )
            physical = 0
            while batch := cursor.fetchmany(1024):
                for original in batch:
                    require(original[-1] == physical, "physical part row order")
                    physical += 1
                    row = original[:-1]
                    (
                        version,
                        venue,
                        _,
                        _,
                        session,
                        ordinal,
                        utc,
                        mono,
                        direction,
                        _,
                        encoding,
                        body,
                        sha,
                    ) = row
                    require(
                        version == 1 and venue == lane and ordinal == previous + 1,
                        "native schema/venue/complete physical ordinal",
                    )
                    require(
                        type(ordinal) is int and isinstance(session, str) and bool(session),
                        "native ordinal/session",
                    )
                    integer(utc)
                    integer(mono)
                    require(
                        direction in ("inbound", "outbound", "local")
                        and encoding in ("utf-8", "utf-8-json")
                        and isinstance(body, bytes)
                        and len(body) <= 1 << 20
                        and hashlib.sha256(body).hexdigest() == sha,
                        "native direction/encoding/bounded hashed payload",
                    )
                    previous = ordinal
                    yield row


class Decoder:
    """Constant native-ID state; regressions fail rather than silently deduplicate."""

    def __init__(self) -> None:
        self.ids: dict[str, tuple[int, str]] = {}
        self.acks: dict[str, set[str]] = {}

    def decode(self, row: tuple[Any, ...]) -> Event | None:
        _, lane, product, channel, session, ordinal, utc, mono, direction, frame, _, raw, sha = row
        body = obj(json_value(raw))
        identity = f"{lane}:{ordinal}:{sha}"
        base = Event(lane, session, ordinal, mono, utc, utc // 1_000_000, mono, identity, "noop")
        if direction != "inbound":
            event = str(body.get("event", ""))
            if channel in ("data_quality", "connection", "gap") and any(
                token in event.lower()
                for token in ("gap", "disconnect", "error", "reset", "overflow", "stale")
            ):
                return replace(base, kind="reset", reason=event)
            return None
        if lane == "hyperliquid":
            require(product == "BTC-PERP", "native HL product")
            actual, data = body.get("channel"), body.get("data")
            if actual == "subscriptionResponse":
                data = obj(data)
                sub = obj(data.get("subscription"))
                require(
                    data.get("method") == "subscribe" and sub.get("coin") == "BTC",
                    "native BTC subscription acknowledgement",
                )
                self.acks.setdefault(session, set()).add(str(sub.get("type")))
                return base
            if actual not in ("bbo", "l2Book"):
                return base  # Still an original inbound receiver-progress witness.
            require(
                actual == channel and actual in self.acks.get(session, set()),
                "book requires its native BTC subscription acknowledgement",
            )
            data = obj(data)
            require(data.get("coin") == "BTC", "native HL book asset")
            native = integer(data.get("time"))
            sides = data.get("bbo") if actual == "bbo" else data.get("levels")
            require(isinstance(sides, list) and len(sides) == 2, "native HL book sides")
            assert isinstance(sides, list)
            result: list[tuple[Level, ...]] = []
            for side in sides:
                levels = [side] if actual == "bbo" else side
                require(isinstance(levels, list) and len(levels) <= 20, "actual observed L2 depth")
                values = []
                for value in levels:
                    if value is None:
                        continue
                    value = obj(value)
                    integer(value.get("n"))
                    values.append(
                        Level(number(value.get("px")), number(value.get("sz"), zero=True))
                    )
                result.append(tuple(values))
            bids, asks = result
            require(
                all(a.price > b.price for a, b in pairwise(bids))
                and all(a.price < b.price for a, b in pairwise(asks)),
                "strict observed level order",
            )
            if not bids or not asks or bids[0].price >= asks[0].price:
                return replace(base, kind="reset", reason="unavailable_or_locked_book")
            return replace(base, kind="hl_book", native_ms=native, bids=bids, asks=asks)
        if channel not in REQUIRED:
            return None
        require(
            product == "BTCUSDT-USDS-M-PERPETUAL" and frame == "text", "native USD-M product/frame"
        )
        data = obj(body.get("data"))
        required_stream = (
            "btcusdt@aggTrade" if channel == "usdm_agg_trade" else "btcusdt@bookTicker"
        )
        kind = "flow" if channel == "usdm_agg_trade" else "bn_book"
        require(
            body.get("stream") == required_stream
            and data.get("s") == "BTCUSDT"
            and data.get("st") == 1
            and data.get("e") == ("aggTrade" if kind == "flow" else "bookTicker"),
            "native USD-M route/symbol/epoch contract",
        )
        integer(data.get("E"))
        native = integer(data.get("T"))
        ident = integer(data.get("a" if kind == "flow" else "u"))
        signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        old = self.ids.get(channel)
        if old is not None:
            require(ident >= old[0], "native ID regression is unknown, not repaired")
            if ident == old[0]:
                require(signature == old[1], "conflicting native ID")
                return None
        self.ids[channel] = ident, signature
        if kind == "flow":
            quantity, price = number(data.get("q")), number(data.get("p"))
            require(
                type(data.get("m")) is bool and integer(data.get("f")) <= integer(data.get("l")),
                "native aggressor and trade range",
            )
            if "nq" in data:
                require(
                    number(data["nq"], zero=True) <= quantity, "observed optional normal quantity"
                )
            return replace(
                base,
                kind="flow",
                native_ms=native,
                signed_notional=price * quantity * (-1 if data["m"] else 1),
            )
        require(data.get("ps") == "BTCUSDT", "native USD-M pair identity")
        bids = (Level(number(data.get("b")), number(data.get("B"), zero=True)),)
        asks = (Level(number(data.get("a")), number(data.get("A"), zero=True)),)
        if bids[0].price >= asks[0].price:
            return replace(base, kind="reset", reason="locked_external_book")
        return replace(base, kind="bn_book", native_ms=native, bids=bids, asks=asks)


@dataclass(frozen=True)
class Projection:
    raw: tuple[str, ...]
    witness: tuple[str, ...]
    start: int
    end: int
    source_counts: dict[str, int]
    bindings: dict[str, dict[str, Any]]


class Streams:
    """Anchored private incremental output; no source paths are writable here."""

    def __init__(self, output: PrivateOutput, *, compress: bool = False) -> None:
        self.output = output
        self.compress = compress
        self.files: dict[str, Any] = {}
        self.sinks: dict[str, BinaryIO] = {}
        self.hashes: dict[str, Any] = {}
        self.sizes: Counter[str] = Counter()
        self.total = 0

    def emit(self, name: str, value: dict[str, Any], *, limit: int = MAX_PROJECTION) -> None:
        require(bool(name) and "/" not in name and name not in (".", ".."), "private exact leaf")
        if name not in self.files:
            require(len(self.files) < 32, "bounded open projection/audit files")
            self.output.assert_bound()
            fd = os.open(
                name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
                dir_fd=self.output.fd,
            )
            self.sinks[name] = os.fdopen(fd, "wb")
            self.hashes[name] = hashlib.sha256()
            sink = CountingSink(self, name)
            self.files[name] = (
                gzip.GzipFile(filename="", mode="wb", fileobj=sink, compresslevel=3, mtime=0)
                if self.compress
                else sink
            )
        raw = (
            json.dumps(text(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode()
        require(
            len(raw) <= 128 << 10 and self.total <= limit,
            "bounded private projection/audit output",
        )
        self.files[name].write(raw)

    def close(self) -> dict[str, dict[str, Any]]:
        for stream in self.files.values():
            if self.compress:
                stream.close()  # Footer is also covered by the actual-byte budget/hash.
        for sink in self.sinks.values():
            sink.flush()
            os.fsync(sink.fileno())
            sink.close()
        os.fsync(self.output.fd)
        self.output.assert_bound()
        return {
            name: {
                "path": str(self.output.path / name),
                "bytes": self.sizes[name],
                "sha256": self.hashes[name].hexdigest(),
            }
            for name in self.files
        }


class CountingSink:
    """Budget and hash bytes actually written, including gzip headers/footers."""

    def __init__(self, owner: Streams, name: str) -> None:
        self.owner, self.name = owner, name

    def write(self, data: bytes) -> int:
        require(
            self.owner.total + len(data) <= MAX_PROJECTION, "four-GiB private output byte ceiling"
        )
        count = self.owner.sinks[self.name].write(data)
        require(count == len(data), "complete private stream write")
        self.owner.hashes[self.name].update(data)
        self.owner.sizes[self.name] += count
        self.owner.total += count
        return count

    def flush(self) -> None:
        self.owner.sinks[self.name].flush()

    def tell(self) -> int:
        return self.owner.sizes[self.name]


def project(index: dict[str, Any], output: PrivateOutput) -> Projection:
    streams = Streams(output, compress=True)
    raw_files: dict[tuple[str, str], str] = {}
    witness_files: list[str] = []
    bounds: dict[str, list[int]] = {}
    counts: Counter[str] = Counter()
    total_rows = 0
    try:
        for lane, capture in index["captures"].items():
            decoder = Decoder()
            high = 0
            clocks: dict[str, int] = {}
            pending: dict[str, Event] = {}
            raw_links: dict[int, RawLink] = {}
            pending_bytes = 0
            serialized = f"witness-{lane}.jsonl.gz"
            witness_files.append(serialized)
            for row in records(capture, lane):
                total_rows += 1
                require(total_rows <= MAX_ROWS, "twenty-million PHYSICAL row ceiling")
                _, _, product, channel, session, ordinal, utc, mono, direction, frame, _, raw, _ = (
                    row
                )
                require(
                    abs(utc - mono - index["clock_offset_ns"]) <= 100_000_000,
                    "fixed prelaunch clock-offset assumption exceeded",
                )
                high = max(high, mono)
                counts[f"{lane}:{direction}:{channel}"] += 1
                if lane == "binance" and direction == "local":
                    marker = obj(json_value(raw))
                    source_channel = marker.get("source_channel")
                    normalized = isinstance(channel, str) and channel.startswith("normalized_")
                    if normalized or (
                        isinstance(source_channel, str) and source_channel in REQUIRED
                    ):
                        require(
                            isinstance(source_channel, str) and source_channel in NORMALIZED,
                            "known LOCAL normalization source channel",
                        )
                        linked = marker.get("raw_message_ordinal")
                        require(
                            type(linked) is int and 0 < linked < ordinal,
                            "required LOCAL witness has a prior raw ordinal",
                        )
                        assert isinstance(linked, int)
                        link = raw_links.pop(linked, None)
                        require(link is not None, "unknown or repeated LOCAL raw witness ordinal")
                        assert link is not None
                        require(
                            session == link.session
                            and product == link.product
                            and source_channel == link.channel
                            and channel == NORMALIZED[link.channel]
                            and frame == "marker",
                            "matching online LOCAL normalization witness identity",
                        )
                        pending_bytes -= link.budget_bytes
                        counts[f"binance:linked_{link.disposition}"] += 1
                        if link.event is not None:
                            released = replace(
                                link.event, available_ns=high, witness=f"{lane}:{ordinal}"
                            )
                            streams.emit(serialized, asdict(released))
                        continue
                # Check required raw WS clocks even if a duplicate has no new event.
                if direction == "inbound" and (lane == "hyperliquid" or channel in REQUIRED):
                    require(mono >= clocks.get(session, 0), "required WS session receipt backstep")
                    clocks[session] = mono
                event = decoder.decode(row)
                if lane == "binance" and direction == "inbound" and channel in NORMALIZED:
                    disposition = (
                        "required"
                        if event is not None
                        else ("duplicate" if channel in REQUIRED else "excluded")
                    )
                    # Conservative fixed overhead plus encoded state bounds both
                    # unresolved identities and event payloads without a history set.
                    budget = 4096 + len(session) + len(product) + len(channel)
                    if event is not None:
                        budget += 2 * len(json.dumps(text(asdict(event))))
                    raw_links[ordinal] = RawLink(
                        session, product, channel, event, disposition, budget
                    )
                    pending_bytes += budget
                    require(pending_bytes <= MAX_PENDING, "bounded unresolved online linkage")
                if lane == "hyperliquid" and direction == "inbound":
                    # Even pong/ACK/context is a genuine later receiver-progress witness.
                    old = pending.pop(session, None)
                    if old is not None:
                        pending_bytes -= len(json.dumps(text(asdict(old))))
                        streams.emit(
                            serialized,
                            asdict(replace(old, available_ns=high, witness=f"{lane}:{ordinal}")),
                        )
                if event is None:
                    continue
                key = lane, session
                if direction == "inbound":
                    require(mono >= clocks.get(session, 0), "required WS session receipt backstep")
                    clocks[session] = mono
                elif event.kind == "reset":
                    # LOCAL quality/control is only known at physical high water.
                    event = replace(event, available_ns=high)
                    key = lane, "physical-controls"
                if key not in raw_files:
                    raw_files[key] = f"raw-{len(raw_files):02d}.jsonl.gz"
                streams.emit(raw_files[key], asdict(event))
                if event.kind in ("flow", "bn_book", "hl_book"):
                    bounds.setdefault(event.kind, [mono, mono])[1] = mono
                if lane == "hyperliquid" and direction == "inbound":
                    pending[session] = event
                    pending_bytes += len(json.dumps(text(asdict(event))))
                elif direction != "inbound":
                    streams.emit(
                        serialized,
                        asdict(replace(event, available_ns=high, witness="physical-control")),
                    )
                require(pending_bytes <= MAX_PENDING, "bounded unresolved online linkage")
            counts[f"{lane}:unwitnessed_tail_events"] = len(pending) + sum(
                link.event is not None for link in raw_links.values()
            )
            counts[f"{lane}:unwitnessed_excluded_or_duplicate_links"] = sum(
                link.event is None for link in raw_links.values()
            )
            for ref in capture["metadata"]:
                read_record(ref)
        require(set(bounds) == {"flow", "bn_book", "hl_book"}, "required native economic streams")
        start, end = max(v[0] for v in bounds.values()), min(v[1] for v in bounds.values())
        require(
            305 * SECOND < end - start <= 3 * 3600 * SECOND, "fixed complete source common envelope"
        )
    finally:
        bindings = streams.close()
    return Projection(
        tuple(raw_files.values()), tuple(witness_files), start, end, dict(counts), bindings
    )


def events(path: Path, ref: dict[str, Any]) -> Iterator[Event]:
    # Open once and hash while streaming, bound regular file without loading GiBs.
    parent = _directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    size = 0
    previous = -1
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "regular projected source")
        with (
            os.fdopen(fd, "rb", closefd=False) as original,
            gzip.GzipFile(fileobj=original) as stream,
        ):
            while line := stream.readline((128 << 10) + 1):
                require(len(line) <= 128 << 10, "bounded projected record")
                size += len(line)
                require(size <= 32 << 30, "bounded decompressed projection stream")
                data = obj(json_value(line))
                data["bids"] = tuple(
                    Level(Decimal(v["price"]), Decimal(v["size"])) for v in data["bids"]
                )
                data["asks"] = tuple(
                    Level(Decimal(v["price"]), Decimal(v["size"])) for v in data["asks"]
                )
                data["signed_notional"] = Decimal(data["signed_notional"])
                event = Event(**data)
                require(event.available_ns >= previous, "projected stream order cannot be repaired")
                previous = event.available_ns
                yield event
    finally:
        os.close(fd)


def merged(output: PrivateOutput, projection: Projection, model: str) -> Iterator[Event]:
    require(model in ("RAW", "WITNESS"), "fixed availability model")
    names = projection.raw if model == "RAW" else projection.witness
    # One head per individually ordered source; no whole-tape post-hoc sort.
    return heapq.merge(
        *(events(output.path / name, projection.bindings[name]) for name in names),
        key=lambda event: event.available_ns,
    )


def verify_projection(projection: Projection) -> None:
    """Check full bytes even when replay intentionally stops before a source tail."""
    for ref in projection.bindings.values():
        path = Path(ref["path"])
        parent = _directory(path.parent)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        digest = hashlib.sha256()
        length = 0
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), "regular projected source")
            while block := os.read(fd, 1 << 20):
                length += len(block)
                require(length <= ref["bytes"], "bounded private projection")
                digest.update(block)
            require(
                length == ref["bytes"] and digest.hexdigest() == ref["sha256"],
                "full source projection identity changed",
            )
        finally:
            os.close(fd)
