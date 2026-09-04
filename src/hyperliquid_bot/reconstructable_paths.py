"""Create-only reconstructable path contract for DATA-1A and COURSE-1 Cockpit.

These paths are the only layout Cockpit should later read. The functions never
create directories, write files, or claim 24/7 service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

DATA1A_PATH_CONTRACT_ID: Final = "data-1a-hyperliquid-btc-perp-v1"
COURSE1_PATH_CONTRACT_ID: Final = "course1-live-public-paper-cockpit-v1"

DATA1A_VENUE: Final = "hyperliquid"
DATA1A_PRODUCT: Final = "BTC-PERP"
DATA1A_RELATIVE_PREFIX: Final = ("data-1a", DATA1A_VENUE, DATA1A_PRODUCT)
COURSE1_RELATIVE_PREFIX: Final = ("course1", "live-public-paper")

DATA1A_CLAIM_NAME: Final = "capture-claim.json"
DATA1A_HEALTH_NAME: Final = "capture-health.json"
DATA1A_RAW_DIR_NAME: Final = "raw"
DATA1A_DATABASE_NAME: Final = "research.duckdb"
DATA1A_PARQUET_GLOB: Final = "part-*.parquet"

COURSE1_RUN_CLAIM_NAME: Final = "run-claim.json"
COURSE1_PAPER_POSITION_NAME: Final = "paper-position.json"
COURSE1_PAPER_PNL_NAME: Final = "paper-pnl.json"
COURSE1_ORDERS_NAME: Final = "orders.json"
COURSE1_FILLS_NAME: Final = "fills.json"
COURSE1_CAPTURE_HEALTH_NAME: Final = "capture-health.json"
COURSE1_PAPER_NAME: Final = "paper.json"
COURSE1_PUBLIC_STREAM_NAME: Final = "public-stream.json"
COURSE1_COMPLETED_RUN_NAME: Final = "completed-run.json"

COURSE1_COCKPIT_FILE_NAMES: Final = (
    COURSE1_RUN_CLAIM_NAME,
    COURSE1_PAPER_POSITION_NAME,
    COURSE1_PAPER_PNL_NAME,
    COURSE1_ORDERS_NAME,
    COURSE1_FILLS_NAME,
    COURSE1_CAPTURE_HEALTH_NAME,
)

DATA1A_PATH_CONTRACT: Final = """\
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
"""

COURSE1_PATH_CONTRACT: Final = """\
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
  paper.json
  public-stream.json
  completed-run.json
"""


def require_run_id(run_id: object) -> str:
    """Accept the same create-only run-id alphabet as the COURSE-1 soak."""

    if type(run_id) is not str:
        raise TypeError("run_id must be a built-in string.")
    text = run_id
    if (
        not text
        or len(text) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in text)
    ):
        raise ValueError(
            "run_id must be 1-64 lowercase ASCII letters, digits, dot, dash or underscore."
        )
    return text


def require_artifact_root(artifact_root: object) -> Path:
    if not isinstance(artifact_root, Path):
        raise TypeError("artifact_root must be a pathlib.Path.")
    if not artifact_root.parts:
        raise ValueError("artifact_root must be a non-empty path.")
    return artifact_root


@dataclass(frozen=True, slots=True)
class Data1ARunPaths:
    """Resolved DATA-1A reconstructable layout for one public BTC-PERP run."""

    contract_id: str
    run_id: str
    artifact_root: Path
    run_dir: Path
    raw_dir: Path
    database_path: Path
    capture_claim_path: Path
    capture_health_path: Path
    parquet_glob: str


@dataclass(frozen=True, slots=True)
class Course1CockpitPaths:
    """Resolved COURSE-1 create-only layout Cockpit should later read."""

    contract_id: str
    run_id: str
    artifact_root: Path
    run_dir: Path
    run_claim_path: Path
    paper_position_path: Path
    paper_pnl_path: Path
    orders_path: Path
    fills_path: Path
    capture_health_path: Path
    paper_path: Path
    public_stream_path: Path
    completed_run_path: Path


def data1a_run_paths(artifact_root: Path, run_id: str) -> Data1ARunPaths:
    """Return the reconstructable DATA-1A directory contract. Create-only later."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*DATA1A_RELATIVE_PREFIX, identity)
    raw_dir = run_dir / DATA1A_RAW_DIR_NAME
    return Data1ARunPaths(
        contract_id=DATA1A_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        database_path=run_dir / DATA1A_DATABASE_NAME,
        capture_claim_path=run_dir / DATA1A_CLAIM_NAME,
        capture_health_path=run_dir / DATA1A_HEALTH_NAME,
        parquet_glob=DATA1A_PARQUET_GLOB,
    )


def course1_cockpit_paths(artifact_root: Path, run_id: str) -> Course1CockpitPaths:
    """Return the reconstructable COURSE-1 Cockpit directory contract."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*COURSE1_RELATIVE_PREFIX, identity)
    return Course1CockpitPaths(
        contract_id=COURSE1_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        run_claim_path=run_dir / COURSE1_RUN_CLAIM_NAME,
        paper_position_path=run_dir / COURSE1_PAPER_POSITION_NAME,
        paper_pnl_path=run_dir / COURSE1_PAPER_PNL_NAME,
        orders_path=run_dir / COURSE1_ORDERS_NAME,
        fills_path=run_dir / COURSE1_FILLS_NAME,
        capture_health_path=run_dir / COURSE1_CAPTURE_HEALTH_NAME,
        paper_path=run_dir / COURSE1_PAPER_NAME,
        public_stream_path=run_dir / COURSE1_PUBLIC_STREAM_NAME,
        completed_run_path=run_dir / COURSE1_COMPLETED_RUN_NAME,
    )
