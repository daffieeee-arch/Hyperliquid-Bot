"""Trusted host supervisor for rolling, finite native public-data campaigns.

Run from a frozen root-owned release on the approved Linux host, never from a
market/UI container. Every generation receives fresh roots, clock and TC proof.
The persistent state is a control record, not permission to resume raw data.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
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
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from . import collector_capacity as capacity
from .collector_clock import parse_tracking
from .collector_instance import FAST, CollectorInstance, instance
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
    "pressure-wait",
    "paused",
    "stopped",
}
require = capacity.require


class HostUnavailableError(ValueError):
    """A read-only host command could not reach its service."""


class CapacityStop(ValueError):
    """A measured host capacity boundary with immutable numeric incident evidence."""

    def __init__(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
        streaks: dict[str, int],
        reasons: list[str],
    ) -> None:
        super().__init__("Capacity stop: " + ",".join(reasons))
        self.previous = previous
        self.current = current
        self.streaks = dict(streaks)
        self.reasons = tuple(reasons)


@contextlib.contextmanager
def watchdog_command(timeout: int) -> Iterator[None]:
    """Keep a bounded host command alive without masking an unbounded stalled loop.

    Only watchdog liveness is extended. An active producer still fails its next
    capacity check if this command created a telemetry gap longer than five seconds.
    """

    def pulse() -> None:
        # A closing systemd socket must not prevent exact owned-process cleanup.
        with contextlib.suppress(OSError):
            notify("WATCHDOG=1")

    pulse()
    stopped = threading.Event()
    deadline = time.monotonic() + timeout + 2

    def heartbeat() -> None:
        while not stopped.wait(1):
            if time.monotonic() >= deadline:
                return
            pulse()

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=2)
        pulse()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def native_app_config(app: dict[str, Any]) -> dict[str, Any]:
    """TrueNAS27 returns Compose directly; the update-input wrapper is not a getter shape."""
    value = app.get("config")
    if not isinstance(value, dict) or set(value) != {
        "name",
        "networks",
        "services",
        "x-notes",
        "x-portals",
    }:
        raise ValueError("Native App getter requires the supported direct Compose shape.")
    services = value["services"]
    require(
        isinstance(services, dict) and set(services) == {"collector", "status"},
        "Native App getter requires exactly the managed Compose services.",
    )
    for role in ("collector", "status"):
        require(
            isinstance(services[role], dict)
            and isinstance(services[role].get("command"), list)
            and all(type(arg) is str for arg in services[role]["command"]),
            "Native App getter requires explicit service command lists.",
        )
    return value


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
    instance: str = "standard"
    first_create: bool = False

    def __post_init__(self) -> None:
        profile = instance(self.instance)
        require(type(self.first_create) is bool, "Exact first-create flag required.")
        require(
            not self.first_create or (profile == FAST and not self.adoption),
            "First-create is only for an unadopted FAST instance.",
        )
        if profile == FAST:
            require(
                self.runtime_root == Path("/mnt/fastdisk/hyperliquid-runtime")
                and self.bind_ip == "192.168.1.234",
                "FAST requires its separate approved runtime dataset and LAN address.",
            )
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
        self.plan(
            self.profile.campaign_prefix + "admission-check"
            if self.profile == FAST
            else "admission-check"
        )
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
    def profile(self) -> CollectorInstance:
        return instance(self.instance)

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
            ("hyperliquid",) if self.profile == FAST else tuple(LANES),
            self.chunk_seconds,
            self.chunks,
            self.first_create,
            self.source_commit,
            self.image_digest,
            self.prune_new_spool,
            "truenas-zfs",
            self.instance,
            self.profile == FAST,
        )


def load_settings(path: Path) -> Settings:
    value = document(path, trusted=True)
    require(value.pop("schema") == "collector-continuous-settings-v1", "Settings schema differs.")
    for name in ("release", "state_root", "runtime_root", "capture", "archive"):
        value[name] = Path(value[name])
    value["capacity"] = capacity.CapacityPolicy(**value["capacity"])
    return Settings(**value)


def verify_release(settings: Settings, *, check_image: bool = True) -> None:
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
    if not check_image:
        return
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
    with watchdog_command(timeout):
        response = subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=ENV | (extra or {}),
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
            if settings.first_create:
                apps = json.loads(
                    command(
                        [
                            "midclt",
                            "call",
                            "app.query",
                            json.dumps([["id", "=", settings.profile.app]]),
                        ]
                    )
                )
                if apps == []:
                    return
            command(["midclt", "call", "app.get_instance", json.dumps(settings.profile.app)])
            inspect(settings.profile.container("collector"))
            status = inspect(settings.profile.container("status"))
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
    profile = instance(plan.instance)
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
        doc["Name"] == "/" + profile.container(role)
        and labels.get("com.docker.compose.project") == profile.project
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
    if role == "status" and profile == FAST:
        require(
            host["PortBindings"]
            == {"8080/tcp": [{"HostIp": "192.168.1.234", "HostPort": str(FAST.port)}]},
            "FAST status must publish only its fixed LAN port.",
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
        ] + (["--instance", plan.instance] if plan.l2_fast else [])
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
    if plan.l2_fast:
        args += ["--instance", plan.instance, "--l2-fast"]
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
        == (["hyperliquid"] if settings.profile == FAST else ["hyperliquid", "binance"]),
        "Reviewed template lanes differ.",
    )
    if not plan.pilot:
        args.remove("--pilot")
    args[args.index("--chunk-seconds") + 1] = str(plan.chunk_seconds)
    args[args.index("--chunks") + 1] = str(plan.chunks)
    at = args.index("--chunk-seconds")
    if settings.profile != FAST:
        args[at:at] = ["--lane", "kraken", "--lane", "bitvavo-standard"]
    if plan.prune_spool:
        args.append("--prune-spool")
    if settings.profile == FAST:
        require(args == process_contract(plan, "collector")[1], "Rendered process differs.")
        require(
            value["services"]["status"]["command"] == process_contract(plan, "status")[1],
            "FAST status process differs.",
        )
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
    if settings.profile == FAST:
        ports = value["services"]["status"].get("ports")
        require(
            isinstance(ports, list)
            and len(ports) == 1
            and ports[0].get("host_ip") == settings.bind_ip
            and str(ports[0].get("published")) == str(FAST.port)
            and ports[0].get("target") == 8080
            and ports[0].get("protocol", "tcp") == "tcp",
            "FAST status service must publish only its fixed LAN port.",
        )
    portal = value["x-portals"]
    require(
        len(portal) == 1
        and portal[0]["host"] == settings.bind_ip
        and type(portal[0]["port"]) is int
        and portal[0]["port"] == settings.profile.port,
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
        current = self.state.get("current")
        if current:
            require(
                instance(current["plan"].get("instance", "standard")) == settings.profile,
                "Cross-instance state refused.",
            )
        require(
            self.state.get("instance", "standard") == settings.instance
            or (not self.path.exists() and not current),
            "Cross-instance state refused.",
        )
        self.stopping = False
        self.previous: dict[str, Any] | None = None
        self.streaks: dict[str, int] = {}
        self.host_memory: dict[str, Any] | None = None
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
        if self.settings.profile == FAST:
            self.state["instance"] = self.settings.instance
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
                "pressure_retry_count": self.state.get("pressure_retry_count", 0),
                "pressure_retry_after_utc_ns": (
                    self.state.get("pressure_recovery", {}).get("retry_after_utc_ns")
                    if self.state.get("pressure_recovery", {}).get("pending")
                    else None
                ),
                "reason": self.state.get("reason"),
                "last_gap": self.state["last_gap"],
                "market_archives_automatically_deleted": False,
                "seamless_coverage_claimed": False,
                "research_ready": False,
                "supervisor_source_commit": self.settings.source_commit,
                "producer_source_commit": generation["plan"]["source_commit"],
            }
            if self.settings.profile == FAST:
                view["instance"] = self.settings.instance
                view["collector_memory"] = getattr(self, "collector_memory", None)
            memory = getattr(self, "host_memory", None)
            if memory is not None:
                view["host_memory"] = memory
                if (
                    self.state["phase"] == "capturing"
                    and memory["runtime_psi_profile"] == capacity.NATIVE_TREND_PSI
                    and memory["state"] == "warning"
                ):
                    available_gib = memory["direct_memavailable_bytes"] / capacity.GIB
                    view["reason"] = (
                        "Geheugenstalling op de NAS; opname blijft actief. "
                        f"Beschikbaar: {available_gib:.2f} GiB."
                    )
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
            if reasons:
                raise CapacityStop(self.previous, current, self.streaks, reasons)
        self.host_memory = capacity.runtime_status(self.previous, current, self.settings.capacity)
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
                doc = inspect(self.settings.profile.container("collector"))
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
                    if self.settings.profile == FAST:
                        memory_root = Path("/sys/fs/cgroup") / cgroup[0].lstrip("/")
                        self.collector_memory = {
                            "current_bytes": int((memory_root / "memory.current").read_text()),
                            "peak_bytes": int((memory_root / "memory.peak").read_text()),
                            "limit_bytes": 2 * capacity.GIB,
                            "sampled_utc_ns": time.time_ns(),
                        }
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

    def record_capacity(self, error: CapacityStop) -> None:
        generation = self.state.get("current")
        campaign = generation["plan"]["campaign"] if generation else "pre-admission"
        name = campaign + "-capacity-" + uuid.uuid4().hex + ".json"
        publish(
            self.settings.state_root / name,
            {
                "schema": "collector-capacity-incident-v1",
                "campaign": campaign,
                "capacity_policy": capacity.policy_document(self.settings.capacity),
                "supervisor_source_commit": self.settings.source_commit,
                "created_utc_ns": time.time_ns(),
                "boot_id": self.boot,
                "before": error.previous,
                "current": error.current,
                "streaks": error.streaks,
                "reasons": list(error.reasons),
            },
        )
        self.state["last_capacity_incident"] = name

    def capacity_events(self, doc: dict[str, Any]) -> dict[str, int]:
        """Read the exact owned producer cgroup before accepting a soft host stop."""
        require(not doc["State"]["OOMKilled"], "Owned collector was OOM killed.")
        if not doc["State"]["Running"]:
            return {"oom": 0, "oom_kill": 0}
        pid = int(doc["State"]["Pid"])
        records = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        groups = [line.removeprefix("0::") for line in records if line.startswith("0::")]
        require(
            len(groups) == 1 and groups[0].startswith("/") and ".." not in Path(groups[0]).parts,
            "Owned cgroup v2 identity missing.",
        )
        values = dict(
            line.split()
            for line in (Path("/sys/fs/cgroup") / groups[0].lstrip("/") / "memory.events")
            .read_text()
            .splitlines()
        )
        events = {key: capacity.count(int(values[key])) for key in ("oom", "oom_kill")}
        require(not any(events.values()), "Owned collector cgroup OOM.")
        return events

    def pressure_rebind(self) -> dict[str, Any]:
        """Explicit recovery may rebind recreated CIDs only under the old full contract."""
        generation = self.state["current"]
        require(
            generation and not generation.get("adopted"), "Pressure recovery needs owned capture."
        )
        for role, path in generation["roots"].items():
            self.verify_store(role, Path(path))
        require(
            digest(self.app()["config"]) == generation["app_config_sha256"],
            "Pressure recovery App configuration differs.",
        )
        rebound = dict(generation)
        if generation["boot_id"] != self.boot:
            rebound.update(collector_id=None, status_id=None)
        collector, status = (
            inspect(self.settings.profile.container("collector")),
            inspect(self.settings.profile.container("status")),
        )
        owned(collector, rebound, "collector")
        owned(status, rebound, "status")
        require(
            status["State"]["Running"], "Read-only status unavailable during pressure recovery."
        )
        generation["collector_id"], generation["status_id"] = collector["Id"], status["Id"]
        generation["boot_id"] = self.boot
        self.verify_current()
        self.pressure_verified_boot = self.boot
        return collector

    def pressure_capture(self, producer: dict[str, Any]) -> dict[str, Any]:
        """Only a live capturing controller can causally become our pressure stop."""
        require(producer["State"]["Running"], "Stopped producer is not a measured pressure stop.")
        generation = self.state["current"]
        runtime = document(Path(generation["roots"]["spool"]) / "runtime-health.json")
        chunk = runtime.get("chunk")
        require(
            runtime.get("campaign") == generation["plan"]["campaign"]
            and runtime.get("phase") == "capturing"
            and type(chunk) is int
            and 0 <= chunk < generation["plan"]["chunks"]
            and runtime.get("run_id") == f"{generation['plan']['campaign']}-{chunk:04d}",
            "Pressure cannot reclassify a failed or publishing controller.",
        )
        # Metadata reads must not grant restart permission after the producer exited.
        require(self.verify_current()["State"]["Running"], "Producer exited before pressure stop.")
        return runtime

    def pressure_health(self) -> None:
        """A capacity stop cannot hide a native writer, integrity or unknown failure."""
        generation = self.state["current"]
        root = Path(generation["roots"]["spool"])
        runtime = document(root / "runtime-health.json")
        require(runtime["campaign"] == generation["plan"]["campaign"], "Pressure runtime identity.")
        chunk = runtime["chunk"]
        require(type(chunk) is int and 0 <= chunk < generation["plan"]["chunks"], "Pressure chunk.")
        run_id = f"{generation['plan']['campaign']}-{chunk:04d}"
        health = []
        for lane in generation["plan"]["lanes"]:
            value = document(
                root
                / generation["plan"]["campaign"]
                / f"chunk-{chunk:04d}"
                / lane
                / LANES[lane][1]
                / run_id
                / "capture-health.json",
                limit=1024**2,
            )
            require(
                value.get("run_id") == run_id
                and value.get("credentialless") is True
                and type(value.get("integrity_events")) is int
                and value["integrity_events"] == 0
                and value.get("status") in {"COMPLETED", "OPERATOR_STOP", "FAILED"},
                "Pressure interruption has unsafe or missing native terminal health.",
            )
            health.append(value)
        require(
            not any(value["status"] == "FAILED" for value in health) or transport_failure(health),
            "Pressure interruption has an unknown native failure.",
        )
        receipt = self.pressure_receipt()
        if receipt["operator_revalidated"] is False:
            before = receipt["pre_stop_runtime"]
            require(
                before["phase"] == "capturing"
                and before["chunk"] == runtime["chunk"]
                and before["run_id"] == runtime.get("run_id")
                and runtime["phase"] == "failed"
                and all(value["status"] in {"COMPLETED", "OPERATOR_STOP"} for value in health)
                and any(value["status"] == "OPERATOR_STOP" for value in health),
                "Pressure stop has no causal operator-stop terminal proof.",
            )

    def begin_pressure_wait(
        self,
        error: CapacityStop | None = None,
        *,
        expected_state_sha256: str | None = None,
        abandon_unsealed: bool = False,
        read_only: bool = False,
    ) -> dict[str, Any]:
        """Persist pressure evidence; operator decisions require an exact paused-state pin."""
        operator = expected_state_sha256 is not None
        require(
            not abandon_unsealed or operator,
            "Unsealed abandonment requires explicit operator recovery.",
        )
        if operator:
            require(
                self.state["phase"] == "paused" and digest(self.state) == expected_state_sha256,
                "Explicit pressure state pin differs.",
            )
            measured = "pressure_recovery" in self.state
            if measured:
                require(
                    isinstance(self.state["pressure_recovery"], dict)
                    and self.state["pressure_recovery"].get("pending") is True,
                    "A present pressure recovery must retain its valid pending receipt.",
                )
            revalidation = self.paused_pressure_incident(abandon_unsealed) if measured else None
            incident = revalidation[0] if revalidation is not None else None
            if incident is None:
                require(
                    self.state.get("reason") == "Capacity stop: memory-pressure",
                    "Only the exact legacy pressure pause can be migrated.",
                )
        else:
            incident = None
            revalidation = None
            require(
                error is not None and error.reasons == ("memory-pressure",),
                "Only measured host PSI pressure is recoverable.",
            )
        previous_state = json.loads(json.dumps(self.state))
        count = capacity.count(self.state.get("pressure_retry_count", 0)) + 1
        delay = retry_delay(count, self.settings.max_retries)
        require(delay is not None, "Host pressure retry budget exhausted.")
        assert delay is not None
        producer = self.pressure_rebind() if operator else self.verify_current()
        require(not self.state["current"].get("adopted"), "Cannot stop adopted pressure producer.")
        if incident is not None:
            require(not producer["State"]["Running"], "Paused pressure producer must be stopped.")
        pre_stop_runtime = None if operator else self.pressure_capture(producer)
        events = self.capacity_events(producer)
        operator_baseline = capacity.snapshot() if operator else None
        if operator_baseline is not None:
            capacity.metric(operator_baseline, self.settings.capacity)
            if incident is not None:
                self.pressure_counters(operator_baseline)
        campaign = self.state["current"]["plan"]["campaign"]
        name = campaign + "-pressure-" + uuid.uuid4().hex + ".json"
        receipt = {
            "schema": "collector-pressure-operator-v1"
            if operator
            else "collector-pressure-incident-v1",
            "campaign": campaign,
            "capacity_policy": capacity.policy_document(self.settings.capacity),
            "supervisor_source_commit": self.settings.source_commit,
            "producer_source_commit": self.state["current"]["plan"]["source_commit"],
            "created_utc_ns": time.time_ns(),
            "wait_deadline_utc_ns": time.time_ns() + 1800 * 1_000_000_000,
            "boot_id": self.boot,
            "reasons": ["memory-pressure"],
            "cgroup_events": events,
            "pre_stop_runtime": pre_stop_runtime,
            "operator_revalidated": operator,
            "prior_state": previous_state if operator else None,
            "prior_state_sha256": expected_state_sha256,
            "before": error.previous if error else None,
            "current": error.current if error else None,
            "operator_capacity_baseline": operator_baseline,
            "streaks": error.streaks if error else None,
            "abandon_unsealed": abandon_unsealed,
            "partial_metadata": self.pressure_metadata() if abandon_unsealed else None,
            "old_partial_research_qualified": False,
        }
        if incident is not None:
            assert revalidation is not None
            require(
                receipt["partial_metadata"] == revalidation[1]
                and digest(
                    document(Path(self.state["current"]["roots"]["spool"]) / "runtime-health.json")
                )
                == revalidation[2],
                "Typed pressure quarantine metadata changed during revalidation.",
            )
            # Keep the original numeric counter baseline across this explicit decision.
            # A fresh operator sample must never erase an intervening hard failure.
            receipt.update(
                schema="collector-pressure-operator-v2",
                prior_pressure_receipt={
                    "name": previous_state["pressure_recovery"]["receipt"],
                    "sha256": previous_state["pressure_recovery"]["receipt_sha256"],
                    "document": incident,
                },
                before=incident["before"],
                current=incident["current"],
                streaks=incident["streaks"],
                pre_stop_runtime=incident["pre_stop_runtime"],
                operator_capacity_baseline=None,
                operator_capacity_readback=operator_baseline,
                quarantined_runtime_sha256=revalidation[2],
            )
        if read_only:
            require(operator, "Read-only pressure preflight requires an operator state pin.")
            self.state = previous_state
            return receipt
        publish(self.settings.state_root / name, receipt)
        self.pressure_transition_started = True
        self.state["pressure_retry_count"] = count
        self.state["pressure_recovery"] = {
            "schema": "collector-pressure-recovery-v1",
            "campaign": campaign,
            "receipt": name,
            "receipt_sha256": digest(receipt),
            "pending": True,
            "retry_after_utc_ns": time.time_ns() + delay * 1_000_000_000,
        }
        self.save("pressure-wait", "Host pressure stopped capture; waiting for stable admission.")
        self.stop_owned()
        # No telemetry interval across a slow stop is represented as continuous.
        self.previous = None
        self.streaks = {}
        self.pressure_samples: list[dict[str, Any]] = []
        self.pressure_stable_since: int | None = None
        return receipt

    def paused_pressure_incident(
        self, abandon_unsealed: bool
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Revalidate one measured incident paused specifically for a missing terminal.

        The computed bounded errno text excludes unrelated pauses. Permission comes
        from the operator's state pin plus the immutable causal receipt and full
        producer contract, never from matching an arbitrary log substring.
        """
        incident = self.pressure_receipt()
        generation = self.state["current"]
        require(
            abandon_unsealed
            and incident["schema"] == "collector-pressure-incident-v1"
            and incident["operator_revalidated"] is False
            and self.state["boot_id"] == generation["boot_id"] == incident["boot_id"] == self.boot,
            "Typed pressure quarantine requires its original same-boot measured incident.",
        )
        before = incident["pre_stop_runtime"]
        plan = generation["plan"]
        runtime = document(Path(generation["roots"]["spool"]) / "runtime-health.json")
        chunk = before.get("chunk")
        require(
            type(chunk) is int
            and 0 <= chunk < plan["chunks"]
            and before.get("phase") == "capturing"
            and runtime.get("phase") == "failed"
            and runtime.get("chunk") == chunk,
            "Typed pressure quarantine lacks its causal capturing controller.",
        )
        expected = {
            key: plan[key]
            for key in (
                "campaign",
                "chunks",
                "chunk_seconds",
                "source_commit",
                "image_digest",
                "storage_profile",
            )
        }
        expected.update(lanes=list(plan["lanes"]), run_id=f"{plan['campaign']}-{chunk:04d}")
        require(
            all(
                before.get(key) == value and runtime.get(key) == value
                for key, value in expected.items()
            ),
            "Typed pressure controller differs from the original producer plan.",
        )
        streaks = incident["streaks"]
        prior_streaks = {key: max(0, capacity.count(streaks[key]) - 1) for key in ("low", "psi")}
        capacity.metric(incident["before"], self.settings.capacity)
        observed_streaks, reasons = capacity.evaluate(
            incident["before"], incident["current"], prior_streaks, self.settings.capacity
        )
        require(
            reasons == ["memory-pressure"] and observed_streaks == streaks,
            "Original pressure interval includes an unapproved capacity failure.",
        )
        records = self.pressure_metadata()
        missing_reasons = []
        for lane in plan["lanes"]:
            base = (
                Path(generation["roots"]["spool"])
                / plan["campaign"]
                / f"chunk-{chunk:04d}"
                / lane
                / LANES[lane][1]
                / expected["run_id"]
            )
            for name in ("capture-claim.json", "capture-live.json"):
                value = document(base / name, limit=1024**2)
                recorded = next(r for r in records if r["lane"] == lane and r["name"] == name)
                require(
                    value.get("run_id") == expected["run_id"]
                    and value.get("definitive_outage") is None
                    and recorded.get("document_sha256") == digest(value),
                    "Typed pressure quarantine has foreign or failed native evidence.",
                )
            if any(
                r["lane"] == lane and r["name"] == "capture-health.json" and not r["present"]
                for r in records
            ):
                missing_reasons.append(
                    str(
                        FileNotFoundError(
                            errno.ENOENT,
                            os.strerror(errno.ENOENT),
                            str(base / "capture-health.json"),
                        )
                    )[:240]
                )
        require(
            self.state.get("reason") in missing_reasons,
            "Only a proved missing-terminal pressure pause can be quarantined.",
        )
        return incident, records, digest(runtime)

    def stopped_capacity_baseline(self) -> None:
        """A verified stopped producer has no continuous capture interval to certify."""
        producer = self.verify_current()
        require(not producer["State"]["Running"], "Capacity reset requires a stopped producer.")
        require(not producer["State"]["OOMKilled"], "Stopped producer was OOM killed.")
        current = capacity.snapshot()
        proof = capacity.metric(current, self.settings.capacity)
        if self.state.get("pressure_recovery", {}).get("pending") is True:
            self.pressure_counters(current)
        if self.previous is not None:
            require(
                current["global_oom_kill"] == self.previous["global_oom_kill"]
                and all(
                    current["arc"][key] == self.previous["arc"][key]
                    for key in ("memory_throttle_count", "evict_not_enough")
                ),
                "Hard capacity counter changed during stopped preparation.",
            )
        require(
            proof["direct_bytes"] >= capacity.GIB, "Hard low memory during stopped preparation."
        )
        self.previous = current
        self.streaks = {}

    def pressure_metadata(self) -> list[dict[str, Any]]:
        """Retain missing terminals under explicit operator authority as unqualified evidence."""
        generation = self.state["current"]
        root = Path(generation["roots"]["spool"])
        runtime = document(root / "runtime-health.json")
        require(
            runtime["campaign"] == generation["plan"]["campaign"], "Quarantine runtime identity."
        )
        chunk = runtime["chunk"]
        require(
            type(chunk) is int and 0 <= chunk < generation["plan"]["chunks"], "Quarantine chunk."
        )
        run_id = f"{generation['plan']['campaign']}-{chunk:04d}"
        records = []
        for lane in generation["plan"]["lanes"]:
            base = (
                root
                / generation["plan"]["campaign"]
                / f"chunk-{chunk:04d}"
                / lane
                / LANES[lane][1]
                / run_id
            )
            for name in ("capture-claim.json", "capture-live.json", "capture-health.json"):
                path = base / name
                record: dict[str, Any] = {"lane": lane, "name": name, "present": path.exists()}
                if path.exists():
                    value = document(path, limit=1024**2)
                    record["document_sha256"] = digest(value)
                    identity = path.lstat()
                    record.update(
                        device=identity.st_dev, inode=identity.st_ino, bytes=identity.st_size
                    )
                    if name == "capture-health.json":
                        require(
                            value.get("run_id") == run_id
                            and value.get("credentialless") is True
                            and type(value.get("integrity_events")) is int
                            and value["integrity_events"] == 0
                            and value.get("status") in {"COMPLETED", "OPERATOR_STOP"},
                            "Operator quarantine cannot erase a known native failure.",
                        )
                records.append(record)
        return records

    def pressure_receipt(self) -> dict[str, Any]:
        value = self.state["pressure_recovery"]
        campaign = self.state["current"]["plan"]["campaign"]
        require(
            value["schema"] == "collector-pressure-recovery-v1"
            and value["campaign"] == campaign
            and value["pending"] is True
            and re.fullmatch(
                re.escape(campaign) + r"-pressure-[0-9a-f]{32}\.json", value["receipt"]
            )
            is not None,
            "Pressure recovery identity differs.",
        )
        receipt = document(self.settings.state_root / value["receipt"], trusted=True)
        require(
            digest(receipt) == value["receipt_sha256"]
            and receipt["campaign"] == campaign
            and receipt["reasons"] == ["memory-pressure"]
            and receipt["capacity_policy"] == capacity.policy_document(self.settings.capacity)
            and receipt["producer_source_commit"] == self.state["current"]["plan"]["source_commit"]
            and re.fullmatch(r"[0-9a-f]{40}", receipt["supervisor_source_commit"]) is not None
            and receipt["schema"]
            in {
                "collector-pressure-incident-v1",
                "collector-pressure-operator-v1",
                "collector-pressure-operator-v2",
            }
            and receipt["cgroup_events"] == {"oom": 0, "oom_kill": 0},
            "Pressure recovery receipt differs.",
        )
        if receipt["schema"] == "collector-pressure-operator-v2":
            prior = receipt["prior_pressure_receipt"]
            require(
                type(prior["name"]) is str
                and re.fullmatch(
                    re.escape(campaign) + r"-pressure-[0-9a-f]{32}\.json", prior["name"]
                )
                is not None,
                "Original pressure receipt path differs.",
            )
            original = document(self.settings.state_root / prior["name"], trusted=True)
            require(
                prior["name"] == receipt["prior_state"]["pressure_recovery"]["receipt"]
                and prior["sha256"] == receipt["prior_state"]["pressure_recovery"]["receipt_sha256"]
                and digest(receipt["prior_state"]) == receipt["prior_state_sha256"]
                and digest(original) == prior["sha256"]
                and original == prior["document"]
                and original["schema"] == "collector-pressure-incident-v1"
                and receipt["operator_revalidated"] is True
                and receipt["abandon_unsealed"] is True
                and receipt["operator_capacity_baseline"] is None
                and re.fullmatch(r"[0-9a-f]{64}", receipt["quarantined_runtime_sha256"]) is not None
                and all(
                    receipt[key] == original[key]
                    for key in (
                        "before",
                        "current",
                        "streaks",
                        "pre_stop_runtime",
                        "boot_id",
                        "producer_source_commit",
                        "capacity_policy",
                        "campaign",
                        "reasons",
                        "cgroup_events",
                    )
                ),
                "Operator pressure decision lost its original incident evidence.",
            )
        return receipt

    def pressure_counters(self, current: dict[str, Any]) -> None:
        """Stopping resets time/PSI sampling, never same-boot hard counters."""
        receipt = self.pressure_receipt()
        original = receipt["current"]
        if original is None:
            require(receipt["operator_revalidated"] is True, "Missing measured capacity evidence.")
            original = receipt["operator_capacity_baseline"]
        capacity.metric(original, self.settings.capacity)
        previous_boot = receipt["boot_id"]
        previous = original
        if previous_boot != self.boot:
            epoch = self.state["pressure_recovery"].get("counter_epoch")
            if epoch is not None:
                previous_boot, previous = epoch["boot_id"], epoch["sample"]
                capacity.metric(previous, self.settings.capacity)
            if previous_boot != self.boot:
                require(
                    getattr(self, "pressure_verified_boot", None) == self.boot
                    and self.state["current"]["boot_id"] == self.boot,
                    "Pressure counter rebase requires a verified actual host boot change.",
                )
                epoch = {
                    "schema": "collector-pressure-counter-epoch-v1",
                    "previous_boot_id": previous_boot,
                    "boot_id": self.boot,
                    "sample": json.loads(json.dumps(current)),
                    "reason": "verified-host-boot-change",
                    "created_utc_ns": time.time_ns(),
                }
                self.state["pressure_recovery"]["counter_epoch"] = epoch
                self.save()
                return
        require(
            current["global_oom_kill"] == previous["global_oom_kill"]
            and all(
                current["arc"][key] == previous["arc"][key]
                for key in ("memory_throttle_count", "evict_not_enough")
            ),
            "Hard capacity counter changed across pressure stop or same-boot restart.",
        )

    def wait_pressure(self) -> bool:
        receipt = self.pressure_receipt()
        require(
            time.time_ns() < receipt["wait_deadline_utc_ns"],
            "Host pressure stable admission deadline expired; operator revalidation required.",
        )
        if not hasattr(self, "pressure_samples"):
            producer = self.pressure_rebind()
            self.capacity_events(producer)
            before_stop = capacity.snapshot()
            capacity.metric(before_stop, self.settings.capacity)
            self.pressure_counters(before_stop)
            self.stop_owned()
            self.previous = None
            self.pressure_samples = []
            self.pressure_stable_since = None
        if time.monotonic() - getattr(self, "last_pressure_check", 0.0) >= 25:
            self.pressure_wait_guards()
            self.last_pressure_check = time.monotonic()
            after_checks = capacity.snapshot()
            capacity.metric(after_checks, self.settings.capacity)
            self.pressure_counters(after_checks)
            if (
                self.previous is not None
                and after_checks["monotonic_ns"] - self.previous["monotonic_ns"] > 5_000_000_000
            ):
                self.stopped_capacity_baseline()
                self.pressure_samples = []
                self.pressure_stable_since = None
        require(not self.stopping, "Operator stopped continuous supervision.")
        time.sleep(1)
        notify("WATCHDOG=1")
        current = capacity.snapshot()
        proof = capacity.metric(current, self.settings.capacity)
        self.pressure_counters(current)
        reasons: list[str] = []
        if self.previous is not None:
            self.streaks, reasons = capacity.evaluate(
                self.previous, current, self.streaks, self.settings.capacity
            )
            require(not set(reasons) - {"memory-pressure"}, "Hard capacity failure while waiting.")
        self.previous = current
        self.pressure_samples = [*self.pressure_samples[-2:], current]
        healthy = (
            not reasons
            and proof["direct_bytes"] >= 2 * capacity.GIB
            and proof["effective_bytes"] >= 5 * capacity.GIB
            and all(
                current["psi"][kind][key] < bound
                for kind, bound in (("full", 1), ("some", 5))
                for key in ("avg10", "avg60", "avg300")
            )
        )
        if healthy and len(self.pressure_samples) == 3:
            try:
                capacity.admit(self.pressure_samples, self.settings.capacity)
            except ValueError as error:
                require(
                    str(error)
                    in {
                        "Insufficient native memory admission headroom.",
                        "Startup PSI average limit.",
                        "Startup PSI interval.",
                    },
                    "Invalid capacity evidence while waiting.",
                )
                healthy = False
        else:
            healthy = False
        if healthy:
            if self.pressure_stable_since is None:
                self.pressure_stable_since = current["monotonic_ns"]
        else:
            self.pressure_stable_since = None
        self.save("pressure-wait")
        if (
            self.pressure_stable_since is None
            or current["monotonic_ns"] - self.pressure_stable_since < 60_000_000_000
            or time.time_ns() < self.state["pressure_recovery"]["retry_after_utc_ns"]
        ):
            return False
        self.verify_current()
        receipt = self.pressure_receipt()
        if receipt.get("abandon_unsealed") is True:
            require(
                receipt["schema"]
                in {"collector-pressure-operator-v1", "collector-pressure-operator-v2"}
                and receipt["operator_revalidated"] is True
                and self.pressure_metadata() == receipt["partial_metadata"]
                and (
                    receipt["schema"] != "collector-pressure-operator-v2"
                    or digest(
                        document(
                            Path(self.state["current"]["roots"]["spool"]) / "runtime-health.json"
                        )
                    )
                    == receipt["quarantined_runtime_sha256"]
                ),
                "Operator quarantine metadata changed.",
            )
        else:
            self.pressure_health()
        self.audit(complete=False)
        self.gap("classified-host-pressure-interruption")
        self.save("between-campaigns", "Stable host capacity and retained copies accepted.")
        return True

    def pressure_wait_guards(self) -> None:
        """Read-only periodic admission checks never renew old producer controls."""
        verify_release(self.settings)
        producer = self.verify_current()
        require(not producer["State"]["Running"], "Pressure wait producer unexpectedly running.")
        self.capacity_events(producer)
        for role, path in self.state["current"]["roots"].items():
            self.verify_store(role, Path(path))
        report = cast(
            dict[str, Any],
            parse_tracking(
                command(["chronyc", "-n", "tracking"], timeout=3), now_ns=time.time_ns()
            ),
        )
        require(
            report["synchronized"] is True
            and abs(float(report["system_offset_seconds"])) <= 0.050
            and 0 <= float(report["root_dispersion_seconds"]) <= 0.250
            and 0 <= float(report["root_delay_seconds"]) <= 0.500,
            "Host clock out of bounds while pressure waiting.",
        )

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
                    json.dumps(self.settings.profile.app),
                    json.dumps({"extra": {"retrieve_config": True}}),
                ]
            )
        )
        require(
            value["id"] == self.settings.profile.app and value["custom_app"] is True,
            "Managed Custom App required.",
        )
        native_app_config(value)
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
                    "label=com.docker.compose.project=" + self.settings.profile.project,
                ]
            ).split()
        )
        require(
            ids == {generation["collector_id"], generation["status_id"]},
            "Unexpected managed App container set.",
        )
        collector, status = (
            inspect(self.settings.profile.container("collector")),
            inspect(self.settings.profile.container("status")),
        )
        owned(collector, generation, "collector")
        owned(status, generation, "status")
        services = app["config"]["services"]
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
        collector, status = (
            inspect(self.settings.profile.container("collector")),
            inspect(self.settings.profile.container("status")),
        )
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
        value = inspect(self.settings.profile.container("collector"))
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
        # Observation never grants authority to mutate/stop the existing
        # producer, including when later identity or guard validation fails.
        generation["adopted"] = True
        plan = dict(generation["plan"])
        plan["lanes"] = tuple(plan["lanes"])
        require(
            RuntimePlan(**plan).instance == self.settings.instance,
            "Cross-instance adoption refused.",
        )
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
        self.stopped_capacity_baseline()
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
        require(re.fullmatch(r"[0-9a-f]{64}", cid) is not None, "Exact audit CID required.")
        try:
            value = inspect(cid)
        except HostUnavailableError:
            # Docker rm can finish before a crash prevents saving audit_id=None.
            # A successful exact-ID query proves absence without trusting a failed inspect.
            present = command(["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + cid])
            require(not present.strip(), "Registered audit identity could not be inspected.")
            self.state["audit_id"] = None
            self.save()
            return
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

    def accept_fast_pilot(self, expected_state_sha256: str) -> None:
        """Explicit operator acceptance; re-audit copies, never start capture here."""
        require(digest(self.state) == expected_state_sha256, "Explicit FAST state pin differs.")
        current = self.state.get("current")
        require(isinstance(current, dict), "Completed FAST pilot state required.")
        current = cast(dict[str, Any], current)
        require(
            self.settings.profile == FAST
            and not self.settings.first_create
            and not current.get("adopted")
            and self.settings.chunk_seconds == 7200
            and self.settings.chunks == 1
            and self.state["phase"] == "paused"
            and self.state.get("reason") == "FAST pilot closed; independent acceptance required."
            and current["plan"].get("instance") == FAST.name
            and current["plan"].get("l2_fast") is True
            and current["plan"]["pilot"] is True
            and current["plan"]["chunk_seconds"] == 600
            and current["plan"]["chunks"] == 1,
            "Only an independently reviewed completed FAST first pilot may be accepted.",
        )
        producer = self.verify_current()
        require(
            not producer["State"]["Running"] and producer["State"]["ExitCode"] == 0,
            "FAST pilot must be stopped successfully.",
        )
        health = document(Path(current["roots"]["spool"]) / "runtime-health.json")
        require(
            health.get("phase") == "completed"
            and health.get("campaign") == current["plan"]["campaign"]
            and health.get("instance") == FAST.name,
            "Completed FAST pilot health required.",
        )
        try:
            self.audit(complete=True)
            receipt = {
                "schema": "collector-fast-pilot-acceptance-v1",
                "instance": FAST.name,
                "campaign": current["plan"]["campaign"],
                "expected_state_sha256": expected_state_sha256,
                "accepted_utc_ns": time.time_ns(),
                "starts_capture": False,
            }
            publish(
                self.settings.state_root / (current["plan"]["campaign"] + "-acceptance.json"),
                receipt,
            )
            self.state["fast_pilot_acceptance"] = receipt
            self.save(
                "between-campaigns", "Explicit FAST pilot accepted; fresh admission required."
            )
        except BaseException:
            self.save("paused", "FAST pilot acceptance failed; evidence retained.")
            raise

    def verify_first_create(self) -> None:
        require(
            self.settings.first_create
            and self.settings.profile == FAST
            and not self.settings.adoption
            and self.state["current"] is None
            and self.state["generations"] == 0,
            "Fresh FAST create-only settings required.",
        )
        apps = json.loads(
            command(
                [
                    "midclt",
                    "call",
                    "app.query",
                    json.dumps([["id", "=", self.settings.profile.app]]),
                ]
            )
        )
        require(apps == [], "FAST App already exists; never overwrite during first-create.")
        ids = command(
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + self.settings.profile.project,
            ]
        )
        require(not ids.strip(), "FAST project already has containers.")

    def launch(self) -> None:
        creating = self.state["current"] is None
        if creating:
            self.verify_first_create()
        else:
            previous = self.verify_current()
            require(not previous["State"]["Running"], "Never replace an active admitted campaign.")
            for role, path in self.state["current"]["roots"].items():
                self.verify_store(role, Path(path))
        campaign = (
            self.settings.profile.campaign_prefix
            + time.strftime("%Y%m%d%H%M%S", time.gmtime())
            + "-"
            + uuid.uuid4().hex[:8]
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
                        self.settings.profile.project,
                        "-f",
                        str(self.settings.release / "deploy" / self.settings.profile.template),
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
        if creating:
            self.verify_first_create()
        else:
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
        self.last_clock = 0
        self.clock(generation)
        self.stopped_capacity_baseline()
        self.admission()
        self.state["current"] = generation
        self.state["generations"] += 1
        self.save("admitting")
        publish(
            self.settings.state_root / (campaign + "-intent.json"),
            {"generation": generation, "rendered_compose_sha256": digest(rendered)},
        )
        job = json.loads(
            self.poll_child(
                (
                    ["midclt", "call", "app.create", "-"]
                    if creating
                    else [
                        "midclt",
                        "call",
                        "app.update",
                        json.dumps(self.settings.profile.app),
                        "-",
                    ]
                ),
                stdin=json.dumps(
                    (
                        {"app_name": self.settings.profile.app, "custom_app": True}
                        if creating
                        else {}
                    )
                    | {"custom_compose_config": rendered}
                ),
                timeout=60,
            )
        )
        require(type(job) is int and job > 0, "TrueNAS App update job identity required.")
        generation["app_job_id"] = job
        self.save()
        for _ in range(90):
            self.tick()
            with contextlib.suppress(ValueError, KeyError):
                doc = inspect(self.settings.profile.container("collector"))
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
            "--instance",
            self.settings.instance,
            "--container",
            self.settings.profile.container("collector"),
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
                status = inspect(self.settings.profile.container("status"))
                owned(status, generation, "status")
                if status["State"]["Running"]:
                    generation["status_id"] = status["Id"]
                    break
        require(generation["status_id"] is not None, "Managed status container did not start.")
        self.save()
        app = self.app()
        require(
            app["config"] == rendered,
            "Actual stored TrueNAS Compose differs from admitted render.",
        )
        generation["app_config_sha256"] = digest(app["config"])
        self.save("capturing")
        if self.state.get("pressure_recovery"):
            self.state["pressure_recovery"]["pending"] = False
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
            self.state["pressure_retry_count"] = 0
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
                doc = inspect(self.settings.profile.container("collector"))
                owned(doc, generation, "collector")
            except (ValueError, KeyError, OSError, subprocess.SubprocessError):
                return
            generation["collector_id"] = doc["Id"]
            # A full quota must never prevent stopping an independently
            # verified own process merely because its receipt cannot be saved.
            with contextlib.suppress(OSError):
                self.save()
        else:
            doc = inspect(self.settings.profile.container("collector"))
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
            pressure = self.state.get("pressure_recovery")
            recovering = (
                self.state["phase"] in {"pressure-wait", "auditing", "between-campaigns"}
                and pressure
                and pressure.get("pending") is True
                and pressure.get("campaign") == self.state["current"]["plan"]["campaign"]
            )
            if recovering:
                self.pressure_receipt()
                self.state["phase"] = "pressure-wait"
            else:
                self.admission()
            if self.state["current"] is None:
                if self.settings.first_create:
                    self.launch()
                else:
                    self.adopt()
            notify("READY=1\nWATCHDOG=1")
            while not self.stopping:
                try:
                    if self.state["phase"] == "pressure-wait":
                        if self.wait_pressure():
                            self.launch()
                        continue
                    generation = self.state["current"]
                    if generation["boot_id"] != self.boot:
                        self.recover_boot()
                    if self.state["phase"] == "retry-wait":
                        if time.time_ns() < self.state["retry_after_utc_ns"]:
                            self.tick()
                            continue
                        self.launch()
                    elif self.state["phase"] == "between-campaigns" or self.observe():
                        if self.settings.first_create:
                            self.save(
                                "paused", "FAST pilot closed; independent acceptance required."
                            )
                            return 0
                        if self.state["phase"] == "retry-wait":
                            continue
                        self.launch()
                    self.tick()
                except CapacityStop as error:
                    self.record_capacity(error)
                    if error.reasons != ("memory-pressure",):
                        raise
                    self.begin_pressure_wait(error)
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
    operation.add_argument("--accept-fast-pilot", action="store_true")
    operation.add_argument("--pause", action="store_true")
    operation.add_argument("--recover-pressure", action="store_true")
    operation.add_argument("--inspect-pressure-recovery", action="store_true")
    parser.add_argument("--expected-state-sha256")
    parser.add_argument("--abandon-unsealed-pressure", action="store_true")
    args = parser.parse_args(argv)
    require(
        bool(args.recover_pressure or args.inspect_pressure_recovery or args.accept_fast_pilot)
        == bool(args.expected_state_sha256),
        "Explicit recovery/acceptance requires the expected state SHA256.",
    )
    require(
        not args.abandon_unsealed_pressure
        or args.recover_pressure
        or args.inspect_pressure_recovery,
        "Unsealed abandonment requires explicit pressure recovery.",
    )
    require(os.getuid() == 0, "Trusted root host operator required.")
    settings = load_settings(args.settings)
    if args.expected_state_sha256 is not None:
        # Refuse a stale/wrong operator pin before even creating the own lock file.
        # begin_pressure_wait checks again after acquiring the lock to close races.
        require(
            digest(Supervisor(settings).state) == args.expected_state_sha256,
            "Explicit pressure state pin differs.",
        )
    if args.pause:
        verify_release(settings, check_image=False)
    elif not args.stop_owned:
        wait_host_ready(settings)
    if args.inspect_only or args.inspect_pressure_recovery:
        supervisor = Supervisor(settings)
        if args.inspect_pressure_recovery:
            supervisor.begin_pressure_wait(
                expected_state_sha256=args.expected_state_sha256,
                abandon_unsealed=args.abandon_unsealed_pressure,
                read_only=True,
            )
        elif supervisor.state["current"] is None:
            if settings.first_create:
                supervisor.verify_first_create()
            else:
                supervisor.adopt(read_only=True)
        else:
            supervisor.verify_current()
        print(
            json.dumps(
                {
                    "schema": "collector-continuous-preflight-v1",
                    "read_only": True,
                    "campaign": (
                        supervisor.state["current"]["plan"]["campaign"]
                        if supervisor.state["current"]
                        else None
                    ),
                    "no_runtime_changes": True,
                }
            )
        )
        return 0
    with singleton(settings.state_root):
        supervisor = Supervisor(settings)
        if args.accept_fast_pilot:
            supervisor.accept_fast_pilot(args.expected_state_sha256)
            print(json.dumps({"fast_pilot_accepted": True, "starts_capture": False}))
            return 0
        if args.recover_pressure:
            try:
                supervisor.begin_pressure_wait(
                    expected_state_sha256=args.expected_state_sha256,
                    abandon_unsealed=args.abandon_unsealed_pressure,
                )
            except (ValueError, OSError, KeyError, subprocess.SubprocessError):
                if getattr(supervisor, "pressure_transition_started", False):
                    supervisor.save("paused", "Explicit pressure recovery admission failed.")
                    supervisor.stop_owned()
                raise
        if args.pause:
            cleanup_verified = True
            try:
                supervisor.save(
                    "paused", "Explicit persistent operator pause; revalidation required."
                )
            finally:
                try:
                    supervisor.stop_owned()
                except (ValueError, OSError, subprocess.SubprocessError):
                    cleanup_verified = False
            print(
                json.dumps(
                    {
                        "persistent_pause": True,
                        "owned_cleanup": "verified" if cleanup_verified else "unconfirmed",
                    }
                )
            )
            return 0 if cleanup_verified else 2
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
