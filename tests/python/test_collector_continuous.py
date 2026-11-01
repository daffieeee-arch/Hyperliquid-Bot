"""Offline host lifecycle and fault injection; never connects to Docker/TrueNAS."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb
import pytest
from scripts.truenas_collector_bootstrap import start_argv

from hyperliquid_bot import collector_capacity as cap
from hyperliquid_bot import collector_continuous as host
from hyperliquid_bot.collector_continuous_audit import audit
from hyperliquid_bot.collector_storage import exclusive_json, publish_closed_chunk


def settings() -> host.Settings:
    return host.Settings(
        release=Path("/mnt/fixture/hyperliquid-runtime/release"),
        state_root=Path("/mnt/fixture/hyperliquid-runtime/continuous-control"),
        runtime_root=Path("/mnt/fixture/hyperliquid-runtime"),
        capture=Path("/mnt/work/hyperliquid-work/market-data/capture"),
        archive=Path("/mnt/archive/hyperliquid-archive/market-data"),
        bind_ip="192.168.0.2",
        source_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        image_id="sha256:" + "c" * 64,
        capacity=cap.CapacityPolicy("fixture-zfs", 2 * cap.GIB, 48 * cap.GIB),
        adoption={},
    )


def sample(index: int = 0) -> dict[str, Any]:
    return {
        "monotonic_ns": (index + 1) * 1_000_000_000,
        "zfs_version": "fixture-zfs",
        "direct_memavailable_bytes": 2 * cap.GIB,
        "memtotal_bytes": 64 * cap.GIB,
        "arc": {
            "size": 20 * cap.GIB,
            "c_min": 2 * cap.GIB,
            "c_max": 48 * cap.GIB,
            "mru_evictable_data": 12 * cap.GIB,
            "mfu_evictable_data": 4 * cap.GIB,
            "memory_throttle_count": 0,
            "evict_not_enough": 0,
        },
        "psi": {
            kind: {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total": 10}
            for kind in ("some", "full")
        },
        "global_oom_kill": 0,
    }


def supervisor(monkeypatch: pytest.MonkeyPatch) -> host.Supervisor:
    # Build without touching runtime paths; all effects stay under explicit fakes.
    item = object.__new__(host.Supervisor)
    item.settings = settings()
    item.boot = "fixture-boot"
    item.state = {
        "schema": "collector-continuous-state-v1",
        "phase": "capturing",
        "current": generation(),
        "retry_count": 0,
        "generations": 0,
        "last_gap": None,
    }
    item.previous = None
    item.streaks = {}
    item.last_clock = 0
    item.stopping = False
    item.owned_child = None

    def save(phase: str | None = None, reason: str | None = None) -> None:
        if phase:
            item.state["phase"] = phase
        if reason:
            item.state["reason"] = reason

    monkeypatch.setattr(item, "save", save)
    return item


def generation() -> dict[str, Any]:
    config = settings()
    return {
        "plan": asdict(config.plan("fixture-campaign")),
        "roots": {key: str(value) for key, value in config.roots("fixture-campaign").items()},
        "image_id": config.image_id,
        "collector_id": "d" * 64,
        "status_id": "e" * 64,
        "boot_id": "fixture-boot",
        "adopted": True,
        "app_config_sha256": "f" * 64,
    }


def test_clean_arc_credit_and_quantized_startup_subset() -> None:
    values = [sample(index) for index in range(3)]
    values[1]["psi"]["full"]["total"] += 2
    values[1]["psi"]["some"]["total"] += 1
    values[2]["psi"]["full"]["total"] += 3
    values[2]["psi"]["some"]["total"] += 3
    # Keep cumulative full a subset, allowing just the delta carry difference.
    for value in values:
        value["psi"]["some"]["total"] += 10
    assert cap.admit(values, settings().capacity)[0]["credit_bytes"] == 3 * cap.GIB
    values[1]["psi"]["full"]["total"] += 1
    with pytest.raises(ValueError, match="delta subset"):
        cap.admit(values, settings().capacity)


@pytest.mark.parametrize("mutation", ["ghost", "version", "oom", "psi-equal", "counter-backward"])
def test_startup_refuses_untrusted_or_material_pressure(mutation: str) -> None:
    values = [sample(index) for index in range(3)]
    if mutation == "ghost":
        for value in values:
            value["arc"]["mru_evictable_data"] = value["arc"]["mfu_evictable_data"] = 0
    elif mutation == "version":
        values[0]["zfs_version"] = "unreviewed"
    elif mutation == "oom":
        values[1]["global_oom_kill"] = 1
    elif mutation == "psi-equal":
        values[1]["psi"]["some"]["total"] = 50_010
    else:
        values[1]["psi"]["some"]["total"] = 9
    with pytest.raises(ValueError):
        cap.admit(values, settings().capacity)


def test_runtime_uses_material_streaks_and_retains_one_microsecond_precision() -> None:
    first, second, third = sample(), sample(1), sample(2)
    second["psi"]["full"]["total"] += 10_000
    third["psi"]["full"]["total"] += 20_000
    streaks, reasons = cap.evaluate(first, second, {}, settings().capacity)
    assert not reasons
    assert cap.evaluate(second, third, streaks, settings().capacity)[1] == ["memory-pressure"]
    second = sample(1)
    second["monotonic_ns"] += 5_000_000_000
    with pytest.raises(ValueError, match="five seconds"):
        cap.evaluate(first, second, {}, settings().capacity)


def test_singleton_refuses_second_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ownership is separately verified in production. flock itself is exercised.
    monkeypatch.setattr(host, "directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        host.os, "fstat", lambda fd: type("Stat", (), {"st_mode": 0o100600, "st_uid": 0})()
    )
    with host.singleton(tmp_path), pytest.raises(BlockingIOError), host.singleton(tmp_path):
        pass


def test_adopt_running_campaign_never_updates_app_or_old_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    original = generation()
    object.__setattr__(item.settings, "adoption", copy.deepcopy(original))
    item.state["current"] = None
    monkeypatch.setattr(host, "directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": True}})
    monkeypatch.setattr(
        host, "command", lambda *args, **kwargs: pytest.fail("Unexpected host command")
    )
    monkeypatch.setattr(host, "publish", lambda *args, **kwargs: pytest.fail("Old control mutated"))
    item.adopt(read_only=True)
    assert item.state["current"]["plan"] == original["plan"]
    assert item.state["current"]["collector_id"] == original["collector_id"]


def test_observe_running_adopted_generation_does_not_launch_or_replace_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": True}})
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(item, "clock", lambda *args: pytest.fail("Adopted clock replaced"))
    monkeypatch.setattr(item, "launch", lambda: pytest.fail("Running capture replaced"))
    assert item.observe() is False


def test_completed_generation_audits_before_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    effects: list[str] = []
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "ExitCode": 0}}
    )
    monkeypatch.setattr(host, "document", lambda *args, **kwargs: {"phase": "completed"})
    monkeypatch.setattr(item, "audit", lambda **kwargs: effects.append("two-copy-audit"))
    monkeypatch.setattr(item, "gap", lambda reason: effects.append("gap"))
    assert item.observe() is True
    assert effects == ["two-copy-audit", "gap"]
    assert item.state["phase"] == "between-campaigns"


def test_failed_seal_never_advances(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "ExitCode": 0}}
    )
    monkeypatch.setattr(host, "document", lambda *args, **kwargs: {"phase": "completed"})

    def fail(**kwargs: Any) -> None:
        raise ValueError("Mismatched archive hash")

    monkeypatch.setattr(item, "audit", fail)
    monkeypatch.setattr(item, "gap", lambda reason: pytest.fail("Bad seal advanced"))
    with pytest.raises(ValueError, match="archive hash"):
        item.observe()


def test_network_retry_requires_explicit_transport_proof_and_is_bounded() -> None:
    good = {
        "status": "FAILED",
        "exception_class": "ConnectionResetError",
        "integrity_events": 0,
        "credentialless": True,
    }
    assert host.transport_failure([good])
    assert not host.transport_failure([good | {"exception_class": "OSError"}])
    assert not host.transport_failure([good | {"integrity_events": 1}])
    assert not host.transport_failure([good | {"exception_class": "WriterError"}])
    assert [host.retry_delay(index, 5) for index in range(1, 7)] == [30, 60, 120, 240, 480, None]


def test_postinit_uses_watchdog_exact_scoped_cleanup_and_no_global_service_changes() -> None:
    cfg = settings()
    argv = start_argv(cfg.release, cfg.state_root / "settings.json", cfg.state_root)
    assert "--property=WatchdogSec=5s" in argv
    assert "--property=Restart=no" in argv
    assert any(arg.startswith("--property=ExecStopPost=") and "--stop-owned" in arg for arg in argv)
    assert "-i" not in argv  # Preserve systemd NOTIFY_SOCKET for the actual watchdog.
    assert not any("ARC" in arg or "reboot" in arg or "daemon-reload" in arg for arg in argv)


def private(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    path.chmod(0o700)
    return path


def closed_fixture(tmp_path: Path) -> tuple[host.RuntimePlan, Path, Path]:
    plan = host.RuntimePlan(
        "synthetic",
        ("hyperliquid",),
        600,
        1,
        True,
        "a" * 40,
        "sha256:" + "b" * 64,
        storage_profile="truenas-zfs",
    )
    source = private(tmp_path / "source")
    raw = private(source / "raw")
    database = duckdb.connect(":memory:")
    database.execute(
        "COPY (SELECT 1 AS synthetic_metadata) TO ? (FORMAT PARQUET)", [str(raw / "part.parquet")]
    )
    database.close()
    exclusive_json(
        source / "capture-health.json",
        {
            "status": "COMPLETED",
            "credentialless": True,
            "run_id": "synthetic-0000",
            "duration_seconds": 600,
        },
    )
    window = exclusive_json(source / "collector-plan.json", {"synthetic": True})
    provenance = {
        "schema": "public-collector-run-provenance-v1",
        "run_id": "synthetic-0000",
        "lane": "hyperliquid",
        "source_commit": plan.source_commit,
        "image_digest": plan.image_digest,
        "chunk_seconds": 600,
        "chunks": 1,
        "signing": False,
        "authenticated_feeds": False,
        "storage_profile": "truenas-zfs",
        "window_plan_sha256": window,
    }
    work, archive = private(tmp_path / "work"), private(tmp_path / "archive")

    def catalogue(parts: Path, db: Path) -> None:
        with duckdb.connect(str(db)) as con:
            con.execute(
                "CREATE VIEW raw_records AS SELECT * FROM read_parquet('"
                + str(parts / "*.parquet")
                + "')"
            )

    for base in (work, archive):
        parent = private(base / plan.campaign / "hyperliquid")
        destination = parent / "synthetic-0000"
        publish_closed_chunk(
            source,
            destination,
            provenance=provenance,
            catalogue=catalogue if base == work else None,
        )
        # Synthetic fixture explicitly supplies the production canonical source
        # binding, while all actual file I/O remains in this disposable tmpdir.
        manifest_path = destination / "publication-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["source_root"] = (
            "/home/chupa/hyperliquid-spool/synthetic/chunk-0000/hyperliquid/data-1a/hyperliquid/BTC-PERP/synthetic-0000"
        )
        manifest_path.write_text(json.dumps(manifest))
        sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        # COMPLETE and private marker are hard links, so update these fixture
        # bytes once; production audit itself is strictly read-only.
        (destination / "COMPLETE.json").write_text(
            json.dumps({"manifest_sha256": sha, "closed_chunk_verified": True})
        )
    return plan, work, archive


def test_real_sealed_copy_and_catalog_audit_reads_both_copies_without_deleting(
    tmp_path: Path,
) -> None:
    plan, work, archive = closed_fixture(tmp_path)
    before = {
        str(path): path.read_bytes()
        for root in (work, archive)
        for path in root.rglob("*")
        if path.is_file()
    }
    result = audit(plan, work, archive, complete=True)
    assert len(result["accepted"]) == 1 and result["accepted"][0]["copies"] == 2
    assert before == {
        str(path): path.read_bytes()
        for root in (work, archive)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_tampered_archive_halts_even_when_complete_marker_exists(tmp_path: Path) -> None:
    plan, work, archive = closed_fixture(tmp_path)
    path = next(archive.rglob("*.parquet"))
    path.write_bytes(b"synthetic-corruption")
    with pytest.raises(ValueError, match="hash or byte count"):
        audit(plan, work, archive, complete=True)
    assert path.exists()


def test_missing_or_half_published_copy_fails_closed(tmp_path: Path) -> None:
    plan, work, archive = closed_fixture(tmp_path)
    next(archive.rglob("COMPLETE.json")).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        audit(plan, work, archive, complete=False)


def test_metadata_parser_refuses_symlink_duplicates_nonfinite(tmp_path: Path) -> None:
    path = tmp_path / "value.json"
    for raw in ('{"a": 1, "a": 2}', '{"x": NaN}'):
        path.write_text(raw)
        with pytest.raises(ValueError):
            host.document(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        host.document(link)
