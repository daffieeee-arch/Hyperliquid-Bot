"""Run all reviewed offline fragments; compare every business field to evidence.

Only the validated top-level runtime metadata may differ between machines.
A successful check reproduces counted PASS/HOLD observations, not execution fit.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fit_gates.hyperliquid_testnet_readiness.probe import (
    VERSION,
    deny_external_io,
    require_safe_environment,
)

ROOT = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class FragmentSpec:
    name: str
    artifact: str
    schema: str
    cases: tuple[str, ...]


SPECS = (
    FragmentSpec(
        "lifecycle",
        "lifecycle-evidence.json",
        "hyperliquid-offline-adapter-fragments-v1",
        ("ack", "reject", "partial", "timeout", "missing_ack"),
    ),
    FragmentSpec(
        "lifecycle_edges",
        "lifecycle-edges-evidence.json",
        "hyperliquid-offline-lifecycle-edges-v1",
        (
            "duplicate_fill",
            "fill_before_accept",
            "buffered_duplicate_fill",
            "ioc_partial_cancel",
            "late_partial_after_cancel",
            "cancel_before_fill",
            "cancel_before_accept_late_fill",
        ),
    ),
    FragmentSpec(
        "report_fragments",
        "report-evidence.json",
        "hyperliquid-offline-report-observations-v1",
        (
            *(
                f"{kind}:{case}"
                for kind in ("order", "orders", "fills", "positions")
                for case in (
                    "empty",
                    "nonempty",
                    "timeout",
                    "error",
                    "cancelled",
                    "conversion_failure",
                    "incomplete",
                    "wrong_account",
                    "wrong_instrument",
                )
            ),
            "orders:unavailable",
            "fills:unavailable",
            "positions:unavailable",
            "orders:partial_conversion_failure",
            "fills:partial_conversion_failure",
            "positions:partial_conversion_failure",
            "order:suppressed",
            "orders:suppressed",
            "order:missing_lookup",
            "order:wrong_order",
        ),
    ),
)
_EXPECTED_RUNNERS = ("lifecycle", "lifecycle_edges", "report_fragments")
_EXPECTED_COUNTS = (5, 7, 46)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence key.")
        result[key] = value
    return result


def _validate_json(value: object) -> None:
    """Preserve JSON scalars/keys; tuples encode as arrays like each runner's CLI."""
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings.")
            _validate_json(item)
    elif type(value) is list or type(value) is tuple:
        for item in value:
            _validate_json(item)
    elif value is not None and type(value) not in {str, int, float, bool}:
        raise ValueError("Observation contains a non-JSON value.")


def _canonical(value: object) -> str:
    _validate_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _runtime(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"python", "machine", "libc"}:
        raise ValueError("Runtime metadata shape differs.")
    for name in ("python", "machine"):
        field = value[name]
        if type(field) is not str or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", field):
            raise ValueError("Runtime metadata must contain bounded strings.")
    libc = value["libc"]
    if (
        type(libc) is not list
        or len(libc) != 2
        or any(
            type(field) is not str or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", field)
            for field in libc
        )
    ):
        raise ValueError("Runtime libc metadata must contain two bounded strings.")
    return value


def _observation(spec: FragmentSpec, value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("Fragment did not produce a JSON object.")
    _canonical(value)
    if (value.get("schema"), value.get("candidate_version"), value.get("decision")) != (
        spec.schema,
        VERSION,
        "HOLD",
    ):
        raise ValueError("Fragment identity or HOLD boundary differs.")
    cases = value.get("cases")
    if type(cases) is not list or len(cases) != len(spec.cases):
        raise ValueError("Fragment case count differs.")
    if any(type(case) is not dict or type(case.get("case")) is not str for case in cases):
        raise ValueError("Fragment case identities are malformed.")
    if {case["case"] for case in cases} != set(spec.cases):
        raise ValueError("Fragment cases are missing or duplicated.")
    _runtime(value.get("runtime"))
    return value


def compare_evidence(spec: FragmentSpec, actual: object, frozen: object) -> dict[str, Any]:
    observed = _observation(spec, actual)
    recorded = _observation(spec, frozen)
    # This is the entire exclusion list. Nested runtime/business fields remain
    # inside the comparison, including source hashes and negative classifications.
    observed_business = {key: value for key, value in observed.items() if key != "runtime"}
    recorded_business = {key: value for key, value in recorded.items() if key != "runtime"}
    if _canonical(observed_business) != _canonical(recorded_business):
        raise ValueError("Actual fragment business evidence differs from the frozen observation.")
    return {
        "runner": spec.name,
        "case_count": len(spec.cases),
        "counted_cases": [case["case"] for case in observed["cases"]],
        "known_conformance_hold_cases": (
            observed["failed_cases"] if spec.name == "lifecycle_edges" else []
        ),
        "comparison": "all-business-fields-match",
        "recorded_runtime": recorded["runtime"],
        "observed_runtime": observed["runtime"],
    }


def run() -> dict[str, Any]:
    require_safe_environment(os.environ)
    sys.addaudithook(deny_external_io)
    if (
        tuple(spec.name for spec in SPECS) != _EXPECTED_RUNNERS
        or tuple(len(spec.cases) for spec in SPECS) != _EXPECTED_COUNTS
    ):
        raise RuntimeError("The fixed offline fragment registry is incomplete.")
    comparisons: list[dict[str, Any]] = []
    for spec in SPECS:
        frozen = json.loads((ROOT / spec.artifact).read_text(), object_pairs_hook=_unique_object)
        _observation(spec, frozen)
        module = importlib.import_module(f"fit_gates.hyperliquid_testnet_readiness.{spec.name}")
        comparisons.append(compare_evidence(spec, module.run(), frozen))
    if len(comparisons) != 3 or sum(item["case_count"] for item in comparisons) != 58:
        raise RuntimeError("Not all required offline fragment observations executed.")
    return {
        "schema": "hyperliquid-offline-fragment-ci-v1",
        "comparison": "all-58-required-observations-reproduced",
        "decision": "HOLD",
        "new_risk_permitted": False,
        "all_safety_cases_pass": False,
        "excluded_fields": ["top-level runtime only; validated and recorded separately"],
        "runners": comparisons,
    }


def main() -> int:
    try:
        result = run()
    except Exception:
        print("Offline fragment CI failed; execution readiness remains HOLD.", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
