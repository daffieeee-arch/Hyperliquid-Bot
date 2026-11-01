"""Trusted host supervisor for rolling, finite native public-data campaigns.

Run from a frozen root-owned release on the approved Linux host, never from a
market/UI container. Every generation receives fresh roots, clock and TC proof.
The persistent state is a control record, not permission to resume raw data.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from . import collector_capacity as capacity
from .collector_clock import parse_tracking
from .collector_native_storage import CONTAINER_ROOTS, approved_host_roots, filesystem_identity
from .collector_runtime import LANES, RuntimePlan, public_environment
from .local_mode import require_local_paper_mode

APP = "hyperliquid-collector"
PROJECT = "ix-" + APP
COLLECTOR = PROJECT + "-collector-1"
STATUS = PROJECT + "-status-1"
IMAGE_REPOSITORY = "ghcr.io/daffieeee-arch/hyperliquid-bot/collector@"
ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "TZ": "UTC",
    "TRADING_MODE": "PAPER",
    "PYTHONDONTWRITEBYTECODE": "1",
}
STATES = {
    "adopting",
    "admitting",
    "capturing",
    "publishing",
    "auditing",
    "between-campaigns",
    "retry-wait",
    "paused",
    "stopped",
}
require = capacity.require


class HostUnavailableError(ValueError):
    """A read-only host command could not reach its service."""


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def directory(path: Path, *, owner: int | None = None) -> None:
    require(path.is_absolute() and ".." not in path.parts, "Absolute directory required.")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = other
        value = os.fstat(descriptor)
        if owner is not None:
            require(
                value.st_uid == owner and stat.S_IMODE(value.st_mode) == 0o700,
                "Private directory ownership/mode differs.",
            )
    finally:
        os.close(descriptor)


def document(path: Path, *, trusted: bool = False, limit: int = 131_072) -> dict[str, Any]:
    directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "Metadata file bound.")
        if trusted:
            require(before.st_uid == 0 and before.st_mode & 0o022 == 0, "Untrusted host control.")
        raw = os.read(fd, limit + 1)
        after = os.fstat(fd)
        require(
            len(raw) == before.st_size
            and len(raw) <= limit
            and (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_ino, after.st_dev, after.st_mtime_ns, after.st_ctime_ns),
            "Metadata changed while reading.",
        )
    finally:
        os.close(fd)

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        require(len(result) == len(pairs), "Duplicate JSON fields.")
        return result

    def invalid(_value: str) -> None:
        raise ValueError("Nonfinite JSON constant.")

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    require(isinstance(value, dict), "Object metadata required.")
    return dict(value)


def publish(path: Path, value: object, *, owner: int = 0) -> None:
    """Atomic bounded own-control replacement; never a market archive path."""
    directory(path.parent)
    raw = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
    require(len(raw) <= 131_072, "Control metadata bound.")
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, owner, owner)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.replace(temporary, path)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


@dataclass(frozen=True)
class Settings:
    release: Path
    state_root: Path
    runtime_root: Path
    capture: Path
    archive: Path
    bind_ip: str
    source_commit: str
    image_digest: str
    image_id: str
    capacity: capacity.CapacityPolicy
    adoption: dict[str, Any]
    storage_sources: dict[str, str]
    storage_guids: dict[str, str]
    storage_inodes: dict[str, int]
    chunk_seconds: int = 14_400
    chunks: int = 6
    max_retries: int = 5
    prune_new_spool: bool = True

    def __post_init__(self) -> None:
        require(
            ipaddress.ip_address(self.bind_ip).is_private, "Explicit private LAN address required."
        )
        require(
            self.runtime_root.name == "hyperliquid-runtime"
            and len(self.runtime_root.parts) == 4
            and self.runtime_root.parts[1] == "mnt",
            "Scoped native runtime dataset required.",
        )
        require(
            self.state_root == self.runtime_root / "continuous-control",
            "Control state must be in its own runtime child.",
        )
        require(
            type(self.max_retries) is int and 0 <= self.max_retries <= 10,
            "Bounded retry budget required.",
        )
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id) is not None,
            "Host-specific immutable engine image identity required.",
        )
        self.plan("admission-check")
        approved_host_roots(self.roots("admission-check"), campaign="admission-check")
        require(
            set(self.storage_sources)
            == set(self.storage_guids)
            == set(self.storage_inodes)
            == {"runtime", "capture", "archive"},
            "Three explicit durable store pins required.",
        )
        for role in self.storage_sources:
            require(
                re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", self.storage_sources[role])
                is not None
                and self.storage_guids[role].isdigit()
                and type(self.storage_inodes[role]) is int
                and self.storage_inodes[role] > 0,
                "Exact project dataset source/GUID/inode pins required.",
            )

    @property
    def image(self) -> str:
        return IMAGE_REPOSITORY + self.image_digest

    def roots(self, campaign: str) -> dict[str, Path]:
        return {
            "spool": self.runtime_root / ("spool-" + campaign),
            "control": self.runtime_root / ("control-" + campaign),
            "capture": self.capture,
            "archive": self.archive,
        }

    def plan(self, campaign: str) -> RuntimePlan:
        return RuntimePlan(
            campaign,
            tuple(LANES),
            self.chunk_seconds,
            self.chunks,
            False,
            self.source_commit,
            self.image_digest,
            self.prune_new_spool,
            "truenas-zfs",
        )


def load_settings(path: Path) -> Settings:
    value = document(path, trusted=True)
    require(value.pop("schema") == "collector-continuous-settings-v1", "Settings schema differs.")
    for name in ("release", "state_root", "runtime_root", "capture", "archive"):
        value[name] = Path(value[name])
    value["capacity"] = capacity.CapacityPolicy(**value["capacity"])
    return Settings(**value)


def verify_release(settings: Settings) -> None:
    """A root daemon never imports mutable development or container-written code."""
    directory(settings.release)
    for base, directories, files in os.walk(settings.release, followlinks=False):
        for name in [*directories, *files]:
            value = (Path(base) / name).lstat()
            require(
                not stat.S_ISLNK(value.st_mode)
                and value.st_uid == 0
                and value.st_mode & 0o022 == 0,
                "Frozen root-owned release required.",
            )
    root = settings.release.stat()
    require(root.st_uid == 0 and root.st_mode & 0o022 == 0, "Untrusted release root.")
    image = json.loads(command(["docker", "image", "inspect", settings.image]))[0]
    require(
        image["Id"] == settings.image_id
        and image["Os"] == "linux"
        and image["Architecture"] == "amd64"
        and image["Config"]["Labels"]["org.opencontainers.image.revision"]
        == settings.source_commit,
        "Pulled image/source/platform identity differs.",
    )


@contextlib.contextmanager
def singleton(root: Path) -> Iterator[None]:
    directory(root, owner=0)
    fd = os.open(root / "supervisor.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 0, "Untrusted supervisor lock.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.connect("\0" + address[1:] if address.startswith("@") else address)
            connection.sendall(message.encode())


def command(
    argv: list[str],
    *,
    stdin: str | None = None,
    timeout: int = 10,
    allowed: tuple[int, ...] = (0,),
    extra: dict[str, str] | None = None,
) -> str:
    response = subprocess.run(
        argv, input=stdin, text=True, capture_output=True, timeout=timeout, env=ENV | (extra or {})
    )
    if response.returncode not in allowed:
        raise HostUnavailableError("Host command failed: " + argv[0])
    require(len(response.stdout) < 2 * 1024**2, "Host command output bound.")
    return response.stdout


def inspect(name: str) -> dict[str, Any]:
    value = json.loads(command(["docker", "inspect", name]))
    require(isinstance(value, list) and len(value) == 1, "Unique managed container required.")
    return dict(value[0])


def wait_host_ready(settings: Settings, *, seconds: int = 180) -> None:
    """Cold boot may precede Docker/Apps readiness; no mutation or blind restart."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            verify_release(settings)
            command(["midclt", "call", "app.get_instance", json.dumps(APP)])
            inspect(COLLECTOR)
            status = inspect(STATUS)
            if status["State"]["Running"]:
                return
        except (HostUnavailableError, subprocess.TimeoutExpired, ConnectionError):
            pass
        require(time.monotonic() < deadline, "Cold host/App readiness budget exhausted.")
        notify("STATUS=Waiting for project App and Docker readiness")
        time.sleep(2)


def owned(
    doc: dict[str, Any], generation: dict[str, Any], role: str, *, cleanup: bool = False
) -> None:
    conf, host = doc["Config"], doc["HostConfig"]
    labels = conf["Labels"]
    memory = (2 * capacity.GIB) if role == "collector" else (256 * 1024**2)
    if cleanup:
        require(
            generation.get(role + "_id") is not None,
            "Cleanup needs a previously latched exact identity.",
        )
    value = dict(generation["plan"])
    value["lanes"] = tuple(value["lanes"])
    plan = RuntimePlan(**value)
    entrypoint, argv = process_contract(plan, role)
    environment = conf["Env"]
    require(
        isinstance(environment, list)
        and all(type(item) is str and "=" in item for item in environment),
        "Process environment schema.",
    )
    pairs = [item.split("=", 1) for item in environment]
    env = dict(pairs)
    require(
        len(env) == len(pairs)
        and set(env)
        == {
            "COLLECTOR_SOURCE_COMMIT",
            "GPG_KEY",
            "HOME",
            "PATH",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONPATH",
            "PYTHONUNBUFFERED",
            "PYTHON_SHA256",
            "PYTHON_VERSION",
            "TRADING_MODE",
            "TZ",
        }
        and env.get("TRADING_MODE") == "PAPER"
        and env.get("COLLECTOR_SOURCE_COMMIT") == plan.source_commit
        and env.get("PYTHONPATH") == "/app/src"
        and env.get("PATH") == "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
        and env.get("HOME") == "/tmp"
        and env.get("TZ") == "UTC"
        and env.get("PYTHONDONTWRITEBYTECODE") == env.get("PYTHONUNBUFFERED") == "1"
        and not set(env) & {"PYTHONHOME", "PYTHONSTARTUP", "LD_PRELOAD", "LD_LIBRARY_PATH"},
        "Public process environment differs.",
    )
    public_environment(env)
    require(
        conf["Cmd"] == argv and conf["Entrypoint"] == entrypoint,
        "Exact public collector/status process contract differs.",
    )
    require(
        doc["Name"] == "/" + (COLLECTOR if role == "collector" else STATUS)
        and labels.get("com.docker.compose.project") == PROJECT
        and labels.get("com.docker.compose.service") == role
        and labels.get("org.hyperliquid-bot.campaign") == generation["plan"]["campaign"]
        and labels.get("org.opencontainers.image.revision") == generation["plan"]["source_commit"]
        and conf["Image"] == IMAGE_REPOSITORY + generation["plan"]["image_digest"]
        and doc["Image"] == generation["image_id"]
        and (generation.get(role + "_id") is None or doc["Id"] == generation[role + "_id"]),
        "Managed container identity differs.",
    )
    require(
        cleanup
        or (
            conf["User"] == "1000:1000"
            and host["ReadonlyRootfs"] is True
            and not host["Privileged"]
            and not host["CapAdd"]
            and host["CapDrop"] == ["ALL"]
            and host["SecurityOpt"] == ["no-new-privileges:true"]
            and host["Memory"] == host["MemorySwap"] == memory
            and host["NanoCpus"] == (2_000_000_000 if role == "collector" else 250_000_000)
            and host["PidsLimit"] == (128 if role == "collector" else 64)
            and host["NetworkMode"] not in {"host", "none"}
            and not host["NetworkMode"].startswith("container:")
            and host["RestartPolicy"]["Name"] == ("no" if role == "collector" else "unless-stopped")
            and not doc["State"]["OOMKilled"]
            and doc["RestartCount"] == 0
        ),
        "Managed container isolation, OOM or restart differs.",
    )
    mounts = [item for item in doc["Mounts"] if item["Type"] == "bind"]
    require(
        len(mounts) == 4 and len({item["Destination"] for item in mounts}) == 4,
        "Exactly four unique project binds required.",
    )
    for mount in mounts:
        dest = mount["Destination"]
        require(dest in CONTAINER_ROOTS.values(), "Unexpected project mount.")
        key = next(key for key, target in CONTAINER_ROOTS.items() if target == dest)
        require(
            mount["Source"] == generation["roots"][key]
            and mount["RW"] is (role == "collector" and key != "control"),
            "Managed project bind identity or rights differ.",
        )
    require(
        all(
            item["Type"] == "bind" or (item["Type"] == "tmpfs" and item["Destination"] == "/tmp")
            for item in doc["Mounts"]
        ),
        "Unexpected mount.",
    )
    if role == "collector" and not cleanup:
        require(not host["PortBindings"], "Collector must not publish a port.")


def process_contract(plan: RuntimePlan, role: str) -> tuple[list[str], list[str]]:
    require(role in {"collector", "status"}, "Only fixed managed process roles permitted.")
    if role == "status":
        return ["python", "-B", "-m", "hyperliquid_bot.collector_status"], [
            "--spool",
            CONTAINER_ROOTS["spool"],
            "--capture",
            CONTAINER_ROOTS["capture"],
            "--archive",
            CONTAINER_ROOTS["archive"],
            "--control",
            "/control",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
        ]
    args = [
        "--storage-profile",
        "truenas-zfs",
        "--expected-source",
        plan.source_commit,
        "--expected-image",
        plan.image_digest,
        "--campaign",
        plan.campaign,
    ]
    for lane in plan.lanes:
        args += ["--lane", lane]
    args += ["--chunk-seconds", str(plan.chunk_seconds), "--chunks", str(plan.chunks)]
    if plan.pilot:
        args.append("--pilot")
    args += [
        "--spool",
        CONTAINER_ROOTS["spool"],
        "--capture",
        CONTAINER_ROOTS["capture"],
        "--archive",
        CONTAINER_ROOTS["archive"],
    ]
    if plan.prune_spool:
        args.append("--prune-spool")
    return ["python", "-B", "-m", "hyperliquid_bot.collector_runtime"], args


def normalize_compose(
    value: dict[str, Any], settings: Settings, plan: RuntimePlan
) -> dict[str, Any]:
    require(set(value["services"]) == {"collector", "status"}, "Exact App services required.")
    roots = settings.roots(plan.campaign)
    args = value["services"]["collector"]["command"]
    require(
        args.count("--pilot") == 1
        and args[args.index("--chunk-seconds") + 1] == "600"
        and args[args.index("--chunks") + 1] == "1",
        "Reviewed finite template required.",
    )
    require(
        [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--lane"]
        == ["hyperliquid", "binance"],
        "Reviewed template lanes differ.",
    )
    args.remove("--pilot")
    args[args.index("--chunk-seconds") + 1] = str(plan.chunk_seconds)
    args[args.index("--chunks") + 1] = str(plan.chunks)
    at = args.index("--chunk-seconds")
    args[at:at] = ["--lane", "kraken", "--lane", "bitvavo-standard"]
    if plan.prune_spool:
        args.append("--prune-spool")
    for role, service in value["services"].items():
        require(service["image"] == settings.image, "Rendered image differs.")
        mounts = service["volumes"]
        require(
            len(mounts) == 4 and len({item["target"] for item in mounts}) == 4,
            "Unique rendered binds required.",
        )
        for mount in mounts:
            target = mount["target"]
            require(target in CONTAINER_ROOTS.values(), "Unexpected rendered mount.")
            key = next(key for key, path in CONTAINER_ROOTS.items() if path == target)
            options = mount.get("bind")
            require(
                mount["type"] == "bind"
                and mount["source"] == str(roots[key])
                and type(mount.get("read_only", False)) is bool
                and mount.get("read_only", False) is (role == "status" or key == "control")
                and type(options) is dict
                and set(options) <= {"create_host_path"}
                and ("create_host_path" not in options or options["create_host_path"] is False),
                "Unsafe rendered bind.",
            )
            options["create_host_path"] = False
        service.setdefault("labels", {})["org.hyperliquid-bot.campaign"] = plan.campaign
    portal = value["x-portals"]
    require(
        len(portal) == 1
        and portal[0]["host"] == settings.bind_ip
        and type(portal[0]["port"]) is int
        and portal[0]["port"] == 18790,
        "Fixed TrueNAS portal required.",
    )
    return value


def retry_delay(attempt: int, maximum: int) -> int | None:
    require(type(attempt) is int and attempt >= 1, "Retry attempt required.")
    return min(900, 30 * 2 ** (attempt - 1)) if attempt <= maximum else None


def transport_failure(health: list[dict[str, Any]]) -> bool:
    """Only explicit known transport exceptions qualify for outer recovery.

    Venue collectors already reconnect internally. Unknown failures, missing
    terminal health, parser/writer errors and integrity events remain stopped.
    """
    failed = [value for value in health if value.get("status") == "FAILED"]
    return (
        bool(failed)
        and all(
            value.get("exception_class")
            in {
                "TimeoutError",
                "ConnectionError",
                "ConnectionResetError",
                "ConnectionAbortedError",
                "ConnectionClosedError",
            }
            and type(value.get("integrity_events")) is int
            and value.get("integrity_events") == 0
            and value.get("credentialless") is True
            for value in failed
        )
        and all(
            value.get("status") in {"COMPLETED", "OPERATOR_STOP", "FAILED"}
            and type(value.get("integrity_events")) is int
            and value.get("integrity_events") == 0
            for value in health
        )
    )


class Supervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.state_root / "state.json"
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.state: dict[str, Any] = (
            document(self.path, trusted=True)
            if self.path.exists()
            else {
                "schema": "collector-continuous-state-v1",
                "phase": "adopting",
                "retry_count": 0,
                "generations": 0,
                "current": None,
                "last_gap": None,
            }
        )
        require(self.state["schema"] == "collector-continuous-state-v1", "State schema differs.")
        self.stopping = False
        self.previous: dict[str, Any] | None = None
        self.streaks: dict[str, int] = {}
        self.last_clock = 0.0
        self.last_store_check = 0.0
        self.owned_child: subprocess.Popen[str] | None = None

    def verify_store(self, role: str, path: Path) -> None:
        identity = filesystem_identity(path)
        key = "runtime" if role in {"spool", "control"} else role
        require(
            identity["filesystem"] == "zfs"
            and identity["source"] == self.settings.storage_sources[key],
            "Project storage source or filesystem differs.",
        )
        guid = command(["zfs", "get", "-H", "-o", "value", "guid", str(identity["source"])]).strip()
        require(guid == self.settings.storage_guids[key], "Project dataset GUID changed.")
        if key == "runtime":
            directory(self.settings.runtime_root)
            require(
                self.settings.runtime_root.stat().st_ino == self.settings.storage_inodes[key],
                "Project runtime parent inode changed.",
            )
        else:
            require(
                identity["inode"] == self.settings.storage_inodes[key],
                "Project destination inode changed.",
            )

    def save(self, phase: str | None = None, reason: str | None = None) -> None:
        if phase is not None:
            require(phase in STATES, "Unknown supervisor phase.")
            self.state["phase"] = phase
        if reason is not None:
            self.state["reason"] = reason
        self.state.update(updated_utc_ns=time.time_ns(), boot_id=self.boot, pid=os.getpid())
        publish(self.path, self.state)
        generation = self.state["current"]
        if generation and not generation.get("adopted"):
            view = {
                "schema": "collector-continuous-status-v1",
                "intent": "continuous-public-data",
                "phase": self.state["phase"],
                "updated_utc_ns": self.state["updated_utc_ns"],
                "campaign": generation["plan"]["campaign"],
                "retry_count": self.state["retry_count"],
                "reason": self.state.get("reason"),
                "last_gap": self.state["last_gap"],
                "market_archives_automatically_deleted": False,
                "seamless_coverage_claimed": False,
                "research_ready": False,
                "supervisor_source_commit": self.settings.source_commit,
                "producer_source_commit": generation["plan"]["source_commit"],
            }
            publish(
                Path(generation["roots"]["control"]) / "continuous-status.json", view, owner=1000
            )

    def gap(self, reason: str) -> None:
        value = {
            "schema": "collector-continuous-gap-v1",
            "detected_utc_ns": time.time_ns(),
            "reason": reason,
            "previous_campaign": self.state["current"]["plan"]["campaign"],
            "end_utc_ns": None,
            "missing_events_known": False,
        }
        self.state["last_gap"] = value
        # One small immutable event per generation, never raw payload retention.
        path = self.settings.state_root / (self.state["current"]["plan"]["campaign"] + "-gap.json")
        if path.exists():
            previous = document(path, trusted=True)
            require(
                previous.get("schema") == value["schema"]
                and previous.get("reason") == reason
                and previous.get("previous_campaign") == value["previous_campaign"],
                "Existing gap record differs from this completed transition.",
            )
            self.state["last_gap"] = previous
        else:
            publish(path, value)

    def tick(self, seconds: float = 1) -> None:
        time.sleep(seconds)
        notify("WATCHDOG=1")
        require(not self.stopping, "Operator stopped continuous supervision.")
        current = capacity.snapshot()
        if self.previous is not None:
            self.streaks, reasons = capacity.evaluate(
                self.previous, current, self.streaks, self.settings.capacity
            )
            require(not reasons, "Capacity stop: " + ",".join(reasons))
        self.previous = current
        self.save()
        generation = self.state.get("current")
        if generation and not generation.get("adopted"):
            for path in (self.settings.runtime_root, self.settings.capture, self.settings.archive):
                space = os.statvfs(path)
                require(
                    space.f_bavail * space.f_frsize >= 2 * capacity.GIB,
                    "Storage reserve reached; no archive deletion is permitted.",
                )
            if generation.get("collector_id") and generation["boot_id"] == self.boot:
                doc = inspect(COLLECTOR)
                owned(doc, generation, "collector")
                if doc["State"]["Running"]:
                    pid = int(doc["State"]["Pid"])
                    records = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                    cgroup = [
                        line.removeprefix("0::") for line in records if line.startswith("0::")
                    ]
                    require(
                        len(cgroup) == 1
                        and cgroup[0].startswith("/")
                        and ".." not in Path(cgroup[0]).parts,
                        "Owned cgroup v2 identity missing.",
                    )
                    events = dict(
                        line.split()
                        for line in (
                            Path("/sys/fs/cgroup") / cgroup[0].lstrip("/") / "memory.events"
                        )
                        .read_text()
                        .splitlines()
                    )
                    require(
                        int(events["oom"]) == int(events["oom_kill"]) == 0,
                        "Owned collector cgroup OOM.",
                    )
            if time.monotonic() - self.last_store_check >= 30:
                for role in ("spool", "capture", "archive"):
                    self.verify_store(role, Path(generation["roots"][role]))
                self.last_store_check = time.monotonic()

    def admission(self) -> None:
        samples = []
        for index in range(3):
            if index:
                self.tick()
            samples.append(capacity.snapshot())
        capacity.admit(samples, self.settings.capacity)
        self.previous = samples[-1]
        self.streaks = {}

    def app(self) -> dict[str, Any]:
        value = json.loads(
            command(
                [
                    "midclt",
                    "call",
                    "app.get_instance",
                    json.dumps(APP),
                    json.dumps({"extra": {"retrieve_config": True}}),
                ]
            )
        )
        require(value["id"] == APP and value["custom_app"] is True, "Managed Custom App required.")
        return dict(value)

    def verify_current(self) -> dict[str, Any]:
        generation = self.state["current"]
        app = self.app()
        require(
            digest(app["config"]) == generation["app_config_sha256"],
            "App configuration changed outside the continuous operator.",
        )
        ids = set(
            command(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--no-trunc",
                    "--filter",
                    "label=com.docker.compose.project=" + PROJECT,
                ]
            ).split()
        )
        require(
            ids == {generation["collector_id"], generation["status_id"]},
            "Unexpected managed App container set.",
        )
        collector, status = inspect(COLLECTOR), inspect(STATUS)
        owned(collector, generation, "collector")
        owned(status, generation, "status")
        services = app["config"]["custom_compose_config"]["services"]
        require(
            collector["Config"]["Cmd"] == services["collector"]["command"]
            and status["Config"]["Cmd"] == services["status"]["command"],
            "Managed container command differs from actual admitted App config.",
        )
        require(status["State"]["Running"], "Read-only App status service unavailable.")
        return collector

    def recover_boot(self) -> None:
        """Rebind only exact managed identities after a changed host boot.

        Old raw namespaces are terminal. A TrueNAS recreate can change CIDs,
        but never grants permission to reuse their data or stale TC controls.
        """
        generation = self.state["current"]
        require(generation["boot_id"] != self.boot, "Boot recovery requires an actual new boot.")
        for role, path in generation["roots"].items():
            self.verify_store(role, Path(path))
        self.check_interrupted_metadata(generation)
        require(
            digest(self.app()["config"]) == generation["app_config_sha256"],
            "App changed across reboot; independent revalidation required.",
        )
        rebound = dict(generation) | {"collector_id": None, "status_id": None}
        collector, status = inspect(COLLECTOR), inspect(STATUS)
        owned(collector, rebound, "collector")
        owned(status, rebound, "status")
        generation["collector_id"], generation["status_id"] = collector["Id"], status["Id"]
        self.verify_current()
        self.save("auditing", "Host reboot interrupted old sessions; fresh admission required.")
        if collector["State"]["Running"]:
            command(["docker", "stop", "--time", "90", collector["Id"]], timeout=100)
        self.audit(complete=False)
        path = self.settings.state_root / (generation["plan"]["campaign"] + "-gap.json")
        if not path.exists():
            self.gap("host-reboot-interruption")
        else:
            previous_gap = document(path, trusted=True)
            require(
                previous_gap.get("previous_campaign") == generation["plan"]["campaign"],
                "Retained boot gap identity differs.",
            )
            self.state["last_gap"] = previous_gap
        publish(
            self.settings.state_root
            / (generation["plan"]["campaign"] + "-boot-" + self.boot[:8] + ".json"),
            {
                "previous_boot_id": generation["boot_id"],
                "current_boot_id": self.boot,
                "old_collector_id": generation["collector_id"],
                "fresh_namespace_required": True,
            },
        )
        self.save("between-campaigns")

    def check_interrupted_metadata(self, generation: dict[str, Any]) -> None:
        root = Path(generation["roots"]["spool"])
        path = root / "runtime-health.json"
        if not path.exists():
            return  # Real abrupt boot can leave no native/terminal document.
        runtime = document(path)
        require(
            runtime["campaign"] == generation["plan"]["campaign"],
            "Interrupted runtime metadata identity differs.",
        )
        graceful = (
            self.state.get("last_graceful_stop_boot_id") == generation["boot_id"]
            and self.state.get("last_graceful_stop_campaign") == generation["plan"]["campaign"]
        )
        require(
            runtime["phase"] != "failed" or graceful,
            "Known failed controller before reboot requires independent revalidation.",
        )
        chunk = runtime.get("chunk")
        if chunk is None:
            return
        require(
            type(chunk) is int and 0 <= chunk < generation["plan"]["chunks"],
            "Interrupted chunk identity differs.",
        )
        run_id = f"{generation['plan']['campaign']}-{chunk:04d}"
        terminal = []
        for lane in generation["plan"]["lanes"]:
            health_path = (
                root
                / generation["plan"]["campaign"]
                / f"chunk-{chunk:04d}"
                / lane
                / LANES[lane][1]
                / run_id
                / "capture-health.json"
            )
            if health_path.exists():
                health = document(health_path, limit=1024**2)
                require(
                    health.get("run_id") == run_id
                    and health.get("credentialless") is True
                    and type(health.get("integrity_events")) is int
                    and health["integrity_events"] == 0
                    and health.get("status") in {"COMPLETED", "OPERATOR_STOP", "FAILED"},
                    "Known native integrity or metadata failure before reboot.",
                )
                terminal.append(health)
        if any(value["status"] == "FAILED" for value in terminal):
            require(transport_failure(terminal), "Known unsafe native failure before reboot.")

    def verify_adopted_guard(self, generation: dict[str, Any]) -> None:
        value = inspect(COLLECTOR)
        if not value["State"]["Running"]:
            return
        guard = generation["capacity_guard"]
        campaign = generation["plan"]["campaign"]
        require(guard["unit"] == campaign + "-capacity.service", "Adopted guard unit differs.")
        records = Path(guard["records"])
        require(
            records == self.settings.runtime_root / ("operator-record-" + campaign),
            "Adopted guard record scope differs.",
        )
        state = document(records / "capacity-state.json", trusted=True)
        active = command(
            ["systemctl", "show", guard["unit"], "--property=ActiveState", "--value"]
        ).strip()
        pid = command(["systemctl", "show", guard["unit"], "--property=MainPID", "--value"]).strip()
        require(
            active == "active"
            and pid == str(state["PID"])
            and state["state"] == "READY"
            and state["boot_id"] == self.boot
            and state["container_id"] == generation["collector_id"]
            and state["policy_sha256"] == guard["policy_sha256"]
            and 0 <= time.monotonic_ns() - state["monotonic_ns"] <= 2_500_000_000,
            "Existing finite capacity supervisor is stale or differs.",
        )

    def adopt(self, *, read_only: bool = False) -> None:
        require(self.state["current"] is None, "Cannot re-adopt after state admission.")
        generation = dict(self.settings.adoption)
        plan = dict(generation["plan"])
        plan["lanes"] = tuple(plan["lanes"])
        RuntimePlan(**plan)
        roots = {key: Path(value) for key, value in generation["roots"].items()}
        approved_host_roots(roots, campaign=plan["campaign"])
        require(
            roots["capture"] == self.settings.capture and roots["archive"] == self.settings.archive,
            "Adopted destinations differ.",
        )
        require(
            roots["spool"].parent == self.settings.runtime_root
            and roots["control"].parent == self.settings.runtime_root,
            "Adopted control/spool must be in the approved runtime dataset.",
        )
        for path in roots.values():
            directory(path, owner=1000)
        for role, path in roots.items():
            self.verify_store(role, path)
        self.state["current"] = generation
        self.verify_current()
        self.verify_adopted_guard(generation)
        # Do not mutate/restart the adopted collector or its finite clock/guard.
        generation["adopted"] = True
        require(generation["boot_id"] == self.boot, "Adoption must pin the actual current boot.")
        if not read_only:
            self.save("adopting", "Existing campaign observed; waiting for native closure.")

    def clock(self, generation: dict[str, Any]) -> None:
        if time.monotonic() - self.last_clock < 25:
            return
        raw = command(["chronyc", "-n", "tracking"], timeout=3)
        report = cast(dict[str, Any], parse_tracking(raw, now_ns=time.time_ns()))
        target = Path(generation["roots"]["control"]) / "clock.json"
        publish(target, report, owner=1000)
        # Same finite runtime guard, evaluated as UID1000 by the market process;
        # pre-admission validation here uses only parsed numeric metadata.
        require(
            report["synchronized"] is True
            and abs(float(report["system_offset_seconds"])) <= 0.050
            and 0 <= float(report["root_dispersion_seconds"]) <= 0.250
            and 0 <= float(report["root_delay_seconds"]) <= 0.500,
            "Host clock out of bounds.",
        )
        self.last_clock = time.monotonic()

    def poll_child(self, argv: list[str], *, stdin: str | None = None, timeout: int = 120) -> str:
        # Long image/gate/audit operations are independently watched every
        # second, so storage hashing cannot suspend pressure/watchdog updates.
        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output_file,
            subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=output_file,
                stderr=subprocess.DEVNULL,
                text=True,
                env=ENV,
            ) as child,
        ):
            self.owned_child = child
            if child.stdin is not None:
                if stdin is not None:
                    child.stdin.write(stdin)
                child.stdin.close()
            deadline = time.monotonic() + timeout
            try:
                while child.poll() is None:
                    require(time.monotonic() < deadline, "Bounded host child deadline expired.")
                    require(
                        os.fstat(output_file.fileno()).st_size <= 131_072,
                        "Host child output bound.",
                    )
                    self.tick()
                    if self.state["current"] and not self.state["current"].get("adopted"):
                        self.clock(self.state["current"])
            except BaseException:
                child.kill()
                child.wait(timeout=10)
                raise
            require(child.returncode == 0, "Host gate or closed-copy audit refused.")
            output_file.seek(0)
            output = output_file.read(131_073)
            require(len(output) <= 131_072, "Host child output bound.")
            self.owned_child = None
            return output

    def audit(self, *, complete: bool) -> None:
        generation = self.state["current"]
        self.save("auditing")
        if not self.state.get("audit_id"):
            # A crash can happen after Docker create but before persisting CID.
            # The deterministic name plus immutable audit identity allows
            # cleanup of only this own networkless readonly verifier.
            try:
                previous = inspect(generation["plan"]["campaign"] + "-closure-audit")
                self.state["audit_id"] = previous["Id"]
            except HostUnavailableError:
                pass
        self.stop_audit()
        argv = [
            "docker",
            "create",
            "-i",
            "--name",
            generation["plan"]["campaign"] + "-closure-audit",
            "--label",
            "org.hyperliquid-bot.continuous-audit=" + generation["plan"]["campaign"],
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "1g",
            "--memory-swap",
            "1g",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=67108864",
            "--env",
            "TRADING_MODE=PAPER",
        ]
        for role in ("capture", "archive"):
            argv += [
                "--mount",
                "type=bind,src="
                + generation["roots"][role]
                + ",dst="
                + CONTAINER_ROOTS[role]
                + ",readonly",
            ]
        argv += [
            "--entrypoint",
            "python",
            self.settings.image,
            "-B",
            "-m",
            "hyperliquid_bot.collector_continuous_audit",
        ]
        request = {
            "plan": generation["plan"],
            "capture": CONTAINER_ROOTS["capture"],
            "archive": CONTAINER_ROOTS["archive"],
            "complete": complete,
        }
        cid = command(argv).strip()
        require(
            re.fullmatch(r"[0-9a-f]{64}", cid) is not None, "Audit container identity required."
        )
        self.state["audit_id"] = cid
        self.save()
        result = json.loads(
            self.poll_child(
                ["docker", "start", "-ai", cid], stdin=json.dumps(request), timeout=1800
            )
        )
        require(
            result["campaign"] == generation["plan"]["campaign"] and result["complete"] is complete,
            "Closed-copy response identity differs.",
        )
        publish(
            self.settings.state_root / (generation["plan"]["campaign"] + "-closure.json"), result
        )
        self.stop_audit()

    def stop_audit(self) -> None:
        cid = self.state.get("audit_id")
        if not cid:
            return
        value = inspect(cid)
        generation = self.state["current"]
        require(
            value["Id"] == cid
            and value["Name"] == "/" + generation["plan"]["campaign"] + "-closure-audit"
            and value["Config"]["Image"] == self.settings.image
            and value["Image"] == self.settings.image_id
            and value["Config"]["Labels"].get("org.hyperliquid-bot.continuous-audit")
            == generation["plan"]["campaign"]
            and value["HostConfig"]["NetworkMode"] == "none",
            "Audit cleanup identity differs.",
        )
        command(["docker", "rm", "--force", cid])
        self.state["audit_id"] = None
        self.save()

    def launch(self) -> None:
        previous = self.verify_current()
        require(not previous["State"]["Running"], "Never replace an active admitted campaign.")
        self.admission()
        for role, path in self.state["current"]["roots"].items():
            self.verify_store(role, Path(path))
        campaign = (
            "hl-cont-" + time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
        )
        plan = self.settings.plan(campaign)
        roots = self.settings.roots(campaign)
        approved_host_roots(roots, campaign=campaign)
        for role in ("capture", "archive"):
            directory(roots[role], owner=1000)
            self.verify_store(role, roots[role])
            require(
                not (roots[role] / campaign).exists(), "Fresh publication reservation required."
            )
        for path in (self.settings.runtime_root, self.settings.capture, self.settings.archive):
            space = os.statvfs(path)
            require(
                space.f_bavail * space.f_frsize >= 4 * capacity.GIB,
                "Storage reserve exhausted; market archives are never deleted.",
            )
        for role in ("spool", "control"):
            roots[role].mkdir(mode=0o700)
            os.chown(roots[role], 1000, 1000)
            self.verify_store(role, roots[role])
        extra = {
            "COLLECTOR_IMAGE": self.settings.image,
            "COLLECTOR_SOURCE_COMMIT": plan.source_commit,
            "COLLECTOR_IMAGE_DIGEST": plan.image_digest,
            "COLLECTOR_CAMPAIGN": campaign,
            "COLLECTOR_STATUS_BIND_IP": self.settings.bind_ip,
        }
        extra |= {"COLLECTOR_NATIVE_" + key.upper(): str(path) for key, path in roots.items()}
        rendered = normalize_compose(
            json.loads(
                command(
                    [
                        "docker",
                        "compose",
                        "--project-name",
                        PROJECT,
                        "-f",
                        str(self.settings.release / "deploy/truenas-collector.compose.yml"),
                        "config",
                        "--format",
                        "json",
                    ],
                    extra=extra,
                )
            ),
            self.settings,
            plan,
        )
        self.verify_current()
        generation = {
            "plan": asdict(plan),
            "roots": {key: str(path) for key, path in roots.items()},
            "image_id": self.settings.image_id,
            "adopted": False,
            "boot_id": self.boot,
            "collector_id": None,
            "status_id": None,
            "app_config_sha256": None,
            "started_utc_ns": time.time_ns(),
            "wall_budget_seconds": 96 * 3600,
        }
        self.state["current"] = generation
        self.state["generations"] += 1
        self.save("admitting")
        publish(
            self.settings.state_root / (campaign + "-intent.json"),
            {"generation": generation, "rendered_compose_sha256": digest(rendered)},
        )
        self.last_clock = 0
        self.clock(generation)
        job = json.loads(
            command(
                ["midclt", "call", "app.update", json.dumps(APP), "-"],
                stdin=json.dumps({"custom_compose_config": rendered}),
            )
        )
        require(type(job) is int and job > 0, "TrueNAS App update job identity required.")
        generation["app_job_id"] = job
        self.save()
        for _ in range(90):
            self.tick()
            with contextlib.suppress(ValueError, KeyError):
                doc = inspect(COLLECTOR)
                owned(doc, generation, "collector")
                if doc["State"]["Running"]:
                    generation["collector_id"] = doc["Id"]
                    break
        require(generation["collector_id"] is not None, "Fresh managed collector did not start.")
        # Persist the exact identity before a separate privileged gate can mint
        # network permission. ExecStopPost must survive an immediate SIGKILL.
        self.save()
        gate = [
            sys.executable,
            "-B",
            str(self.settings.release / "scripts/collector_network_gate.py"),
            "--profile",
            "truenas-zfs",
            "--container",
            COLLECTOR,
            "--source",
            plan.source_commit,
            "--image",
            self.settings.image,
            "--campaign",
            campaign,
        ]
        for role, path in roots.items():
            gate += ["--" + role + "-host", str(path)]
        self.poll_child(gate, timeout=90)
        for _ in range(60):
            self.tick()
            with contextlib.suppress(ValueError, KeyError):
                status = inspect(STATUS)
                owned(status, generation, "status")
                if status["State"]["Running"]:
                    generation["status_id"] = status["Id"]
                    break
        require(generation["status_id"] is not None, "Managed status container did not start.")
        self.save()
        app = self.app()
        require(
            app["config"]["custom_compose_config"] == rendered,
            "Actual stored TrueNAS Compose differs from admitted render.",
        )
        generation["app_config_sha256"] = digest(app["config"])
        self.save("capturing")
        if self.state["last_gap"]:
            self.state["last_gap"]["end_utc_ns"] = time.time_ns()
        self.save()

    def observe(self) -> bool:
        generation = self.state["current"]
        doc = self.verify_current()
        if doc["State"]["Running"]:
            require(
                generation["boot_id"] == self.boot, "Host reboot interrupted a native campaign."
            )
            if not generation.get("adopted"):
                self.clock(generation)
                require(
                    time.time_ns() - generation["started_utc_ns"]
                    < generation["wall_budget_seconds"] * 1_000_000_000,
                    "Finite generation wall budget exhausted.",
                )
            health_path = Path(generation["roots"]["spool"]) / "runtime-health.json"
            if health_path.exists():
                health = document(health_path)
                require(
                    health["campaign"] == generation["plan"]["campaign"],
                    "Runtime identity differs.",
                )
                self.save("publishing" if health["phase"] == "publishing" else "capturing")
            return False
        health = document(Path(generation["roots"]["spool"]) / "runtime-health.json")
        complete = doc["State"]["ExitCode"] == 0 and health["phase"] == "completed"
        if complete:
            self.audit(complete=True)
            self.state["retry_count"] = 0
            self.gap("scheduled-publication-and-new-sessions")
            self.save("between-campaigns")
        else:
            # Preserve all original failures. Never parse logs or arbitrary
            # exception strings into permission to restart.
            root = Path(generation["roots"]["spool"]) / generation["plan"]["campaign"]
            chunk = int(health["chunk"])
            run_id = f"{generation['plan']['campaign']}-{chunk:04d}"
            native = [
                document(
                    root
                    / f"chunk-{chunk:04d}"
                    / lane
                    / LANES[lane][1]
                    / run_id
                    / "capture-health.json",
                    limit=1024**2,
                )
                for lane in generation["plan"]["lanes"]
            ]
            require(
                transport_failure(native),
                "Unknown or integrity failure; operator revalidation required.",
            )
            self.audit(complete=False)
            self.state["retry_count"] += 1
            delay = retry_delay(self.state["retry_count"], self.settings.max_retries)
            if delay is None:
                raise ValueError("Transport retry budget exhausted.")
            self.gap("classified-transport-interruption")
            self.state["retry_after_utc_ns"] = time.time_ns() + delay * 1_000_000_000
            self.save("retry-wait")
        return True

    def stop_owned(self) -> None:
        try:
            self.stop_collector()
        finally:
            self.stop_audit()

    def stop_collector(self) -> None:
        generation = self.state.get("current")
        if not generation or generation.get("adopted"):
            return
        if not generation.get("collector_id"):
            # app.update may have created the exact new waiting container just
            # before the parent died. Recover only its full admitted identity;
            # the old adopted campaign will fail the campaign/mount/image pins.
            try:
                doc = inspect(COLLECTOR)
                owned(doc, generation, "collector")
            except (ValueError, KeyError, OSError, subprocess.SubprocessError):
                return
            generation["collector_id"] = doc["Id"]
            # A full quota must never prevent stopping an independently
            # verified own process merely because its receipt cannot be saved.
            with contextlib.suppress(OSError):
                self.save()
        else:
            doc = inspect(COLLECTOR)
            owned(doc, generation, "collector", cleanup=True)
        if doc["State"]["Running"]:
            command(["docker", "stop", "--time", "90", generation["collector_id"]], timeout=100)

    def shutdown_phase(self) -> str:
        """A shutdown signal cannot erase a failure already written by runtime."""
        generation = self.state["current"]
        if generation:
            path = Path(generation["roots"]["spool"]) / "runtime-health.json"
            try:
                if path.exists():
                    phase = document(path).get("phase")
                    if phase not in {"starting", "capturing", "publishing", "completed"}:
                        return "paused"
            except (OSError, ValueError):
                return "paused"
            self.state["last_graceful_stop_campaign"] = generation["plan"]["campaign"]
        self.state["last_graceful_stop_boot_id"] = self.boot
        return "stopped"

    def run(self) -> int:
        try:
            if self.state["phase"] == "paused":
                return 2
            if self.state["phase"] == "stopped":
                current = self.state["current"]
                if current is None or current["boot_id"] == self.boot:
                    return 2
                # Graceful OS shutdown sends the same SIGTERM as systemctl stop.
                # Continuous intent survives to the next boot; an explicit
                # operator/integrity pause is a different persistent state.
                self.state["phase"] = "auditing"
            if self.state["current"] and self.state["current"]["boot_id"] != self.boot:
                self.state["phase"] = "auditing"
            self.admission()
            if self.state["current"] is None:
                self.adopt()
            notify("READY=1\nWATCHDOG=1")
            while not self.stopping:
                generation = self.state["current"]
                if generation["boot_id"] != self.boot:
                    self.recover_boot()
                if self.state["phase"] == "retry-wait":
                    if time.time_ns() < self.state["retry_after_utc_ns"]:
                        self.tick()
                        continue
                    self.launch()
                elif self.state["phase"] == "between-campaigns" or self.observe():
                    if self.state["phase"] == "retry-wait":
                        continue
                    self.launch()
                self.tick()
        except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
            phase = (
                self.shutdown_phase()
                if self.stopping and str(error) == "Operator stopped continuous supervision."
                else "paused"
            )
            try:
                self.save(phase, str(error)[:240])
            finally:
                self.stop_owned()
            return 0 if phase == "stopped" else 2
        if self.stopping:
            phase = self.shutdown_phase()
            try:
                self.save(phase, "Supervisor stopped; continuous intent retained for next boot.")
            finally:
                self.stop_owned()
            return 0 if phase == "stopped" else 2
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--stop-owned", action="store_true")
    operation.add_argument("--inspect-only", action="store_true")
    operation.add_argument("--pause", action="store_true")
    args = parser.parse_args(argv)
    require(os.getuid() == 0, "Trusted root host operator required.")
    settings = load_settings(args.settings)
    if not args.stop_owned:
        wait_host_ready(settings)
    if args.inspect_only:
        supervisor = Supervisor(settings)
        if supervisor.state["current"] is None:
            supervisor.adopt(read_only=True)
        else:
            supervisor.verify_current()
        print(
            json.dumps(
                {
                    "schema": "collector-continuous-preflight-v1",
                    "read_only": True,
                    "campaign": supervisor.state["current"]["plan"]["campaign"],
                    "no_runtime_changes": True,
                }
            )
        )
        return 0
    with singleton(settings.state_root):
        supervisor = Supervisor(settings)
        if args.pause:
            try:
                supervisor.save(
                    "paused", "Explicit persistent operator pause; revalidation required."
                )
            finally:
                supervisor.stop_owned()
            return 0
        if args.stop_owned:
            try:
                if supervisor.state["phase"] not in {"paused", "stopped"}:
                    supervisor.save("paused", "Host supervisor stopped; admission needs review.")
            finally:
                supervisor.stop_owned()
            return 0

        def stop(_signum: int, _frame: object) -> None:
            supervisor.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
