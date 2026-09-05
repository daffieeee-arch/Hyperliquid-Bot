"""Deterministic COURSE-1 exit-gate evidence verifier.

Reconstructs final PAPER position and assumed cash/PnL from committed soak
artifacts. No Nautilus engine, no network, no signing. Fail-closed on mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, cast

SCHEMA: Final = "course1-exit-gate-evidence-v1"
COCKPIT_SCHEMA: Final = "course1-cockpit-paper-artifacts-v1"
PATH_CONTRACT: Final = "course1-live-public-paper-cockpit-v1"
CLAIM_SCHEMA: Final = "course1-live-public-paper-v1"
COMPLETION_SCHEMA: Final = "course1-live-public-paper-completed-v1"
PAPER_SCHEMA: Final = "course1-live-public-paper-v1"

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_DIR: Final = Path("tests/fixtures/course1_cockpit/live-public-soak")

# Project-owned D41 overlay assumptions (stdlib mirror; no fit_gate import).
STARTING_CASH_USDC: Final = Decimal(100000)
TAKER_FEE_BPS: Final = Decimal("4.5")
HALF_SPREAD_BPS: Final = Decimal("0.5")
SLIPPAGE_BPS: Final = Decimal("1.0")
BPS_DENOMINATOR: Final = Decimal(10000)

ARTIFACT_FILE_NAMES: Final = (
    "run-claim.json",
    "paper-position.json",
    "paper-pnl.json",
    "orders.json",
    "fills.json",
    "capture-health.json",
    "paper.json",
    "public-stream.json",
    "completed-run.json",
)

INTEGRITY_SOURCE_PATHS: Final = (
    Path(".github/workflows/ci.yml"),
    Path("src/hyperliquid_bot/course1_cockpit_artifacts.py"),
    Path("src/hyperliquid_bot/reconstructable_paths.py"),
    Path("vertical_slices/course1_exit_gate/__init__.py"),
    Path("vertical_slices/course1_exit_gate/verify.py"),
    Path("tests/python/test_course1_exit_gate.py"),
)

PROVES: Final = (
    (
        "Committed live-public PAPER soak artifacts independently reconstruct "
        "the final PAPER position and assumed cash/PnL state from fills plus "
        "the D41 cost overlay (fee, half-spread, slippage, funding=0)."
    ),
    (
        "Cross-artifact fail-closed invariants hold for the committed soak "
        "fixture: mode PAPER, assumed overlay PnL, no signing, no D22-B, "
        "twenty_four_seven false, schema/path_contract and count bindings."
    ),
    (
        "Soak survival is evidenced by the committed live-public-soak fixture "
        "bytes (not by a live network call inside this verifier or CI job)."
    ),
)

DOES_NOT_PROVE: Final = (
    "profitability or strategy promotion",
    "TESTNET readiness",
    "SHADOW readiness",
    "LIVE readiness",
    "24/7 collection or service",
    "funding settlement (funding remains the modeled zero payment)",
    "D22-B venue-authoritative reconciliation",
    "live-network soak inside CI (soak survival is via committed artifacts only)",
    "definitive runtime ADR or VPS migration readiness",
)


class ExitGateError(RuntimeError):
    """Raised when COURSE-1 exit-gate evidence fails closed."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ExitGateError(f"Exit-gate input is missing: {path}")
    return {"bytes": path.stat().st_size, "sha256": _sha256_file(path)}


def _read_object(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if type(raw) is not dict:
        raise ExitGateError(f"Expected a JSON object at {path}.")
    return cast(dict[str, object], raw)


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ExitGateError(f"{field_name} must be a non-empty string.")
    return value


def _require_bool(value: object, *, field_name: str, expected: bool) -> bool:
    if value is not expected:
        raise ExitGateError(f"{field_name} must be {expected}.")
    return expected


def _require_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise ExitGateError(f"{field_name} must be a built-in integer.")
    return value


def _require_list_of_objects(value: object, *, field_name: str) -> list[dict[str, object]]:
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise ExitGateError(f"{field_name} must be a list of JSON objects.")
    return [cast(dict[str, object], item) for item in cast(list[object], value)]


def _decimal_field(mapping: Mapping[str, object], field_name: str) -> Decimal:
    value = mapping.get(field_name)
    if type(value) is not str:
        raise ExitGateError(f"{field_name} must be a decimal string.")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ExitGateError(f"{field_name} is not a valid decimal: {value!r}") from exc


def _signed_quantity(*, side: str, quantity: Decimal) -> Decimal:
    if side == "BUY":
        return quantity
    if side == "SELL":
        return -quantity
    raise ExitGateError(f"Unsupported fill side: {side!r}")


def reconstruct_from_fills(
    fills: Sequence[Mapping[str, object]],
    *,
    mark_price: Decimal,
    starting_cash: Decimal,
    funding_payment: Decimal,
) -> dict[str, Decimal]:
    """Independently reconstruct position and assumed equity from fills.

    Identity checked by callers:
    starting + realized_pnl_from_fills - fee - half_spread - slippage + funding
    == ending_equity, where realized_pnl_from_fills is trade cash delta plus MTM,
    and funding is the signed cash contribution (-funding_payment).
    """

    cash = starting_cash
    position = Decimal(0)
    total_fee = Decimal(0)
    total_spread = Decimal(0)
    total_slippage = Decimal(0)
    realized_trade_cash = Decimal(0)
    for fill in fills:
        side = _require_text(fill.get("side"), field_name="fill.side")
        quantity = _decimal_field(fill, "quantity")
        price = _decimal_field(fill, "price")
        signed = _signed_quantity(side=side, quantity=quantity)
        notional = quantity * price
        fee = notional * TAKER_FEE_BPS / BPS_DENOMINATOR
        spread = notional * HALF_SPREAD_BPS / BPS_DENOMINATOR
        slippage = notional * SLIPPAGE_BPS / BPS_DENOMINATOR
        trade_cash = -signed * price
        cash += trade_cash
        cash -= fee + spread + slippage
        position += signed
        realized_trade_cash += trade_cash
        total_fee += fee
        total_spread += spread
        total_slippage += slippage
    # funding_payment is a cost when positive; signed funding contribution is negative.
    cash -= funding_payment
    funding_signed = -funding_payment
    mark_to_market = position * mark_price
    ending_equity = cash + mark_to_market
    realized_pnl_from_fills = realized_trade_cash + mark_to_market
    return {
        "starting_cash_usdc_assumed": starting_cash,
        "realized_pnl_from_fills": realized_pnl_from_fills,
        "fee_cost_usdc": total_fee,
        "half_spread_cost_usdc": total_spread,
        "slippage_cost_usdc": total_slippage,
        "funding_payment_usdc": funding_payment,
        "funding_signed_usdc": funding_signed,
        "final_position_btc": position,
        "ending_cash_usdc_assumed": cash,
        "ending_equity_usdc_assumed": ending_equity,
        "mark_price": mark_price,
        "net_pnl_usdc_assumed": ending_equity - starting_cash,
    }


def _assert_equal_decimal(actual: Decimal, expected: Decimal, *, field_name: str) -> None:
    if actual != expected:
        raise ExitGateError(f"{field_name} mismatch: reconstructed={actual} stored={expected}")


def _assert_mode_paper(mapping: Mapping[str, object], *, field_name: str) -> None:
    if mapping.get("mode") != "PAPER":
        raise ExitGateError(f"{field_name} must be PAPER.")


def _integrity_manifest(
    *,
    artifact_dir: Path,
    repository_root: Path,
) -> dict[str, object]:
    artifact_records = {name: _file_record(artifact_dir / name) for name in ARTIFACT_FILE_NAMES}
    source_records: dict[str, object] = {}
    for relative in INTEGRITY_SOURCE_PATHS:
        absolute = repository_root / relative
        if absolute.is_file():
            source_records[str(relative)] = _file_record(absolute)
    return {
        "hash_algorithm": "sha256",
        "artifact_files": artifact_records,
        "source_files": source_records,
    }


def verify_artifacts(
    artifact_dir: Path,
    *,
    repository_root: Path | None = None,
) -> dict[str, object]:
    """Verify COURSE-1 exit-gate evidence from create-only committed artifacts."""

    root = REPOSITORY_ROOT if repository_root is None else repository_root
    if not artifact_dir.is_dir():
        raise ExitGateError(f"Artifact directory is missing: {artifact_dir}")

    paths = {name: artifact_dir / name for name in ARTIFACT_FILE_NAMES}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ExitGateError(f"Exit-gate refuses incomplete artifacts: {missing}")

    claim = _read_object(paths["run-claim.json"])
    position = _read_object(paths["paper-position.json"])
    pnl = _read_object(paths["paper-pnl.json"])
    orders = _read_object(paths["orders.json"])
    fills_doc = _read_object(paths["fills.json"])
    health = _read_object(paths["capture-health.json"])
    paper = _read_object(paths["paper.json"])
    completion = _read_object(paths["completed-run.json"])

    for doc, schema, label in (
        (position, COCKPIT_SCHEMA, "paper-position"),
        (pnl, COCKPIT_SCHEMA, "paper-pnl"),
        (orders, COCKPIT_SCHEMA, "orders"),
        (fills_doc, COCKPIT_SCHEMA, "fills"),
        (health, COCKPIT_SCHEMA, "capture-health"),
    ):
        if doc.get("schema") != schema:
            raise ExitGateError(f"{label}.schema must be {schema}.")
        if doc.get("path_contract") != PATH_CONTRACT:
            raise ExitGateError(f"{label}.path_contract must be {PATH_CONTRACT}.")

    if claim.get("schema") != CLAIM_SCHEMA:
        raise ExitGateError(f"run-claim.schema must be {CLAIM_SCHEMA}.")
    if paper.get("schema") != PAPER_SCHEMA:
        raise ExitGateError(f"paper.schema must be {PAPER_SCHEMA}.")
    if completion.get("schema") != COMPLETION_SCHEMA:
        raise ExitGateError(f"completed-run.schema must be {COMPLETION_SCHEMA}.")

    _assert_mode_paper(claim, field_name="run-claim.mode")
    _assert_mode_paper(position, field_name="paper-position.mode")
    _assert_mode_paper(pnl, field_name="paper-pnl.mode")
    _assert_mode_paper(orders, field_name="orders.mode")
    _assert_mode_paper(fills_doc, field_name="fills.mode")

    execution = paper.get("execution")
    if type(execution) is not dict:
        raise ExitGateError("paper.execution must be a JSON object.")
    execution_obj = cast(dict[str, object], execution)
    _assert_mode_paper(execution_obj, field_name="paper.execution.mode")
    _require_bool(
        execution_obj.get("signing"), field_name="paper.execution.signing", expected=False
    )
    _require_bool(execution_obj.get("live"), field_name="paper.execution.live", expected=False)
    _require_bool(
        execution_obj.get("testnet"), field_name="paper.execution.testnet", expected=False
    )
    _require_bool(execution_obj.get("shadow"), field_name="paper.execution.shadow", expected=False)
    _require_bool(
        execution_obj.get("venue_orders_submitted"),
        field_name="paper.execution.venue_orders_submitted",
        expected=False,
    )
    _require_bool(
        execution_obj.get("venue_authoritative_reconciliation"),
        field_name="paper.execution.venue_authoritative_reconciliation",
        expected=False,
    )
    _require_bool(
        claim.get("d22b_venue_authoritative_reconciliation"),
        field_name="run-claim.d22b_venue_authoritative_reconciliation",
        expected=False,
    )
    _require_bool(
        position.get("venue_authoritative"),
        field_name="paper-position.venue_authoritative",
        expected=False,
    )
    _require_bool(
        position.get("twenty_four_seven"),
        field_name="paper-position.twenty_four_seven",
        expected=False,
    )
    _require_bool(
        health.get("twenty_four_seven"),
        field_name="capture-health.twenty_four_seven",
        expected=False,
    )
    _require_bool(pnl.get("assumed"), field_name="paper-pnl.assumed", expected=True)
    _require_bool(pnl.get("venue_pnl"), field_name="paper-pnl.venue_pnl", expected=False)
    _require_bool(
        orders.get("venue_orders_submitted"),
        field_name="orders.venue_orders_submitted",
        expected=False,
    )

    order_rows = _require_list_of_objects(orders.get("orders"), field_name="orders.orders")
    fill_rows = _require_list_of_objects(fills_doc.get("fills"), field_name="fills.fills")
    order_count = _require_int(orders.get("order_count"), field_name="orders.order_count")
    fill_count = _require_int(fills_doc.get("fill_count"), field_name="fills.fill_count")
    if order_count != len(order_rows):
        raise ExitGateError("orders.order_count does not equal len(orders).")
    if fill_count != len(fill_rows):
        raise ExitGateError("fills.fill_count does not equal len(fills).")

    stored_starting = _decimal_field(pnl, "starting_cash_usdc_assumed")
    if stored_starting != STARTING_CASH_USDC:
        raise ExitGateError(
            f"starting_cash_usdc_assumed must be {STARTING_CASH_USDC}, got {stored_starting}."
        )
    stored_funding = _decimal_field(pnl, "funding_payment_usdc")
    if stored_funding != Decimal(0):
        raise ExitGateError("COURSE-1 exit-gate requires modeled funding_payment_usdc == 0.")
    mark_price = _decimal_field(pnl, "mark_price")
    stored_ending_equity = _decimal_field(pnl, "ending_equity_usdc_assumed")
    stored_ending_cash = _decimal_field(pnl, "ending_cash_usdc_assumed")
    stored_fee = _decimal_field(pnl, "fee_cost_usdc")
    stored_spread = _decimal_field(pnl, "half_spread_cost_usdc")
    stored_slippage = _decimal_field(pnl, "slippage_cost_usdc")
    stored_net = _decimal_field(pnl, "net_pnl_usdc_assumed")
    stored_position = _decimal_field(position, "final_position_btc")
    stored_pnl_position = _decimal_field(pnl, "final_position_btc")

    reconstructed = reconstruct_from_fills(
        fill_rows,
        mark_price=mark_price,
        starting_cash=stored_starting,
        funding_payment=stored_funding,
    )
    _assert_equal_decimal(
        reconstructed["fee_cost_usdc"],
        stored_fee,
        field_name="fee_cost_usdc",
    )
    _assert_equal_decimal(
        reconstructed["half_spread_cost_usdc"],
        stored_spread,
        field_name="half_spread_cost_usdc",
    )
    _assert_equal_decimal(
        reconstructed["slippage_cost_usdc"],
        stored_slippage,
        field_name="slippage_cost_usdc",
    )
    _assert_equal_decimal(
        reconstructed["ending_cash_usdc_assumed"],
        stored_ending_cash,
        field_name="ending_cash_usdc_assumed",
    )
    _assert_equal_decimal(
        reconstructed["ending_equity_usdc_assumed"],
        stored_ending_equity,
        field_name="ending_equity_usdc_assumed",
    )
    _assert_equal_decimal(
        reconstructed["net_pnl_usdc_assumed"],
        stored_net,
        field_name="net_pnl_usdc_assumed",
    )
    _assert_equal_decimal(
        reconstructed["final_position_btc"],
        stored_position,
        field_name="paper-position.final_position_btc",
    )
    _assert_equal_decimal(
        reconstructed["final_position_btc"],
        stored_pnl_position,
        field_name="paper-pnl.final_position_btc",
    )

    # Core exit-gate identity (Decimal-only; funding_signed is -funding_payment).
    identity_equity = (
        reconstructed["starting_cash_usdc_assumed"]
        + reconstructed["realized_pnl_from_fills"]
        - reconstructed["fee_cost_usdc"]
        - reconstructed["half_spread_cost_usdc"]
        - reconstructed["slippage_cost_usdc"]
        + reconstructed["funding_signed_usdc"]
    )
    _assert_equal_decimal(
        identity_equity,
        stored_ending_equity,
        field_name="exit-gate cash/PnL identity",
    )

    overlay = paper.get("cost_overlay")
    if type(overlay) is not dict:
        raise ExitGateError("paper.cost_overlay must be a JSON object.")
    overlay_obj = cast(dict[str, object], overlay)
    for field_name in (
        "starting_cash_usdc_assumed",
        "ending_cash_usdc_assumed",
        "ending_equity_usdc_assumed",
        "net_pnl_usdc_assumed",
        "fee_cost_usdc",
        "half_spread_cost_usdc",
        "slippage_cost_usdc",
        "funding_payment_usdc",
        "mark_price",
        "final_position_btc",
    ):
        if overlay_obj.get(field_name) != pnl.get(field_name):
            raise ExitGateError(f"paper.cost_overlay.{field_name} disagrees with paper-pnl.")

    business = paper.get("business")
    if type(business) is not dict:
        raise ExitGateError("paper.business must be a JSON object.")
    business_obj = cast(dict[str, object], business)
    business_fills = _require_list_of_objects(
        business_obj.get("fills"),
        field_name="paper.business.fills",
    )
    business_intents = _require_list_of_objects(
        business_obj.get("order_intents"),
        field_name="paper.business.order_intents",
    )
    if business_fills != fill_rows:
        raise ExitGateError("fills.json does not match paper.business.fills.")
    if business_intents != order_rows:
        raise ExitGateError("orders.json does not match paper.business.order_intents.")
    business_position = _decimal_field(business_obj, "final_position_quantity")
    _assert_equal_decimal(
        business_position,
        stored_position,
        field_name="paper.business.final_position_quantity",
    )

    run_id = _require_text(claim.get("run_id"), field_name="run-claim.run_id")
    if completion.get("run_id") != run_id:
        raise ExitGateError("completed-run.run_id does not match run-claim.run_id.")
    if paper.get("run_identity") != claim.get("run_identity"):
        raise ExitGateError("paper.run_identity does not match run-claim.run_identity.")

    integrity = _integrity_manifest(artifact_dir=artifact_dir, repository_root=root)
    return {
        "schema": SCHEMA,
        "status": "VERIFIED",
        "artifact_dir": str(artifact_dir),
        "run_id": run_id,
        "reconstruction": {
            "starting_cash_usdc_assumed": str(reconstructed["starting_cash_usdc_assumed"]),
            "realized_pnl_from_fills": str(reconstructed["realized_pnl_from_fills"]),
            "fee_cost_usdc": str(reconstructed["fee_cost_usdc"]),
            "half_spread_cost_usdc": str(reconstructed["half_spread_cost_usdc"]),
            "slippage_cost_usdc": str(reconstructed["slippage_cost_usdc"]),
            "funding_payment_usdc": str(reconstructed["funding_payment_usdc"]),
            "funding_signed_usdc": str(reconstructed["funding_signed_usdc"]),
            "final_position_btc": str(reconstructed["final_position_btc"]),
            "ending_equity_usdc_assumed": str(reconstructed["ending_equity_usdc_assumed"]),
            "net_pnl_usdc_assumed": str(reconstructed["net_pnl_usdc_assumed"]),
            "cost_assumptions": {
                "taker_fee_bps": str(TAKER_FEE_BPS),
                "half_spread_bps_per_fill": str(HALF_SPREAD_BPS),
                "slippage_bps_per_fill": str(SLIPPAGE_BPS),
                "funding_payment_usdc": "0",
            },
        },
        "invariants": {
            "mode": "PAPER",
            "assumed_pnl": True,
            "venue_pnl": False,
            "signing": False,
            "d22b_venue_authoritative_reconciliation": False,
            "twenty_four_seven": False,
            "order_count": order_count,
            "fill_count": fill_count,
            "path_contract": PATH_CONTRACT,
            "cockpit_schema": COCKPIT_SCHEMA,
        },
        "integrity": integrity,
        "proves": list(PROVES),
        "does_not_prove": list(DOES_NOT_PROVE),
        "claim_boundary": {
            "proves": list(PROVES),
            "does_not_prove": list(DOES_NOT_PROVE),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify COURSE-1 exit-gate evidence from committed artifacts.",
    )
    parser.add_argument("command", choices=("verify",))
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="Directory with committed COURSE-1 soak Cockpit artifacts.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifact_dir = args.artifact_dir
    if not artifact_dir.is_absolute():
        artifact_dir = REPOSITORY_ROOT / artifact_dir
    try:
        result = verify_artifacts(artifact_dir, repository_root=REPOSITORY_ROOT)
    except ExitGateError as exc:
        failure = {
            "schema": SCHEMA,
            "status": "FAILED",
            "error": str(exc),
            "proves": [],
            "does_not_prove": list(DOES_NOT_PROVE),
        }
        print(json.dumps(failure, ensure_ascii=True, sort_keys=True, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
