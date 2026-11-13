"""Offline additive FAST identity, admission and lifecycle boundaries."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts.collector_network_gate import validate_container
from scripts.truenas_collector_bootstrap import start_argv

from hyperliquid_bot import collector_continuous as host
from hyperliquid_bot import collector_runtime as runtime
from hyperliquid_bot import collector_status as status
from hyperliquid_bot.capture_live_status import write_capture_live
from hyperliquid_bot.collector_instance import FAST, STANDARD, instance
from hyperliquid_bot.collector_native_storage import CONTAINER_ROOTS
from hyperliquid_bot.collector_storage import exclusive_json, publish_closed_chunk
from test_collector_continuous import generation, managed_fixture, settings, supervisor
from test_collector_runtime import clock_document, network_document


def fast_settings(*, pilot: bool = False) -> host.Settings:
    root = Path("/mnt/fastdisk/hyperliquid-runtime")
    return replace(
        settings(),
        instance=FAST.name,
        runtime_root=root,
        state_root=root / "continuous-control",
        release=root / "release",
        bind_ip="192.168.1.234",
        first_create=pilot,
        chunk_seconds=600 if pilot else 7200,
        chunks=1,
    )


def fast_generation(*, pilot: bool = False) -> dict[str, Any]:
    config = fast_settings(pilot=pilot)
    campaign = "hl-fast-fixture"
    return generation() | {
        "plan": asdict(config.plan(campaign)),
        "adopted": False,
        "roots": {k: str(v) for k, v in config.roots(campaign).items()},
    }


def fast_container(gen: dict[str, Any], role: str) -> dict[str, Any]:
    doc = managed_fixture(gen, role)
    doc["Name"] = "/" + FAST.container(role)
    doc["Config"]["Labels"]["com.docker.compose.project"] = FAST.project
    doc["State"]["Pid"] = 123
    if role == "status":
        doc["HostConfig"]["PortBindings"] = {
            "8080/tcp": [{"HostIp": "192.168.1.234", "HostPort": "18791"}]
        }
    doc["HostConfig"]["Tmpfs"] = {"/tmp": "rw,noexec,nosuid,size=536870912,mode=1777"}
    return doc


def rendered(config: host.Settings, plan: runtime.RuntimePlan) -> dict[str, Any]:
    pilot = replace(plan, chunk_seconds=600, chunks=1, pilot=True, prune_spool=False)
    return {
        "name": FAST.project,
        "services": {
            role: {
                **(
                    {
                        "ports": [
                            {
                                "host_ip": config.bind_ip,
                                "published": "18791",
                                "target": 8080,
                                "protocol": "tcp",
                            }
                        ]
                    }
                    if role == "status"
                    else {}
                ),
                "image": config.image,
                "command": host.process_contract(pilot, role)[1],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(config.roots(plan.campaign)[key]),
                        "target": target,
                        "read_only": role == "status" or key == "control",
                        "bind": {"create_host_path": False},
                    }
                    for key, target in CONTAINER_ROOTS.items()
                ],
            }
            for role in ("collector", "status")
        },
        "x-portals": [{"host": config.bind_ip, "port": 18791}],
    }


def pilot_closure(campaign: str = "hl-fast-fixture") -> dict[str, Any]:
    return {
        "schema": "collector-continuous-closure-v1",
        "campaign": campaign,
        "complete": True,
        "research_ready": False,
        "accepted": [
            {
                "lane": "hyperliquid",
                "run_id": campaign + "-0000",
                "copies": 2,
                "work_manifest_sha256": "1" * 64,
                "archive_manifest_sha256": "2" * 64,
            }
        ],
    }


def install_fast_acceptance(
    item: host.Supervisor, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    pilot = item.owned_fast_pilot(fast_generation(pilot=True))
    receipt = {
        "schema": "collector-fast-pilot-acceptance-v2",
        "instance": FAST.name,
        "pilot": pilot,
        "closure": pilot_closure(),
        "closure_sha256": host.digest(pilot_closure()),
        "expected_state_sha256": "3" * 64,
        "accepted_utc_ns": 123,
        "starts_capture": False,
    }
    item.state.update(fast_pilot=pilot, fast_pilot_acceptance=copy.deepcopy(receipt))
    if not item.state["current"]["plan"]["pilot"]:
        item.state["current"]["fast_pilot_acceptance_sha256"] = host.digest(receipt)
    documents = {
        "hl-fast-fixture-acceptance.json": receipt,
        "hl-fast-fixture-closure.json": pilot_closure(),
    }

    def document(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path.name not in documents:
            raise FileNotFoundError(path)
        return copy.deepcopy(documents[path.name])

    monkeypatch.setattr(host, "document", document)
    return documents


def test_default_commands_and_instance_selection_remain_fixed() -> None:
    config = settings()
    plan = config.plan("fixture")
    assert plan.lanes == tuple(runtime.LANES) and plan.instance == STANDARD.name
    assert not plan.l2_fast and not config.first_create
    assert "--l2-fast" not in host.process_contract(plan, "collector")[1]
    assert "--instance" not in host.process_contract(plan, "status")[1]
    assert instance() == STANDARD
    for name in ("custom", "hyperliquid-collector", None, True):
        with pytest.raises(ValueError):
            instance(name)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"l2_fast": 1},
        {"instance": "standard"},
        {"lanes": ("hyperliquid", "binance")},
        {"campaign": "hl-cont-foreign"},
        {"storage_profile": "vm-nfs"},
    ],
)
def test_fast_plan_refuses_mixed_or_implicit_identity(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(fast_settings().plan("hl-fast-fixture"), **changes)


def test_fast_process_render_and_bootstrap_use_separate_fixed_identity() -> None:
    cfg = fast_settings()
    plan = cfg.plan("hl-fast-fixture")
    assert plan.lanes == ("hyperliquid",) and plan.l2_fast
    command = runtime.native_command("hyperliquid", Path("/fixture"), "run", 600, l2_fast=True)
    assert command[-1] == "--l2-fast"
    value = host.normalize_compose(rendered(cfg, plan), cfg, plan)
    assert value["services"]["collector"]["command"] == host.process_contract(plan, "collector")[1]
    assert value["services"]["status"]["command"][-2:] == ["--instance", FAST.name]
    argv = start_argv(
        cfg.release, cfg.state_root / "settings.json", cfg.state_root, instance_name=FAST.name
    )
    assert "--unit=" + FAST.unit in argv
    assert not any(STANDARD.unit in item for item in argv)
    assert any("--stop-owned" in item for item in argv)
    with pytest.raises(ValueError):
        replace(cfg, runtime_root=settings().runtime_root, state_root=settings().state_root)
    wrong = rendered(cfg, plan)
    wrong["x-portals"][0]["port"] = 18790
    with pytest.raises(ValueError, match="portal"):
        host.normalize_compose(wrong, cfg, plan)


@pytest.mark.parametrize("role", ["collector", "status"])
def test_cross_instance_container_and_cleanup_refused(role: str) -> None:
    gen = fast_generation()
    doc = fast_container(gen, role)
    host.owned(doc, gen, role)
    host.owned(doc, gen, role, cleanup=True)
    for field in ("Name", "project", "cid", "command", "port"):
        other = copy.deepcopy(doc)
        if field == "Name":
            other["Name"] = "/" + STANDARD.container(role)
        elif field == "project":
            other["Config"]["Labels"]["com.docker.compose.project"] = STANDARD.project
        elif field == "cid":
            other["Id"] = "f" * 64
        elif field == "port":
            if role != "status":
                continue
            other["HostConfig"]["PortBindings"] = {
                "8080/tcp": [{"HostIp": "192.168.1.234", "HostPort": "18790"}]
            }
        else:
            other["Config"]["Cmd"] = host.process_contract(settings().plan("fixture"), role)[1]
        with pytest.raises(ValueError):
            host.owned(other, gen, role, cleanup=True)


def test_fast_network_admission_requires_own_container_mode_and_roots() -> None:
    gen = fast_generation()
    doc = fast_container(gen, "collector")
    roots = {key: Path(value) for key, value in gen["roots"].items()}
    kwargs = dict(
        image=fast_settings().image,
        source=gen["plan"]["source_commit"],
        profile="truenas-zfs",
        host_roots=roots,
        container=FAST.container("collector"),
        campaign=gen["plan"]["campaign"],
        instance_name=FAST.name,
    )
    assert validate_container(doc, **kwargs) == 123
    for change in ({"instance_name": "standard"}, {"container": STANDARD.container("collector")}):
        with pytest.raises(ValueError):
            validate_container(doc, **(kwargs | change))
    doc["Config"]["Cmd"].remove("--l2-fast")
    with pytest.raises(ValueError):
        validate_container(doc, **kwargs)


def test_fast_network_marker_cannot_reuse_main_or_wrong_admission(tmp_path: Path) -> None:
    plan = fast_settings().plan("hl-fast-fixture")
    marker = network_document() | {"campaign": plan.campaign, "instance": FAST.name}
    path = tmp_path / "network.json"
    exclusive_json(path, marker)
    runtime.require_network_marker(path, plan)
    for key, value in (
        ("instance", "standard"),
        ("campaign", "hl-cont-foreign"),
        ("container_netns_inode", 0),
    ):
        path.write_text(json.dumps(marker | {key: value}))
        with pytest.raises(ValueError):
            runtime.require_network_marker(path, plan)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        runtime.require_network_marker(path, plan)


def test_first_create_absence_probe_is_readonly_and_refuses_existing_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings(pilot=True)
    item.state["current"] = None
    calls: list[list[str]] = []

    def command(argv: list[str], **kwargs: Any) -> str:
        calls.append(argv)
        return "[]" if "app.query" in argv else ""

    monkeypatch.setattr(host, "command", command)
    item.verify_first_create()
    assert all("app.create" not in argv and "app.update" not in argv for argv in calls)
    assert all(STANDARD.app not in json.dumps(argv) for argv in calls)
    monkeypatch.setattr(host, "command", lambda *a, **k: '[{"id":"hyperliquid-fast-collector"}]')
    with pytest.raises(ValueError, match="already exists"):
        item.verify_first_create()
    item.settings = fast_settings()
    with pytest.raises(ValueError, match="create-only"):
        item.verify_first_create()


@pytest.mark.parametrize("successor", [False, True])
def test_fast_launch_separates_first_pilot_and_receipt_bound_successor(
    monkeypatch: pytest.MonkeyPatch,
    successor: bool,
) -> None:
    item = supervisor(monkeypatch)
    cfg = item.settings = fast_settings(pilot=not successor)
    item.state["current"] = fast_generation(pilot=True) if successor else None
    if successor:
        install_fast_acceptance(item, monkeypatch)
        monkeypatch.setattr(
            item, "verify_current", lambda: {"State": {"Running": False, "ExitCode": 0}}
        )
    effects: list[str] = []
    actual: dict[str, Any] = {}
    monkeypatch.setattr(item, "verify_store", lambda *a: None)
    monkeypatch.setattr(host, "directory", lambda *a, **k: None)
    monkeypatch.setattr(item, "clock", lambda *a: effects.append("clock"))
    monkeypatch.setattr(item, "admission", lambda: effects.append("capacity"))
    monkeypatch.setattr(item, "stopped_capacity_baseline", lambda: None)
    monkeypatch.setattr(item, "tick", lambda: None)
    monkeypatch.setattr(host, "publish", lambda *a, **k: effects.append("intent"))
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(Path, "exists", lambda *a: False)
    monkeypatch.setattr(os, "chown", lambda *a: None)
    monkeypatch.setattr(
        os, "statvfs", lambda *a: type("Space", (), {"f_bavail": 20, "f_frsize": 1024**3})()
    )

    def command(argv: list[str], **kwargs: Any) -> str:
        if "app.query" in argv:
            return "[]"
        if "ps" in argv:
            return ""
        assert "compose" in argv
        plan = cfg.plan(kwargs["extra"]["COLLECTOR_CAMPAIGN"])
        return json.dumps(rendered(cfg, plan))

    monkeypatch.setattr(host, "command", command)

    def child(argv: list[str], **kwargs: Any) -> str:
        assert STANDARD.app not in argv
        if "app.create" in argv or "app.update" in argv:
            assert ("app.update" in argv) is successor
            effects.append("update" if successor else "create")
            body = json.loads(kwargs["stdin"])
            if successor:
                assert json.dumps(FAST.app) in argv
            else:
                assert body["app_name"] == FAST.app and body["custom_app"] is True
            actual.update(body["custom_compose_config"])
            return "42"
        assert "--instance" in argv and FAST.name in argv
        effects.append("network-gate")
        return ""

    monkeypatch.setattr(item, "poll_child", child)

    def inspect(name: str) -> dict[str, Any]:
        role = "collector" if name == FAST.container("collector") else "status"
        assert name == FAST.container(role)
        gen = copy.deepcopy(item.state["current"])
        gen.update(collector_id="d" * 64, status_id="e" * 64)
        return fast_container(gen, role)

    monkeypatch.setattr(host, "inspect", inspect)
    monkeypatch.setattr(item, "app", lambda: {"config": actual})
    item.launch()
    action = "update" if successor else "create"
    assert effects.index("clock") < effects.index("capacity") < effects.index(action)
    assert effects.index(action) < effects.index("network-gate")
    assert item.state["phase"] == "capturing"
    assert item.state["current"]["plan"]["pilot"] is not successor
    if successor:
        assert item.state["current"]["fast_pilot_acceptance_sha256"] == host.digest(
            item.state["fast_pilot_acceptance"]
        )
    assert item.state["fast_pilot"]["plan"]["pilot"] is True
    assert item.state["current"]["plan"]["lanes"] == ("hyperliquid",)


def test_fast_readonly_status_refuses_foreign_metadata_and_projects_ack(tmp_path: Path) -> None:
    roots = status.StatusRoots(
        *(tmp_path / name for name in ("spool", "work", "archive", "control")), instance=FAST.name
    )
    for path in (roots.spool, roots.capture, roots.archive, roots.control):
        path.mkdir(mode=0o700)
    now = time.time_ns()
    plan = fast_settings(pilot=True).plan("hl-fast-fixture")
    controller = {
        "schema": "public-collector-runtime-health-v1",
        "phase": "capturing",
        "campaign": plan.campaign,
        "run_id": plan.campaign + "-0000",
        "chunk": 0,
        "source_commit": plan.source_commit,
        "image_digest": plan.image_digest,
        "lanes": ["hyperliquid"],
        "storage_profile": "truenas-zfs",
        "updated_utc_ns": now,
        "instance": FAST.name,
        "l2_mode": "fast-5",
    }
    health = roots.spool / "runtime-health.json"
    exclusive_json(health, controller)
    exclusive_json(roots.control / "clock.json", clock_document(now))
    live = (
        roots.spool
        / plan.campaign
        / "chunk-0000"
        / "hyperliquid"
        / runtime.LANES["hyperliquid"][1]
        / (plan.campaign + "-0000")
        / "capture-live.json"
    )
    source = {
        "mode": "fast-5",
        "l2_session_ack": True,
        "transport_config_version": "hyperliquid-fast-bounded-v1",
        "max_message_bytes": 1048576,
        "max_queue_frames": 16,
        "gap_count": 2,
        "reconnect_count": 1,
        "integrity_error_count": 0,
        "connection_state": "connected",
    }
    write_capture_live(
        live,
        run_id=plan.campaign + "-0000",
        writer_pending_records=7,
        writer_published_parts=3,
        definitive_outage=None,
        source_status=source,
        feeds=[
            {
                "name": name,
                "role": "required",
                "state": "fresh",
                "last_market_utc": datetime.fromtimestamp(now / 1e9, UTC).isoformat(),
                "silence_bound_seconds": 90.0,
            }
            for name in status.REQUIRED_FEEDS["hyperliquid"]
        ],
    )
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = status.snapshot(roots, now_ns=now + 10**9)
    assert result["lanes"][0]["source"]["l2_session_ack"] is True
    assert result["lanes"][0]["source"]["gap_count"] == 2
    assert result["research_ready"] is False
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    health.write_text(json.dumps(controller | {"instance": "standard"}))
    assert status.snapshot(roots, now_ns=now)["controller"] is None


@pytest.mark.parametrize("failure", [None, "claim", "integrity", "archive"])
def test_fast_publication_checks_source_and_retains_partial_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    from hyperliquid_bot.hyperliquid_raw_research import data1a_capture_claim
    from hyperliquid_bot.hyperliquid_retained_instruments import build_hyperliquid_retained_plan
    from hyperliquid_bot.reconstructable_paths import data1a_run_paths
    from test_collector_runtime import FakeChild, prepare_controller

    roots = prepare_controller(tmp_path, monkeypatch)
    plan = fast_settings(pilot=True).plan("hl-fast-fixture")
    marker = network_document() | {
        "campaign": plan.campaign,
        "instance": FAST.name,
        "storage_profile": "truenas-zfs",
    }
    roots["network_marker"].write_text(json.dumps(marker))
    # Storage inode attestation has separate real native fixtures. This local
    # controller test exercises source, prelaunch and both publication boundaries.
    monkeypatch.setattr(runtime, "require_native_storage", lambda *a, **k: {})
    monkeypatch.setattr(
        "hyperliquid_bot.capture_live_status.process_code_version", lambda: plan.source_commit
    )

    def start(argv: list[str], **kwargs: Any) -> FakeChild:
        assert argv[-1] == "--l2-fast"
        artifact = Path(argv[argv.index("--artifact-root") + 1])
        run_id = argv[argv.index("--run-id") + 1]
        paths = data1a_run_paths(artifact, run_id)
        source = artifact / runtime.LANES["hyperliquid"][1] / run_id
        source.mkdir(parents=True, mode=0o700)
        (source / "raw").mkdir(mode=0o700)
        (source / "raw/part.parquet").write_bytes(b"synthetic")
        claim = data1a_capture_claim(
            run_id=run_id,
            duration_seconds=600,
            paths=paths,
            instrument_plan=build_hyperliquid_retained_plan(l2_fast=True),
        )
        claim["code_version"] = plan.source_commit
        if failure == "claim":
            claim["transport_max_queue_frames"] = 1024
        exclusive_json(source / "capture-claim.json", claim)
        exclusive_json(
            source / "capture-health.json",
            {
                "status": "COMPLETED",
                "credentialless": True,
                "run_id": run_id,
                "duration_seconds": 600,
                "integrity_events": 1 if failure == "integrity" else 0,
            },
        )
        return FakeChild(0)

    monkeypatch.setattr(subprocess, "Popen", start)
    original = publish_closed_chunk

    def publish(source: Path, destination: Path, **kwargs: Any) -> dict[str, object]:
        if failure == "archive" and destination.is_relative_to(roots["archive"]):
            destination.mkdir(mode=0o700)
            raise OSError("synthetic archive interruption")
        return original(source, destination, **kwargs)

    monkeypatch.setattr(runtime, "publish_closed_chunk", publish)
    if failure:
        with pytest.raises((ValueError, OSError)):
            runtime.collect_campaign(plan, **roots)
        assert list(roots["spool"].rglob("capture-claim.json"))
        assert not list(roots["archive"].rglob("COMPLETE.json"))
        assert len(list(roots["capture"].rglob("COMPLETE.json"))) == (
            1 if failure == "archive" else 0
        )
    else:
        assert runtime.collect_campaign(plan, **roots)["phase"] == "completed"
        for root in (roots["capture"], roots["archive"]):
            target = root / plan.campaign / "hyperliquid" / (plan.campaign + "-0000")
            manifest = json.loads((target / "publication-manifest.json").read_text())
            assert manifest["provenance"]["instance"] == FAST.name
            assert manifest["provenance"]["l2_mode"] == "fast-5"
            assert (target / "COMPLETE.json").exists()
            assert (target / "capture-claim.json").exists()


def test_fast_restart_audits_only_fast_and_preserves_old_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    gen = item.state["current"] = fast_generation()
    gen["boot_id"] = "old-boot"
    original_roots = copy.deepcopy(gen["roots"])
    config = {"fixture": "fast"}
    gen["app_config_sha256"] = host.digest(config)
    install_fast_acceptance(item, monkeypatch)
    monkeypatch.setattr(item, "app", lambda: {"config": config})
    monkeypatch.setattr(item, "verify_store", lambda *a: None)
    monkeypatch.setattr(item, "check_interrupted_metadata", lambda *a: None)
    monkeypatch.setattr(item, "verify_current", lambda: {"State": {"Running": True}})

    def inspect(name: str) -> dict[str, Any]:
        role = "collector" if name == FAST.container("collector") else "status"
        assert name == FAST.container(role)
        return fast_container(gen, role)

    monkeypatch.setattr(host, "inspect", inspect)
    effects: list[str] = []

    def command(argv: list[str], **kwargs: Any) -> str:
        effects.append(argv[-1])
        return ""

    monkeypatch.setattr(host, "command", command)
    monkeypatch.setattr(item, "audit", lambda **k: effects.append("two-copy-audit"))
    monkeypatch.setattr(item, "gap", lambda reason: effects.append(reason))
    monkeypatch.setattr(host, "publish", lambda *a, **k: None)
    monkeypatch.setattr(Path, "exists", lambda *a: False)
    item.recover_boot()
    assert effects == [gen["collector_id"], "two-copy-audit", "host-reboot-interruption"]
    assert gen["roots"] == original_roots
    assert item.state["phase"] == "between-campaigns"


def test_fast_first_pilot_requires_acceptance_before_another_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings(pilot=True)
    item.state["current"] = fast_generation(pilot=True)
    monkeypatch.setattr(item, "admission", lambda: None)
    monkeypatch.setattr(item, "observe", lambda: True)
    monkeypatch.setattr(item, "launch", lambda: pytest.fail("Pilot self-promoted"))
    monkeypatch.setattr(host, "notify", lambda *a: None)
    assert item.run() == 0
    assert item.state["phase"] == "paused"


@pytest.mark.parametrize("failure", [None, "state-pin", "audit", "foreign"])
def test_explicit_fast_pilot_acceptance_reaudits_and_never_starts_capture(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    item.state["current"] = fast_generation(pilot=True)
    item.state.update(phase="paused", reason="FAST pilot closed; independent acceptance required.")
    if failure == "foreign":
        item.state["current"] = generation()
    expected = host.digest(item.state)
    effects: list[str] = []
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "ExitCode": 0}}
    )
    documents = {
        "runtime-health.json": {
            "phase": "completed",
            "instance": FAST.name,
            "campaign": "hl-fast-fixture",
        },
        "hl-fast-fixture-closure.json": pilot_closure(),
    }

    def document(path: Path, **kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(documents[path.name])

    def publish(path: Path, value: Any) -> None:
        effects.append("acceptance-receipt")
        documents[path.name] = copy.deepcopy(value)

    monkeypatch.setattr(host, "document", document)
    monkeypatch.setattr(Path, "exists", lambda path: path.name in documents)

    def audit(**kwargs: Any) -> None:
        effects.append("both-copy-audit")
        if failure == "audit":
            raise ValueError("synthetic bad seal")

    monkeypatch.setattr(item, "audit", audit)
    monkeypatch.setattr(host, "publish", publish)
    monkeypatch.setattr(item, "launch", lambda: pytest.fail("Acceptance started capture"))
    if failure:
        with pytest.raises(ValueError):
            item.accept_fast_pilot("0" * 64 if failure == "state-pin" else expected)
        assert item.state["phase"] == "paused"
        assert "acceptance-receipt" not in effects
    else:
        item.accept_fast_pilot(expected)
        assert effects == ["both-copy-audit", "acceptance-receipt"]
        assert item.state["phase"] == "between-campaigns"
        assert item.state["fast_pilot_acceptance"]["starts_capture"] is False


def test_fast_adoption_refuses_main_before_touching_main_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = replace(fast_settings(), adoption=generation())
    item.state["current"] = None
    monkeypatch.setattr(host, "directory", lambda *a, **k: pytest.fail("Main path touched"))
    monkeypatch.setattr(host, "inspect", lambda *a: pytest.fail("Main process touched"))
    with pytest.raises(ValueError, match="Cross-instance adoption"):
        item.adopt(read_only=True)


@pytest.mark.parametrize("foreign", ["plan", "state"])
def test_fast_state_load_refuses_main_identity(
    monkeypatch: pytest.MonkeyPatch, foreign: str
) -> None:
    state = {
        "schema": "collector-continuous-state-v1",
        "instance": FAST.name,
        "current": fast_generation() if foreign == "state" else generation(),
    }
    if foreign == "state":
        state["instance"] = "standard"
    monkeypatch.setattr(Path, "exists", lambda *a: True)
    monkeypatch.setattr(host, "document", lambda *a, **k: state)
    with pytest.raises(ValueError, match="Cross-instance state"):
        host.Supervisor(fast_settings())


@pytest.mark.parametrize("phase", ["auditing", "between-campaigns", "retry-wait", "pressure-wait"])
@pytest.mark.parametrize("changed_boot", [False, True])
def test_mutable_settings_never_promote_an_unaccepted_persisted_pilot(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    changed_boot: bool,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    item.state.update(current=fast_generation(pilot=True), phase=phase)
    if changed_boot:
        item.state["current"]["boot_id"] = "old-boot"
    effects: list[str] = []
    monkeypatch.setattr(item, "launch", lambda: pytest.fail("Unaccepted FAST launched"))
    monkeypatch.setattr(item, "admission", lambda: pytest.fail("Unaccepted recovery advanced"))
    monkeypatch.setattr(item, "stop_owned", lambda: effects.append("owned-cleanup"))
    assert item.run() == 2
    assert item.state["phase"] == "paused"
    assert effects == ["owned-cleanup"]


@pytest.mark.parametrize("operation", ["launch", "recover_boot", "pressure_rebind", "adopt"])
def test_fast_mutation_paths_require_durable_acceptance_before_external_effects(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    gen = fast_generation()
    gen["boot_id"] = "old-boot"
    item.state["current"] = gen
    if operation == "adopt":
        item.settings = replace(item.settings, adoption=gen)
        item.state["current"] = None
    monkeypatch.setattr(item, "verify_current", lambda: pytest.fail("External identity queried"))
    monkeypatch.setattr(host, "directory", lambda *a, **k: pytest.fail("External path touched"))
    with pytest.raises(ValueError, match=r"acceptance|adoption refused"):
        getattr(item, operation)()


@pytest.mark.parametrize(
    "corruption",
    [
        "marker-missing",
        "receipt-missing",
        "receipt-differs",
        "source",
        "image",
        "process",
        "config",
        "publication",
        "pilot-campaign",
        "successor",
    ],
)
def test_fast_acceptance_requires_exact_durable_owned_identity_and_publication_proof(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    item.state["current"] = fast_generation()
    documents = install_fast_acceptance(item, monkeypatch)
    item.require_fast_acceptance()
    marker = item.state["fast_pilot_acceptance"]
    if corruption == "marker-missing":
        item.state.pop("fast_pilot_acceptance")
    elif corruption == "receipt-missing":
        documents.pop("hl-fast-fixture-acceptance.json")
    elif corruption == "receipt-differs":
        documents["hl-fast-fixture-acceptance.json"]["accepted_utc_ns"] += 1
    elif corruption == "source":
        item.state["fast_pilot"]["plan"]["source_commit"] = "9" * 40
    elif corruption == "image":
        item.state["fast_pilot"]["image_id"] = "sha256:" + "9" * 64
    elif corruption == "process":
        item.state["fast_pilot"]["collector_id"] = "9" * 64
    elif corruption == "config":
        item.state["fast_pilot"]["app_config_sha256"] = "9" * 64
    elif corruption == "publication":
        marker["closure"]["accepted"][0]["archive_manifest_sha256"] = "9" * 64
    elif corruption == "pilot-campaign":
        item.state["current"] = fast_generation(pilot=True)
        item.state["current"]["plan"]["campaign"] = "hl-fast-foreign"
    else:
        item.state["current"].pop("fast_pilot_acceptance_sha256")
    with pytest.raises((ValueError, FileNotFoundError)):
        item.require_fast_acceptance()


@pytest.mark.parametrize("successor", [False, True])
def test_committed_fast_acceptance_survives_bootstrap_and_publication_reaudits(
    monkeypatch: pytest.MonkeyPatch,
    successor: bool,
) -> None:
    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    item.state.update(current=fast_generation(pilot=not successor), phase="between-campaigns")
    documents = install_fast_acceptance(item, monkeypatch)
    # A later boot audit can replace the ordinary closure projection; accepted
    # publication evidence is durably embedded in the acceptance receipt.
    documents["hl-fast-fixture-closure.json"] = pilot_closure() | {"complete": False}
    effects: list[str] = []
    monkeypatch.setattr(item, "admission", lambda: effects.append("fresh-capacity"))
    monkeypatch.setattr(host, "notify", lambda *a: None)

    def launch() -> None:
        item.require_fast_acceptance()
        effects.append("accepted-launch")
        item.stopping = True

    monkeypatch.setattr(item, "launch", launch)
    monkeypatch.setattr(item, "tick", lambda: None)
    monkeypatch.setattr(item, "shutdown_phase", lambda: "stopped")
    monkeypatch.setattr(item, "stop_owned", lambda: None)
    assert item.run() == 0
    assert effects == ["fresh-capacity", "accepted-launch"]


@pytest.mark.parametrize("crash_point", ["audit", "receipt"])
def test_real_process_death_during_acceptance_stays_paused_and_explicit_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    import sys
    import textwrap

    item = supervisor(monkeypatch)
    item.settings = fast_settings()
    item.state.update(
        current=fast_generation(pilot=True),
        phase="paused",
        reason="FAST pilot closed; independent acceptance required.",
    )
    initial = tmp_path / "initial.json"
    durable = tmp_path / "state.json"
    receipt_file = tmp_path / "receipt.json"
    initial.write_text(json.dumps(item.state))
    script = tmp_path / "kill_acceptance.py"
    script.write_text(
        textwrap.dedent("""
        import json, os, sys
        from pathlib import Path
        from unittest.mock import patch
        from hyperliquid_bot import collector_continuous as host
        from test_collector_fast_instance import fast_settings, pilot_closure
        initial, durable, receipt_file, crash_point = map(Path, sys.argv[1:])
        item = object.__new__(host.Supervisor)
        item.settings = fast_settings()
        item.state = json.loads(initial.read_text())
        def save(phase=None, reason=None):
            if phase is not None:
                item.state['phase'] = phase
            if reason is not None:
                item.state['reason'] = reason
            durable.write_text(json.dumps(item.state))
        item.save = save
        item.verify_current = lambda: {'State': {'Running': False, 'ExitCode': 0}}
        def audit(**kwargs):
            save('auditing')
            if str(crash_point) == 'audit':
                os._exit(86)
        item.audit = audit
        def document(path, **kwargs):
            if path.name == 'runtime-health.json':
                return {'phase':'completed', 'instance':'hyperliquid-fast',
                        'campaign':'hl-fast-fixture'}
            return pilot_closure()
        def publish(path, value):
            receipt_file.write_text(json.dumps(value))
            os._exit(87)
        with patch.object(Path, 'exists', return_value=False), \\
             patch.object(host, 'document', document), patch.object(host, 'publish', publish):
            item.accept_fast_pilot(host.digest(item.state))
        raise AssertionError('Acceptance did not reach the crash point')
    """)
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(script), str(initial), str(durable), str(receipt_file), crash_point],
        check=False,
        timeout=15,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join(
                [str(root), str(root / "src"), str(root / "tests/python")]
            ),
        },
    )
    assert result.returncode == (86 if crash_point == "audit" else 87), result.stderr
    item.state = json.loads(durable.read_text())
    assert item.state["phase"] == "auditing"
    assert "fast_pilot_acceptance" not in item.state
    monkeypatch.setattr(item, "launch", lambda: pytest.fail("Crash promoted pilot"))
    monkeypatch.setattr(item, "admission", lambda: pytest.fail("Crash advanced admission"))
    monkeypatch.setattr(item, "stop_owned", lambda: None)
    assert item.run() == 2
    assert item.state["phase"] == "paused"

    documents = {
        "runtime-health.json": {
            "phase": "completed",
            "instance": FAST.name,
            "campaign": "hl-fast-fixture",
        },
        "hl-fast-fixture-closure.json": pilot_closure(),
    }
    if receipt_file.exists():
        documents["hl-fast-fixture-acceptance.json"] = json.loads(receipt_file.read_text())

    def document(path: Path, **kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(documents[path.name])

    monkeypatch.setattr(host, "document", document)
    monkeypatch.setattr(Path, "exists", lambda path: path.name in documents)
    monkeypatch.setattr(
        item, "verify_current", lambda: {"State": {"Running": False, "ExitCode": 0}}
    )
    effects: list[str] = []

    def audit(**kwargs: Any) -> None:
        effects.append("complete-audit")
        item.save("auditing")

    def publish(path: Path, value: Any) -> None:
        effects.append("receipt-write")
        documents[path.name] = copy.deepcopy(value)

    monkeypatch.setattr(item, "audit", audit)
    monkeypatch.setattr(host, "publish", publish)
    item.accept_fast_pilot(host.digest(item.state))
    assert effects == (["complete-audit", "receipt-write"] if crash_point == "audit" else [])
    assert item.state["phase"] == "between-campaigns"
    item.require_fast_acceptance()
    committed = copy.deepcopy(documents["hl-fast-fixture-acceptance.json"])
    effects.clear()
    item.accept_fast_pilot(host.digest(item.state))
    assert effects == []
    assert documents["hl-fast-fixture-acceptance.json"] == committed
