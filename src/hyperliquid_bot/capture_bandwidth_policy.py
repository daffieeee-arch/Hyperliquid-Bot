"""Capture-environment bandwidth policy and measure hooks.

The entire Phase A capture environment is capped at <=100 Mbit/s download and
<=5 Mbit/s upload, and never more than 10% of the operator-measured real
upload. This module is the policy and conflict reporter. A monitor-with-stop
is not a hard limiter.

Process-scoped enforcement on Ubuntu/WSL uses ``trickle`` (userspace, per
command) when present. Whole-house or whole-WSL interface shapers are refused.
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

MBIT_TO_KIB_PER_SEC: Final = 1024.0 / 8.0
CAPTURE_DOWNLOAD_CAP_MBIT: Final = 100.0
CAPTURE_UPLOAD_CAP_MBIT: Final = 5.0
CAPTURE_UPLOAD_FRACTION_OF_MEASURED: Final = 0.10
MEASURE_HOOKS: Final = ("nethogs", "ss", "cgroup_io")

LimiterKind = Literal["trickle", "attested", "missing"]


class CaptureBandwidthPolicyError(ValueError):
    """Raised when the capture environment would exceed the documented cap."""


@dataclass(frozen=True, slots=True)
class CaptureBandwidthPolicy:
    """Hard caps for the joint capture process set, not the household uplink."""

    download_cap_mbit: float = CAPTURE_DOWNLOAD_CAP_MBIT
    upload_cap_mbit: float = CAPTURE_UPLOAD_CAP_MBIT
    measured_upload_mbit: float | None = None

    def __post_init__(self) -> None:
        if self.download_cap_mbit <= 0 or self.upload_cap_mbit <= 0:
            raise CaptureBandwidthPolicyError("bandwidth caps must be positive")
        if self.download_cap_mbit > CAPTURE_DOWNLOAD_CAP_MBIT:
            raise CaptureBandwidthPolicyError(
                "refusing to raise the capture download cap above 100 Mbit/s"
            )
        if self.upload_cap_mbit > CAPTURE_UPLOAD_CAP_MBIT:
            raise CaptureBandwidthPolicyError(
                "refusing to raise the capture upload cap above 5 Mbit/s"
            )
        if self.measured_upload_mbit is not None:
            if self.measured_upload_mbit <= 0:
                raise CaptureBandwidthPolicyError("measured upload must be positive")
            allowed = self.measured_upload_mbit * CAPTURE_UPLOAD_FRACTION_OF_MEASURED
            if self.upload_cap_mbit > allowed:
                raise CaptureBandwidthPolicyError(
                    "upload cap exceeds 10% of measured real upload; "
                    "report conflict rather than raising the budget"
                )

    @property
    def download_kib_per_sec(self) -> int:
        return max(1, math.floor(self.download_cap_mbit * MBIT_TO_KIB_PER_SEC))

    @property
    def upload_kib_per_sec(self) -> int:
        return max(1, math.floor(self.upload_cap_mbit * MBIT_TO_KIB_PER_SEC))


def effective_upload_cap_mbit(measured_upload_mbit: float | None) -> float:
    """Return min(5 Mbit/s, 10% of measured upload) when a measurement exists."""

    if measured_upload_mbit is None:
        return CAPTURE_UPLOAD_CAP_MBIT
    if measured_upload_mbit <= 0:
        raise CaptureBandwidthPolicyError("measured upload must be positive")
    return min(
        CAPTURE_UPLOAD_CAP_MBIT,
        measured_upload_mbit * CAPTURE_UPLOAD_FRACTION_OF_MEASURED,
    )


def detect_limiter_kind(*, attested: bool = False) -> LimiterKind:
    if attested:
        return "attested"
    if shutil.which("trickle") is not None:
        return "trickle"
    return "missing"


def trickle_argv(policy: CaptureBandwidthPolicy) -> tuple[str, ...]:
    """Process-scoped trickle wrapper. Not a whole-WSL or household shaper."""

    return (
        "trickle",
        "-s",
        "-d",
        str(policy.download_kib_per_sec),
        "-u",
        str(policy.upload_kib_per_sec),
    )


def report_rate_conflict(
    *,
    observed_download_mbit: float,
    observed_upload_mbit: float,
    policy: CaptureBandwidthPolicy | None = None,
) -> None:
    """Fail closed when a measured capture rate would exceed the cap.

    Callers must not drop samples, sleep in receive loops, or raise the budget.
    """

    resolved = policy if policy is not None else CaptureBandwidthPolicy()
    conflicts: list[str] = []
    if observed_download_mbit > resolved.download_cap_mbit:
        conflicts.append(
            f"download {observed_download_mbit:g} Mbit/s exceeds "
            f"{resolved.download_cap_mbit:g} Mbit/s"
        )
    if observed_upload_mbit > resolved.upload_cap_mbit:
        conflicts.append(
            f"upload {observed_upload_mbit:g} Mbit/s exceeds {resolved.upload_cap_mbit:g} Mbit/s"
        )
    if conflicts:
        raise CaptureBandwidthPolicyError(
            "capture bandwidth conflict; do not degrade quality or raise the cap: "
            + "; ".join(conflicts)
        )


def bandwidth_preflight(
    *,
    measured_upload_mbit: float | None = None,
    attested: bool = False,
    hl_addons_enabled: bool = False,
) -> dict[str, object]:
    """Operator-printable limiter status. Never prints secrets."""

    upload_cap = effective_upload_cap_mbit(measured_upload_mbit)
    policy = CaptureBandwidthPolicy(
        upload_cap_mbit=upload_cap,
        measured_upload_mbit=measured_upload_mbit,
    )
    limiter = detect_limiter_kind(attested=attested)
    addon_conflict = None
    if hl_addons_enabled:
        addon_conflict = (
            "Hyperliquid ETH/SOL add-ons stay deferred at Phase A start; "
            "enabling them is a documented bandwidth conflict until measured"
        )
    return {
        "schema": "phase-a-bandwidth-preflight-v1",
        "download_cap_mbit": policy.download_cap_mbit,
        "upload_cap_mbit": policy.upload_cap_mbit,
        "download_kib_per_sec": policy.download_kib_per_sec,
        "upload_kib_per_sec": policy.upload_kib_per_sec,
        "measured_upload_mbit": measured_upload_mbit,
        "limiter_kind": limiter,
        "limiter_hard": limiter != "missing",
        "measure_hooks": list(MEASURE_HOOKS),
        "monitor_is_not_limiter": True,
        "scope": "capture-processes-only",
        "whole_house_or_wsl_shaper": False,
        "hl_addons_enabled": hl_addons_enabled,
        "hl_addon_conflict": addon_conflict,
        "trickle_argv": list(trickle_argv(policy)) if limiter == "trickle" else [],
    }


def require_hard_limiter_for_72h(preflight: Mapping[str, object]) -> None:
    if preflight.get("limiter_hard") is not True:
        raise CaptureBandwidthPolicyError(
            "72h retain requires a process-scoped hard limiter (trickle or "
            "CAPTURE_BANDWIDTH_ENFORCED=attested). A monitor-with-stop is not enough."
        )
    if preflight.get("hl_addon_conflict"):
        raise CaptureBandwidthPolicyError(str(preflight["hl_addon_conflict"]))


def dumps_preflight(preflight: Mapping[str, object]) -> str:
    return json.dumps(dict(preflight), indent=2, sort_keys=True, ensure_ascii=True)


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(dumps_preflight(bandwidth_preflight()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
