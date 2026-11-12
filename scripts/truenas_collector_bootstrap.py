"""Supported TrueNAS POSTINIT entry: start only the project host supervisor.

Stage this script with its sibling src/deploy tree in a root-owned persistent
project release. Registration is explicit; importing it performs no operation.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
from pathlib import Path

UNIT = "hyperliquid-continuous-supervisor.service"
COMMENT = "Hyperliquid public collector continuous supervisor (project-owned)"


def trusted(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Absolute frozen paths required.")
    for current in (path, *path.parents):
        value = current.lstat()
        if stat.S_ISLNK(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022:
            raise ValueError("Root-owned nonwritable bootstrap ancestry required.")


def call(argv: list[str]) -> str:
    result = subprocess.run(
        argv,
        check=True,
        text=True,
        capture_output=True,
        timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    return result.stdout


def start_argv(
    release: Path, settings: Path, state_root: Path, recovery: tuple[str, ...] = ()
) -> list[str]:
    entry = [
        "/usr/bin/env",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "TRADING_MODE=PAPER",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONPATH=" + str(release / "src"),
        "/usr/bin/python3",
        "-B",
        "-m",
        "hyperliquid_bot.collector_continuous",
        "--settings",
        str(settings),
    ]
    return [
        "systemd-run",
        "--no-block",
        "--unit=" + UNIT,
        "--property=Type=notify",
        "--property=NotifyAccess=main",
        "--property=After=docker.service middlewared.service",
        "--property=Requires=docker.service middlewared.service",
        "--property=WatchdogSec=5s",
        "--property=TimeoutStartSec=300s",
        "--property=TimeoutStopSec=100s",
        "--property=TimeoutAbortSec=5s",
        "--property=Restart=no",
        "--property=LimitCORE=0",
        "--property=NoNewPrivileges=yes",
        "--property=ProtectSystem=strict",
        "--property=ReadWritePaths=" + str(state_root.parent),
        "--property=PrivateTmp=yes",
        "--property=StandardOutput=null",
        "--property=StandardError=journal",
        "--property=LogRateLimitIntervalSec=60s",
        "--property=LogRateLimitBurst=100",
        "--property=ExecStopPost=" + shlex.join([*entry, "--stop-owned"]),
        *entry,
        *recovery,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--register-postinit", action="store_true")
    parser.add_argument("--recover-pressure", action="store_true")
    parser.add_argument("--expected-state-sha256")
    parser.add_argument("--abandon-unsealed-pressure", action="store_true")
    args = parser.parse_args()
    if (
        bool(args.recover_pressure) != bool(args.expected_state_sha256)
        or (args.abandon_unsealed_pressure and not args.recover_pressure)
        or (args.register_postinit and args.recover_pressure)
    ):
        parser.error(
            "Pressure recovery requires a state pin and cannot be registered for POSTINIT."
        )
    if os.getuid() != 0:
        parser.error("Trusted root host operation required.")
    script = Path(__file__).absolute()
    release = script.parent.parent
    trusted(script)
    trusted(args.settings)
    with args.settings.open() as stream:
        settings = json.load(stream)
    if settings["release"] != str(release):
        raise ValueError("Settings and bootstrap frozen releases differ.")
    state_root = Path(settings["state_root"])
    trusted(state_root)
    if args.register_postinit:
        command = shlex.join(
            ["/usr/bin/python3", "-B", str(script), "--settings", str(args.settings)]
        )
        scripts = json.loads(call(["midclt", "call", "initshutdownscript.query"]))
        existing = [item for item in scripts if item["comment"] == COMMENT]
        intent = {
            "type": "COMMAND",
            "command": command,
            "when": "POSTINIT",
            "enabled": True,
            "timeout": 60,
            "comment": COMMENT,
        }
        if existing:
            if len(existing) != 1 or any(
                existing[0].get(key) != value for key, value in intent.items()
            ):
                raise ValueError("Existing project POSTINIT differs; operator review required.")
        else:
            call(["midclt", "call", "initshutdownscript.create", json.dumps(intent)])
        print(json.dumps({"postinit_registered": True, "starts_now": False}))
        return 0
    active = call(["systemctl", "show", UNIT, "--property=ActiveState", "--value"]).strip()
    if active in {"active", "activating", "deactivating"}:
        raise ValueError("Project supervisor unit already exists; refusing a second start.")
    # A previous same-boot failure is retained, not reset/restarted automatically.
    loaded = call(["systemctl", "show", UNIT, "--property=LoadState", "--value"]).strip()
    if loaded != "not-found":
        raise ValueError("Existing supervisor unit needs operator revalidation.")
    recovery: tuple[str, ...] = ()
    if args.recover_pressure:
        recovery = ("--recover-pressure", "--expected-state-sha256", args.expected_state_sha256)
        if args.abandon_unsealed_pressure:
            recovery += ("--abandon-unsealed-pressure",)
    call(start_argv(release, args.settings, state_root, recovery))
    print(json.dumps({"unit": UNIT, "start_requested": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
