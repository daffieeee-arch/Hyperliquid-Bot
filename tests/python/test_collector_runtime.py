"""Offline collector lifecycle, source-mode, publication and resource boundaries."""

from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, TypedDict

import duckdb
import pytest
from scripts.collector_network_gate import owned_veth, validate_container, verified_rate

from hyperliquid_bot import collector_runtime as runtime
from hyperliquid_bot.capture_live_status import process_code_version
from hyperliquid_bot.collector_clock import parse_tracking
from hyperliquid_bot.collector_storage import (
    CollectorStorageError,
    checked_relative,
    exclusive_json,
    publish_closed_chunk,
    read_json,
    remove_verified_spool_duplicate,
    snapshot_files,
)
from hyperliquid_bot.parquet_research import ParquetResearchWriter, create_research_catalog
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
)

SOURCE = "a" * 40
IMAGE = "sha256:" + "b" * 64


def private(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    return path


def plan(**changes: Any) -> runtime.RuntimePlan:
    values: dict[str, Any] = dict(
        campaign="fixture",
        lanes=("hyperliquid", "binance"),
        chunk_seconds=600,
        chunks=1,
        pilot=True,
        source_commit=SOURCE,
        image_digest=IMAGE,
    )
    return runtime.RuntimePlan(**(values | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"campaign": "../original"},
        {"lanes": ()},
        {"lanes": ("kraken-l3",)},
        {"lanes": ("hyperliquid", "hyperliquid")},
        {"chunk_seconds": 601},
        {"chunks": 2},
        {"pilot": False, "chunk_seconds": 600},
        {"source_commit": "main"},
        {"image_digest": "latest"},
        {"pilot": False, "chunk_seconds": 21600, "chunks": 13},
    ],
)
def test_plan_rejects_unsafe_or_unbounded_campaigns(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        plan(**changes)


def test_longer_chunks_are_explicit_and_bounded() -> None:
    assert plan(pilot=False, chunk_seconds=21600, chunks=12).chunks == 12
    assert plan(lanes=("kraken", "bitvavo-standard")).pilot


@pytest.mark.parametrize(
    "environment",
    [
        {"TRADING_MODE": "LIVE"},
        {"TRADING_MODE": "SHADOW"},
        {"TRADING_MODE": "TESTNET"},
        {"TRADING_MODE": "paper"},
        {"D41_EXECUTION_MODE": "PAPER"},
        {"KRAKEN_API_SECRET": "synthetic-unused"},
        {"UNRELATED_TOKEN": "synthetic-unused"},
    ],
)
def test_no_execution_or_credential_environment(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        runtime.public_environment(environment)


def test_child_environment_is_allowlisted_and_signing_free() -> None:
    result = runtime.public_environment({"TRADING_MODE": "PAPER", "UNRELATED": "not-forwarded"})
    assert "UNRELATED" not in result and result["TRADING_MODE"] == "PAPER"
    for lane in runtime.LANES:
        command = runtime.native_command(lane, Path("/fixture"), "fixture-0000", 600)
        assert "--duration-seconds" in command and command[-1] == "600"
        assert not any("key" in part or "l3" == part for part in command)


@pytest.mark.parametrize("text", ["", ".", "..", "a/../b", "/absolute", "a//b", "a/./b"])
def test_only_safe_relative_publication_paths(text: str) -> None:
    with pytest.raises(CollectorStorageError):
        checked_relative(text)


def test_bounded_json_and_symlinks_fail_closed(tmp_path: Path) -> None:
    root = private(tmp_path / "root")
    (root / "object.json").write_text("[]")
    with pytest.raises(ValueError):
        read_json(root / "object.json")
    with pytest.raises(ValueError):
        read_json(root / "object.json", limit=1)
    (root / "link.json").symlink_to(root / "object.json")
    with pytest.raises(OSError):
        read_json(root / "link.json")
    (root / "link-dir").symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        snapshot_files(root)


@pytest.mark.parametrize("content", ['{"a":1,"a":2}', '{"a":NaN}'])
def test_ambiguous_or_nonfinite_control_json_is_rejected(tmp_path: Path, content: str) -> None:
    root = private(tmp_path / "root")
    (root / "control.json").write_text(content)
    with pytest.raises(ValueError):
        read_json(root / "control.json")


def make_source(root: Path) -> Path:
    private(root)
    private(root / "raw")
    (root / "raw" / "part.parquet").write_bytes(b"synthetic-raw-bytes")
    exclusive_json(root / "capture-claim.json", {"raw_dir": str(root / "raw")})
    (root / "research.duckdb").write_bytes(b"local-only-catalogue")
    return root


def fake_catalogue(_raw: Path, database: Path) -> None:
    database.write_bytes(b"destination-specific-catalogue")


def test_two_publications_bind_original_claim_then_safe_local_cleanup(tmp_path: Path) -> None:
    campaign = private(tmp_path / "spool" / "campaign")
    exclusive_json(campaign / "campaign.json", {"schema": "fixture"})
    source = make_source(campaign / "chunk")
    original_claim = (source / "capture-claim.json").read_bytes()
    first = publish_closed_chunk(
        source,
        private(tmp_path / "ssd") / "closed",
        provenance={"source": SOURCE},
        catalogue=fake_catalogue,
    )
    second = publish_closed_chunk(
        source, private(tmp_path / "hdd") / "closed", provenance={"source": SOURCE}, catalogue=None
    )
    for receipt in (first, second):
        target = Path(str(receipt["destination"]))
        manifest_bytes = (target / "publication-manifest.json").read_bytes()
        assert hashlib.sha256(manifest_bytes).hexdigest() == receipt["manifest_sha256"]
        assert read_json(target / "COMPLETE.json")["manifest_sha256"] == receipt["manifest_sha256"]
        assert (target / "capture-claim.json").read_bytes() == original_claim
        manifest = json.loads(manifest_bytes)
        assert manifest["source_root"] == str(source)
        assert manifest["destination_root"] == str(target)
        assert manifest["research_ready"] is False
    assert not (Path(str(second["destination"])) / "research.duckdb").exists()
    remove_verified_spool_duplicate(source, first, second, campaign=campaign)
    assert not source.exists() and (campaign / "campaign.json").is_file()
    assert (Path(str(first["destination"])) / "raw" / "part.parquet").is_file()


def test_corrupt_second_copy_preserves_entire_spool(tmp_path: Path) -> None:
    campaign = private(tmp_path / "campaign")
    exclusive_json(campaign / "campaign.json", {})
    source = make_source(campaign / "chunk")
    original = snapshot_files(source)
    first = publish_closed_chunk(
        source, private(tmp_path / "ssd") / "run", provenance={}, catalogue=fake_catalogue
    )
    second = publish_closed_chunk(
        source, private(tmp_path / "hdd") / "run", provenance={}, catalogue=None
    )
    (Path(str(second["destination"])) / "raw" / "part.parquet").write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        remove_verified_spool_duplicate(source, first, second, campaign=campaign)
    assert snapshot_files(source) == original
    with pytest.raises(ValueError):
        remove_verified_spool_duplicate(campaign, first, second, campaign=campaign)


def test_publication_failure_and_collision_never_publish_complete(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    target = private(tmp_path / "destination") / "run"

    def failure() -> None:
        raise OSError("synthetic write failure")

    with pytest.raises(OSError):
        publish_closed_chunk(source, target, provenance={}, catalogue=None, progress=failure)
    assert target.is_dir() and not (target / "COMPLETE.json").exists()
    with pytest.raises(FileExistsError):
        publish_closed_chunk(source, target, provenance={}, catalogue=None)
    assert not (target / "COMPLETE.json").exists()


def test_source_change_or_symlink_is_not_a_closed_chunk(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    destination = private(tmp_path / "destination")
    changed = False

    def mutation() -> None:
        nonlocal changed
        if not changed:
            (source / "capture-claim.json").write_text("changed")
            changed = True

    with pytest.raises(ValueError):
        publish_closed_chunk(
            source, destination / "mutated", provenance={}, catalogue=None, progress=mutation
        )
    assert not (destination / "mutated" / "COMPLETE.json").exists()
    (source / "external-link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        publish_closed_chunk(source, destination / "linked", provenance={}, catalogue=None)
    assert not (destination / "linked").exists()


@pytest.mark.asyncio
async def test_real_native_parquet_catalog_regenerates_destination_paths(tmp_path: Path) -> None:
    source = private(tmp_path / "source")
    raw = private(source / "raw")
    writer = ParquetResearchWriter(raw)
    await writer.append(
        RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue="hyperliquid",
            product="BTC-PERP",
            channel="bbo",
            session_id="offline-collector",
            message_ordinal=1,
            received_utc_ns=1,
            received_monotonic_ns=2,
            direction=MessageDirection.INBOUND,
            frame_type=FrameType.TEXT,
            payload_encoding=PayloadEncoding.UTF8,
            payload_bytes=b'{"channel":"bbo","data":{"coin":"BTC","bbo":[]}}',
        )
    )
    await writer.aclose()
    create_research_catalog(raw, source / "research.duckdb")
    destination = private(tmp_path / "destination") / "run"
    publish_closed_chunk(source, destination, provenance={}, catalogue=create_research_catalog)
    with duckdb.connect(str(destination / "research.duckdb"), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM raw_records").fetchone() == (1,)
        sql = str(
            connection.execute(
                "SELECT sql FROM duckdb_views() WHERE view_name='raw_records'"
            ).fetchone()
        )
        assert str(destination / "raw") in sql and str(source / "raw") not in sql


def network_document() -> dict[str, object]:
    return {
        "schema": "collector-container-network-v1",
        "campaign": "fixture",
        "source_commit": SOURCE,
        "image_digest": IMAGE,
        "container_netns_inode": Path("/proc/self/ns/net").stat().st_ino,
        "download_bits_per_second": 100_000_000,
        "upload_bits_per_second": 5_000_000,
        "kernel_tc_verified": True,
    }


def clock_document(now: int) -> dict[str, object]:
    return {
        "schema": "collector-host-clock-v1",
        "sampled_utc_ns": now,
        "synchronized": True,
        "system_offset_seconds": 0.001,
        "root_delay_seconds": 0.010,
        "root_dispersion_seconds": 0.002,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_digest", "latest"),
        ("source_commit", "main"),
        ("container_netns_inode", 0),
        ("upload_bits_per_second", 20_000_000),
        ("download_bits_per_second", 400_000_000),
        ("kernel_tc_verified", False),
    ],
)
def test_network_gate_binds_combined_caps_and_exact_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    root = private(tmp_path / "control")
    exclusive_json(root / "network.json", network_document() | {field: value})
    with pytest.raises(ValueError):
        runtime.require_network_marker(root / "network.json", plan())


@pytest.mark.parametrize(
    "changes",
    [
        {"sampled_utc_ns": 0},
        {"sampled_utc_ns": 10**20},
        {"synchronized": False},
        {"system_offset_seconds": 0.051},
        {"root_dispersion_seconds": 0.251},
        {"root_delay_seconds": -0.01},
        {"system_offset_seconds": True},
        {"system_offset_seconds": "0.001"},
    ],
)
def test_clock_uncertainty_or_staleness_stops_capture(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    root = private(tmp_path / "control")
    exclusive_json(root / "clock.json", clock_document(200_000_000_000) | changes)
    with pytest.raises(ValueError):
        runtime.clock_evidence(root / "clock.json", now_ns=200_000_000_000)


def test_chrony_parser_keeps_actual_offset_sign_and_sync_status() -> None:
    raw = (
        "System time : 0.001 seconds slow of NTP time\nRoot delay : 0.01 seconds\n"
        "Root dispersion : 0.002 seconds\nLeap status : Normal\n"
    )
    report = parse_tracking(raw, now_ns=123)
    assert report["system_offset_seconds"] == -0.001 and report["synchronized"] is True
    assert (
        parse_tracking(raw.replace("Normal", "Not synchronised"), now_ns=123)["synchronized"]
        is False
    )
    with pytest.raises(ValueError):
        parse_tracking(raw + "Leap status : Normal\n", now_ns=123)


def container_document() -> dict[str, Any]:
    return {
        "State": {"Running": True, "Pid": 123},
        "Config": {
            "Image": "fixture@" + IMAGE,
            "User": "1000:1000",
            "Labels": {"org.opencontainers.image.revision": SOURCE},
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "NetworkMode": "isolated",
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PortBindings": {},
            "Memory": 8 * 1024**3,
            "MemorySwap": 8 * 1024**3,
            "NanoCpus": 4_000_000_000,
            "PidsLimit": 128,
            "RestartPolicy": {"Name": "no"},
        },
        "Mounts": [
            {"Type": "bind", "Destination": path, "Source": path, "RW": True}
            for path in (
                "/home/chupa/hyperliquid-spool",
                "/mnt/hyperliquid-work/market-data/capture",
                "/mnt/hyperliquid-archive/market-data",
            )
        ]
        + [
            {
                "Type": "bind",
                "Destination": "/control",
                "Source": "/home/chupa/hyperliquid-runtime",
                "RW": False,
            }
        ],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("Privileged", True),
        ("NetworkMode", "host"),
        ("ReadonlyRootfs", False),
        ("Memory", 0),
        ("NanoCpus", 0),
        ("CapAdd", ["NET_ADMIN"]),
    ],
)
def test_host_tc_gate_refuses_wrong_container_isolation(field: str, value: object) -> None:
    document = container_document()
    document["HostConfig"][field] = value
    with pytest.raises(ValueError):
        validate_container(document, image="fixture@" + IMAGE, source=SOURCE)


def test_tc_gate_cannot_select_nas_or_host_nic_and_checks_si_rates() -> None:
    assert validate_container(container_document(), image="fixture@" + IMAGE, source=SOURCE) == 123
    container = {"ifindex": 2, "link_index": 8}
    peer = {
        "ifindex": 8,
        "ifname": "veth123abc",
        "link_index": 2,
        "linkinfo": {"info_kind": "veth"},
    }
    assert owned_veth([peer], container) == "veth123abc"
    with pytest.raises(ValueError):
        owned_veth([peer | {"ifname": "enp101s0"}], container)
    with pytest.raises(ValueError):
        owned_veth([peer | {"link_index": 3}], container)
    assert verified_rate([{"root": True, "kind": "tbf", "options": {"rate": 625_000}}], 625_000)
    assert not verified_rate([{"root": True, "kind": "tbf", "options": {"rate": 640_000}}], 625_000)


class ControllerPaths(TypedDict):
    spool: Path
    capture: Path
    archive: Path
    network_marker: Path
    clock_path: Path


def prepare_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ControllerPaths:
    roots = {name: private(tmp_path / name) for name in ("spool", "capture", "archive", "control")}
    exclusive_json(roots["control"] / "network.json", network_document())
    exclusive_json(roots["control"] / "clock.json", clock_document(time.time_ns()))
    monkeypatch.setattr(
        runtime, "mounted_filesystem", lambda path: "ext4" if path == roots["spool"] else "nfs4"
    )
    monkeypatch.setattr(runtime, "_catalogue", fake_catalogue)
    monkeypatch.setattr(
        runtime, "network_counters", lambda: {"received_bytes": 1, "transmitted_bytes": 2}
    )
    monkeypatch.setattr("hyperliquid_bot.collector_runtime.signal.signal", lambda *_args: None)
    monkeypatch.setattr(
        "hyperliquid_bot.collector_runtime.os.environ",
        {"TRADING_MODE": "PAPER", "COLLECTOR_SOURCE_COMMIT": SOURCE},
    )
    return {
        "spool": roots["spool"],
        "capture": roots["capture"],
        "archive": roots["archive"],
        "network_marker": roots["control"] / "network.json",
        "clock_path": roots["control"] / "clock.json",
    }


class FakeChild:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, number: int) -> None:
        self.signals.append(number)

    def wait(self, timeout: float) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def test_controller_publishes_only_completed_public_native_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = prepare_controller(tmp_path, monkeypatch)

    def fake_start(command: list[str], **kwargs: Any) -> FakeChild:
        artifact = Path(command[command.index("--artifact-root") + 1])
        run_id = command[command.index("--run-id") + 1]
        lane = artifact.name
        source = artifact / runtime.LANES[lane][1] / run_id
        source.mkdir(parents=True, mode=0o700)
        make_source_inner = private(source / "raw")
        (make_source_inner / "part.parquet").write_bytes(b"raw")
        exclusive_json(
            source / "capture-health.json",
            {
                "status": "COMPLETED",
                "credentialless": True,
                "run_id": run_id,
                "duration_seconds": 600,
            },
        )
        assert kwargs["env"]["TRADING_MODE"] == "PAPER" and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["env"]["CAPTURE_CODE_SHA"] == SOURCE
        with monkeypatch.context() as child_context:
            child_context.setattr("hyperliquid_bot.capture_live_status.os.environ", kwargs["env"])
            assert process_code_version() == SOURCE
        return FakeChild(0)

    monkeypatch.setattr("hyperliquid_bot.collector_runtime.subprocess.Popen", fake_start)
    assert runtime.collect_campaign(plan(), **roots)["phase"] == "completed"
    assert len(list(roots["capture"].rglob("COMPLETE.json"))) == 2
    assert len(list(roots["archive"].rglob("COMPLETE.json"))) == 2


@pytest.mark.parametrize("failure", ["budget", "child", "clock"])
def test_controller_stops_all_siblings_without_partial_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    roots = prepare_controller(tmp_path, monkeypatch)
    children: list[FakeChild] = []

    def start(*_args: Any, **_kwargs: Any) -> FakeChild:
        child = FakeChild(1 if failure == "child" and not children else None)
        children.append(child)
        if failure == "clock" and roots["clock_path"].exists():
            roots["clock_path"].unlink()
        return child

    calls = 0

    def allocated(_root: Path) -> int:
        nonlocal calls
        calls += 1
        return runtime.SPOOL_STOP_BYTES if failure == "budget" and calls > 2 else 0

    monkeypatch.setattr("hyperliquid_bot.collector_runtime.subprocess.Popen", start)
    monkeypatch.setattr(runtime, "allocated_bytes", allocated)
    with pytest.raises((RuntimeError, OSError)):
        runtime.collect_campaign(plan(), **roots, sleep=lambda _seconds: None)
    assert all(child.returncode is not None for child in children)
    assert children[-1].signals == [signal.SIGINT]
    assert read_json(roots["spool"] / "runtime-health.json")["phase"] == "failed"
    assert not list(roots["capture"].rglob("COMPLETE.json"))
    assert not list(roots["archive"].rglob("COMPLETE.json"))
