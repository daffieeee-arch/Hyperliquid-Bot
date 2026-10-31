"""Bounded credentialless public collection, closed-chunk publication and health."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .collector_storage import (
    exclusive_json,
    private_directory,
    publish_closed_chunk,
    read_bytes,
    read_json,
    remove_verified_spool_duplicate,
)
from .local_mode import require_local_paper_mode

GIB: Final = 1024**3
SPOOL_MAX_BYTES: Final = 10 * GIB
SPOOL_STOP_BYTES: Final = 8 * GIB
LANES: Final = {
    "hyperliquid": ("hyperliquid_raw_research", "data-1a/hyperliquid/BTC-PERP"),
    "binance": ("binance_public_research", "data-1f/binance/BTCUSDT"),
    "kraken": ("kraken_l3_research", "data-1b/kraken/BTC-USD"),
    "bitvavo-standard": ("bitvavo_standard_research", "data-1d/bitvavo/BTC-EUR"),
}
WINDOW_PLAN_NAME: Final = "collector-plan.json"
WINDOW_PLAN_MAX_BYTES: Final = 16_384


@dataclass(frozen=True)
class RuntimePlan:
    campaign: str
    lanes: tuple[str, ...]
    chunk_seconds: int
    chunks: int
    pilot: bool
    source_commit: str
    image_digest: str
    prune_spool: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.chunk_seconds) is not int
            or type(self.chunks) is not int
            or type(self.pilot) is not bool
            or type(self.prune_spool) is not bool
        ):
            raise ValueError("Collector counts and flags require exact built-in types.")
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", self.campaign) is None:
            raise ValueError("Invalid collector campaign identity.")
        if (
            not self.lanes
            or len(set(self.lanes)) != len(self.lanes)
            or any(lane not in LANES for lane in self.lanes)
        ):
            raise ValueError("Select only distinct supported public collector lanes.")
        if self.pilot:
            if self.chunk_seconds != 600 or self.chunks != 1:
                raise ValueError("First pilot is exactly one 600-second chunk.")
        elif not 7200 <= self.chunk_seconds <= 21600:
            raise ValueError("Retained chunks must be explicitly 2-6 hours after pilot review.")
        if not 1 <= self.chunks <= 36 or self.chunk_seconds * self.chunks > 259_200:
            raise ValueError("Bounded campaign must not exceed 72 hours.")
        if re.fullmatch(r"[0-9a-f]{40}", self.source_commit) is None:
            raise ValueError("Full reviewed source commit required.")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_digest) is None:
            raise ValueError("Immutable verified image digest required.")


def public_environment(source: Mapping[str, str]) -> dict[str, str]:
    require_local_paper_mode(source.get("TRADING_MODE"))
    if "D41_EXECUTION_MODE" in source:
        raise ValueError("Collector cannot receive execution configuration.")
    for name in source:
        if name != "GPG_KEY" and re.search(r"(?:_KEY|_SECRET|_TOKEN|PASSWORD|CREDENTIALS)$", name):
            raise ValueError("Collector container must not contain account or signing credentials.")
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": "/app/src",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TRADING_MODE": "PAPER",
        "TZ": "UTC",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }


def native_command(lane: str, artifact_root: Path, run_id: str, seconds: int) -> list[str]:
    module = LANES[lane][0]
    return [
        sys.executable,
        "-B",
        "-m",
        "hyperliquid_bot." + module,
        "--artifact-root",
        str(artifact_root),
        "--run-id",
        run_id,
        "--duration-seconds",
        str(seconds),
    ]


def capture_window_plan(
    plan: RuntimePlan,
    lane: str,
    chunk: int,
    clock: dict[str, object],
    *,
    utc_before_ns: int,
    monotonic_ns: int,
    utc_after_ns: int,
) -> dict[str, object]:
    """Record a prelaunch lower bound on an uninterrupted native timer's end.

    The UTC/monotonic bracket is sampled before spawning the child. Early
    failures remain possible; this record cannot certify an actual stop time.
    """
    if lane not in plan.lanes or type(chunk) is not int or not 0 <= chunk < plan.chunks:
        raise ValueError("Window plan must identify this campaign's lane and chunk.")
    if (
        any(
            type(value) is not int or value <= 0
            for value in (utc_before_ns, monotonic_ns, utc_after_ns)
        )
        or not 0 <= utc_after_ns - utc_before_ns <= 1_000_000_000
    ):
        raise ValueError("Prelaunch clock bracket is invalid or exceeds one second.")
    sampled = clock.get("sampled_utc_ns")
    if type(sampled) is not int or not 0 <= utc_before_ns - sampled <= 120_000_000_000:
        raise ValueError("Window plan requires fresh prelaunch host-clock evidence.")
    _, venue, product = LANES[lane][1].split("/")
    duration_ns = plan.chunk_seconds * 1_000_000_000
    result = {
        "schema": "public-collector-window-plan-v1",
        "campaign": plan.campaign,
        "run_id": f"{plan.campaign}-{chunk:04d}",
        "chunk_index": chunk,
        "lane": lane,
        "venue": venue,
        "product": product,
        "source_commit": plan.source_commit,
        "image_digest": plan.image_digest,
        "requested_duration_seconds": plan.chunk_seconds,
        "prelaunch_utc_ns": utc_before_ns,
        "prelaunch_monotonic_ns": monotonic_ns,
        "prelaunch_utc_after_ns": utc_after_ns,
        "clock_pair_order": "utc-monotonic-utc",
        "earliest_planned_end_utc_ns": utc_before_ns + duration_ns,
        "earliest_planned_end_monotonic_ns": monotonic_ns + duration_ns,
        "end_semantics": "lower-bound-on-uninterrupted-native-timer-not-actual-stop",
        "host_clock_evidence": clock,
    }
    content = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(content) > WINDOW_PLAN_MAX_BYTES:
        raise ValueError("Prelaunch planning record exceeds its bound.")
    return result


def allocated_bytes(root: Path) -> int:
    total = 0

    def walking_error(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(root, followlinks=False, onerror=walking_error):
        if any((Path(current) / name).is_symlink() for name in (*directories, *files)):
            raise ValueError("Symlink in runtime-owned spool.")
        for name in files:
            try:
                total += (Path(current) / name).stat(follow_symlinks=False).st_blocks * 512
            except FileNotFoundError:
                # Native partial->closed rotation and our health rename are
                # atomic. The 2 GiB reserve covers this bounded sampling race.
                continue
    return total


def require_network_marker(path: Path, plan: RuntimePlan) -> dict[str, object]:
    marker = read_json(path)
    if (
        marker.get("schema") != "collector-container-network-v1"
        or marker.get("campaign") != plan.campaign
        or marker.get("source_commit") != plan.source_commit
        or marker.get("image_digest") != plan.image_digest
        or marker.get("container_netns_inode") != Path("/proc/self/ns/net").stat().st_ino
        or type(marker.get("download_bits_per_second")) is not int
        or marker.get("download_bits_per_second") != 100_000_000
        or type(marker.get("upload_bits_per_second")) is not int
        or marker.get("upload_bits_per_second") != 5_000_000
        or marker.get("kernel_tc_verified") is not True
    ):
        raise ValueError("Independent container-only network enforcement is not verified.")
    return marker


def clock_evidence(path: Path, *, now_ns: int) -> dict[str, object]:
    report = read_json(path)
    sampled = report.get("sampled_utc_ns")
    if (
        report.get("schema") != "collector-host-clock-v1"
        or report.get("synchronized") is not True
        or type(sampled) is not int
        or not 0 <= now_ns - sampled <= 120_000_000_000
    ):
        raise ValueError("Host clock evidence is missing, stale or unsynchronized.")
    numeric: dict[str, float] = {}
    for name in ("system_offset_seconds", "root_delay_seconds", "root_dispersion_seconds"):
        value = report.get(name)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError("Host clock uncertainty is not finite.")
        numeric[name] = float(value)
    if (
        abs(numeric["system_offset_seconds"]) > 0.050
        or not 0 <= numeric["root_dispersion_seconds"] <= 0.250
        or not 0 <= numeric["root_delay_seconds"] <= 0.500
    ):
        raise ValueError("Host clock exceeds the declared engineering alignment bounds.")
    return report


def mounted_filesystem(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        lines = Path(f"/proc/self/fdinfo/{descriptor}").read_text().splitlines()
        mount_id = next(
            line.partition(":")[2].strip() for line in lines if line.startswith("mnt_id:")
        )
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, separator, after = line.partition(" - ")
            if separator and before.split()[0] == mount_id:
                return after.split()[0]
        raise ValueError("Effective collector storage mount unavailable.")
    finally:
        os.close(descriptor)


def _status(path: Path, report: dict[str, object]) -> None:
    report["updated_utc_ns"] = time.time_ns()
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, allow_nan=False)
    temporary.replace(path)


def _catalogue(raw: Path, database: Path) -> None:
    # Catalog SQL comes from this reviewed image, never from a copied database.
    from .parquet_research import create_research_catalog

    create_research_catalog(raw, database)


def network_counters() -> dict[str, int]:
    for line in Path("/proc/net/dev").read_text().splitlines():
        name, separator, raw = line.partition(":")
        if separator and name.strip() == "eth0":
            values = raw.split()
            return {"received_bytes": int(values[0]), "transmitted_bytes": int(values[8])}
    raise ValueError("Verified container interface counters unavailable.")


def append_metric(path: Path, clock: dict[str, object], allocated: int) -> None:
    report = {
        "sampled_utc_ns": time.time_ns(),
        "sampled_monotonic_ns": time.monotonic_ns(),
        "clock": clock,
        "spool_allocated_bytes": allocated,
        "network": network_counters(),
    }
    content = (json.dumps(report, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        if os.fstat(descriptor).st_size + len(content) > 1024**2:
            raise ValueError("Collector metric file exceeds its bound.")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def collect_campaign(
    plan: RuntimePlan,
    *,
    spool: Path,
    capture: Path,
    archive: Path,
    network_marker: Path,
    clock_path: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    environment = public_environment(os.environ)
    os.umask(0o077)
    if os.environ.get("COLLECTOR_SOURCE_COMMIT") != plan.source_commit or os.getuid() != 1000:
        raise ValueError("Run only the reviewed source image as nonrootUID1000.")
    # Native claim stamping reads this explicit start-time version because the
    # immutable image intentionally contains no Git checkout/executable.
    environment["CAPTURE_CODE_SHA"] = plan.source_commit
    for root in (spool, capture, archive):
        private_directory(root)
    if (
        mounted_filesystem(spool) not in {"ext4", "xfs", "btrfs"}
        or mounted_filesystem(capture) not in {"nfs", "nfs4"}
        or mounted_filesystem(archive) not in {"nfs", "nfs4"}
    ):
        raise ValueError("Verified local spool and two explicit NFS destinations required.")
    if capture == archive:
        raise ValueError("Capture and archive must be distinct approved namespaces.")
    if allocated_bytes(spool) >= SPOOL_STOP_BYTES:
        raise ValueError("Existing spool exceeds capture admission bound.")
    for root in (spool, capture, archive):
        space = os.statvfs(root)
        if space.f_bavail * space.f_frsize < 2 * GIB:
            raise ValueError("Collector roots need at least 2 GiB available headroom.")
    # Root applies TC only to this waiting container's network namespace.
    # There are no adapters or network clients before the marker is verified.
    startup_deadline = time.monotonic() + 120
    while True:
        try:
            network = require_network_marker(network_marker, plan)
            clock = clock_evidence(clock_path, now_ns=time.time_ns())
            break
        except FileNotFoundError:
            if time.monotonic() >= startup_deadline:
                raise ValueError(
                    "Host network/clock verification was not provided in time."
                ) from None
            sleep(1)
    lock_descriptor = os.open(spool / "runtime.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    campaign = spool / plan.campaign
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        campaign.mkdir(mode=0o700)
        for root in (capture, archive):
            (root / plan.campaign).mkdir(mode=0o700)
    except BaseException:
        os.close(lock_descriptor)
        raise
    provenance = {
        "schema": "public-collector-campaign-v1",
        "source_commit": plan.source_commit,
        "image_digest": plan.image_digest,
        "network_verification": network,
        "clock_at_start": clock,
        "lanes": plan.lanes,
        "chunk_seconds": plan.chunk_seconds,
        "chunks": plan.chunks,
        "spool_ceiling_bytes": SPOOL_MAX_BYTES,
        "spool_stop_bytes": SPOOL_STOP_BYTES,
        "spool_enforcement": "monitor-stop-with-reserve-not-filesystem-quota",
        "cross_chunk_continuity": "new-sessions-with-explicit-gaps-not-seamless",
        "signing": False,
        "authenticated_feeds": False,
        "research_ready": False,
    }
    try:
        exclusive_json(campaign / "campaign.json", provenance)
    except BaseException:
        os.close(lock_descriptor)
        raise
    status_path = spool / "runtime-health.json"
    status: dict[str, object] = {
        "schema": "public-collector-runtime-health-v1",
        "phase": "starting",
        "campaign": plan.campaign,
        "source_commit": plan.source_commit,
        "image_digest": plan.image_digest,
        "research_ready": False,
    }
    children: list[subprocess.Popen[bytes]] = []
    stopping = False

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        for chunk in range(plan.chunks):
            clock_evidence(clock_path, now_ns=time.time_ns())
            if stopping or allocated_bytes(spool) >= SPOOL_STOP_BYTES:
                raise RuntimeError("Capture admission stopped at operator or spool boundary.")
            run_id = f"{plan.campaign}-{chunk:04d}"
            chunk_root = campaign / f"chunk-{chunk:04d}"
            chunk_root.mkdir(mode=0o700)
            metrics_path = chunk_root / "collector-metrics.ndjson"
            metrics_path.touch(mode=0o600, exist_ok=False)
            last_sample = float("-inf")
            status.update(phase="capturing", chunk=chunk, run_id=run_id)
            window_plans: dict[str, dict[str, object]] = {}
            window_hashes: dict[str, str] = {}
            for lane in plan.lanes:
                lane_root = chunk_root / lane
                lane_root.mkdir(mode=0o700)
                clock = clock_evidence(clock_path, now_ns=time.time_ns())
                utc_before = time.time_ns()
                monotonic = time.monotonic_ns()
                utc_after = time.time_ns()
                window = capture_window_plan(
                    plan,
                    lane,
                    chunk,
                    clock,
                    utc_before_ns=utc_before,
                    monotonic_ns=monotonic,
                    utc_after_ns=utc_after,
                )
                # Persist before Popen, outside the native create-only run root.
                window_hashes[lane] = exclusive_json(lane_root / WINDOW_PLAN_NAME, window)
                window_plans[lane] = window
                output = (lane_root / "collector-output.log").open("xb")
                try:
                    child = subprocess.Popen(
                        native_command(lane, lane_root, run_id, plan.chunk_seconds),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
                finally:
                    output.close()
                children.append(child)
            deadline = time.monotonic() + plan.chunk_seconds + 180
            while any(child.poll() is None for child in children):
                clock = clock_evidence(clock_path, now_ns=time.time_ns())
                allocated = allocated_bytes(spool)
                status.update(spool_allocated_bytes=allocated, clock=clock)
                if time.monotonic() - last_sample >= 30:
                    append_metric(metrics_path, clock, allocated)
                    last_sample = time.monotonic()
                _status(status_path, status)
                if (
                    stopping
                    or allocated >= SPOOL_STOP_BYTES
                    or time.monotonic() > deadline
                    or any(child.poll() not in (None, 0) for child in children)
                ):
                    raise RuntimeError(
                        "Capture stopped at operator, resource or integrity boundary."
                    )
                sleep(1)
            if any(child.returncode != 0 for child in children):
                raise RuntimeError("Native public collector failed; retain incomplete spool.")
            children.clear()
            append_metric(
                metrics_path,
                clock_evidence(clock_path, now_ns=time.time_ns()),
                allocated_bytes(spool),
            )
            metrics = read_bytes(metrics_path, limit=1024**2)
            status["phase"] = "publishing"

            def progress() -> None:
                status["clock"] = clock_evidence(clock_path, now_ns=time.time_ns())
                _status(status_path, status)
                if stopping:
                    raise RuntimeError("Operator stopped collector publication.")

            for lane in plan.lanes:
                source = chunk_root / lane / LANES[lane][1] / run_id
                health = read_json(source / "capture-health.json", limit=1024**2)
                if (
                    health.get("status") != "COMPLETED"
                    or health.get("credentialless") is not True
                    or health.get("run_id") != run_id
                    or health.get("duration_seconds") != plan.chunk_seconds
                ):
                    raise RuntimeError("Only completed credentialless chunks may be published.")
                original_plan = read_bytes(
                    chunk_root / lane / WINDOW_PLAN_NAME, limit=WINDOW_PLAN_MAX_BYTES
                )
                if hashlib.sha256(original_plan).hexdigest() != window_hashes[lane]:
                    raise RuntimeError(
                        "Original prelaunch planning bytes changed or were short-read."
                    )
                if (
                    exclusive_json(source / WINDOW_PLAN_NAME, window_plans[lane])
                    != window_hashes[lane]
                ):
                    raise RuntimeError("Closed-run planning copy differs from its prelaunch bytes.")
                run_provenance = provenance | {
                    "schema": "public-collector-run-provenance-v1",
                    "run_id": run_id,
                    "lane": lane,
                    "window_plan_sha256": window_hashes[lane],
                }
                exclusive_json(source / "collector-runtime.json", run_provenance)
                with (source / "collector-metrics.ndjson").open("xb") as stream:
                    stream.write(metrics)
                    stream.flush()
                    os.fsync(stream.fileno())
                for root in (capture, archive):
                    (root / plan.campaign / lane).mkdir(mode=0o700, exist_ok=True)
                first = publish_closed_chunk(
                    source,
                    capture / plan.campaign / lane / run_id,
                    provenance=run_provenance,
                    catalogue=_catalogue,
                    progress=progress,
                )
                second = publish_closed_chunk(
                    source,
                    archive / plan.campaign / lane / run_id,
                    provenance=run_provenance,
                    catalogue=None,
                    progress=progress,
                )
                if plan.prune_spool:
                    remove_verified_spool_duplicate(source, first, second, campaign=campaign)
            _status(status_path, status)
        status["phase"] = "completed"
        _status(status_path, status)
        return status
    except BaseException:
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
        shutdown_deadline = time.monotonic() + 30
        for child in children:
            try:
                child.wait(timeout=max(0, shutdown_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        status["phase"] = "failed"
        _status(status_path, status)
        raise
    finally:
        os.close(lock_descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    public_environment(os.environ)  # Before adapter imports or storage access.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-image", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-image")
    parser.add_argument("--campaign")
    parser.add_argument("--lane", action="append", choices=tuple(LANES))
    parser.add_argument("--chunk-seconds", type=int)
    parser.add_argument("--chunks", type=int)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--prune-spool", action="store_true")
    parser.add_argument("--spool", type=Path, default=Path("/spool"))
    parser.add_argument("--capture", type=Path, default=Path("/capture"))
    parser.add_argument("--archive", type=Path, default=Path("/archive"))
    parser.add_argument("--network-marker", type=Path, default=Path("/control/network-ready.json"))
    parser.add_argument("--clock", type=Path, default=Path("/control/clock.json"))
    args = parser.parse_args(argv)
    if os.environ.get("COLLECTOR_SOURCE_COMMIT") != args.expected_source:
        parser.error("Image source does not equal the expected reviewed commit.")
    if re.fullmatch(r"[0-9a-f]{40}", args.expected_source) is None:
        parser.error("A full source commit is required.")
    if args.health:
        report = read_json(args.spool / "runtime-health.json")
        updated = report.get("updated_utc_ns")
        return (
            0
            if (
                report.get("source_commit") == args.expected_source
                and report.get("phase") in {"capturing", "publishing", "completed"}
                and type(updated) is int
                and 0 <= time.time_ns() - updated <= 120_000_000_000
            )
            else 2
        )
    if args.check_image:
        import importlib

        if sys.version_info[:3] != (3, 13, 15):
            parser.error("Reviewed image requires Python 3.13.15.")
        for module, _prefix in LANES.values():
            importlib.import_module("hyperliquid_bot." + module)
        print(
            json.dumps(
                {
                    "source_commit": args.expected_source,
                    "mode": "PAPER",
                    "credentialless": True,
                    "signing": False,
                }
            )
        )
        return 0
    if any(
        value is None
        for value in (
            args.campaign,
            args.lane,
            args.chunk_seconds,
            args.chunks,
            args.expected_image,
        )
    ):
        parser.error("Explicit bounded campaign, lanes, duration and image digest required.")
    try:
        plan = RuntimePlan(
            args.campaign,
            tuple(args.lane),
            args.chunk_seconds,
            args.chunks,
            args.pilot,
            args.expected_source,
            args.expected_image,
            args.prune_spool,
        )
        collect_campaign(
            plan,
            spool=args.spool,
            capture=args.capture,
            archive=args.archive,
            network_marker=args.network_marker,
            clock_path=args.clock,
        )
    except (ValueError, RuntimeError, OSError):
        print(
            "Public collector refused or stopped; inspect private health evidence.", file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
