"""One bounded, offline, engineering-only native PAPER replay command.

Inputs are copied to private local staging before eager replay. Publication is
create-only, not directory-atomic; health becomes visible last. Failed staging
and incomplete publications are retained for inspection, never resumed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from hyperliquid_bot.local_mode import require_local_paper_mode
from hyperliquid_bot.migration_verify import MountIdentity
from hyperliquid_bot.paper_engine.engine import PaperEngine, PaperEngineConfig
from hyperliquid_bot.paper_engine.errors import PaperTapeError
from hyperliquid_bot.paper_engine.events import MarketEvent, event_sort_key
from hyperliquid_bot.paper_engine.ledger import validate_run_id
from hyperliquid_bot.paper_engine.offline_inputs import (
    OfflineInputError,
    ReplayLimits,
    absolute,
    bind_remote,
    descriptor_identity,
    descriptor_mount,
    directory,
    preflight,
    relative_part,
    require_local,
    revalidate_parts,
    snapshot_parts,
)
from hyperliquid_bot.paper_engine.replay import ReplayResources, load_hyperliquid_parquet_tape
from hyperliquid_bot.paper_engine.strategy import NonProductionReferenceStrategy

SCHEMA = "native-paper-offline-replay-v1"
LOCAL_IMAGE = "local-offline:no-oci-image"
JSON_BYTE_LIMIT = 256 * 1024
LEDGER_BYTE_LIMIT = 2 * 1024 * 1024
LEDGER_RECORD_LIMIT = 5000
ARTIFACTS = ("run-claim.json", "ledger.jsonl", "state.json", "health.json")
DECIMAL_FIELDS = frozenset(
    {
        "position_quantity",
        "average_entry_price",
        "cash_usdc",
        "realized_pnl_usdc",
        "unrealized_pnl_usdc",
        "fees_usdc",
        "equity_usdc",
        "mark_price",
        "stop_price",
        "quantity",
        "filled_quantity",
        "price",
        "limit_price",
        "fee_usdc",
        "target_quantity",
        "stop_distance_fraction",
        "entry_price_band_fraction",
        "exit_price_band_fraction",
    }
)
SOURCE_FILES = (
    tuple(
        f"src/hyperliquid_bot/paper_engine/{name}.py"
        for name in (
            "__init__",
            "engine",
            "events",
            "execution",
            "ledger",
            "errors",
            "precision",
            "replay",
            "strategy",
            "time_display",
            "offline_inputs",
            "offline_replay",
        )
    )
    + tuple(
        f"src/hyperliquid_bot/{name}.py"
        for name in (
            "paper_risk",
            "local_mode",
            "migration_verify",
            "parquet_research",
            "raw_research",
            "reconstructable_paths",
        )
    )
    + ("pyproject.toml", "uv.lock", ".python-version")
)


@dataclass(frozen=True)
class ReplayRequest:
    input_root: Path
    parts: tuple[str, ...]
    store_root: Path
    run_id: str
    source_commit: str
    input_mode: str
    inputs_complete: bool = False
    strategy: str = "flat"
    source_provenance: str = "verified-head"
    expected_mount: MountIdentity | None = None
    limits: ReplayLimits = field(default_factory=ReplayLimits)
    resources: ReplayResources = field(default_factory=ReplayResources)


class OfflineReplayError(PaperTapeError):
    def __init__(self, staging: Path | None, reason: str) -> None:
        super().__init__(
            "Offline replay failed; no successful run advertised. Inspect local staging evidence."
        )
        self.staging = staging
        self.reason = reason


def _jsonable(value: Any) -> Any:
    if type(value) is Decimal:
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def source_provenance(commit: str, mode: str) -> dict[str, object]:
    if type(commit) is not str or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise OfflineInputError("source_commit must be a full lowercase Git SHA.")
    if mode not in {"verified-head", "operator-asserted"}:
        raise OfflineInputError("Unknown source provenance mode.")
    root = Path(__file__).resolve().parents[3]
    if mode == "verified-head":
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        if head != commit or status:
            raise OfflineInputError(
                "Verified source requires the actual HEAD and a clean checkout."
            )
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    return {
        "mode": mode,
        "commit": commit,
        "engine_source_commit": commit
        if mode == "verified-head"
        else f"operator-asserted:{commit}",
        "code_and_lock_sha256": hashes,
        "code_binding_sha256": _fingerprint(hashes),
        "image_digest": LOCAL_IMAGE,
        "deployment": False,
        "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__},
    }


def _same_directory(path: Path, descriptor: int) -> None:
    with directory(path) as current:
        if descriptor_identity(current) != descriptor_identity(descriptor):
            raise OfflineInputError("Root directory identity changed.")


def _reject_mount_alias(first: tuple[int, int, str], second: tuple[int, int, str]) -> None:
    if first[0] == second[0] and first[2] != second[2]:
        raise OfflineInputError("Same-device directory aliases across mounts are unsupported.")


def _new_staging(input_root: Path, source_identity: tuple[int, int, str]) -> Path:
    # The output store may be on another local filesystem. Replay itself stays
    # in an independent private local directory, never under the source mount.
    scratch = absolute(Path(tempfile.gettempdir()))
    with directory(scratch) as descriptor:
        require_local(descriptor)
        _reject_mount_alias(source_identity, descriptor_identity(descriptor))
        name = f"hl-native-paper-{uuid4().hex}"
        if (scratch / name).is_relative_to(input_root):
            raise OfflineInputError("Local staging must also be outside the entire input root.")
        os.mkdir(name, mode=0o700, dir_fd=descriptor)
    path = scratch / name
    (path / "inputs").mkdir()
    (path / "runs").mkdir()
    return path


def _artifact_bytes(run_dir: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name in ARTIFACTS:
        path = run_dir / name
        limit = LEDGER_BYTE_LIMIT if name == "ledger.jsonl" else JSON_BYTE_LIMIT
        if path.stat().st_size > limit:
            raise OfflineInputError("Run exceeds the native cockpit artifact byte limit.")
        with path.open("rb") as handle:
            value = handle.read(limit + 1)
        if len(value) > limit:
            raise OfflineInputError("Run artifact grew beyond the native cockpit byte limit.")
        if name == "ledger.jsonl":
            lines = value.splitlines()
            if len(lines) > LEDGER_RECORD_LIMIT or (value and not value.endswith(b"\n")):
                raise OfflineInputError("Run exceeds the complete cockpit ledger record limit.")
            for line in lines:
                record = json.loads(line)
                if type(record) is not dict:
                    raise OfflineInputError("Malformed native ledger record.")
                _check_cockpit_scalars(record)
        else:
            payload = json.loads(value)
            if type(payload) is not dict:
                raise OfflineInputError("Malformed native JSON artifact.")
            _check_cockpit_scalars(payload)
        result[name] = value
    health = json.loads(result["health.json"])
    if health["status"] == "FAILED" or health["ledger_write_failed"] is not False:
        raise OfflineInputError("Failed engine artifacts cannot be published.")
    return result


def _check_event_decimals(event: MarketEvent) -> None:
    # Reject extreme exponents before the engine ever renders fixed-point text.
    # A compact Decimal('1e-1000000000') must not allocate a gigabyte string.
    for value in vars(event).values():
        if type(value) is Decimal:
            exponent = value.as_tuple().exponent
            if type(exponent) is not int or not value.is_finite():
                raise OfflineInputError("Event decimal is not finite.")
            fraction = max(0, -exponent)
            whole = max(1, value.adjusted() + 1)
            length = int(value.is_signed()) + whole + (fraction + 1 if fraction else 0)
            if fraction > 40 or length > 80:
                raise OfflineInputError("Event decimal exceeds the bounded cockpit representation.")


def _check_cockpit_scalars(value: Any) -> None:
    # This guard covers producer scalar representation, not a second ledger
    # reconciler. The existing TS reader remains the semantic validator.
    if type(value) is dict:
        for key, item in value.items():
            if key in DECIMAL_FIELDS and item is not None:
                if (
                    type(item) is not str
                    or len(item) > 80
                    or re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d{1,40})?", item) is None
                ):
                    raise OfflineInputError(
                        "Produced decimal is outside the cockpit representation."
                    )
            _check_cockpit_scalars(item)
    elif type(value) is list:
        for item in value:
            _check_cockpit_scalars(item)
    elif type(value) is str and (len(value) > 512 or value.strip() != value or not value):
        raise OfflineInputError("Produced text is outside the cockpit representation.")


def _write_exclusive(descriptor: int, name: str, value: bytes) -> None:
    fd = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor
    )
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def _check_artifact_sizes(run_dir: Path) -> None:
    for name in ARTIFACTS:
        limit = LEDGER_BYTE_LIMIT if name == "ledger.jsonl" else JSON_BYTE_LIMIT
        if (run_dir / name).stat().st_size > limit:
            raise OfflineInputError("Run exceeds the native cockpit artifact byte limit.")


def publish(store_descriptor: int, run_id: str, artifacts: dict[str, bytes]) -> None:
    # mkdir is the exclusive run-ID reservation. Existing directories, even
    # empty ones, are never replaced or removed. Failure leaves an incomplete
    # new run; no cleanup/resume or directory-atomic publication is promised.
    os.mkdir(run_id, mode=0o700, dir_fd=store_descriptor)
    descriptor = os.open(
        run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store_descriptor
    )
    try:
        actual = descriptor_identity(descriptor)
        expected = descriptor_identity(store_descriptor)
        if (actual[0], actual[2]) != (expected[0], expected[2]):
            raise OfflineInputError("Reserved output directory crossed a changed or nested mount.")
        for name in ("replay-claim.json", "run-claim.json", "ledger.jsonl", "state.json"):
            _write_exclusive(descriptor, name, artifacts[name])
        _write_exclusive(descriptor, ".health.publishing", artifacts["health.json"])
        # A reader cannot accept the publication while health is absent or a
        # partial write. Link makes the already-fsynced health visible without
        # replacing any pre-existing file. No fallible required step follows.
        os.link(
            ".health.publishing",
            "health.json",
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
            follow_symlinks=False,
        )
        with contextlib.suppress(OSError):
            os.unlink(".health.publishing", dir_fd=descriptor)
    finally:
        os.close(descriptor)


def run_replay(request: ReplayRequest) -> dict[str, object]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    staging: Path | None = None
    try:
        if request.inputs_complete is not True:
            raise OfflineInputError("Explicit completed-input attestation is required.")
        if request.input_mode not in {"remote", "local-fixture"}:
            raise OfflineInputError("Input mode must be explicit remote or local-fixture.")
        if (request.input_mode == "remote") != (request.expected_mount is not None):
            raise OfflineInputError(
                "Remote mode requires exact mount identity; local mode cannot carry it."
            )
        if (
            type(request.limits) is not ReplayLimits
            or type(request.resources) is not ReplayResources
        ):
            raise OfflineInputError("Explicit typed resource bounds are required.")
        validate_run_id(request.run_id)
        absolute(request.input_root)
        absolute(request.store_root)
        if request.store_root.is_relative_to(
            request.input_root
        ) or request.input_root.is_relative_to(request.store_root):
            raise OfflineInputError(
                "Output store and the entire input/archive root must be disjoint."
            )
        parts = tuple(relative_part(value) for value in request.parts)
        if not 1 <= len(parts) <= request.limits.files or len(set(parts)) != len(parts):
            raise OfflineInputError("Explicit part selection exceeds its count/uniqueness bound.")
        provenance = source_provenance(request.source_commit, request.source_provenance)
        config = PaperEngineConfig(
            trading_mode="PAPER",
            source_commit=str(provenance["engine_source_commit"]),
            image_digest=LOCAL_IMAGE,
        )
        strategy = NonProductionReferenceStrategy(mode=request.strategy)
        mount = (
            bind_remote(request.input_root, request.expected_mount)
            if request.expected_mount
            else None
        )
        with directory(request.input_root) as source_fd, directory(request.store_root) as store_fd:
            source_mount = descriptor_mount(source_fd)
            if mount is None:
                require_local(source_fd)
            elif source_mount != mount:
                raise OfflineInputError(
                    "Input root descriptor differs from the verified remote mount."
                )
            require_local(store_fd)
            if descriptor_identity(source_fd)[:2] == descriptor_identity(store_fd)[:2]:
                raise OfflineInputError("Source and output resolve to the same directory identity.")
            _reject_mount_alias(descriptor_identity(source_fd), descriptor_identity(store_fd))
            try:
                os.stat(request.run_id, dir_fd=store_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OfflineInputError("Run ID already exists; nothing can replace or resume it.")
            staging = _new_staging(request.input_root, descriptor_identity(source_fd))
            _same_directory(request.input_root, source_fd)
            _same_directory(request.store_root, store_fd)
            plan = {
                "schema": SCHEMA,
                "mode": "PAPER",
                "engineering_only": True,
                "production_eligible": False,
                "run_id": request.run_id,
                "source": provenance,
                "limits": asdict(request.limits),
                "resources": request.resources.connection_config(),
                "input_mode": request.input_mode,
                "input_mount": asdict(mount) | {"target": str(mount.target)} if mount else None,
                "config": _jsonable(asdict(config)),
                "strategy": {
                    "mode": strategy.mode,
                    "id": strategy.strategy_id,
                    "configuration_version": strategy.configuration_version,
                },
            }
            (staging / "replay-plan.json").write_bytes(_json_bytes(plan))
            snapshots = snapshot_parts(source_fd, parts, staging / "inputs", request.limits)
            input_claims = [item.claim() for item in snapshots]
            (staging / "input-claim.json").write_bytes(_json_bytes(input_claims))
            counts = preflight(snapshots, request.limits, request.resources)
            events = load_hyperliquid_parquet_tape(
                tuple(item.path for item in snapshots), resources=request.resources
            )
            if len(events) > request.limits.events:
                raise OfflineInputError("Normalized event count exceeds its preflight bound.")
            for event in events:
                _check_event_decimals(event)
            keys = [event_sort_key(event) for event in events]
            if len(set(keys)) != len(keys):
                raise OfflineInputError("Ambiguous normalized event ordering.")
            created = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                microseconds=events[0].received_utc_ns // 1000
            )
            engine = PaperEngine(
                store_root=staging / "runs",
                run_id=request.run_id,
                created_at_utc=created,
                config=config,
                strategy=strategy,
            )
            run_dir = staging / "runs" / request.run_id
            try:
                for event in events:
                    engine.on_event(event)
                    _check_artifact_sizes(run_dir)
            finally:
                engine.close()
            artifacts = _artifact_bytes(run_dir)
            revalidate_parts(source_fd, snapshots)
            _same_directory(request.input_root, source_fd)
            _same_directory(request.store_root, store_fd)
            if (
                mount is not None
                and request.expected_mount is not None
                and bind_remote(request.input_root, request.expected_mount) != mount
            ):
                raise OfflineInputError("Remote mount changed during the replay.")
            if source_provenance(request.source_commit, request.source_provenance) != provenance:
                raise OfflineInputError("Source provenance changed during replay.")
            binding = {
                "inputs": input_claims,
                "source": provenance,
                "config": plan["config"],
                "strategy": plan["strategy"],
            }
            claim = {
                **plan,
                "input_parts": input_claims,
                "counts": {**counts, "normalized_events": len(events)},
                "business_fingerprint_sha256": _fingerprint(binding),
                "artifact_sha256": {
                    name: hashlib.sha256(value).hexdigest() for name, value in artifacts.items()
                },
                "funding": "UNSETTLED",
                "venue_reconciliation": "UNAVAILABLE",
                "resume": False,
                "ending_position_quantity": json.loads(artifacts["state.json"])[
                    "position_quantity"
                ],
                "publication": "create-only-files-health-last-not-directory-atomic",
            }
            encoded = _json_bytes(claim)
            if len(encoded) > JSON_BYTE_LIMIT:
                raise OfflineInputError("Replay provenance exceeds the bounded JSON limit.")
            (staging / "replay-claim.json").write_bytes(encoded)
            artifacts["replay-claim.json"] = encoded
            publish(store_fd, request.run_id, artifacts)
            return {
                "schema": SCHEMA,
                "status": "COMPLETE",
                "mode": "PAPER",
                "engineering_only": True,
                "run_dir": str(request.store_root / request.run_id),
                "staging_dir": str(staging),
                "business_fingerprint_sha256": claim["business_fingerprint_sha256"],
                "counts": claim["counts"],
                "funding": "UNSETTLED",
                "resume": False,
            }
    except Exception as error:
        reason = (
            str(error)
            if isinstance(error, OfflineInputError)
            else f"Offline replay rejected ({type(error).__name__}); inspect staging evidence."
        )
        if staging is not None:
            with contextlib.suppress(OSError):
                (staging / "failure.json").write_bytes(
                    _json_bytes(
                        {
                            "status": "FAILED",
                            "error_type": type(error).__name__,
                            "reason": reason,
                            "successful_publication": False,
                        }
                    )
                )
        raise OfflineReplayError(staging, reason) from error


def main() -> int:
    try:
        require_local_paper_mode(os.environ.get("TRADING_MODE"))
    except ValueError:
        print(json.dumps({"status": "FAILED", "reason": "PAPER mode required"}))
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--part", action="append", required=True)
    parser.add_argument("--input-mode", required=True, choices=("remote", "local-fixture"))
    parser.add_argument("--expected-mount-target", type=Path)
    parser.add_argument("--expected-mount-source")
    parser.add_argument("--expected-mount-filesystem", choices=("nfs", "nfs4", "cifs"))
    parser.add_argument("--store-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--source-provenance",
        choices=("verified-head", "operator-asserted"),
        default="verified-head",
    )
    parser.add_argument("--inputs-complete", action="store_true")
    parser.add_argument("--strategy", choices=("flat", "toy"), default="flat")
    for name, default in asdict(ReplayLimits()).items():
        parser.add_argument(f"--max-{name.replace('_', '-')}", type=int, default=default)
    args = parser.parse_args()
    expected = (
        args.expected_mount_target,
        args.expected_mount_source,
        args.expected_mount_filesystem,
    )
    if any(item is not None for item in expected) and not all(
        item is not None for item in expected
    ):
        parser.error("All three expected mount fields must be supplied together.")
    try:
        request = ReplayRequest(
            input_root=args.input_root,
            parts=tuple(args.part),
            store_root=args.store_root,
            run_id=args.run_id,
            source_commit=args.source_commit,
            input_mode=args.input_mode,
            inputs_complete=args.inputs_complete,
            strategy=args.strategy,
            source_provenance=args.source_provenance,
            expected_mount=MountIdentity(*expected)
            if all(item is not None for item in expected)
            else None,
            limits=ReplayLimits(
                **{name: getattr(args, f"max_{name}") for name in asdict(ReplayLimits())}
            ),
        )
        result = run_replay(request)
    except (OfflineReplayError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "reason": error.reason
                    if isinstance(error, OfflineReplayError)
                    else "Invalid replay bounds or configuration.",
                    "staging_dir": str(error.staging)
                    if isinstance(error, OfflineReplayError) and error.staging
                    else None,
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
