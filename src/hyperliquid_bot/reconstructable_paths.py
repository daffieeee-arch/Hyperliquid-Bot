"""Create-only reconstructable path contracts for DATA-1A/B/C/D/E/F and COURSE-1.

These paths are the only layout Cockpit should later read. The functions never
create directories, write files, or claim 24/7 service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

DATA1A_PATH_CONTRACT_ID: Final = "data-1a-hyperliquid-btc-perp-v1"
DATA1B_PATH_CONTRACT_ID: Final = "data-1b-kraken-btc-usd-v1"
DATA1B_RETIRED_PATH_CONTRACT_ID: Final = "data-1b-kraken-btc-eur-v1"
DATA1C_PATH_CONTRACT_ID: Final = "data-1c-okx-btc-usdt-swap-v1"
DATA1D_PATH_CONTRACT_ID: Final = "data-1d-bitvavo-btc-eur-v1"
DATA1E_PATH_CONTRACT_ID: Final = "data-1e-bitvavo-btc-eur-v1"
DATA1F_PATH_CONTRACT_ID: Final = "data-1f-binance-btcusdt-v1"
COURSE1_PATH_CONTRACT_ID: Final = "course1-live-public-paper-cockpit-v1"

DATA1A_VENUE: Final = "hyperliquid"
DATA1A_PRODUCT: Final = "BTC-PERP"
DATA1A_RELATIVE_PREFIX: Final = ("data-1a", DATA1A_VENUE, DATA1A_PRODUCT)
DATA1B_VENUE: Final = "kraken"
DATA1B_PRODUCT: Final = "BTC-USD"
DATA1B_WIRE_PRODUCT: Final = "BTC/USD"
DATA1B_RETIRED_PRODUCT: Final = "BTC-EUR"
DATA1B_RETIRED_WIRE_PRODUCT: Final = "BTC/EUR"
DATA1B_RELATIVE_PREFIX: Final = ("data-1b", DATA1B_VENUE, DATA1B_PRODUCT)
DATA1B_RETIRED_RELATIVE_PREFIX: Final = ("data-1b", DATA1B_VENUE, DATA1B_RETIRED_PRODUCT)
DATA1C_VENUE: Final = "okx"
DATA1C_PRODUCT: Final = "BTC-USDT-SWAP"
DATA1C_RELATIVE_PREFIX: Final = ("data-1c", DATA1C_VENUE, DATA1C_PRODUCT)
DATA1D_VENUE: Final = "bitvavo"
DATA1D_PRODUCT: Final = "BTC-EUR"
DATA1D_RELATIVE_PREFIX: Final = ("data-1d", DATA1D_VENUE, DATA1D_PRODUCT)
DATA1E_VENUE: Final = "bitvavo"
DATA1E_PRODUCT: Final = "BTC-EUR"
DATA1E_RELATIVE_PREFIX: Final = ("data-1e", DATA1E_VENUE, DATA1E_PRODUCT)
DATA1F_VENUE: Final = "binance"
DATA1F_PRODUCT: Final = "BTCUSDT"
DATA1F_RELATIVE_PREFIX: Final = ("data-1f", DATA1F_VENUE, DATA1F_PRODUCT)
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

DATA1B_PATH_CONTRACT: Final = """\
<artifact-root>/data-1b/kraken/BTC-USD/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
"""

DATA1C_PATH_CONTRACT: Final = """\
<artifact-root>/data-1c/okx/BTC-USDT-SWAP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
"""

DATA1D_PATH_CONTRACT: Final = """\
<artifact-root>/data-1d/bitvavo/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
"""

DATA1E_PATH_CONTRACT: Final = """\
<artifact-root>/data-1e/bitvavo/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
"""

DATA1F_PATH_CONTRACT: Final = """\
<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/
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
class Data1BRunPaths:
    """Resolved DATA-1B reconstructable layout for one Kraken BTC-USD run."""

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
class Data1CRunPaths:
    """Resolved DATA-1C reconstructable layout for one public OKX BTC-USDT-SWAP run."""

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
class Data1DRunPaths:
    """Resolved DATA-1D reconstructable layout for one Bitvavo Standard BTC-EUR run."""

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
class Data1ERunPaths:
    """Resolved DATA-1E reconstructable layout for one Bitvavo MD Pro BTC-EUR run."""

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
class Data1FRunPaths:
    """Resolved DATA-1F reconstructable layout for one public BTCUSDT run."""

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


def data1b_retired_eur_run_dir(artifact_root: Path, run_id: str) -> Path:
    """Return the retired EUR layout. Never treat this as the current DATA-1B contract."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    return root.joinpath(*DATA1B_RETIRED_RELATIVE_PREFIX, identity)


def data1b_run_paths(artifact_root: Path, run_id: str) -> Data1BRunPaths:
    """Return the reconstructable DATA-1B directory contract. Create-only later."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*DATA1B_RELATIVE_PREFIX, identity)
    raw_dir = run_dir / DATA1A_RAW_DIR_NAME
    return Data1BRunPaths(
        contract_id=DATA1B_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        database_path=run_dir / DATA1A_DATABASE_NAME,
        capture_claim_path=run_dir / DATA1A_CLAIM_NAME,
        capture_health_path=run_dir / DATA1A_HEALTH_NAME,
        parquet_glob=DATA1A_PARQUET_GLOB,
    )


def data1c_run_paths(artifact_root: Path, run_id: str) -> Data1CRunPaths:
    """Return the reconstructable DATA-1C directory contract. Create-only later."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*DATA1C_RELATIVE_PREFIX, identity)
    raw_dir = run_dir / DATA1A_RAW_DIR_NAME
    return Data1CRunPaths(
        contract_id=DATA1C_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        database_path=run_dir / DATA1A_DATABASE_NAME,
        capture_claim_path=run_dir / DATA1A_CLAIM_NAME,
        capture_health_path=run_dir / DATA1A_HEALTH_NAME,
        parquet_glob=DATA1A_PARQUET_GLOB,
    )


def data1d_run_paths(artifact_root: Path, run_id: str) -> Data1DRunPaths:
    """Return the reconstructable DATA-1D directory contract. Never DATA-1E Pro."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*DATA1D_RELATIVE_PREFIX, identity)
    raw_dir = run_dir / DATA1A_RAW_DIR_NAME
    return Data1DRunPaths(
        contract_id=DATA1D_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        database_path=run_dir / DATA1A_DATABASE_NAME,
        capture_claim_path=run_dir / DATA1A_CLAIM_NAME,
        capture_health_path=run_dir / DATA1A_HEALTH_NAME,
        parquet_glob=DATA1A_PARQUET_GLOB,
    )


def data1e_run_paths(artifact_root: Path, run_id: str) -> Data1ERunPaths:
    """Return the reconstructable DATA-1E directory contract. Create-only later."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*DATA1E_RELATIVE_PREFIX, identity)
    raw_dir = run_dir / DATA1A_RAW_DIR_NAME
    return Data1ERunPaths(
        contract_id=DATA1E_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        database_path=run_dir / DATA1A_DATABASE_NAME,
        capture_claim_path=run_dir / DATA1A_CLAIM_NAME,
        capture_health_path=run_dir / DATA1A_HEALTH_NAME,
        parquet_glob=DATA1A_PARQUET_GLOB,
    )


def data1f_run_paths(artifact_root: Path, run_id: str) -> Data1FRunPaths:
    """Return the reconstructable DATA-1F directory contract. Create-only later."""

    root = require_artifact_root(artifact_root)
    identity = require_run_id(run_id)
    run_dir = root.joinpath(*DATA1F_RELATIVE_PREFIX, identity)
    raw_dir = run_dir / DATA1A_RAW_DIR_NAME
    return Data1FRunPaths(
        contract_id=DATA1F_PATH_CONTRACT_ID,
        run_id=identity,
        artifact_root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        database_path=run_dir / DATA1A_DATABASE_NAME,
        capture_claim_path=run_dir / DATA1A_CLAIM_NAME,
        capture_health_path=run_dir / DATA1A_HEALTH_NAME,
        parquet_glob=DATA1A_PARQUET_GLOB,
    )


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
