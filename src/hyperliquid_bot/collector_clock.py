"""Parse host-owned chrony evidence; collection never adjusts the system clock."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path

from .collector_storage import exclusive_json, private_directory


def parse_tracking(text: str, *, now_ns: int) -> dict[str, object]:
    pairs = [line.split(":", 1) for line in text.splitlines() if ":" in line]
    if len({key.strip() for key, _value in pairs}) != len(pairs):
        raise ValueError("Duplicate chrony tracking field.")
    values = dict(pairs)
    fields = {key.strip(): value.strip() for key, value in values.items()}
    system = re.fullmatch(
        r"([0-9.]+) seconds (fast|slow) of NTP time", fields.get("System time", "")
    )
    if system is None:
        raise ValueError("Chrony offset evidence unavailable.")
    result: dict[str, object] = {
        "schema": "collector-host-clock-v1",
        "sampled_utc_ns": now_ns,
        "synchronized": fields.get("Leap status") == "Normal",
        "system_offset_seconds": float(system[1]) * (1 if system[2] == "fast" else -1),
    }
    for label, key in (
        ("Root delay", "root_delay_seconds"),
        ("Root dispersion", "root_dispersion_seconds"),
    ):
        value = re.fullmatch(r"([0-9.]+) seconds", fields.get(label, ""))
        if value is None:
            raise ValueError("Chrony uncertainty evidence unavailable.")
        result[key] = float(value[1])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 259_200 or not args.output.is_absolute():
        parser.error("Use an absolute output path and an explicit duration up to 72h.")
    os.umask(0o077)
    private_directory(args.output.parent)
    if args.output.is_symlink() or (args.output.exists() and not args.output.is_file()):
        parser.error("Clock output must be a regular private file.")
    deadline = time.monotonic() + args.duration_seconds
    while time.monotonic() < deadline:
        # No account data or exchange configuration is read or forwarded.
        response = subprocess.run(
            ["chronyc", "-n", "tracking"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        report = parse_tracking(response.stdout, now_ns=time.time_ns())
        temporary = args.output.with_name(args.output.name + ".tmp")
        exclusive_json(temporary, report)
        temporary.replace(args.output)
        time.sleep(min(30, max(0, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
