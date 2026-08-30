"""Create and verify the bounded D01 publication manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from typing import Final

import nautilus_trader

from vertical_slices.d01_btc_perp.slice import probe_completed_run

SCHEMA: Final = "d01-publication-integrity-v1"
PARENT_BASIS: Final = "cf50a77d99893f1f90cd8707ff41e0ce8c8211d9"
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
PUBLIC_RUN: Final = Path("vertical_slices/d01_btc_perp/runs/d01-publication-proof-20260830")
MANIFEST_PATH: Final = Path(
    "vertical_slices/d01_btc_perp/publication-integrity-d01-publication-proof-20260830.json"
)

RUN_FILE_NAMES: Final = (
    "completed-run.json",
    "paper.json",
    "replay-1.json",
    "replay-2.json",
    "run-claim.json",
)

PUBLICATION_PATHS: Final = (
    Path(".github/workflows/ci.yml"),
    Path("vertical_slices/__init__.py"),
    Path("vertical_slices/d01_btc_perp/README.md"),
    Path("vertical_slices/d01_btc_perp/__init__.py"),
    MANIFEST_PATH,
    Path("vertical_slices/d01_btc_perp/publication.py"),
    Path("vertical_slices/d01_btc_perp/run_slice.py"),
    Path("vertical_slices/d01_btc_perp/slice.py"),
    Path("vertical_slices/d01_btc_perp/test_slice.py"),
    *(PUBLIC_RUN / name for name in RUN_FILE_NAMES),
)

REUSED_RUNTIME_INPUTS: Final = (
    Path(".python-version"),
    Path("pyproject.toml"),
    Path("uv.lock"),
    Path("fit_gates/__init__.py"),
    Path("fit_gates/d41_nautilus/__init__.py"),
    Path("fit_gates/d41_nautilus/fit_gate.py"),
    Path("fit_gates/d41_nautilus/requirements.lock"),
    Path("fit_gates/d41_nautilus/runs/20260830T011412Z/dataset.json"),
    Path("src/hyperliquid_bot/__init__.py"),
    Path("src/hyperliquid_bot/contracts.py"),
    Path("src/hyperliquid_bot/local_mode.py"),
)

PROVES: Final = (
    "local deterministic replay -> credentialless sandbox-PAPER for one bounded BTC-PERP dataset",
)

DOES_NOT_PROVE: Final = (
    "live-public-data soak",
    "durable persistence",
    "mid-run recovery",
    "exactly-once venue submission",
    "venue reconciliation",
    "deployment readiness",
    "profitability",
)


def _canonical_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(relative_path: Path) -> dict[str, object]:
    path = REPOSITORY_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"D01 publication input is missing: {relative_path}")
    return {"bytes": path.stat().st_size, "sha256": _sha256_file(path)}


def _file_records(paths: tuple[Path, ...]) -> dict[str, object]:
    return {str(path): _file_record(path) for path in sorted(paths)}


def _dependency_pins() -> dict[str, str]:
    lock_path = REPOSITORY_ROOT / "fit_gates/d41_nautilus/requirements.lock"
    pins: dict[str, str] = {}
    for line_number, raw_line in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s=]+)", line)
        if match is None:
            raise ValueError(f"D01 requirement is not an exact pin at line {line_number}: {line!r}")
        name, version = match.groups()
        canonical_name = _canonical_distribution_name(name)
        if canonical_name in pins:
            raise ValueError(f"D01 dependency lock repeats {canonical_name}.")
        pins[canonical_name] = version
    if pins.get("nautilus-trader") != "1.231.0":
        raise ValueError("D01 dependency lock must contain nautilus-trader==1.231.0.")
    return dict(sorted(pins.items()))


def assert_installed_dependency_closure() -> None:
    """Prove that the current isolated environment is exactly the D41 lock closure."""

    expected = _dependency_pins()
    actual = {
        _canonical_distribution_name(str(distribution.metadata["Name"])): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    if actual != expected:
        missing = sorted(set(expected.items()) - set(actual.items()))
        extra = sorted(set(actual.items()) - set(expected.items()))
        raise RuntimeError(
            f"D01 installed dependency closure differs from the lock; missing={missing}, "
            f"extra={extra}"
        )
    if sys.version_info[:3] != (3, 13, 15):
        raise RuntimeError(f"D01 requires CPython 3.13.15, got {sys.version_info[:3]}.")
    module_path = Path(nautilus_trader.__file__).resolve()
    environment_root = Path(sys.prefix).resolve()
    if sys.prefix == sys.base_prefix or not module_path.is_relative_to(environment_root):
        raise RuntimeError("D01 NautilusTrader was not imported from the isolated environment.")
    if importlib.metadata.version("nautilus-trader") != "1.231.0":
        raise RuntimeError("D01 imported a NautilusTrader version other than 1.231.0.")


def publication_payload() -> dict[str, object]:
    """Recompute the complete, D01-specific publication payload from repository bytes."""

    run_directory = REPOSITORY_ROOT / PUBLIC_RUN
    actual_run_files = sorted(path.name for path in run_directory.iterdir() if path.is_file())
    if actual_run_files != sorted(RUN_FILE_NAMES):
        raise ValueError(f"D01 publication run has an unexpected file set: {actual_run_files}")
    restart = probe_completed_run(run_directory)
    if restart.get("decision") != "NOOP_ALREADY_COMPLETE":
        raise ValueError("D01 publication completion did not recompute as completed and flat.")
    pins = _dependency_pins()
    publication_files = tuple(path for path in PUBLICATION_PATHS if path != MANIFEST_PATH)
    return {
        "schema": SCHEMA,
        "parent_basis": PARENT_BASIS,
        "hash_algorithm": "sha256",
        "candidate_paths": [str(path) for path in sorted(PUBLICATION_PATHS)],
        "publication_files": _file_records(publication_files),
        "reused_runtime_inputs": _file_records(REUSED_RUNTIME_INPUTS),
        "run_binding": {
            "directory": str(PUBLIC_RUN),
            "completion_sha256": restart["source_completed_run_sha256"],
            "run_id": restart["run_id"],
            "run_identity": restart["run_identity"],
            "restart_probe": "NOOP_ALREADY_COMPLETE",
            "verification": (
                "The verifier reloads the dataset, start claim, both replays, PAPER result, "
                "and completion; it recomputes source/config/run identities, lifecycle "
                "relations, artifact hashes, fills, economics, and the completed-flat verdict."
            ),
        },
        "dependency_contract": {
            "python": "3.13.15",
            "uv": "0.12.5",
            "requirements": "fit_gates/d41_nautilus/requirements.lock",
            "requirements_sha256": _sha256_file(
                REPOSITORY_ROOT / "fit_gates/d41_nautilus/requirements.lock"
            ),
            "exact_version_pins": pins,
            "distribution_count": len(pins),
            "nautilus_trader": "1.231.0",
            "root_dependency_adoption": False,
            "package_bytes_hash_locked": False,
        },
        "claim_boundary": {
            "proves": list(PROVES),
            "does_not_prove": list(DOES_NOT_PROVE),
        },
        "manifest_self_hash": {
            "included": False,
            "binding": "The final Git commit and tree bind the manifest bytes.",
        },
    }


def write_manifest(path: Path) -> None:
    """Write the manifest once; publication evidence is never overwritten."""

    output = REPOSITORY_ROOT / path
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(publication_payload(), stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")


def verify_manifest(path: Path, *, check_installed_dependencies: bool) -> dict[str, object]:
    """Verify exact repository bytes and rederive every decisive run relationship."""

    manifest_path = REPOSITORY_ROOT / path
    observed = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = publication_payload()
    if observed != expected:
        raise ValueError("D01 publication manifest does not recompute exactly.")
    if check_installed_dependencies:
        assert_installed_dependency_closure()
    return {
        "schema": SCHEMA,
        "status": "VERIFIED",
        "manifest_sha256": _sha256_file(manifest_path),
        "candidate_path_count": len(PUBLICATION_PATHS),
        "dependency_distribution_count": len(_dependency_pins()),
        "installed_dependency_closure_checked": check_installed_dependencies,
        "run_id": expected["run_binding"]["run_id"],  # type: ignore[index]
        "claim": PROVES[0],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("write", "verify"))
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--check-installed-dependencies", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "write":
        if args.check_installed_dependencies:
            raise SystemExit("--check-installed-dependencies applies only to verify.")
        write_manifest(args.manifest)
        result: dict[str, object] = {"status": "WRITTEN", "manifest": str(args.manifest)}
    else:
        result = verify_manifest(
            args.manifest,
            check_installed_dependencies=args.check_installed_dependencies,
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
