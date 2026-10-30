"""Bounded read-only metadata inventory for a separately completed NAS copy.

Footer statistics are observations, never payload/transfer integrity evidence.
The cooperative deadline cannot interrupt a filesystem call blocked in NFS.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from hyperliquid_bot.local_mode import require_local_paper_mode
from hyperliquid_bot.parquet_research import _CREATE_SEGMENT_TABLE
from hyperliquid_bot.raw_research import RAW_RESEARCH_SCHEMA_VERSION
from hyperliquid_bot.reconstructable_paths import (
    DATA1A_CLAIM_NAME,
    DATA1A_HEALTH_NAME,
    DATA1A_PARQUET_GLOB,
    DATA1A_PATH_CONTRACT_ID,
    DATA1A_RAW_DIR_NAME,
    DATA1A_RELATIVE_PREFIX,
    DATA1B_PATH_CONTRACT_ID,
    DATA1B_RELATIVE_PREFIX,
    DATA1C_PATH_CONTRACT_ID,
    DATA1C_RELATIVE_PREFIX,
    DATA1D_PATH_CONTRACT_ID,
    DATA1D_RELATIVE_PREFIX,
    DATA1E_PATH_CONTRACT_ID,
    DATA1E_RELATIVE_PREFIX,
    DATA1F_PATH_CONTRACT_ID,
    DATA1F_RELATIVE_PREFIX,
    require_run_id,
)

SCHEMA = "migration-metadata-inventory-v1"
_REMOTE_FILESYSTEMS = frozenset({"nfs", "nfs4", "cifs"})
_LANES = (
    (DATA1A_RELATIVE_PREFIX, DATA1A_PATH_CONTRACT_ID),
    (DATA1B_RELATIVE_PREFIX, DATA1B_PATH_CONTRACT_ID),
    (DATA1C_RELATIVE_PREFIX, DATA1C_PATH_CONTRACT_ID),
    (DATA1D_RELATIVE_PREFIX, DATA1D_PATH_CONTRACT_ID),
    (DATA1E_RELATIVE_PREFIX, DATA1E_PATH_CONTRACT_ID),
    (DATA1F_RELATIVE_PREFIX, DATA1F_PATH_CONTRACT_ID),
)


@dataclass(frozen=True, slots=True)
class MountIdentity:
    target: Path
    source: str
    filesystem: str
    device: str = ""
    mount_id: str = ""


@dataclass(frozen=True, slots=True)
class ScanLimits:
    entries: int = 500
    runs: int = 12
    footers: int = 12
    depth: int = 5
    seconds: float = 10.0
    json_bytes: int = 131_072
    footer_bytes: int = 2_097_152

    def __post_init__(self) -> None:
        for name, maximum in (
            ("entries", 5000),
            ("runs", 100),
            ("footers", 100),
            ("depth", 8),
            ("json_bytes", 524288),
            ("footer_bytes", 8388608),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}.")
        if not math.isfinite(self.seconds) or not 0 < self.seconds <= 60:
            raise ValueError("seconds must be finite, positive and at most 60.")


_DEFAULT_LIMITS = ScanLimits()


class _Stop(Exception):
    pass


class _Unsafe(Exception):
    pass


class _Scan:
    def __init__(
        self, mount: MountIdentity, limits: ScanLimits, clock: Callable[[], float]
    ) -> None:
        self.mount = mount
        self.limits = limits
        self.clock = clock
        self.deadline = clock() + limits.seconds
        self.entries = 0
        self.runs = 0
        self.footers = 0
        self.reasons: set[str] = set()

    def tick(self) -> None:
        if self.clock() >= self.deadline:
            self.reasons.add("cooperative_deadline")
            raise _Stop

    def _check_descriptor(self, descriptor: int) -> os.stat_result:
        value = os.fstat(descriptor)
        device = f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}"
        if device != self.mount.device or _descriptor_mount_id(descriptor) != self.mount.mount_id:
            raise _Unsafe("changed_or_nested_mount")
        if stat.S_ISLNK(value.st_mode):
            raise _Unsafe("symlink_not_permitted")
        return value

    def _open(self, path: Path, flags: int) -> int:
        self.tick()
        _absolute(path)
        path.relative_to(self.mount.target)
        # Anchor every lookup to its already-open parent. O_NOFOLLOW on the leaf
        # alone cannot prevent an ancestor being replaced by a symlink mid-walk.
        descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY)
        current = Path("/")
        try:
            for index, part in enumerate(path.parts[1:]):
                self.tick()
                current /= part
                last = index == len(path.parts) - 2
                next_descriptor = os.open(
                    part,
                    (flags if last else os.O_PATH | os.O_DIRECTORY) | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
                if current.is_relative_to(self.mount.target):
                    self._check_descriptor(descriptor)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def safe(self, path: Path) -> os.stat_result:
        descriptor = self._open(path, os.O_PATH)
        try:
            return self._check_descriptor(descriptor)
        finally:
            os.close(descriptor)

    def children(self, directory: Path) -> Iterator[Path]:
        descriptor = self._open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK)
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    self.tick()
                    if self.entries >= self.limits.entries:
                        self.reasons.add("entry_limit")
                        raise _Stop
                    self.entries += 1
                    yield directory / entry.name
        finally:
            os.close(descriptor)

    def open_regular(self, path: Path) -> int:
        descriptor = self._open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(self._check_descriptor(descriptor).st_mode):
                raise _Unsafe("not_a_regular_file_on_expected_mount")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise


def _descriptor_mount_id(descriptor: int) -> str:
    # fdinfo is kernel-local metadata. Device numbers alone cannot distinguish
    # a nested bind mount of another directory on the same remote filesystem.
    for line in Path(f"/proc/self/fdinfo/{descriptor}").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition(":")
        if key == "mnt_id" and value.strip().isdigit():
            return value.strip()
    raise _Unsafe("descriptor_mount_identity_unavailable")


def _absolute(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise _Unsafe("absolute_paths_without_parent_traversal_required")
    return path


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def parse_mountinfo(text: str) -> tuple[MountIdentity, ...]:
    """Parse kernel mount identities without exposing mount options/credentials."""
    result: list[MountIdentity] = []
    for line in text.splitlines():
        before, separator, after = line.partition(" - ")
        fields, trailing = before.split(), after.split()
        if not separator or len(fields) < 6 or len(trailing) < 3:
            raise _Unsafe("invalid_kernel_mount_metadata")
        result.append(
            MountIdentity(
                Path(_unescape(fields[4])),
                _unescape(trailing[1]),
                trailing[0],
                fields[2],
                fields[0],
            )
        )
    return tuple(result)


def _read_mountinfo() -> str:
    return Path("/proc/self/mountinfo").read_text(encoding="utf-8")


def _bound_mount(
    root: Path, expected: MountIdentity
) -> tuple[MountIdentity, tuple[MountIdentity, ...]]:
    _absolute(root)
    _absolute(expected.target)
    if (
        expected.target == Path("/")
        or expected.filesystem not in _REMOTE_FILESYSTEMS
        or not expected.source
        or "@" in expected.source
    ):
        raise _Unsafe("explicit_remote_mount_identity_required")
    mounts = parse_mountinfo(_read_mountinfo())
    candidates = [mount for mount in mounts if root.is_relative_to(mount.target)]
    if not candidates:
        raise _Unsafe("root_has_no_matching_mount")
    observed = max(candidates, key=lambda mount: len(mount.target.parts))
    if (observed.target, observed.source, observed.filesystem) != (
        expected.target,
        expected.source,
        expected.filesystem,
    ):
        raise _Unsafe("mount_identity_mismatch")
    if any(
        mount.target != observed.target and mount.target.is_relative_to(root) for mount in mounts
    ):
        raise _Unsafe("nested_mount_inside_selected_root")
    return observed, mounts


def _signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _json_file(scan: _Scan, path: Path) -> tuple[str, dict[str, object] | None]:
    try:
        descriptor = scan.open_regular(path)
        try:
            before = os.fstat(descriptor)
            if before.st_size > scan.limits.json_bytes:
                scan.reasons.add("json_byte_limit")
                return "not_checked", None
            content = bytearray()
            # A short read can end at a valid JSON prefix. Read through EOF,
            # within the byte budget, before accepting the complete document.
            while len(content) <= scan.limits.json_bytes:
                scan.tick()
                chunk = os.read(descriptor, scan.limits.json_bytes + 1 - len(content))
                if not chunk:
                    break
                content.extend(chunk)
            if _signature(before) != _signature(os.fstat(descriptor)):
                return "in_progress", None
            if len(content) != before.st_size:
                return "in_progress", None
        finally:
            os.close(descriptor)
        value = json.loads(content, object_pairs_hook=_unique_object)
        if len(content) > scan.limits.json_bytes or not isinstance(value, dict):
            return "invalid", None
        return "present", value
    except FileNotFoundError:
        return "missing", None
    except (OSError, _Unsafe, ValueError):
        return "invalid", None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _footer(scan: _Scan, path: Path, venue: str, products: frozenset[str]) -> dict[str, object]:
    if scan.footers >= scan.limits.footers:
        scan.reasons.add("footer_limit")
        return {"state": "not_checked", "reason": "footer_limit"}
    scan.footers += 1
    descriptor = scan.open_regular(path)
    try:
        before = os.fstat(descriptor)
        trailer = os.pread(descriptor, 8, max(0, before.st_size - 8))
        if before.st_size < 12 or os.pread(descriptor, 4, 0) != b"PAR1" or trailer[-4:] != b"PAR1":
            return {"state": "invalid", "reason": "incomplete_or_invalid_parquet_envelope"}
        if int.from_bytes(trailer[:4], "little") > scan.limits.footer_bytes:
            scan.reasons.add("footer_byte_limit")
            return {"state": "not_checked", "reason": "footer_byte_limit"}
        connection = duckdb.connect(":memory:")
        try:
            connection.execute("SET threads = 1")
            connection.execute(_CREATE_SEGMENT_TABLE)
            expected = [
                (row[0], row[1]) for row in connection.execute("DESCRIBE raw_segment").fetchall()
            ]
            source = f"/proc/self/fd/{descriptor}"
            actual = [
                (row[0], row[1])
                for row in connection.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [source]
                ).fetchall()
            ]
            if actual != expected:
                return {"state": "invalid", "reason": "raw_schema_mismatch"}
            rows = connection.execute(
                "SELECT path_in_schema, "
                "CASE WHEN min_is_exact AND max_is_exact THEN stats_min_value END, "
                "CASE WHEN min_is_exact AND max_is_exact THEN stats_max_value END, "
                "stats_null_count, "
                "row_group_num_rows FROM parquet_metadata(?) WHERE path_in_schema IN "
                "('schema_version', 'venue', 'product', 'received_utc_ns')",
                [source],
            ).fetchall()
        finally:
            connection.close()
        if _signature(before) != _signature(os.fstat(descriptor)):
            return {"state": "in_progress", "reason": "part_changed_during_read"}
        return _footer_statistics(rows, venue, products)
    except (duckdb.Error, OSError, ValueError):
        return {"state": "invalid", "reason": "parquet_metadata_unreadable"}
    finally:
        os.close(descriptor)


def _footer_statistics(
    rows: Sequence[tuple[object, ...]],
    venue: str,
    products: frozenset[str] = frozenset({"BTC-PERP"}),
) -> dict[str, object]:
    stamps: list[tuple[int, int]] = []
    fields: set[str] = set()
    for name, low, high, nulls, count in rows:
        fields.add(str(name))
        if nulls is not None and nulls != 0:
            return {"state": "invalid", "reason": "invalid_required_footer_values"}
        if low is None or high is None or nulls is None:
            return {"state": "not_checked", "reason": "footer_statistics_missing"}
        if not isinstance(count, int) or count <= 0:
            return {"state": "invalid", "reason": "invalid_required_footer_values"}
        if name == "schema_version" and (low != str(RAW_RESEARCH_SCHEMA_VERSION) or high != low):
            return {"state": "invalid", "reason": "raw_schema_version_mismatch"}
        if name == "venue" and (low != venue or high != venue):
            return {"state": "invalid", "reason": "venue_footer_bounds_mismatch"}
        if name == "product" and (low not in products or high not in products):
            return {"state": "invalid", "reason": "product_footer_bounds_mismatch"}
        if name == "received_utc_ns":
            stamps.append((int(str(low)), int(str(high))))
    if fields != {"schema_version", "venue", "product", "received_utc_ns"} or not stamps:
        return {"state": "not_checked", "reason": "receipt_statistics_missing"}
    if any(low < 0 or high < low for low, high in stamps):
        return {"state": "invalid", "reason": "invalid_receipt_statistics"}
    return {
        "state": "present",
        "receipt_utc_ns_min": min(low for low, _ in stamps),
        "receipt_utc_ns_max": max(high for _, high in stamps),
        "record_coverage": "not_checked",
    }


def _capture_run(
    scan: _Scan,
    directory: Path,
    prefix: tuple[str, ...],
    contract: str,
    result: dict[str, object],
) -> dict[str, object]:
    try:
        require_run_id(directory.name)
    except (TypeError, ValueError):
        result["claim"] = "invalid"
        return result
    state, claim = _json_file(scan, directory / DATA1A_CLAIM_NAME)
    if claim is not None:
        required = {
            "schema": f"{prefix[0]}-retained-capture-claim-v1",
            "run_id": directory.name,
            "venue": prefix[1],
            "product": prefix[2],
            "path_contract": contract,
            "signing": False,
        }
        if any(
            type(claim.get(key)) is not type(value) or claim.get(key) != value
            for key, value in required.items()
        ):
            state = "invalid"
    result["claim"] = state
    health_state, health = _json_file(scan, directory / DATA1A_HEALTH_NAME)
    if health is not None and (
        health.get("schema") != f"{prefix[0]}-retained-capture-health-v1"
        or health.get("run_id") != directory.name
        or type(health.get("status")) is not str
        or health.get("status") not in {"COMPLETED", "OPERATOR_STOP", "FAILED"}
    ):
        health_state = "invalid"
    result["health"] = health_state
    result["health_status"] = (
        health.get("status") if health is not None and health_state == "present" else None
    )
    parts: list[dict[str, object]] = []
    published_count = 0
    products = {prefix[2]}
    if claim is not None:
        coins = claim.get("started_coins")
        if prefix[0] == "data-1a" and isinstance(coins, list):
            products.update(
                f"{coin}-PERP"
                for coin in coins
                if isinstance(coin, str) and re.fullmatch(r"[A-Z0-9]{1,16}", coin)
            )
        if prefix[0] == "data-1b":
            products.add("BTC/USD")
        if prefix[0] == "data-1f":
            products.update({"BTCUSDT-SPOT", "BTCUSDT-USDS-M-PERPETUAL"})
    result["parts"] = parts
    try:
        for path in scan.children(directory / DATA1A_RAW_DIR_NAME):
            if path.name.endswith(".partial"):
                parts.append({"name": path.name, "state": "in_progress"})
            elif fnmatch.fnmatchcase(path.name, DATA1A_PARQUET_GLOB):
                published_count += 1
                result["published_parts_seen"] = published_count
                try:
                    metadata = (
                        _footer(scan, path, prefix[1], frozenset(products))
                        if state == "present"
                        else {"state": "not_checked", "reason": "claim_not_validated"}
                    )
                except (OSError, _Unsafe):
                    metadata = {"state": "invalid", "reason": "unsafe_or_missing_part"}
                parts.append({"name": path.name, **metadata})
        if health is not None and type(health.get("parquet_files")) is int:
            result["part_count_check"] = {
                "state": "present" if health["parquet_files"] == published_count else "in_progress",
                "expected": health["parquet_files"],
                "seen": published_count,
            }
    except _Stop:
        result["raw_directory"] = "not_checked"
    except FileNotFoundError:
        result["raw_directory"] = "missing"
    except (OSError, _Unsafe):
        result["raw_directory"] = "invalid"
    return result


def _captures(scan: _Scan, root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = [
        {
            "lane": prefix[0],
            "venue": prefix[1],
            "product": prefix[2],
            "state": "not_checked",
            "runs": [],
        }
        for prefix, _ in _LANES
    ]
    for (prefix, contract), lane in zip(_LANES, result, strict=True):
        runs: list[dict[str, object]] = []
        lane.update(state="present", runs=runs)
        try:
            for path in scan.children(root.joinpath(*prefix)):
                if scan.runs >= scan.limits.runs:
                    scan.reasons.add("run_limit")
                    raise _Stop
                if not stat.S_ISDIR(scan.safe(path).st_mode):
                    continue
                scan.runs += 1
                run: dict[str, object] = {
                    "run_id": path.name,
                    "claim": "not_checked",
                    "health": "not_checked",
                    "parts": [],
                    "published_parts_seen": 0,
                }
                runs.append(run)
                _capture_run(scan, path, prefix, contract, run)
        except _Stop:
            lane["selection_complete"] = False
            return result
        except FileNotFoundError:
            lane["state"] = "missing"
        except (OSError, _Unsafe):
            lane["state"] = "invalid"
        if not runs and lane["state"] == "present":
            lane["state"] = "not_checked"
            lane["reason"] = "no_run_directories_observed"
    return result


def _history(scan: _Scan, root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    result: dict[str, object] = {
        "state": "present",
        "files": files,
        "checksums": "not_checked",
        "catalog_targets": "not_checked",
    }
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            for path in scan.children(directory):
                metadata = scan.safe(path)
                if stat.S_ISDIR(metadata.st_mode):
                    if depth >= scan.limits.depth:
                        scan.reasons.add("history_depth_limit")
                    else:
                        pending.append((path, depth + 1))
                elif stat.S_ISREG(metadata.st_mode) and (
                    path.suffix.lower() in {".zip", ".csv", ".parquet", ".checksum", ".partial"}
                    or path.name.endswith(".sources.json")
                    or path.name in {"catalog.sql", "research.duckdb"}
                ):
                    files.append(
                        {
                            "path": str(path.relative_to(root)),
                            "bytes": metadata.st_size,
                            "state": "in_progress" if path.name.endswith(".partial") else "present",
                            "content_integrity": "not_checked",
                            "filename_dates": re.findall(r"20\d{2}-\d{2}(?:-\d{2})?", path.name),
                        }
                    )
        except FileNotFoundError:
            result["state"] = "missing" if directory == root else "in_progress"
        except _Stop:
            result["selection_complete"] = False
            return result
        except (OSError, _Unsafe):
            result["state"] = "invalid"
    if not files and result["state"] == "present":
        result["state"] = "not_checked"
    return result


def inventory(
    root: Path,
    expected: MountIdentity,
    *,
    copy_complete: bool,
    capture_subdir: str = "data-capture",
    history_subdir: str = "hist-archives",
    limits: ScanLimits = _DEFAULT_LIMITS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Inspect explicit mount-bound paths; stdout CLI is the only report sink."""
    report: dict[str, object] = {
        "schema": SCHEMA,
        "migration_complete": False,
        "transfer_integrity": "not_checked",
        "record_coverage": "not_checked",
        "mount": {"state": "not_checked"},
        "root": {"state": "not_checked"},
        "captures": [],
        "history": {"state": "not_checked"},
        "limits": asdict(limits),
        "limited_by": [],
        "selection_complete": False,
    }
    if copy_complete is not True:
        report["reason"] = "copy_completion_must_be_confirmed_before_inspection"
        return report
    try:
        for relative in (capture_subdir, history_subdir):
            if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise _Unsafe("relative_data_subdirectories_required")
        observed, _mounts = _bound_mount(root, expected)
        report["mount"] = {
            "state": "present",
            "target": str(observed.target),
            "source": observed.source,
            "filesystem": observed.filesystem,
        }
        scan = _Scan(observed, limits, clock)
    except (OSError, _Unsafe) as error:
        report["mount"] = {"state": "invalid"}
        report["reason"] = (
            str(error) if isinstance(error, _Unsafe) else "mount_identity_unavailable"
        )
        return report
    try:
        if not stat.S_ISDIR(scan.safe(root).st_mode):
            raise _Unsafe("root_is_not_a_directory")
        report["root"] = {"state": "present"}
        try:
            report["captures"] = _captures(scan, root / capture_subdir)
            if not scan.reasons.intersection({"entry_limit", "run_limit", "cooperative_deadline"}):
                report["history"] = _history(scan, root / history_subdir)
            report["selection_complete"] = not scan.reasons
        except _Stop:
            pass
        report["limited_by"] = sorted(scan.reasons)
        report["observed_counts"] = {
            "entries": scan.entries,
            "runs": scan.runs,
            "footers": scan.footers,
        }
        # Kernel metadata only: detect an export unmounted/replaced during the inventory.
        final, _ = _bound_mount(root, expected)
        if final != observed:
            raise _Unsafe("mount_changed_during_inventory")
    except FileNotFoundError:
        report["root"] = {"state": "missing"}
    except _Stop:
        report["limited_by"] = sorted(scan.reasons)
    except (OSError, _Unsafe):
        report["mount"] = {"state": "invalid"}
        report["root"] = {"state": "invalid"}
        report["selection_complete"] = False
    return report


def _states(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"state", "claim", "health", "raw_directory"} and isinstance(item, str):
                yield item
            yield from _states(item)
    elif isinstance(value, list):
        for item in value:
            yield from _states(item)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--expected-mount-target", required=True, type=Path)
    parser.add_argument("--expected-mount-source", required=True)
    parser.add_argument("--expected-filesystem", required=True, choices=sorted(_REMOTE_FILESYSTEMS))
    parser.add_argument("--copy-complete", action="store_true")
    parser.add_argument("--capture-subdir", default="data-capture")
    parser.add_argument("--history-subdir", default="hist-archives")
    for name, default in (("entries", 500), ("runs", 12), ("footers", 12), ("depth", 5)):
        parser.add_argument(f"--max-{name}", type=int, default=default)
    parser.add_argument("--max-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        require_local_paper_mode(os.environ.get("TRADING_MODE"))
        limits = ScanLimits(
            args.max_entries, args.max_runs, args.max_footers, args.max_depth, args.max_seconds
        )
        report = inventory(
            args.root,
            MountIdentity(
                args.expected_mount_target, args.expected_mount_source, args.expected_filesystem
            ),
            copy_complete=args.copy_complete,
            capture_subdir=args.capture_subdir,
            history_subdir=args.history_subdir,
            limits=limits,
        )
    except (ValueError, TypeError):
        parser.error("Invalid bounds or non-PAPER development mode.")
    print(json.dumps(report, indent=2, sort_keys=True))
    states = set(_states(report))
    if "invalid" in states:
        return 2
    return (
        0
        if report["selection_complete"]
        and not states.intersection({"missing", "not_checked", "in_progress"})
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
