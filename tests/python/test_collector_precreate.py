"""Virtual external host; real first-create launch, ownership and admission code."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hyperliquid_bot import collector_capacity as cap
from hyperliquid_bot import collector_continuous as host
from hyperliquid_bot.collector_instance import FAST
from test_collector_continuous import sample
from test_collector_fast_instance import fast_container, fast_settings, rendered


class ExternalHost:
    """Mock only processes, middleware/kernel reads and filesystem operations.

    Supervisor methods (including baseline, admission, tick, verify_current,
    source/image/store/clock and owned checks) are never replaced.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cfg = fast_settings(pilot=True)
        self.elapsed = 100
        self.created = False
        self.tc = False
        self.calls: list[list[str]] = []
        self.config: dict[str, Any] = {}
        self.files: dict[Path, bytes] = {}
        self.dirs: set[Path] = {
            self.cfg.runtime_root,
            self.cfg.state_root,
            self.cfg.capture,
            self.cfg.archive,
            self.cfg.release,
        }
        self.bad: str | None = None
        self.settings_path = self.cfg.state_root / "settings.json"
        self.files[self.settings_path] = json.dumps(
            {"schema": "collector-continuous-settings-v1", **asdict(self.cfg)}, default=str
        ).encode()
        original_exists, original_stat = Path.exists, Path.stat
        original_mkdir, original_read_text, original_read_bytes = (
            Path.mkdir,
            Path.read_text,
            Path.read_bytes,
        )
        original_iterdir, original_lstat = Path.iterdir, Path.lstat

        def virtual(path: Path) -> bool:
            return str(path).startswith("/mnt/")

        def exists(path: Path) -> bool:
            return (
                path in self.dirs or path in self.files if virtual(path) else original_exists(path)
            )

        def st(path: Path, **kwargs: Any) -> Any:
            if not virtual(path):
                return original_stat(path, **kwargs)
            if not exists(path):
                raise FileNotFoundError(path)
            return SimpleNamespace(st_ino=42, st_uid=0, st_mode=stat.S_IFDIR | 0o700)

        def mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
            if not virtual(path):
                original_mkdir(path, *args, **kwargs)
            elif exists(path):
                raise FileExistsError(path)
            else:
                assert kwargs.get("mode") == 0o700
                self.dirs.add(path)

        def read_bytes(path: Path) -> bytes:
            return self.files[path] if path in self.files else original_read_bytes(path)

        def read_text(path: Path, **kwargs: Any) -> str:
            values = {
                "/proc/sys/kernel/random/boot_id": "fixture-boot",
                "/proc/123/cgroup": "0::/fixture\n",
                "/sys/fs/cgroup/fixture/memory.events": "oom 0\noom_kill 0\n",
                "/sys/fs/cgroup/fixture/memory.current": str(128 * 1024**2),
                "/sys/fs/cgroup/fixture/memory.peak": str(256 * 1024**2),
            }
            return values[str(path)] if str(path) in values else original_read_text(path, **kwargs)

        def iterdir(path: Path) -> Any:
            if virtual(path):
                return iter(
                    sorted((p for p in self.files.keys() | self.dirs if p.parent == path), key=str)
                )
            return original_iterdir(path)

        monkeypatch.setattr(Path, "exists", exists)
        monkeypatch.setattr(Path, "stat", st)
        monkeypatch.setattr(Path, "lstat", lambda p: st(p) if virtual(p) else original_lstat(p))
        monkeypatch.setattr(Path, "mkdir", mkdir)
        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setattr(Path, "iterdir", iterdir)
        monkeypatch.setattr(host, "directory", self.directory)
        monkeypatch.setattr(host, "document", self.document)
        monkeypatch.setattr(host, "control_bytes", lambda path, **kwargs: self.files[path])
        monkeypatch.setattr(host, "publish", self.publish)
        monkeypatch.setattr(host, "filesystem_identity", self.identity)
        monkeypatch.setattr(host, "command", self.command)
        monkeypatch.setattr(host, "inspect", self.inspect)
        monkeypatch.setattr(host, "notify", lambda *a: None)
        monkeypatch.setattr(
            host,
            "time",
            SimpleNamespace(
                sleep=lambda seconds: setattr(self, "elapsed", self.elapsed + int(seconds)),
                monotonic=lambda: float(self.elapsed),
                monotonic_ns=lambda: self.elapsed * 10**9,
                time_ns=lambda: 1_800_000_000_000_000_000 + self.elapsed * 10**9,
                strftime=time.strftime,
                gmtime=time.gmtime,
            ),
        )
        original_walk = os.walk
        monkeypatch.setattr(
            os,
            "walk",
            lambda path, **kwargs: (
                iter([]) if path == self.cfg.release else original_walk(path, **kwargs)
            ),
        )
        monkeypatch.setattr(os, "chown", lambda *a: None)
        monkeypatch.setattr(
            os, "statvfs", lambda *a: SimpleNamespace(f_bavail=20, f_frsize=cap.GIB)
        )
        monkeypatch.setattr(cap, "snapshot", self.snapshot)
        monkeypatch.setattr(subprocess, "Popen", self.popen)
        self.item = host.Supervisor(self.cfg)

    def directory(self, path: Path, *, owner: int | None = None) -> None:
        if path not in self.dirs:
            raise FileNotFoundError(path)
        assert owner in (None, 1000)

    def document(self, path: Path, **kwargs: Any) -> dict[str, Any]:
        if path not in self.files:
            raise FileNotFoundError(path)
        raw = self.files[path]
        assert len(raw) <= kwargs.get("limit", 131_072)
        return json.loads(raw)  # type: ignore[no-any-return]

    def publish(self, path: Path, value: Any, **kwargs: Any) -> None:
        self.directory(path.parent)
        if kwargs.get("exclusive") and path in self.files:
            raise FileExistsError(path)
        self.files[path] = json.dumps(value, sort_keys=True).encode()

    def identity(self, path: Path) -> dict[str, Any]:
        self.directory(path)
        role = (
            "capture"
            if path == self.cfg.capture
            else "archive"
            if path == self.cfg.archive
            else "runtime"
        )
        return {
            "filesystem": "zfs",
            "source": self.cfg.storage_sources[role],
            "inode": 43 if self.bad == "store" else 42,
            "device": 1,
        }

    def snapshot(self) -> dict[str, Any]:
        result = sample(self.elapsed)
        if self.bad == "memory":
            result["direct_memavailable_bytes"] = cap.GIB
        if self.bad == "oom" and self.elapsed % 2:
            result["global_oom_kill"] = 1
        if self.bad == "psi":
            result["psi"]["some"]["avg10"] = 6
        return result

    def command(self, argv: list[str], **kwargs: Any) -> str:
        self.calls.append(argv)
        if argv[:3] == ["midclt", "call", "app.query"]:
            return json.dumps([{"id": FAST.app}] if self.created else [])
        if "app.get_instance" in argv:
            if not self.created:
                raise host.HostUnavailableError(
                    "[ENOENT] ALL: App hyperliquid-fast-collector does not exist"
                )
            return json.dumps({"id": FAST.app, "custom_app": True, "config": self.config})
        if argv[:2] == ["docker", "ps"]:
            return "d" * 64 + "\n" + "e" * 64 if self.created or self.bad == "project" else ""
        if argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": self.cfg.image_id,
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": "f" * 40
                                if self.bad == "image"
                                else self.cfg.source_commit
                            }
                        },
                    }
                ]
            )
        if argv[:2] == ["docker", "compose"]:
            plan = self.cfg.plan(kwargs["extra"]["COLLECTOR_CAMPAIGN"])
            value = rendered(self.cfg, plan)
            value.update(name=FAST.project, networks={}, **{"x-notes": "fixture"})
            return json.dumps(value)
        if argv[0] == "zfs":
            return "12345"
        if argv[0] == "chronyc":
            return (
                "System time : 0.001 seconds slow of NTP time\nRoot delay : 0.01 seconds\n"
                "Root dispersion : 0.002 seconds\nLeap status : "
                + ("Not synchronised" if self.bad == "clock" else "Normal")
                + "\n"
            )
        raise AssertionError(argv)

    def inspect(self, name: str) -> dict[str, Any]:
        assert self.created
        role = "collector" if name == FAST.container("collector") else "status"
        assert name == FAST.container(role)
        gen = copy.deepcopy(self.item.state["current"])
        gen.update(collector_id="d" * 64, status_id="e" * 64)
        return fast_container(gen, role)

    def popen(self, argv: list[str], **kwargs: Any) -> Any:
        outer = self

        class Child:
            stdin: io.StringIO
            returncode = 0

            def __init__(self) -> None:
                self.polls = 0
                child = self

                class Input(io.StringIO):
                    def close(stream) -> None:
                        body = stream.getvalue()
                        if argv[:3] == ["midclt", "call", "app.create"]:
                            assert not outer.created
                            outer.config = json.loads(body)["custom_compose_config"]
                            outer.created = True
                            kwargs["stdout"].write("42")
                        else:
                            assert "--instance" in argv and FAST.name in argv
                            assert outer.created
                            outer.tc = True
                        kwargs["stdout"].flush()
                        super().close()

                child.stdin = Input()

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

            def poll(self) -> int | None:
                self.polls += 1
                return None if self.polls == 1 else 0

            def kill(self) -> None:
                self.returncode = -9

            def wait(self, **kwargs: Any) -> int:
                return self.returncode

        return Child()

    def paused_attempt(self) -> tuple[dict[str, Any], str]:
        failed = {
            "schema": "collector-continuous-state-v1",
            "instance": FAST.name,
            "phase": "paused",
            "current": None,
            "generations": 0,
            "retry_count": 0,
            "last_gap": None,
            "reason": "Host command failed: midclt",
            "boot_id": "fixture-boot",
            "pid": 1234,
            "updated_utc_ns": 1_800_000_000_000_000_000,
        }
        self.item.state = copy.deepcopy(failed)
        self.files[self.item.path] = (json.dumps(failed, indent=2) + "\n").encode()
        campaign = "hl-fast-failed-fixture"
        roots = self.cfg.roots(campaign)
        self.dirs.update([roots["spool"], roots["control"]])
        self.files[roots["control"] / "clock.json"] = b'{"retained":"stale-not-admission"}'
        return failed, campaign


def test_full_first_create_uses_real_admission_and_ownership_without_existing_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = ExternalHost(monkeypatch)
    with pytest.raises(host.HostUnavailableError, match="ENOENT"):
        remote.item.app()
    with pytest.raises(host.HostUnavailableError, match="ENOENT"):
        remote.item.stopped_capacity_baseline()
    host.verify_release(remote.cfg)
    remote.item.launch()
    assert remote.created and remote.tc
    assert remote.item.state["phase"] == "capturing"
    proof = remote.item.state["current"]["precreate_admission"]
    assert len(proof["samples"]) == len(proof["proofs"]) == 3
    assert proof["producer_absent"] is True
    assert remote.item.state["current"]["plan"]["pilot"] is True
    remote.item.verify_current()
    assert all("app.update" not in call for call in remote.calls)


@pytest.mark.parametrize(
    "failure", ["memory", "oom", "psi", "store", "clock", "image", "collision"]
)
def test_first_create_real_guard_failures_never_create_an_app(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    remote = ExternalHost(monkeypatch)
    remote.bad = failure
    remote.created = failure == "collision"
    with pytest.raises(ValueError):
        host.verify_release(remote.cfg)
        remote.item.launch()
    assert not remote.tc
    assert remote.item.state["current"] is None


def test_explicit_precreate_revalidation_preserves_raw_failure_and_orphans_without_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = ExternalHost(monkeypatch)
    failed, campaign = remote.paused_attempt()
    original = copy.deepcopy(remote.files)
    pin = host.digest(failed)
    assert pin != hashlib.sha256(original[remote.item.path]).hexdigest()
    remote.item.revalidate_fast_precreate(
        expected_state_sha256=pin,
        expected_settings_sha256=host.digest(remote.document(remote.settings_path)),
        settings_path=remote.settings_path,
        failed_campaign=campaign,
    )
    assert not remote.created and not remote.tc
    assert remote.item.state["current"] is None and remote.item.state["generations"] == 0
    assert remote.item.state["phase"] == "adopting"
    receipt = remote.document(
        remote.cfg.state_root / remote.item.state["precreate_revalidation"]["receipt"]
    )
    assert receipt["failed_state"] == failed
    assert receipt["failed_state_json"].encode() == original[remote.item.path]
    assert receipt["starts_capture"] is False
    old_clock = remote.cfg.roots(campaign)["control"] / "clock.json"
    assert remote.files[old_clock] == original[old_clock]
    remote.item.launch()
    current: Any = remote.item.state["current"]
    assert current["plan"]["campaign"] != campaign
    assert remote.files[old_clock] == original[old_clock]


@pytest.mark.parametrize(
    "failure",
    [
        "state-pin",
        "raw-pin",
        "settings-pin",
        "generations",
        "current",
        "instance",
        "reason",
        "app",
        "project",
        "orphan-data",
        "memory",
        "clock",
        "image",
        "store",
        "receipt",
    ],
)
def test_precreate_revalidation_refuses_stale_or_foreign_failure_without_reset(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    remote = ExternalHost(monkeypatch)
    failed, campaign = remote.paused_attempt()
    if failure in {"generations", "current", "instance", "reason"}:
        failed[failure] = {
            "generations": 1,
            "current": {"collector_id": "d" * 64},
            "instance": "standard",
            "reason": "different failure",
        }[failure]
        remote.item.state = copy.deepcopy(failed)
        remote.files[remote.item.path] = json.dumps(failed).encode()
    remote.created = failure == "app"
    if failure in {"memory", "clock", "image", "store", "project"}:
        remote.bad = failure
    if failure == "orphan-data":
        remote.files[remote.cfg.roots(campaign)["spool"] / "native-raw.parquet"] = b"retain"
    state_pin = host.digest(failed)
    if failure == "state-pin":
        state_pin = "0" * 64
    elif failure == "raw-pin":
        state_pin = hashlib.sha256(remote.files[remote.item.path]).hexdigest()
    settings_pin = host.digest(remote.document(remote.settings_path))
    if failure == "settings-pin":
        settings_pin = "0" * 64
    if failure == "receipt":
        remote.files[remote.cfg.state_root / ("precreate-" + state_pin + ".json")] = (
            b'{"retain":true}'
        )
    before_files = copy.deepcopy(remote.files)
    before_state = copy.deepcopy(remote.item.state)
    before_dirs = set(remote.dirs)
    with pytest.raises(ValueError):
        remote.item.revalidate_fast_precreate(
            expected_state_sha256=state_pin,
            expected_settings_sha256=settings_pin,
            settings_path=remote.settings_path,
            failed_campaign=campaign,
        )
    assert remote.item.state == before_state
    assert remote.files == before_files
    assert remote.dirs == before_dirs
    assert not remote.tc


def test_exclusive_host_receipt_never_overwrites_existing_evidence(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / "receipt.json"
    host.publish(path, {"original": True}, owner=os.getuid(), exclusive=True)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        host.publish(path, {"changed": True}, owner=os.getuid(), exclusive=True)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_host_only_admission_retains_prior_hard_counter_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = ExternalHost(monkeypatch)
    remote.item.previous = sample(remote.elapsed - 10)
    original = remote.snapshot

    def snapshot() -> dict[str, Any]:
        value = original()
        value["global_oom_kill"] = 1
        return value

    monkeypatch.setattr(cap, "snapshot", snapshot)
    with pytest.raises(ValueError, match="Hard capacity counter"):
        remote.item.precreate_admission()
    assert not remote.created


def test_precreate_receipt_survives_failed_state_write_and_explicit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = ExternalHost(monkeypatch)
    failed, campaign = remote.paused_attempt()
    raw = remote.files[remote.item.path]
    kwargs: dict[str, Any] = {
        "expected_state_sha256": host.digest(failed),
        "expected_settings_sha256": host.digest(remote.document(remote.settings_path)),
        "settings_path": remote.settings_path,
        "failed_campaign": campaign,
    }

    def publish(path: Path, value: Any, **options: Any) -> None:
        if path == remote.item.path:
            raise OSError("synthetic state fsync failure")
        remote.publish(path, value, **options)

    monkeypatch.setattr(host, "publish", publish)
    with pytest.raises(OSError, match="fsync"):
        remote.item.revalidate_fast_precreate(**kwargs)
    assert remote.files[remote.item.path] == raw
    receipt_path = remote.cfg.state_root / ("precreate-" + host.digest(failed) + ".json")
    original_receipt = remote.files[receipt_path]
    remote.item = host.Supervisor(remote.cfg)
    assert remote.item.state["phase"] == "paused"
    monkeypatch.setattr(host, "publish", remote.publish)
    remote.item.revalidate_fast_precreate(**kwargs)
    assert remote.files[receipt_path] == original_receipt
    assert not remote.created and not remote.tc
