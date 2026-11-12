"""Offline host lifecycle and fault injection; never connects to Docker/TrueNAS."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from scripts.truenas_collector_bootstrap import start_argv

from hyperliquid_bot import collector_capacity as cap
from hyperliquid_bot import collector_continuous as host
from hyperliquid_bot.collector_continuous_audit import audit
from hyperliquid_bot.collector_native_storage import CONTAINER_ROOTS
from hyperliquid_bot.collector_runtime import LANES, RuntimePlan
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
        storage_sources={
            role: "fixture/project-" + role for role in ("runtime", "capture", "archive")
        },
        storage_guids={role: "12345" for role in ("runtime", "capture", "archive")},
        storage_inodes={role: 42 for role in ("runtime", "capture", "archive")},
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
    item.last_store_check = 0
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
        os, "fstat", lambda fd: type("Stat", (), {"st_mode": 0o100600, "st_uid": 0})()
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
    monkeypatch.setattr(item, "verify_adopted_guard", lambda generation: None)
    monkeypatch.setattr(item, "verify_store", lambda *args: None)
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


def closed_fixture(tmp_path: Path) -> tuple[RuntimePlan, Path, Path]:
    plan = RuntimePlan(
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
            "integrity_events": 0,
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


def compose_fixture(config: host.Settings, plan: RuntimePlan) -> dict[str, Any]:
    return {
        "name": host.PROJECT,
        "networks": {"default": {"name": host.PROJECT + "_default", "ipam": {}}},
        "x-notes": "Synthetic Compose renderer fixture.",
        "services": {
            role: {
                "image": config.image,
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(config.roots(plan.campaign)[key]),
                        "target": target,
                        "read_only": role == "status" or key == "control",
                        "bind": {},
                    }
                    for key, target in CONTAINER_ROOTS.items()
                ],
                "command": [
                    "--campaign",
                    plan.campaign,
                    "--lane",
                    "hyperliquid",
                    "--lane",
                    "binance",
                    "--chunk-seconds",
                    "600",
                    "--chunks",
                    "1",
                    "--pilot",
                ],
            }
            for role in ("collector", "status")
        },
        "x-portals": [{"host": config.bind_ip, "port": 18790}],
    }


@pytest.mark.parametrize("invalid", [True, 0, "false", None])
def test_render_never_weakens_bind_creation_contract(invalid: object) -> None:
    config = settings()
    plan = config.plan("fixture-next")
    value = compose_fixture(config, plan)
    value["services"]["status"]["volumes"][0]["bind"]["create_host_path"] = invalid
    with pytest.raises(ValueError, match="rendered bind"):
        host.normalize_compose(value, config, plan)


def test_known_renderer_omission_is_restored_for_all_eight_binds() -> None:
    config = settings()
    plan = config.plan("fixture-next")
    value = host.normalize_compose(compose_fixture(config, plan), config, plan)
    assert all(
        mount["bind"] == {"create_host_path": False}
        for service in value["services"].values()
        for mount in service["volumes"]
    )
    args = value["services"]["collector"]["command"]
    assert "--pilot" not in args and "--prune-spool" in args
    assert [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--lane"] == list(
        LANES
    )


@pytest.mark.parametrize("getter_shape", ["direct", "wrapped", "mixed"])
@pytest.mark.parametrize("slow_stopped_setup", [False, True])
def test_full_launch_executes_managed_app_then_gate_then_persists_new_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    getter_shape: str,
    slow_stopped_setup: bool,
) -> None:
    item = supervisor(monkeypatch)
    config = item.settings
    object.__setattr__(config, "runtime_root", private(tmp_path / "runtime"))
    object.__setattr__(config, "state_root", private(config.runtime_root / "continuous-control"))
    object.__setattr__(config, "capture", private(tmp_path / "capture"))
    object.__setattr__(config, "archive", private(tmp_path / "archive"))
    old_campaign = item.state["current"]["plan"]["campaign"]
    effects: list[str] = []
    durable: list[dict[str, Any]] = []
    original_save = item.save

    def persist(phase: str | None = None, reason: str | None = None) -> None:
        original_save(phase, reason)
        durable.append(copy.deepcopy(item.state))

    monkeypatch.setattr(item, "save", persist)
    rendered: dict[str, Any] = {}
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "OOMKilled": False}}
    )
    monkeypatch.setattr(cap, "snapshot", lambda: sample())
    monkeypatch.setattr(item, "admission", lambda: effects.append("admit"))
    monkeypatch.setattr(host, "approved_host_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "filesystem_identity", lambda *args: {})
    monkeypatch.setattr(item, "verify_store", lambda *args: None)
    monkeypatch.setattr(os, "chown", lambda *args: None)
    monkeypatch.setattr(host, "publish", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "owned", lambda *args: None)
    monkeypatch.setattr(item, "tick", lambda *args: None)
    monkeypatch.setattr(item, "clock", lambda generation: effects.append("fresh-clock"))
    if slow_stopped_setup:
        elapsed = [0]
        monkeypatch.setattr(
            time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + int(seconds))
        )
        monkeypatch.setattr(host, "notify", lambda message: None)
        monkeypatch.setattr(cap, "snapshot", lambda: sample(elapsed[0]))

        def delayed_verify() -> dict[str, Any]:
            elapsed[0] += 6
            return {"State": {"Running": False, "OOMKilled": False}}

        def real_capacity_tick(*args: Any) -> None:
            current = item.state["current"]
            adopted = current["adopted"]
            current["adopted"] = True  # Isolate kernel/store I/O, retain the real gap guard.
            try:
                host.Supervisor.tick(item)
            finally:
                current["adopted"] = adopted

        def fresh_admission() -> None:
            effects.append("admit")
            host.Supervisor.admission(item)

        item.previous = sample()
        monkeypatch.setattr(item, "verify_current", delayed_verify)
        monkeypatch.setattr(item, "tick", real_capacity_tick)
        monkeypatch.setattr(item, "admission", fresh_admission)

    def fake_command(argv: list[str], **kwargs: Any) -> str:
        if argv[:2] == ["docker", "compose"]:
            return json.dumps(
                compose_fixture(config, config.plan(kwargs["extra"]["COLLECTOR_CAMPAIGN"]))
            )
        if "app.update" in argv:
            effects.append("app.update")
            request = json.loads(kwargs["stdin"])
            assert set(request) == {"custom_compose_config"}
            rendered.update(request["custom_compose_config"])
            return "123"
        if "app.get_instance" in argv:
            effects.append("app.get_instance")
            value = rendered if getter_shape == "direct" else {"custom_compose_config": rendered}
            if getter_shape == "mixed":
                value = rendered | value
            return json.dumps({"id": host.APP, "custom_app": True, "config": value})
        raise AssertionError(argv)

    monkeypatch.setattr(host, "command", fake_command)
    monkeypatch.setattr(
        host,
        "inspect",
        lambda name: {
            "Id": "1" * 64 if name == host.COLLECTOR else "2" * 64,
            "State": {"Running": True},
        },
    )

    def gate(argv: list[str], **kwargs: Any) -> str:
        if "app.update" in argv:
            if slow_stopped_setup:
                item.tick()
            return fake_command(argv, **kwargs)
        assert durable[-1]["current"]["collector_id"] == "1" * 64
        effects.append("TC-gate")
        return ""

    monkeypatch.setattr(item, "poll_child", gate)
    if getter_shape != "direct":
        with pytest.raises(ValueError, match="direct Compose shape"):
            item.launch()
        assert item.state["current"]["app_config_sha256"] is None
        assert effects[-3:] == ["app.update", "TC-gate", "app.get_instance"]
        return
    item.launch()
    current = item.state["current"]
    assert current["plan"]["campaign"] != old_campaign
    assert current["plan"]["source_commit"] == config.source_commit
    assert current["collector_id"] == "1" * 64
    assert current["status_id"] == "2" * 64
    assert current["app_config_sha256"] == host.digest(rendered)
    assert effects == ["fresh-clock", "admit", "app.update", "TC-gate", "app.get_instance"]
    assert current["roots"]["spool"] != current["roots"]["control"]
    assert item.state["phase"] == "capturing"


def test_launch_cannot_replace_running_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": True}})
    monkeypatch.setattr(item, "admission", lambda: pytest.fail("Running generation replaced"))
    with pytest.raises(ValueError, match="active admitted campaign"):
        item.launch()


def test_restart_between_campaigns_uses_fresh_launch_without_double_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.state["phase"] = "between-campaigns"
    monkeypatch.setattr(item, "admission", lambda: None)
    monkeypatch.setattr(host, "notify", lambda *args: None)
    monkeypatch.setattr(item, "observe", lambda: pytest.fail("Reaccepted already closed namespace"))
    seen = []

    def launch() -> None:
        seen.append("new")
        item.stopping = True

    monkeypatch.setattr(item, "launch", launch)
    monkeypatch.setattr(item, "tick", lambda: None)
    assert item.run() == 0 and seen == ["new"]


def test_fatal_boundary_pauses_and_stops_only_new_owned_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.state["current"]["adopted"] = False
    monkeypatch.setattr(item, "admission", lambda: None)
    monkeypatch.setattr(host, "notify", lambda *args: None)

    def fail() -> bool:
        raise ValueError("Storage reserve reached")

    monkeypatch.setattr(item, "observe", fail)
    stopped = []
    monkeypatch.setattr(item, "stop_owned", lambda: stopped.append("only-owned"))
    assert item.run() == 2
    assert item.state["phase"] == "paused" and stopped == ["only-owned"]
    assert item.state["reason"] == "Storage reserve reached"


def test_paused_state_never_restarts_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    item.state["phase"] = "paused"
    monkeypatch.setattr(item, "admission", lambda: pytest.fail("Paused boundary bypassed"))
    assert item.run() == 2


def test_large_child_output_does_not_deadlock_on_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(item, "tick", lambda *args: None)
    import sys

    output = item.poll_child([sys.executable, "-c", "print('a' * 24000)"], timeout=5)
    assert len(output.strip()) == 24000


def test_admitting_cleanup_recovers_only_new_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.state["current"].update(adopted=False, collector_id=None)
    doc = {"Id": "3" * 64, "State": {"Running": True}}
    monkeypatch.setattr(host, "inspect", lambda name: doc)
    monkeypatch.setattr(host, "owned", lambda value, gen, role: None)
    seen = []

    def stop_command(argv: list[str], **kwargs: Any) -> str:
        seen.append(argv)
        return ""

    monkeypatch.setattr(host, "command", stop_command)
    item.stop_owned()
    assert item.state["current"]["collector_id"] == "3" * 64
    assert seen == [["docker", "stop", "--time", "90", "3" * 64]]


def test_admitting_cleanup_refuses_prior_campaign_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    item.state["current"].update(adopted=False, collector_id=None)
    monkeypatch.setattr(host, "inspect", lambda name: {"Id": "old-adopted"})

    def wrong(*args: Any) -> None:
        raise ValueError("Old campaign differs")

    monkeypatch.setattr(host, "owned", wrong)
    monkeypatch.setattr(
        host, "command", lambda *args, **kwargs: pytest.fail("Old campaign stopped")
    )
    item.stop_owned()
    assert item.state["current"]["collector_id"] is None


def test_boot_recovery_audits_then_requires_fresh_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.state["current"]["boot_id"] = "previous-boot"
    effects = []
    monkeypatch.setattr(item, "app", lambda: {"config": {"fixture": True}})
    item.state["current"]["app_config_sha256"] = host.digest({"fixture": True})
    monkeypatch.setattr(host, "inspect", lambda name: {"Id": "new-cid", "State": {"Running": True}})
    monkeypatch.setattr(host, "owned", lambda *args: None)
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": True}})
    monkeypatch.setattr(item, "check_interrupted_metadata", lambda generation: None)
    monkeypatch.setattr(item, "verify_store", lambda *args: None)

    def stop_command(argv: list[str], **kwargs: Any) -> str:
        effects.append("old-stop")
        return ""

    monkeypatch.setattr(host, "command", stop_command)
    monkeypatch.setattr(item, "audit", lambda **kwargs: effects.append("audit-closed-copies"))
    monkeypatch.setattr(item, "gap", lambda reason: effects.append(reason))
    monkeypatch.setattr(host, "publish", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "exists", lambda path: False)
    item.recover_boot()
    assert effects == ["old-stop", "audit-closed-copies", "host-reboot-interruption"]
    assert item.state["phase"] == "between-campaigns"
    assert item.state["current"]["boot_id"] == "previous-boot"


def test_already_journaled_gap_survives_crash_without_duplicate_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    retained = {
        "schema": "collector-continuous-gap-v1",
        "reason": "scheduled-publication-and-new-sessions",
        "previous_campaign": item.state["current"]["plan"]["campaign"],
        "detected_utc_ns": 123,
        "end_utc_ns": None,
    }
    monkeypatch.setattr(Path, "exists", lambda path: True)
    monkeypatch.setattr(host, "document", lambda *args, **kwargs: copy.deepcopy(retained))
    monkeypatch.setattr(
        host, "publish", lambda *args, **kwargs: pytest.fail("Immutable gap overwritten")
    )
    item.gap("scheduled-publication-and-new-sessions")
    assert item.state["last_gap"] == retained


def test_cold_boot_waits_only_for_availability_with_bounded_readonly_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []

    def release(config: host.Settings) -> None:
        attempts.append("probe")
        if len(attempts) == 1:
            raise host.HostUnavailableError("Docker not ready")

    monkeypatch.setattr(host, "verify_release", release)
    monkeypatch.setattr(host, "command", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(host, "inspect", lambda *args: {"State": {"Running": True}})
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(host, "notify", lambda *args: None)
    host.wait_host_ready(settings(), seconds=10)
    assert attempts == ["probe", "probe"]

    def bad_source(config: host.Settings) -> None:
        raise ValueError("Wrong source proof")

    monkeypatch.setattr(host, "verify_release", bad_source)
    with pytest.raises(ValueError, match="Wrong source"):
        host.wait_host_ready(settings(), seconds=10)


@pytest.mark.parametrize("bad", [1, None, True, -1])
def test_valid_seal_with_native_integrity_or_missing_evidence_is_refused(
    tmp_path: Path,
    bad: object,
) -> None:
    plan, work, archive = closed_fixture(tmp_path)
    for root in (work, archive):
        destination = root / plan.campaign / "hyperliquid" / "synthetic-0000"
        health_path = destination / "capture-health.json"
        value = json.loads(health_path.read_text())
        if bad is None:
            value.pop("integrity_events")
        else:
            value["integrity_events"] = bad
        health_path.write_text(json.dumps(value))
        manifest_path = destination / "publication-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["capture-health.json"] = {
            "bytes": health_path.stat().st_size,
            "sha256": hashlib.sha256(health_path.read_bytes()).hexdigest(),
        }
        manifest_path.write_text(json.dumps(manifest))
        (destination / "COMPLETE.json").write_text(
            json.dumps(
                {
                    "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    "closed_chunk_verified": True,
                }
            )
        )
    with pytest.raises(ValueError, match="Native terminal health"):
        audit(plan, work, archive, complete=True)


def managed_fixture(gen: dict[str, Any], role: str) -> dict[str, Any]:
    values = dict(gen["plan"])
    values["lanes"] = tuple(values["lanes"])
    plan = RuntimePlan(**values)
    entry, args = host.process_contract(plan, role)
    environment = {
        "COLLECTOR_SOURCE_COMMIT": plan.source_commit,
        "GPG_KEY": "synthetic-public-gpg",
        "HOME": "/tmp",
        "PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "/app/src",
        "PYTHONUNBUFFERED": "1",
        "PYTHON_SHA256": "synthetic",
        "PYTHON_VERSION": "3.13.15",
        "TRADING_MODE": "PAPER",
        "TZ": "UTC",
    }
    memory = 2 * cap.GIB if role == "collector" else 256 * 1024**2
    return {
        "Id": gen[role + "_id"],
        "Name": "/" + (host.COLLECTOR if role == "collector" else host.STATUS),
        "Image": gen["image_id"],
        "RestartCount": 0,
        "State": {"Running": True, "OOMKilled": False},
        "Config": {
            "Image": host.IMAGE_REPOSITORY + plan.image_digest,
            "User": "1000:1000",
            "Cmd": args,
            "Entrypoint": entry,
            "Env": [key + "=" + value for key, value in environment.items()],
            "Labels": {
                "com.docker.compose.project": host.PROJECT,
                "com.docker.compose.service": role,
                "org.hyperliquid-bot.campaign": plan.campaign,
                "org.opencontainers.image.revision": plan.source_commit,
            },
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": memory,
            "MemorySwap": memory,
            "NanoCpus": 2_000_000_000 if role == "collector" else 250_000_000,
            "PidsLimit": 128 if role == "collector" else 64,
            "NetworkMode": "project-default",
            "RestartPolicy": {"Name": "no" if role == "collector" else "unless-stopped"},
            "PortBindings": {},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Destination": target,
                "Source": gen["roots"][key],
                "RW": role == "collector" and key != "control",
            }
            for key, target in CONTAINER_ROOTS.items()
        ],
    }


@pytest.mark.parametrize("role", ["collector", "status"])
def test_real_process_contract_accepts_exact_existing_and_new_generations(role: str) -> None:
    gen = generation()
    host.owned(managed_fixture(gen, role), gen, role)
    gen["plan"].update(source_commit="d" * 40, prune_spool=False)
    host.owned(managed_fixture(gen, role), gen, role)


@pytest.mark.parametrize("change", ["command", "entrypoint", "mode", "source-env", "extra-env"])
def test_discovery_never_claims_another_process_under_source_labels(change: str) -> None:
    gen = generation()
    value = managed_fixture(gen, "collector")
    if change == "command":
        value["Config"]["Cmd"] = ["--health"]
    elif change == "entrypoint":
        value["Config"]["Entrypoint"] = ["python", "-c", "print('synthetic')"]
    elif change == "mode":
        value["Config"]["Env"] = [
            item.replace("TRADING_MODE=PAPER", "TRADING_MODE=LIVE")
            for item in value["Config"]["Env"]
        ]
    elif change == "source-env":
        value["Config"]["Env"] = [
            item.replace("COLLECTOR_SOURCE_COMMIT=", "MISSING_SOURCE=")
            for item in value["Config"]["Env"]
        ]
    else:
        value["Config"]["Env"].append("LD_PRELOAD=/tmp/synthetic.so")
    with pytest.raises(ValueError):
        host.owned(value, gen, "collector")


def test_graceful_shutdown_retains_intent_but_new_boot_admits_fresh_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(item, "admission", lambda: None)
    monkeypatch.setattr(host, "notify", lambda *args: None)
    monkeypatch.setattr(item, "observe", lambda: False)
    stopped = []
    monkeypatch.setattr(item, "stop_owned", lambda: stopped.append("owned-only"))

    def terminate() -> None:
        item.stopping = True
        raise ValueError("Operator stopped continuous supervision.")

    monkeypatch.setattr(item, "tick", terminate)
    assert item.run() == 0
    assert item.state["phase"] == "stopped"
    item.stopping = False
    assert item.run() == 2
    item.boot = "new-boot"
    effects = []

    def recover() -> None:
        effects.append("verify-old-boot-and-copies")
        item.state["phase"] = "between-campaigns"

    def launch() -> None:
        effects.append("fresh-campaign")
        item.state["current"]["boot_id"] = item.boot
        item.stopping = True

    monkeypatch.setattr(item, "recover_boot", recover)
    monkeypatch.setattr(item, "launch", launch)
    monkeypatch.setattr(item, "tick", lambda: None)
    assert item.run() == 0
    assert effects == ["verify-old-boot-and-copies", "fresh-campaign"]
    assert len(stopped) == 2


def test_integrity_pause_stays_paused_across_host_reboot(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    item.state["phase"] = "paused"
    item.boot = "new-boot"
    monkeypatch.setattr(item, "admission", lambda: pytest.fail("Integrity pause bypassed on boot"))
    assert item.run() == 2


def test_latched_container_is_stopped_despite_resource_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    gen = item.state["current"]
    gen["adopted"] = False
    value = managed_fixture(gen, "collector")
    value["HostConfig"]["Memory"] += 1
    value["HostConfig"]["NanoCpus"] += 1
    with pytest.raises(ValueError):
        host.owned(value, gen, "collector")
    monkeypatch.setattr(host, "inspect", lambda name: value)
    commands = []

    def stop_command(argv: list[str], **kwargs: Any) -> str:
        commands.append(argv)
        return ""

    monkeypatch.setattr(host, "command", stop_command)
    item.stop_owned()
    assert commands == [["docker", "stop", "--time", "90", gen["collector_id"]]]
    value["Id"] = "9" * 64
    commands.clear()
    with pytest.raises(ValueError, match="identity differs"):
        item.stop_owned()
    assert not commands


def test_failed_control_save_never_prevents_owned_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(item, "admission", lambda: None)
    monkeypatch.setattr(host, "notify", lambda *args: None)

    def fail() -> bool:
        raise ValueError("Storage full")

    def save(*args: Any) -> None:
        raise OSError("ENOSPC synthetic fixture")

    monkeypatch.setattr(item, "observe", fail)
    monkeypatch.setattr(item, "save", save)
    stopped = []
    monkeypatch.setattr(item, "stop_owned", lambda: stopped.append(True))
    with pytest.raises(OSError, match="ENOSPC"):
        item.run()
    assert stopped == [True]


@pytest.mark.parametrize("unsafe", ["integrity", "writer", "failed-controller"])
def test_boot_cannot_erase_native_or_controller_failure_before_observation(
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    item = supervisor(monkeypatch)
    gen = item.state["current"]
    gen["boot_id"] = "old-boot"
    monkeypatch.setattr(Path, "exists", lambda path: True)

    def metadata(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path.name == "runtime-health.json":
            return {
                "campaign": gen["plan"]["campaign"],
                "chunk": 0,
                "phase": "failed" if unsafe == "failed-controller" else "capturing",
            }
        return {
            "status": "FAILED",
            "run_id": gen["plan"]["campaign"] + "-0000",
            "credentialless": True,
            "integrity_events": 1 if unsafe == "integrity" else 0,
            "exception_class": "WriterError",
        }

    monkeypatch.setattr(host, "document", metadata)
    with pytest.raises(ValueError):
        item.check_interrupted_metadata(gen)


def test_graceful_operator_stop_and_absent_abrupt_terminal_are_allowed_at_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    gen = item.state["current"]
    gen["boot_id"] = "old-boot"
    monkeypatch.setattr(Path, "exists", lambda path: False)
    item.check_interrupted_metadata(gen)
    item.state["last_graceful_stop_boot_id"] = "old-boot"
    item.state["last_graceful_stop_campaign"] = gen["plan"]["campaign"]
    monkeypatch.setattr(Path, "exists", lambda path: True)

    def metadata(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path.name == "runtime-health.json":
            return {"campaign": gen["plan"]["campaign"], "chunk": 0, "phase": "failed"}
        return {
            "status": "OPERATOR_STOP",
            "run_id": gen["plan"]["campaign"] + "-0000",
            "credentialless": True,
            "integrity_events": 0,
        }

    monkeypatch.setattr(host, "document", metadata)
    item.check_interrupted_metadata(gen)


def test_real_startup_sampling_does_not_check_stale_preboot_cid_before_rebind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.boot = "new-boot"
    item.state["current"].update(adopted=False, boot_id="old-boot")
    values = iter(sample(index) for index in range(20))
    monkeypatch.setattr(cap, "snapshot", lambda: next(values))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(host, "notify", lambda *args: None)
    monkeypatch.setattr(
        os, "statvfs", lambda path: type("Space", (), {"f_bavail": 10 * cap.GIB, "f_frsize": 1})()
    )
    monkeypatch.setattr(
        host, "inspect", lambda name: pytest.fail("Old CID checked before boot rebind")
    )
    monkeypatch.setattr(Path, "exists", lambda path: False)
    effects = []

    def rebind() -> None:
        effects.append("exact-boot-rebind")
        item.state["phase"] = "between-campaigns"

    def launch() -> None:
        effects.append("fresh-admission")
        item.state["current"]["boot_id"] = item.boot
        item.stopping = True

    monkeypatch.setattr(item, "recover_boot", rebind)
    monkeypatch.setattr(item, "launch", launch)
    monkeypatch.setattr(item, "stop_owned", lambda: None)
    monkeypatch.setattr(item, "verify_store", lambda *args: None)
    assert item.run() == 0
    assert effects == ["exact-boot-rebind", "fresh-admission"]


def test_store_guid_and_inode_pins_fail_closed_without_changing_any_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    identity = {
        "filesystem": "zfs",
        "source": item.settings.storage_sources["capture"],
        "inode": 42,
        "device": 7,
    }
    monkeypatch.setattr(host, "filesystem_identity", lambda path: identity)
    monkeypatch.setattr(host, "command", lambda argv, **kwargs: "12345\n")
    item.verify_store("capture", item.settings.capture)
    identity["inode"] = 43
    with pytest.raises(ValueError, match="inode changed"):
        item.verify_store("capture", item.settings.capture)
    identity["inode"] = 42
    monkeypatch.setattr(host, "command", lambda argv, **kwargs: "54321\n")
    with pytest.raises(ValueError, match="GUID changed"):
        item.verify_store("capture", item.settings.capture)


def test_persistent_operator_pause_survives_docker_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = supervisor(monkeypatch)
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(host, "load_settings", lambda path: item.settings)
    monkeypatch.setattr(host, "verify_release", lambda cfg, **kwargs: None)
    monkeypatch.setattr(
        host, "wait_host_ready", lambda *args: pytest.fail("Pause waited for Docker")
    )
    monkeypatch.setattr(host, "singleton", lambda root: contextlib.nullcontext())
    monkeypatch.setattr(host, "Supervisor", lambda config: item)

    def unavailable() -> None:
        raise host.HostUnavailableError("Docker is unavailable")

    monkeypatch.setattr(item, "stop_owned", unavailable)
    assert host.main(["--settings", "/tmp/synthetic-settings.json", "--pause"]) == 2
    assert item.state["phase"] == "paused"
    value = json.loads(capsys.readouterr().out)
    assert value == {"persistent_pause": True, "owned_cleanup": "unconfirmed"}


@pytest.mark.parametrize("guard_ready", [False, True])
def test_real_run_adoption_without_authority_flag_never_mutates_existing_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guard_ready: bool,
) -> None:
    item = supervisor(monkeypatch)
    original = generation()
    original.pop("adopted")
    original["capacity_guard"] = {
        "unit": original["plan"]["campaign"] + "-capacity.service",
        "records": str(
            item.settings.runtime_root / ("operator-record-" + original["plan"]["campaign"])
        ),
        "policy_sha256": "f" * 64,
    }
    object.__setattr__(item.settings, "adoption", copy.deepcopy(original))
    item.state.update(current=None, phase="adopting")
    item.path = tmp_path / "root-supervisor-state.json"
    # Exercise real run/admission/adopt/save/owned/guard/cleanup methods; fake
    # only kernel, filesystem, Docker/middleware and systemd boundaries.
    monkeypatch.setattr(item, "save", host.Supervisor.save.__get__(item, host.Supervisor))
    values = iter(sample(index) for index in range(10))
    monkeypatch.setattr(cap, "snapshot", lambda: next(values))
    sleeps = 0

    def sleep(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 3:
            item.stopping = True

    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(time, "monotonic_ns", lambda: 5_000_000_000)
    monkeypatch.setattr(host, "notify", lambda message: None)
    monkeypatch.setattr(host, "directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "exists", lambda path: False)
    actual_stat = Path.stat

    def stat_path(path: Path, **kwargs: Any) -> Any:
        if path == item.settings.runtime_root:
            return SimpleNamespace(st_ino=42)
        return actual_stat(path, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_path)

    def filesystem(path: Path) -> dict[str, object]:
        role = (
            "capture"
            if path == item.settings.capture
            else ("archive" if path == item.settings.archive else "runtime")
        )
        return {
            "filesystem": "zfs",
            "source": item.settings.storage_sources[role],
            "device": 1,
            "inode": 42,
        }

    monkeypatch.setattr(host, "filesystem_identity", filesystem)
    plan_values = dict(original["plan"])
    plan_values["lanes"] = tuple(plan_values["lanes"])
    plan = RuntimePlan(**plan_values)
    config = json.loads(
        (Path(__file__).parent / "fixtures/truenas_app_get_instance_direct.json").read_text()
    )["config"]
    for role in ("collector", "status"):
        config["services"][role]["command"] = host.process_contract(plan, role)[1]
    original["app_config_sha256"] = host.digest(config)
    item.settings.adoption["app_config_sha256"] = original["app_config_sha256"]
    commands: list[list[str]] = []

    def command(argv: list[str], **kwargs: Any) -> str:
        commands.append(argv)
        if argv[0] == "zfs":
            return "12345\n"
        if argv[:3] == ["midclt", "call", "app.get_instance"]:
            return json.dumps({"id": host.APP, "custom_app": True, "config": config})
        if argv[:2] == ["docker", "ps"]:
            return str(original["collector_id"]) + "\n" + str(original["status_id"])
        if argv[:2] == ["docker", "inspect"]:
            role = "collector" if argv[2] == host.COLLECTOR else "status"
            return json.dumps([managed_fixture(original, role)])
        if argv[:2] == ["systemctl", "show"]:
            return (
                ("active" if guard_ready else "inactive")
                if "--property=ActiveState" in argv
                else "123"
            )
        if argv[:2] == ["docker", "stop"]:
            return argv[-1]  # Record a regression's mutation without executing it.
        raise AssertionError("Unexpected administrative command: " + argv[0])

    monkeypatch.setattr(host, "command", command)

    def metadata(path: Path, **kwargs: Any) -> dict[str, Any]:
        assert path == Path(original["capacity_guard"]["records"]) / "capacity-state.json"
        return {
            "PID": 123,
            "state": "READY",
            "boot_id": item.boot,
            "container_id": original["collector_id"],
            "policy_sha256": "f" * 64,
            "monotonic_ns": 5_000_000_000,
        }

    monkeypatch.setattr(host, "document", metadata)
    writes: list[tuple[Path, dict[str, Any]]] = []

    def publish(path: Path, value: dict[str, Any], **kwargs: Any) -> None:
        writes.append((path, copy.deepcopy(value)))

    monkeypatch.setattr(host, "publish", publish)
    assert item.run() == (0 if guard_ready else 2)
    assert item.state["phase"] == ("stopped" if guard_ready else "paused")
    assert item.state["current"]["adopted"] is True
    assert "adopted" not in item.settings.adoption
    assert writes and all(path == item.path for path, _value in writes)
    assert all(argv[0] in {"zfs", "docker", "midclt", "systemctl"} for argv in commands)
    assert not any(
        argv[:2] == ["docker", "stop"]
        or "app.update" in argv
        or any(arg in {"stop", "restart", "tc", "nsenter"} for arg in argv)
        for argv in commands
    )


def test_real_app_getter_and_current_identity_keep_full_raw_config_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    gen = item.state["current"]
    response = json.loads(
        (Path(__file__).parent / "fixtures/truenas_app_get_instance_direct.json").read_text()
    )
    values = dict(gen["plan"])
    values["lanes"] = tuple(values["lanes"])
    for role in ("collector", "status"):
        response["config"]["services"][role]["command"] = host.process_contract(
            RuntimePlan(**values), role
        )[1]
    gen["app_config_sha256"] = host.digest(response["config"])
    raw_before = copy.deepcopy(response["config"])

    def command(argv: list[str], **kwargs: Any) -> str:
        if argv[:3] == ["midclt", "call", "app.get_instance"]:
            assert json.loads(argv[-1]) == {"extra": {"retrieve_config": True}}
            return json.dumps(response)
        if argv[:2] == ["docker", "ps"]:
            return str(gen["collector_id"]) + "\n" + str(gen["status_id"])
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps(
                [managed_fixture(gen, "collector" if argv[2] == host.COLLECTOR else "status")]
            )
        raise AssertionError("Unexpected host mutation: " + argv[0])

    monkeypatch.setattr(host, "command", command)
    assert item.verify_current()["Id"] == gen["collector_id"]
    assert response["config"] == raw_before
    assert host.native_app_config(response) is response["config"]
    assert gen["app_config_sha256"] == host.digest(raw_before)
    response["config"]["x-notes"] += " synthetic drift"
    with pytest.raises(ValueError, match="configuration changed"):
        item.verify_current()


@pytest.mark.parametrize(
    "shape", ["wrapped", "mixed", "unknown", "services-list", "wrong-services", "command-string"]
)
def test_real_app_getter_refuses_unsupported_or_ambiguous_config_shapes(
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    item = supervisor(monkeypatch)
    response = json.loads(
        (Path(__file__).parent / "fixtures/truenas_app_get_instance_direct.json").read_text()
    )
    config = response["config"]
    if shape == "wrapped":
        response["config"] = {"custom_compose_config": config}
    elif shape == "mixed":
        response["config"] = config | {"custom_compose_config": copy.deepcopy(config)}
    elif shape == "unknown":
        response["config"] = {"different": config}
    elif shape == "services-list":
        config["services"] = []
    elif shape == "wrong-services":
        config["services"]["unrelated"] = {"command": []}
    else:
        config["services"]["collector"]["command"] = "synthetic-string"
    calls = []

    def command(argv: list[str], **kwargs: Any) -> str:
        calls.append(argv)
        assert argv[:3] == ["midclt", "call", "app.get_instance"]
        return json.dumps(response)

    monkeypatch.setattr(host, "command", command)
    with pytest.raises(ValueError, match="Native App getter"):
        item.app()
    assert len(calls) == 1


def pressure_error() -> host.CapacityStop:
    previous, current = sample(), sample(1)
    current["psi"]["full"]["total"] += 20_000
    return host.CapacityStop(previous, current, {"psi": 2, "low": 0}, ["memory-pressure"])


def pressure_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[host.Supervisor, dict[str, dict[str, Any]], list[str]]:
    item = supervisor(monkeypatch)
    item.state["current"]["adopted"] = False
    monkeypatch.setattr(cap, "snapshot", lambda: sample())
    saved: dict[str, dict[str, Any]] = {}
    effects: list[str] = []
    monkeypatch.setattr(
        host,
        "publish",
        lambda path, value, **kw: saved.__setitem__(path.name, copy.deepcopy(value)),
    )
    monkeypatch.setattr(host, "document", lambda path, **kw: copy.deepcopy(saved[path.name]))
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "OOMKilled": False}}
    )
    monkeypatch.setattr(item, "stop_owned", lambda: effects.append("stop-owned"))
    monkeypatch.setattr(item, "pressure_health", lambda: effects.append("native-health"))
    monkeypatch.setattr(item, "pressure_capture", lambda producer: {"phase": "capturing"})
    monkeypatch.setattr(item, "pressure_wait_guards", lambda: None)
    monkeypatch.setattr(item, "audit", lambda **kwargs: effects.append("audit"))
    monkeypatch.setattr(item, "gap", lambda reason: effects.append(reason))
    monkeypatch.setattr(host, "notify", lambda message: None)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(time, "time_ns", lambda: 100_000_000_000)
    return item, saved, effects


def test_tick_raises_structured_capacity_incident_before_other_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.previous = sample()
    item.streaks = {"psi": 1}
    current = sample(1)
    current["psi"]["full"]["total"] += 20_000
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(host, "notify", lambda message: None)
    monkeypatch.setattr(cap, "snapshot", lambda: current)
    with pytest.raises(host.CapacityStop) as caught:
        item.tick()
    assert caught.value.reasons == ("memory-pressure",)
    assert caught.value.previous == sample()
    assert caught.value.current == current
    assert caught.value.streaks["psi"] == 2


def test_pressure_records_proof_and_waits_a_full_stable_minute_before_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    assert item.state["phase"] == "pressure-wait" and item.state["pressure_retry_count"] == 1
    receipt = item.pressure_receipt()
    assert receipt["before"] == sample() and receipt["streaks"]["psi"] == 2
    assert receipt["cgroup_events"] == {"oom": 0, "oom_kill": 0}
    assert item.previous is None and effects == ["stop-owned"]
    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000_000_000)
    samples = iter(sample(index) for index in range(64))
    monkeypatch.setattr(cap, "snapshot", lambda: next(samples))
    for _ in range(62):
        assert item.wait_pressure() is False
    assert item.wait_pressure() is True
    assert effects == [
        "stop-owned",
        "native-health",
        "audit",
        "classified-host-pressure-interruption",
    ]
    assert item.state["phase"] == "between-campaigns"
    assert len(saved) == 1


def test_pressure_run_uses_fresh_launch_after_waiting_and_retains_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    monkeypatch.setattr(item, "admission", lambda: None)
    monkeypatch.setattr(item, "observe", lambda: (_ for _ in ()).throw(pressure_error()))

    def wait() -> bool:
        effects.append("stable-and-audited")
        return True

    def launch() -> None:
        effects.append("fresh-launch")
        item.state["current"]["plan"]["campaign"] = "fresh-fixture"
        item.stopping = True

    monkeypatch.setattr(item, "wait_pressure", wait)
    monkeypatch.setattr(item, "launch", launch)
    monkeypatch.setattr(item, "shutdown_phase", lambda: "stopped")
    assert item.run() == 0
    assert effects[:3] == ["stop-owned", "stable-and-audited", "fresh-launch"]
    assert item.state["current"]["plan"]["campaign"] == "fresh-fixture"


@pytest.mark.parametrize("reason", ["global-oom", "low-memory", "zfs-throttle"])
def test_hard_capacity_conditions_never_enter_pressure_recovery(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    item, saved, effects = pressure_fixture(monkeypatch)
    monkeypatch.setattr(item, "admission", lambda: None)
    error = host.CapacityStop(sample(), sample(1), {}, ["memory-pressure", reason])
    monkeypatch.setattr(item, "observe", lambda: (_ for _ in ()).throw(error))
    assert item.run() == 2
    assert item.state["phase"] == "paused" and effects == ["stop-owned"]
    assert len(saved) == 1 and "pressure_recovery" not in item.state
    incident = next(iter(saved.values()))
    assert incident["schema"] == "collector-capacity-incident-v1"
    assert incident["reasons"] == ["memory-pressure", reason]


def test_pressure_receipt_tampering_retry_exhaustion_and_adopted_capture_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, saved, _effects = pressure_fixture(monkeypatch)
    item.state["current"]["adopted"] = True
    with pytest.raises(ValueError, match="adopted"):
        item.begin_pressure_wait(pressure_error())
    assert not saved
    item.state["current"]["adopted"] = False
    item.state["pressure_retry_count"] = 5
    with pytest.raises(ValueError, match="budget"):
        item.begin_pressure_wait(pressure_error())
    item.state["pressure_retry_count"] = 0
    item.begin_pressure_wait(pressure_error())
    receipt = saved[item.state["pressure_recovery"]["receipt"]]
    receipt["reasons"] = ["global-oom"]
    with pytest.raises(ValueError, match="receipt differs"):
        item.pressure_receipt()


def test_unknown_partial_native_health_and_failed_sealed_copy_hard_pause_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    item.pressure_stable_since = 1
    item.pressure_samples = [sample(61), sample(62)]
    item.previous = sample(62)
    monkeypatch.setattr(cap, "snapshot", lambda: sample(63))
    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000_000_000)
    monkeypatch.setattr(
        item, "pressure_health", lambda: (_ for _ in ()).throw(ValueError("Unknown native failure"))
    )
    with pytest.raises(ValueError, match="Unknown native"):
        item.wait_pressure()
    assert "audit" not in effects
    monkeypatch.setattr(item, "pressure_health", lambda: None)
    monkeypatch.setattr(cap, "snapshot", lambda: sample(64))
    monkeypatch.setattr(
        item, "audit", lambda **kw: (_ for _ in ()).throw(ValueError("Bad sealed copy"))
    )
    with pytest.raises(ValueError, match="Bad sealed"):
        item.wait_pressure()
    assert "classified-host-pressure-interruption" not in effects


def test_pressure_wait_resets_after_restart_and_rebinds_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    del item.pressure_samples
    item.previous = sample(0)  # Old timestamp must not span a long stopped interval.
    monkeypatch.setattr(
        item, "pressure_rebind", lambda: {"State": {"Running": False, "OOMKilled": False}}
    )
    monkeypatch.setattr(cap, "snapshot", lambda: sample(1000))
    assert item.wait_pressure() is False
    assert item.previous == sample(1000) and effects == ["stop-owned", "stop-owned"]


def test_legacy_operator_recovery_requires_exact_state_pin_and_explicit_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, saved, effects = pressure_fixture(monkeypatch)
    item.state.update(phase="paused", reason="Capacity stop: memory-pressure")
    monkeypatch.setattr(
        item, "pressure_rebind", lambda: {"State": {"Running": False, "OOMKilled": False}}
    )
    monkeypatch.setattr(
        item,
        "pressure_metadata",
        lambda: [{"lane": "binance", "name": "capture-health.json", "present": False}],
    )
    with pytest.raises(ValueError, match="pin differs"):
        item.begin_pressure_wait(expected_state_sha256="0" * 64, abandon_unsealed=True)
    before = copy.deepcopy(item.state)
    item.begin_pressure_wait(expected_state_sha256=host.digest(before), abandon_unsealed=True)
    receipt = item.pressure_receipt()
    assert receipt["schema"] == "collector-pressure-operator-v1"
    assert host.digest(receipt["prior_state"]) == host.digest(before)
    assert receipt["operator_revalidated"] is True
    assert (
        receipt["abandon_unsealed"] is True and receipt["old_partial_research_qualified"] is False
    )
    assert receipt["partial_metadata"][0]["present"] is False
    assert effects == ["stop-owned"] and len(saved) == 1


def test_bootstrap_recovery_flags_do_not_recur_in_cleanup_or_postinit_entry() -> None:
    cfg = settings()
    flags = (
        "--recover-pressure",
        "--expected-state-sha256",
        "a" * 64,
        "--abandon-unsealed-pressure",
    )
    argv = start_argv(cfg.release, cfg.state_root / "settings.json", cfg.state_root, flags)
    assert argv[-len(flags) :] == list(flags)
    cleanup = next(arg for arg in argv if arg.startswith("--property=ExecStopPost="))
    assert "--recover-pressure" not in cleanup and "--stop-owned" in cleanup
    assert "--property=LogRateLimitBurst=100" in argv


def test_watchdog_command_keeps_heartbeat_only_for_bounded_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    notices = []
    called = threading.Event()

    def notify(message: str) -> None:
        notices.append(message)
        if len(notices) >= 2:
            called.set()

    monkeypatch.setattr(host, "notify", notify)
    with host.watchdog_command(5):
        assert called.wait(5)
        assert len(notices) >= 2
    count = len(notices)
    time.sleep(0.1)
    assert len(notices) == count


def test_closing_notify_socket_never_prevents_bounded_command_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host, "notify", lambda message: (_ for _ in ()).throw(OSError("closing")))
    import sys

    assert host.command([sys.executable, "-c", "print('cleanup-ready')"]).strip() == "cleanup-ready"


def test_operator_quarantine_records_absent_terminal_and_refuses_known_native_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    campaign = item.state["current"]["plan"]["campaign"]
    run_id = campaign + "-0000"

    def read(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path.name == "runtime-health.json":
            return {"campaign": campaign, "chunk": 0}
        if path.name == "capture-health.json":
            return {
                "run_id": run_id,
                "credentialless": True,
                "integrity_events": 0,
                "status": "OPERATOR_STOP",
            }
        return {"fixture": True}

    monkeypatch.setattr(host, "document", read)
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: not ("binance" in path.parts and path.name == "capture-health.json"),
    )
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_dev=1, st_ino=2, st_size=3))
    records = item.pressure_metadata()
    assert len(records) == 12
    assert [r for r in records if r["present"] is False] == [
        {"lane": "binance", "name": "capture-health.json", "present": False}
    ]
    assert all("document_sha256" in r for r in records if r["present"])

    def unsafe(path: Path, **kwargs: Any) -> dict[str, Any]:
        value = read(path, **kwargs)
        return value | {"status": "FAILED"} if path.name == "capture-health.json" else value

    monkeypatch.setattr(host, "document", unsafe)
    with pytest.raises(ValueError, match="cannot erase"):
        item.pressure_metadata()


def test_hard_counter_change_during_pressure_wait_never_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    item.previous = sample()
    current = sample(1)
    current["global_oom_kill"] = 1
    monkeypatch.setattr(cap, "snapshot", lambda: current)
    with pytest.raises(ValueError, match="Hard capacity"):
        item.wait_pressure()
    assert effects == ["stop-owned"]


def test_restart_from_valid_pressure_receipt_does_not_reenter_old_boot_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    item.state["current"]["boot_id"] = "previous-boot"
    monkeypatch.setattr(
        item, "admission", lambda: pytest.fail("stale admission before pressure wait")
    )
    monkeypatch.setattr(item, "recover_boot", lambda: pytest.fail("unknown old boot recovery"))
    monkeypatch.setattr(item, "shutdown_phase", lambda: "stopped")

    def wait() -> bool:
        effects.append("receipt-revalidated-pressure-wait")
        item.stopping = True
        return False

    monkeypatch.setattr(item, "wait_pressure", wait)
    assert item.run() == 0
    assert "receipt-revalidated-pressure-wait" in effects


def test_completed_audit_cleanup_is_idempotent_only_after_proven_exact_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    cid = "8" * 64
    item.state["audit_id"] = cid
    monkeypatch.setattr(
        host,
        "inspect",
        lambda value: (_ for _ in ()).throw(host.HostUnavailableError("inspect failed")),
    )
    calls = []

    def query(argv: list[str], **kwargs: Any) -> str:
        calls.append(argv)
        return ""

    monkeypatch.setattr(host, "command", query)
    item.stop_audit()
    assert item.state["audit_id"] is None
    assert calls == [["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + cid]]
    item.state["audit_id"] = cid
    monkeypatch.setattr(host, "command", lambda *args, **kw: cid)
    with pytest.raises(ValueError, match="could not be inspected"):
        item.stop_audit()
    assert item.state["audit_id"] == cid
    monkeypatch.setattr(
        host,
        "command",
        lambda *args, **kw: (_ for _ in ()).throw(host.HostUnavailableError("docker unavailable")),
    )
    with pytest.raises(host.HostUnavailableError):
        item.stop_audit()
    assert item.state["audit_id"] == cid


def test_watchdog_worker_datagrams_keep_main_process_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import struct

    address = str(tmp_path / "notify.sock")
    monkeypatch.setenv("NOTIFY_SOCKET", address)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        server.bind(address)
        server.settimeout(5)
        with host.watchdog_command(5):
            # Receive immediate main-thread pulse and then the background pulse.
            for _ in range(2):
                message, ancillary, _flags, _address = server.recvmsg(
                    128, socket.CMSG_SPACE(struct.calcsize("3i"))
                )
                pid = next(
                    struct.unpack("3i", data)[0]
                    for level, kind, data in ancillary
                    if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS
                )
                assert pid == os.getpid() and message == b"WATCHDOG=1"


@pytest.mark.parametrize(
    "phase,running", [("capturing", False), ("failed", True), ("publishing", True)]
)
def test_pressure_never_reclassifies_already_stopped_or_failed_controller(
    monkeypatch: pytest.MonkeyPatch, phase: str, running: bool
) -> None:
    item, saved, effects = pressure_fixture(monkeypatch)
    monkeypatch.setattr(item, "pressure_capture", host.Supervisor.pressure_capture.__get__(item))
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": running}})
    campaign = item.state["current"]["plan"]["campaign"]
    monkeypatch.setattr(
        host,
        "document",
        lambda *args, **kw: {
            "campaign": campaign,
            "phase": phase,
            "chunk": 0,
            "run_id": campaign + "-0000",
        },
    )
    with pytest.raises(ValueError, match=r"Stopped producer|failed or publishing"):
        item.begin_pressure_wait(pressure_error())
    assert not saved and not effects and "pressure_recovery" not in item.state


def test_pressure_wait_deadline_survives_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    deadline = item.pressure_receipt()["wait_deadline_utc_ns"]
    del item.pressure_samples
    monkeypatch.setattr(time, "time_ns", lambda: deadline)
    with pytest.raises(ValueError, match="deadline expired"):
        item.wait_pressure()
    assert effects == ["stop-owned"]


def test_wrong_operator_state_pin_cli_never_changes_state_or_stops_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, saved, effects = pressure_fixture(monkeypatch)
    item.state.update(phase="paused", reason="Capacity stop: memory-pressure")
    before = copy.deepcopy(item.state)
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(host, "load_settings", lambda path: item.settings)
    monkeypatch.setattr(host, "wait_host_ready", lambda *args: None)
    monkeypatch.setattr(host, "singleton", lambda root: contextlib.nullcontext())
    monkeypatch.setattr(host, "Supervisor", lambda cfg: item)
    with pytest.raises(ValueError, match="pin differs"):
        host.main(
            [
                "--settings",
                "/tmp/fixture.json",
                "--recover-pressure",
                "--expected-state-sha256",
                "0" * 64,
            ]
        )
    assert item.state == before and not saved and not effects


def test_read_only_pressure_preflight_restores_old_boot_identity_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, saved, effects = pressure_fixture(monkeypatch)
    item.state.update(phase="paused", reason="Capacity stop: memory-pressure")
    item.state["current"]["boot_id"] = "old-boot"
    before = copy.deepcopy(item.state)

    def rebind() -> dict[str, Any]:
        item.state["current"].update(boot_id=item.boot, collector_id="9" * 64)
        return {"State": {"Running": False, "OOMKilled": False}}

    monkeypatch.setattr(item, "pressure_rebind", rebind)
    receipt = item.begin_pressure_wait(expected_state_sha256=host.digest(before), read_only=True)
    assert receipt["operator_revalidated"] is True
    assert host.digest(item.state) == host.digest(before) and not saved and not effects


def test_stopped_baseline_refuses_active_producer_and_hard_counter_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, _effects = pressure_fixture(monkeypatch)
    item.previous = sample()
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": True}})
    with pytest.raises(ValueError, match="stopped producer"):
        item.stopped_capacity_baseline()
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "OOMKilled": False}}
    )
    current = sample(10)
    current["global_oom_kill"] = 1
    monkeypatch.setattr(cap, "snapshot", lambda: current)
    with pytest.raises(ValueError, match="Hard capacity counter"):
        item.stopped_capacity_baseline()


def test_real_pressure_wait_and_audit_admit_after_slow_stopped_docker_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, _effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    elapsed = [0]
    monkeypatch.setattr(
        time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + int(seconds))
    )
    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000_000_000 + elapsed[0] * 1_000_000_000)
    monkeypatch.setattr(cap, "snapshot", lambda: sample(elapsed[0]))
    monkeypatch.setattr(item, "audit", host.Supervisor.audit.__get__(item))
    monkeypatch.setattr(item, "stop_audit", lambda: None)
    monkeypatch.setattr(
        host, "inspect", lambda name: (_ for _ in ()).throw(host.HostUnavailableError("absent"))
    )

    def verify() -> dict[str, Any]:
        elapsed[0] += 6
        return {"State": {"Running": False, "OOMKilled": False}}

    monkeypatch.setattr(item, "verify_current", verify)

    def create(argv: list[str], **kwargs: Any) -> str:
        assert argv[:2] == ["docker", "create"]
        elapsed[0] += 6
        return "8" * 64

    monkeypatch.setattr(host, "command", create)

    def audited(argv: list[str], **kwargs: Any) -> str:
        # Execute the exact first sampling step of poll_child with the real guard.
        item.state["current"]["adopted"] = True
        try:
            host.Supervisor.tick(item)
        finally:
            item.state["current"]["adopted"] = False
        request = json.loads(kwargs["stdin"])
        return json.dumps({"campaign": request["plan"]["campaign"], "complete": False})

    monkeypatch.setattr(item, "poll_child", audited)
    for _ in range(70):
        if item.wait_pressure():
            break
    else:
        pytest.fail("Stable pressure wait never reached the real stopped-copy audit")
    assert item.state["phase"] == "between-campaigns"
    item.stopped_capacity_baseline()
    item.state["current"]["adopted"] = True
    host.Supervisor.admission(item)
    assert item.previous == sample(elapsed[0])


def test_watchdog_stops_notifying_after_deadline_while_command_body_is_still_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    elapsed = [0.0]
    notices: list[str] = []
    reached_deadline = threading.Event()
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])

    def notify(message: str) -> None:
        notices.append(message)
        if len(notices) == 2:
            elapsed[0] = 100.0
            reached_deadline.set()

    monkeypatch.setattr(host, "notify", notify)
    with host.watchdog_command(1):
        assert reached_deadline.wait(4)
        count = len(notices)
        time.sleep(1.1)
        assert len(notices) == count


def test_pressure_rebind_preserves_same_boot_container_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.state["current"]["adopted"] = False
    item.state["current"]["app_config_sha256"] = host.digest({})
    monkeypatch.setattr(item, "verify_store", lambda *args: None)
    monkeypatch.setattr(item, "app", lambda: {"config": {}})
    monkeypatch.setattr(item, "verify_current", lambda: {})
    monkeypatch.setattr(
        host,
        "inspect",
        lambda name: {
            "Id": "9" * 64 if name == host.COLLECTOR else "8" * 64,
            "State": {"Running": True},
        },
    )

    def owned(doc: dict[str, Any], generation: dict[str, Any], role: str) -> None:
        pinned = generation[role + "_id"]
        if pinned is not None and pinned != doc["Id"]:
            raise ValueError("Exact pinned CID differs")

    monkeypatch.setattr(host, "owned", owned)
    with pytest.raises(ValueError, match="pinned CID"):
        item.pressure_rebind()
    assert item.state["current"]["collector_id"] == "d" * 64
    item.state["current"]["boot_id"] = "previous-boot"
    item.pressure_rebind()
    assert item.state["current"]["collector_id"] == "9" * 64
    assert item.state["current"]["boot_id"] == item.boot


def test_pressure_wait_rechecks_release_store_and_clock_without_renewing_old_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, saved, _effects = pressure_fixture(monkeypatch)
    checks = []
    monkeypatch.setattr(host, "verify_release", lambda config: checks.append("source-image"))
    monkeypatch.setattr(item, "verify_store", lambda role, path: checks.append(role))
    monkeypatch.setattr(host, "command", lambda *args, **kw: "tracking")
    monkeypatch.setattr(
        host,
        "parse_tracking",
        lambda *args, **kw: {
            "synchronized": True,
            "system_offset_seconds": 0.0,
            "root_dispersion_seconds": 0.01,
            "root_delay_seconds": 0.01,
        },
    )
    host.Supervisor.pressure_wait_guards(item)
    assert checks == ["source-image", *item.state["current"]["roots"]]
    assert not saved
    monkeypatch.setattr(host, "parse_tracking", lambda *args, **kw: {"synchronized": False})
    with pytest.raises(ValueError, match="clock out of bounds"):
        host.Supervisor.pressure_wait_guards(item)


@pytest.mark.parametrize(
    "counter", ["global_oom_kill", "memory_throttle_count", "evict_not_enough"]
)
@pytest.mark.parametrize("same_boot_restart", [False, True])
def test_pressure_stop_never_discards_cumulative_hard_counters(
    monkeypatch: pytest.MonkeyPatch, counter: str, same_boot_restart: bool
) -> None:
    item, _saved, effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    if same_boot_restart:
        del item.pressure_samples
        monkeypatch.setattr(
            item, "pressure_rebind", lambda: {"State": {"Running": False, "OOMKilled": False}}
        )
    current = sample(1000)
    if counter == "global_oom_kill":
        current[counter] += 1
    else:
        current["arc"][counter] += 1
    monkeypatch.setattr(cap, "snapshot", lambda: current)
    with pytest.raises(ValueError, match="Hard capacity counter changed across pressure stop"):
        item.wait_pressure()
    assert item.state["phase"] == "pressure-wait"
    assert "audit" not in effects and "classified-host-pressure-interruption" not in effects


def test_pressure_counter_epoch_rebases_only_after_verified_changed_host_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, _effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    current = sample(100)
    current["global_oom_kill"] = 10
    current["arc"].update(memory_throttle_count=20, evict_not_enough=30)
    item.boot = "new-actual-boot"
    with pytest.raises(ValueError, match="verified actual host boot change"):
        item.pressure_counters(current)
    item.state["current"]["boot_id"] = item.boot
    with pytest.raises(ValueError, match="verified actual host boot change"):
        item.pressure_counters(current)
    item.pressure_verified_boot = item.boot  # Set only by successful full pressure_rebind.
    item.pressure_counters(current)
    epoch = copy.deepcopy(item.state["pressure_recovery"]["counter_epoch"])
    assert epoch["previous_boot_id"] == "fixture-boot"
    assert epoch["reason"] == "verified-host-boot-change" and epoch["sample"] == current
    del item.pressure_verified_boot  # A same-boot process restart must keep this epoch.
    item.pressure_counters(copy.deepcopy(current))
    current["arc"]["evict_not_enough"] += 1
    with pytest.raises(ValueError, match="Hard capacity counter changed across pressure stop"):
        item.pressure_counters(current)
    assert item.state["pressure_recovery"]["counter_epoch"] == epoch


def test_legacy_operator_retains_fresh_pre_stop_counter_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, _effects = pressure_fixture(monkeypatch)
    item.state.update(phase="paused", reason="Capacity stop: memory-pressure")
    monkeypatch.setattr(
        item, "pressure_rebind", lambda: {"State": {"Running": False, "OOMKilled": False}}
    )
    item.begin_pressure_wait(expected_state_sha256=host.digest(item.state))
    assert item.pressure_receipt()["current"] is None
    assert item.pressure_receipt()["operator_capacity_baseline"] == sample()
    current = sample(100)
    current["global_oom_kill"] = 1
    with pytest.raises(ValueError, match="Hard capacity counter changed across pressure stop"):
        item.pressure_counters(current)


def test_changed_boot_counter_epoch_is_retained_before_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, _saved, _effects = pressure_fixture(monkeypatch)
    item.begin_pressure_wait(pressure_error())
    del item.pressure_samples
    item.boot = "new-actual-boot"
    current = sample(100)
    current["global_oom_kill"] = 10
    monkeypatch.setattr(cap, "snapshot", lambda: copy.deepcopy(current))

    def rebind() -> dict[str, Any]:
        item.state["current"]["boot_id"] = item.boot
        item.pressure_verified_boot = item.boot
        return {"State": {"Running": False, "OOMKilled": False}}

    def stop() -> None:
        assert item.state["pressure_recovery"]["counter_epoch"]["sample"]["global_oom_kill"] == 10
        current["global_oom_kill"] = 11

    monkeypatch.setattr(item, "pressure_rebind", rebind)
    monkeypatch.setattr(item, "stop_owned", stop)
    with pytest.raises(ValueError, match="Hard capacity counter changed across pressure stop"):
        item.wait_pressure()
