"""Create-only PAPER run directory.

A ``run_id`` is a single new directory. If it already exists, opening it
raises and nothing is appended. There is no resume API. Human-facing times in
the health file use Europe/Amsterdam; numeric state stays decimal text and UTC.

``ledger.jsonl`` is the complete audit trail. Lines appended while one event
is processed are buffered and written together by ``commit()``, which the
engine calls before it rewrites the projections. With ``durable=True`` that
write, the run claim, and the new directory entries are fsynced, so a
committed event survives a crash or power loss. ``state.json`` and
``health.json`` are projections rewritten on every event. They are replaced
atomically but never fsynced, and ``state.json`` keeps only the most recent
records so its size does not grow with run length.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Final

from hyperliquid_bot.paper_engine.errors import RunAlreadyExistsError

HEALTH_SCHEMA: Final = "paper-engine-health-v1"
STATE_SCHEMA: Final = "paper-engine-state-v2"
STATE_RECENT_RECORD_LIMIT: Final = 100
CLAIM_SCHEMA: Final = "paper-engine-run-claim-v1"
_RUN_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def validate_run_id(run_id: str) -> str:
    """Reject path-like or empty run identifiers before any directory is created."""

    if type(run_id) is not str or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("run_id must match ^[a-z0-9][a-z0-9._-]{0,63}$ so it cannot be a path.")
    return run_id


class RunStore:
    """Append-only ledger plus replaceable state and health projections."""

    def __init__(self, root: Path, run_id: str, *, durable: bool = True) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path.")
        if type(durable) is not bool:
            raise TypeError("durable must be a bool.")
        self.durable = durable
        self._pending: list[str] = []
        self.run_id = validate_run_id(run_id)
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_dir = self.root / self.run_id
        try:
            self.run_dir.mkdir(parents=False, exist_ok=False)
        except FileExistsError as exc:
            raise RunAlreadyExistsError(
                f"run_id {self.run_id!r} already exists and cannot be resumed."
            ) from exc
        self.claim_path = self.run_dir / "run-claim.json"
        self.ledger_path = self.run_dir / "ledger.jsonl"
        self.state_path = self.run_dir / "state.json"
        self.health_path = self.run_dir / "health.json"
        self.ledger_path.touch()

    def write_claim(self, payload: dict[str, object]) -> None:
        _write_json(self.claim_path, payload, durable=self.durable)
        if self.durable:
            # Persist the run directory entry and its claim/ledger entries.
            _fsync_directory(self.run_dir)
            _fsync_directory(self.root)
            _fsync_directory(self.root.parent)

    def append(self, payload: dict[str, object]) -> None:
        """Buffer one ledger line. ``commit()`` writes it."""

        self._pending.append(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    def commit(self) -> None:
        """Write buffered ledger lines in one append, fsynced when durable."""

        if not self._pending:
            return
        text = "".join(self._pending)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            # Clear only after the write, so a failed write can be retried.
            self._pending.clear()
            if self.durable:
                handle.flush()
                os.fsync(handle.fileno())

    def write_state(self, payload: dict[str, object]) -> None:
        _write_json(self.state_path, payload)

    def write_health(self, payload: dict[str, object]) -> None:
        _write_json(self.health_path, payload)


def read_health(path: Path) -> dict[str, object]:
    """Read a health file. This does not open a run and cannot resume one."""

    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("health file must be a JSON object.")
    if payload.get("schema") != HEALTH_SCHEMA:
        raise ValueError("health schema is not paper-engine-health-v1.")
    if payload.get("mode") != "PAPER":
        raise ValueError("health file is not a PAPER run.")
    if payload.get("venue_orders_submitted") is not False:
        raise ValueError("health file must record that no venue order was submitted.")
    return payload


def _write_json(path: Path, payload: dict[str, object], *, durable: bool = False) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        if durable:
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(temporary, path)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
