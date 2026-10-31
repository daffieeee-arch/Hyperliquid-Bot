"""Metadata-only binding to reviewed, closed native collector publications."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

from research.btc_flow import MINUTE, SECOND, CaptureWindow, require
from research.btc_flow_io import read_bytes


def qualify_capture(
    references: object,
    parts: object,
    decode: Callable[[bytes], dict[str, object]],
) -> tuple[dict[tuple[str, str], CaptureWindow], dict[str, str]]:
    # The strict JSON decoder is shared with the study manifest, passed as a
    # callable to avoid a dependency from this metadata module back to the CLI.
    parser = decode
    require(
        isinstance(references, list) and 0 < len(references) <= 4096,
        "capture publications required",
    )
    require(isinstance(parts, list) and 0 < len(parts) <= 70_000, "capture-bound parts required")
    hashes: dict[str, str] = {}

    def read(path: Path, expected: object, cap: int, size: int | None = None) -> dict[str, object]:
        require(
            path.is_absolute() and not any(c in str(path) for c in "*?[]{}"),
            "exact capture control path",
        )
        content = read_bytes(path, cap)
        require(size is None or len(content) == size, "capture control size mismatch")
        digest = hashlib.sha256(content).hexdigest()
        require(isinstance(expected, str) and digest == expected, "capture control hash mismatch")
        hashes[str(path)] = digest
        return parser(content)

    publications: dict[str, tuple[Path, dict[str, object], CaptureWindow]] = {}
    all_parts: dict[str, set[str]] = {}
    for reference in cast(list[dict[str, object]], references):
        require(
            isinstance(reference, dict) and set(reference) == {"path", "sha256"},
            "capture reference schema",
        )
        require(
            isinstance(reference["path"], str) and isinstance(reference["sha256"], str),
            "capture reference identity",
        )
        path = Path(cast(str, reference["path"]))
        digest = cast(str, reference["sha256"])
        require(
            digest not in publications and path.name == "publication-manifest.json",
            "distinct publication manifest required",
        )
        pub = read(path, digest, 32 << 20)
        require(
            pub.get("schema") == "public-collector-closed-chunk-v1"
            and pub.get("destination_root") == str(path.parent),
            "publication destination contract",
        )
        files, prov = pub.get("files"), pub.get("provenance")
        require(isinstance(files, dict) and isinstance(prov, dict), "publication files/provenance")
        file_records = cast(dict[str, dict[str, object]], files)
        prov = cast(dict[str, object], prov)

        def control(
            name: str,
            records: dict[str, dict[str, object]] = file_records,
            root: Path = path.parent,
        ) -> dict[str, object]:
            require(
                name in records and isinstance(records[name], dict),
                "required capture control missing",
            )
            record = records[name]
            require(
                type(record.get("bytes")) is int and 0 < cast(int, record["bytes"]) <= 64 << 10,
                "capture control byte bound",
            )
            return read(root / name, record.get("sha256"), 64 << 10, cast(int, record["bytes"]))

        complete_raw = read_bytes(path.parent / "COMPLETE.json", 16 << 10)
        complete = parser(complete_raw)
        require(
            complete.get("manifest_sha256") == digest
            and complete.get("closed_chunk_verified") is True,
            "closed publication seal required",
        )
        hashes[str(path.parent / "COMPLETE.json")] = hashlib.sha256(complete_raw).hexdigest()
        plan, claim, runtime, health = (
            control(name)
            for name in (
                "collector-plan.json",
                "capture-claim.json",
                "collector-runtime.json",
                "capture-health.json",
            )
        )
        require(
            runtime == prov and prov.get("schema") == "public-collector-run-provenance-v1",
            "run provenance binding",
        )
        require(
            prov.get("window_plan_sha256") == file_records["collector-plan.json"]["sha256"],
            "planning hash binding",
        )
        lane = plan.get("lane")
        require(lane in ("hyperliquid", "binance"), "research capture lanes")
        venue, product, claim_schema = (
            ("hyperliquid", "BTC-PERP", "data-1a-retained-capture-claim-v1")
            if lane == "hyperliquid"
            else ("binance", "BTCUSDT", "data-1f-retained-capture-claim-v1")
        )
        required = {
            "schema",
            "campaign",
            "run_id",
            "chunk_index",
            "lane",
            "venue",
            "product",
            "source_commit",
            "image_digest",
            "requested_duration_seconds",
            "prelaunch_utc_ns",
            "prelaunch_monotonic_ns",
            "prelaunch_utc_after_ns",
            "clock_pair_order",
            "earliest_planned_end_utc_ns",
            "earliest_planned_end_monotonic_ns",
            "end_semantics",
            "host_clock_evidence",
        }
        require(
            set(plan) == required and plan["schema"] == "public-collector-window-plan-v1",
            "planning schema",
        )
        require(
            plan["venue"] == venue and plan["product"] == product, "planning container identity"
        )
        campaign, chunk, run = plan["campaign"], plan["chunk_index"], plan["run_id"]
        require(
            isinstance(campaign, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", campaign) is not None
            and type(chunk) is int
            and 0 <= chunk < 36
            and run == f"{campaign}-{chunk:04d}",
            "planning campaign/run identity",
        )
        source, image = plan["source_commit"], plan["image_digest"]
        require(
            isinstance(source, str)
            and re.fullmatch(r"[0-9a-f]{40}", source) is not None
            and isinstance(image, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None,
            "planning source/image",
        )
        duration, before, mono, after = (
            plan[name]
            for name in (
                "requested_duration_seconds",
                "prelaunch_utc_ns",
                "prelaunch_monotonic_ns",
                "prelaunch_utc_after_ns",
            )
        )
        require(
            all(type(v) is int for v in (duration, before, mono, after)), "planning integer clocks"
        )
        duration, before, mono, after = (
            cast(int, duration),
            cast(int, before),
            cast(int, mono),
            cast(int, after),
        )
        require(
            600 <= duration <= 21600 and before > 0 and mono > 0 and 0 <= after - before <= SECOND,
            "planning bounded clock bracket",
        )
        require(
            plan["clock_pair_order"] == "utc-monotonic-utc"
            and plan["end_semantics"] == "lower-bound-on-uninterrupted-native-timer-not-actual-stop"
            and plan["earliest_planned_end_utc_ns"] == before + duration * SECOND
            and plan["earliest_planned_end_monotonic_ns"] == mono + duration * SECOND,
            "planning lower-bound arithmetic",
        )
        clock = plan["host_clock_evidence"]
        require(isinstance(clock, dict), "host clock evidence")
        clock = cast(dict[str, object], clock)
        sampled = clock.get("sampled_utc_ns")
        require(
            clock.get("schema") == "collector-host-clock-v1"
            and clock.get("synchronized") is True
            and type(sampled) is int
            and 0 <= before - sampled <= 2 * MINUTE,
            "fresh synchronized prelaunch clock",
        )
        for key, maximum in (
            ("system_offset_seconds", 0.050),
            ("root_delay_seconds", 0.500),
            ("root_dispersion_seconds", 0.250),
        ):
            value = clock.get(key)
            require(
                type(value) in (int, float)
                and math.isfinite(cast(float, value))
                and abs(cast(float, value)) <= maximum
                and (key == "system_offset_seconds" or cast(float, value) >= 0),
                "host clock uncertainty bound",
            )
        require(
            all(
                prov.get(name) == plan[name]
                for name in ("campaign", "run_id", "lane", "source_commit", "image_digest")
            )
            and prov.get("chunk_seconds") == duration,
            "run/source/image planning consistency",
        )
        require(
            claim.get("schema") == claim_schema
            and claim.get("state") == "STARTED_FAIL_CLOSED"
            and claim.get("run_id") == run
            and claim.get("venue") == venue
            and claim.get("product") == product
            and claim.get("duration_seconds") == duration
            and claim.get("code_version") == source
            and claim.get("credentialless") is True
            and claim.get("signing") is False,
            "native capture claim consistency",
        )
        require(
            isinstance(pub.get("source_root"), str)
            and claim.get("raw_dir") == str(pub["source_root"]) + "/raw",
            "verbatim native source path binding",
        )
        require(
            health.get("status") == "COMPLETED"
            and health.get("run_id") == run
            and health.get("duration_seconds") == duration
            and health.get("credentialless") is True,
            "completed native health required",
        )
        publications[digest] = (
            path.parent,
            cast(dict[str, object], file_records),
            CaptureWindow(digest, venue, cast(str, run), before, mono, before + duration * SECOND),
        )
        all_parts[digest] = {
            str(path.parent / relative)
            for relative in file_records
            if relative.startswith("raw/") and Path(relative).suffix == ".parquet"
        }
        require(bool(all_parts[digest]), "publication has no closed raw parts")

    contexts: dict[tuple[str, str], CaptureWindow] = {}
    used: set[str] = set()
    selected_parts: dict[str, set[str]] = {}
    for item in cast(list[dict[str, object]], parts):
        require(
            isinstance(item, dict) and set(item) == {"path", "sha256", "publication_sha256"},
            "publication-bound raw part fields",
        )
        part_text, expected, publication = item["path"], item["sha256"], item["publication_sha256"]
        require(
            isinstance(part_text, str)
            and isinstance(expected, str)
            and isinstance(publication, str)
            and publication in publications,
            "raw publication identity",
        )
        part_text, publication = cast(str, part_text), cast(str, publication)
        root, files, context = publications[publication]
        raw_path = Path(part_text)
        require(
            raw_path.is_relative_to(root / "raw")
            and raw_path.suffix == ".parquet"
            and ".." not in raw_path.parts,
            "raw publication destination path",
        )
        relative = str(raw_path.relative_to(root))
        record = files.get(relative)
        require(
            isinstance(record, dict)
            and record.get("sha256") == expected
            and type(record.get("bytes")) is int
            and 0 < cast(int, record["bytes"]) <= 512 << 20,
            "raw publication hash/size binding",
        )
        binding = (part_text, context.venue)
        require(binding not in contexts, "duplicate raw capture binding")
        contexts[binding] = context
        used.add(publication)
        selected_parts.setdefault(publication, set()).add(part_text)
    require(used == set(publications), "unused capture publication refused")
    require(
        selected_parts == all_parts, "every published raw part must be selected without omission"
    )
    return contexts, hashes
