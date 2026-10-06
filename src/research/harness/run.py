"""Lock a spec, then run it. The sha256 is fixed before any metric is computed."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hyperliquid_bot.local_mode import UnsafeTradingModeError, require_local_paper_mode
from research.harness.data import fingerprint_inputs, load_bars
from research.harness.errors import HarnessError, LockError, SpecError
from research.harness.evaluate import decide
from research.harness.report import (
    completed_document,
    dump_json,
    failure_document,
    render_markdown,
    source_environment,
)
from research.harness.spec import (
    Json,
    load_document,
    spec_sha256,
    validate_spec,
    verify_lock,
    write_lock,
)

_EXIT_OK = 0
_EXIT_FAILED = 2


@dataclass(frozen=True, slots=True)
class RunOutcome:
    exit_code: int
    document: dict[str, Json]


def lock_spec(spec_path: Path) -> tuple[Path, str]:
    """Hash-lock a spec and the declared input. Does not score the hypothesis."""

    _require_paper()
    document = load_document(spec_path)
    spec = validate_spec(document)
    fingerprint = fingerprint_inputs(spec, spec_path.parent)
    return write_lock(spec_path, fingerprint)


def execute(spec_path: Path, output_dir: Path) -> RunOutcome:
    """Run one locked spec and write result.json plus result.md."""

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _require_paper()
        source_environment()
    except HarnessError as error:
        mode = _mode_token() if error.failure_kind == "unsafe_mode" else "PAPER"
        return _emit(output_dir, _failure(error.failure_kind, (str(error),), None, None, mode))

    digest: str | None = None
    hypothesis_id: str | None = None
    try:
        document = load_document(spec_path)
        digest = spec_sha256(document)
        spec = validate_spec(document)
        hypothesis_id = spec.hypothesis_id
        locked_fingerprint = verify_lock(spec_path, document, digest)
        fingerprint = fingerprint_inputs(spec, spec_path.parent)
        if fingerprint != locked_fingerprint:
            raise LockError("Data fingerprint differs from the lock. Re-lock the spec before run.")
        table = load_bars(spec, spec_path.parent)
        decision = decide(spec, table)
        payload = completed_document(spec, digest, table, decision, fingerprint)
    except (SpecError, LockError, HarnessError) as error:
        payload = _failure(error.failure_kind, (str(error),), digest, hypothesis_id, "PAPER")
    _write_pair(output_dir, payload)
    exit_code = _EXIT_OK if payload.get("status") == "completed" else _EXIT_FAILED
    return RunOutcome(exit_code=exit_code, document=payload)


def _require_paper() -> None:
    try:
        require_local_paper_mode(os.environ.get("TRADING_MODE"))
    except UnsafeTradingModeError as error:
        raise HarnessError("unsafe_mode", str(error)) from error


def _failure(
    failure_kind: str,
    reasons: tuple[str, ...],
    digest: str | None,
    hypothesis_id: str | None,
    trading_mode: str,
) -> dict[str, Json]:
    return failure_document(
        failure_kind=failure_kind,
        reasons=reasons,
        spec_sha256=digest,
        hypothesis_id=hypothesis_id,
        trading_mode=trading_mode,
    )


def _emit(output_dir: Path, document: dict[str, Json]) -> RunOutcome:
    _write_pair(output_dir, document)
    return RunOutcome(exit_code=_EXIT_FAILED, document=document)


def _write_pair(output_dir: Path, document: dict[str, Json]) -> None:
    _atomic(output_dir / "result.json", dump_json(document))
    _atomic(output_dir / "result.md", render_markdown(document))


def _atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _mode_token() -> str:
    raw = os.environ.get("TRADING_MODE")
    if isinstance(raw, str) and 0 < len(raw) <= 32 and raw.isascii() and raw.isidentifier():
        return raw
    return "rejected"
