"""Offline sealed-copy acceptance; invoked in the pinned, networkless image.

Only metadata and file hashes are examined. This does not qualify any research
window or score market data. No file is deleted, repaired or rewritten.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from .collector_capacity import require
from .collector_runtime import LANES, RuntimePlan
from .collector_storage import MAX_FILES, checked_relative, directory_fd, file_hash, read_json


def publication_inventory(root: Path) -> set[str]:
    """Source snapshots forbid hard links; publications have two linked seals."""
    os.close(directory_fd(root))
    complete = (root / "COMPLETE.json").lstat()
    private = (root / ".COMPLETE.publishing").lstat()
    require(
        complete.st_nlink == private.st_nlink == 2
        and (complete.st_dev, complete.st_ino) == (private.st_dev, private.st_ino),
        "Publication seals are not the exclusive original hard-link pair.",
    )
    result = set()
    for base, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            os.close(directory_fd(Path(base) / name))
        for name in files:
            path = Path(base) / name
            relative = str(path.relative_to(root))
            value = path.lstat()
            require(
                stat.S_ISREG(value.st_mode)
                and value.st_uid == os.getuid()
                and value.st_nlink
                == (2 if relative in {"COMPLETE.json", ".COMPLETE.publishing"} else 1),
                "Unexpected publication inode or link.",
            )
            result.add(relative)
            require(len(result) <= MAX_FILES + 3, "Publication inventory bound.")
    return result


def sealed_copy(root: Path, plan: RuntimePlan, lane: str, run_id: str) -> dict[str, Any]:
    seal = read_json(root / "COMPLETE.json")
    manifest = read_json(root / "publication-manifest.json", limit=16 * 1024**2)
    digest, _ = file_hash(root / "publication-manifest.json")
    require(
        seal.get("closed_chunk_verified") is True and seal.get("manifest_sha256") == digest,
        "Closed publication seal differs from manifest.",
    )
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Publication provenance required.")
    for key, value in {
        "schema": "public-collector-run-provenance-v1",
        "run_id": run_id,
        "lane": lane,
        "source_commit": plan.source_commit,
        "image_digest": plan.image_digest,
        "chunk_seconds": plan.chunk_seconds,
        "chunks": plan.chunks,
        "signing": False,
        "authenticated_feeds": False,
        "storage_profile": "truenas-zfs",
    }.items():
        require(provenance.get(key) == value, "Publication provenance mismatch: " + key)
    expected = str(
        Path("/home/chupa/hyperliquid-spool")
        / plan.campaign
        / f"chunk-{int(run_id.rsplit('-', 1)[1]):04d}"
        / lane
        / LANES[lane][1]
        / run_id
    )
    require(
        manifest.get("schema") == "public-collector-closed-chunk-v1"
        and manifest.get("source_root") == expected
        and manifest.get("destination_root") == str(root)
        and manifest.get("native_claims_preserved_verbatim") is True,
        "Publication relocation binding mismatch.",
    )
    records = manifest.get("files")
    if not isinstance(records, dict) or not records:
        raise ValueError("Publication inventory required.")
    require(
        publication_inventory(root)
        == set(records) | {"COMPLETE.json", ".COMPLETE.publishing", "publication-manifest.json"},
        "Publication inventory changed.",
    )
    for relative, record in records.items():
        require(isinstance(relative, str) and isinstance(record, dict), "Malformed file record.")
        actual, identity = file_hash(root / checked_relative(relative))
        require(
            actual == record.get("sha256") and identity.st_size == record.get("bytes"),
            "Published file hash or byte count differs.",
        )
    native = read_json(root / "capture-health.json", limit=1024**2)
    require(
        native.get("status") == "COMPLETED"
        and native.get("credentialless") is True
        and native.get("run_id") == run_id
        and native.get("duration_seconds") == plan.chunk_seconds
        and type(native.get("integrity_events")) is int
        and native["integrity_events"] == 0,
        "Native terminal health is not accepted.",
    )
    window_sha, _ = file_hash(root / "collector-plan.json")
    require(window_sha == provenance.get("window_plan_sha256"), "Planning hash differs.")
    return {"files": records, "manifest_sha256": digest}


def audit(plan: RuntimePlan, capture: Path, archive: Path, *, complete: bool) -> dict[str, Any]:
    require(
        plan.storage_profile == "truenas-zfs" and capture != archive,
        "Independent native destinations required.",
    )
    accepted = []
    for index in range(plan.chunks):
        run_id = f"{plan.campaign}-{index:04d}"
        for lane in plan.lanes:
            roots = [base / plan.campaign / lane / run_id for base in (capture, archive)]
            existence = [root.exists() for root in roots]
            # A failed/partial reservation is retained and blocks continuation;
            # an entirely unpublished chunk is allowed only after a known
            # transport/boot interruption. Never bless a half-published copy.
            if not any(existence) and not complete:
                continue
            require(all(existence), "One destination publication is missing.")
            copies = [sealed_copy(root, plan, lane, run_id) for root in roots]
            first = dict(copies[0]["files"])
            require("research.duckdb" in first, "Regenerated work catalog missing.")
            first.pop("research.duckdb")
            require(first == copies[1]["files"], "Work/archive raw inventories differ.")
            import duckdb

            # Query only structural row metadata from the destination-bound
            # native catalog; no prices, returns or OOS analysis are exposed.
            with duckdb.connect(str(roots[0] / "research.duckdb"), read_only=True) as database:
                rows = database.execute("SELECT count(*) FROM raw_records").fetchone()
            require(rows is not None and rows[0] > 0, "Catalog cannot resolve native raw parts.")
            accepted.append(
                {
                    "lane": lane,
                    "run_id": run_id,
                    "copies": 2,
                    "work_manifest_sha256": copies[0]["manifest_sha256"],
                    "archive_manifest_sha256": copies[1]["manifest_sha256"],
                }
            )
    return {
        "schema": "collector-continuous-closure-v1",
        "campaign": plan.campaign,
        "complete": complete,
        "accepted": accepted,
        "research_ready": False,
    }


def main() -> int:
    raw = sys.stdin.buffer.read(32_769)
    require(len(raw) <= 32_768, "Audit request exceeds bound.")
    request = json.loads(raw)
    value = request["plan"]
    value["lanes"] = tuple(value["lanes"])
    result = audit(
        RuntimePlan(**value),
        Path(request["capture"]),
        Path(request["archive"]),
        complete=request["complete"],
    )
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
