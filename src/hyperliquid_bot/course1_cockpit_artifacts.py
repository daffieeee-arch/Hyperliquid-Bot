"""Create-only COURSE-1 PAPER projections Cockpit can later read.

These helpers copy assumed overlay economics and observed soak stream health.
They do not invent fills, positions, or PnL, and they do not claim 24/7 service.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

from .reconstructable_paths import (
    COURSE1_CAPTURE_HEALTH_NAME,
    COURSE1_FILLS_NAME,
    COURSE1_ORDERS_NAME,
    COURSE1_PAPER_PNL_NAME,
    COURSE1_PAPER_POSITION_NAME,
    COURSE1_PATH_CONTRACT_ID,
)

COCKPIT_ARTIFACT_SCHEMA: Final = "course1-cockpit-paper-artifacts-v1"

_POSITION_OVERLAY_FIELDS: Final = ("final_position_btc",)
_PNL_OVERLAY_FIELDS: Final = (
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
)


def _require_object(value: object, *, field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise TypeError(f"{field_name} must be a JSON object.")
    return cast(dict[str, object], value)


def _require_list_of_objects(value: object, *, field_name: str) -> list[dict[str, object]]:
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise TypeError(f"{field_name} must be a list of JSON objects.")
    return [cast(dict[str, object], item) for item in cast(list[object], value)]


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{field_name} must be a non-empty string.")
    return value


def _require_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    return value


def _copy_required_strings(
    source: Mapping[str, object],
    *,
    field_names: tuple[str, ...],
    source_name: str,
) -> dict[str, str]:
    copied: dict[str, str] = {}
    for field_name in field_names:
        value = source.get(field_name)
        if type(value) is not str:
            raise ValueError(f"{source_name}.{field_name} must be a string overlay field.")
        copied[field_name] = value
    return copied


def paper_position_artifact(paper: Mapping[str, object]) -> dict[str, object]:
    """Project the assumed sandbox PAPER position. Not venue truth."""

    execution = _require_object(paper.get("execution"), field_name="execution")
    business = _require_object(paper.get("business"), field_name="business")
    overlay = _require_object(paper.get("cost_overlay"), field_name="cost_overlay")
    overlay_fields = _copy_required_strings(
        overlay,
        field_names=_POSITION_OVERLAY_FIELDS,
        source_name="cost_overlay",
    )
    if execution.get("mode") != "PAPER":
        raise ValueError("Cockpit position artifacts exist only for PAPER runs.")
    return {
        "schema": COCKPIT_ARTIFACT_SCHEMA,
        "kind": "paper-position",
        "path_contract": COURSE1_PATH_CONTRACT_ID,
        "mode": "PAPER",
        "instrument_id": _require_text(
            execution.get("instrument_id"),
            field_name="execution.instrument_id",
        ),
        "final_position_btc": overlay_fields["final_position_btc"],
        "business_final_position_quantity": _require_text(
            business.get("final_position_quantity"),
            field_name="business.final_position_quantity",
        ),
        "venue_authoritative": False,
        "twenty_four_seven": False,
        "limitations": [
            "Sandbox PAPER position only; no venue account truth.",
            "D22-B venue-authoritative reconciliation is not implemented.",
            "This is not 24/7 service evidence.",
        ],
    }


def paper_pnl_artifact(paper: Mapping[str, object]) -> dict[str, object]:
    """Project assumed overlay cash/PnL. Never invent a second PnL number."""

    execution = _require_object(paper.get("execution"), field_name="execution")
    overlay = _require_object(paper.get("cost_overlay"), field_name="cost_overlay")
    overlay_fields = _copy_required_strings(
        overlay,
        field_names=_PNL_OVERLAY_FIELDS,
        source_name="cost_overlay",
    )
    if execution.get("mode") != "PAPER":
        raise ValueError("Cockpit PnL artifacts exist only for PAPER runs.")
    if overlay_fields["funding_payment_usdc"] != "0":
        raise ValueError("COURSE-1 overlay funding must remain the modeled zero payment.")
    return {
        "schema": COCKPIT_ARTIFACT_SCHEMA,
        "kind": "paper-pnl",
        "path_contract": COURSE1_PATH_CONTRACT_ID,
        "mode": "PAPER",
        "assumed": True,
        "venue_pnl": False,
        "instrument_id": _require_text(
            execution.get("instrument_id"),
            field_name="execution.instrument_id",
        ),
        **overlay_fields,
        "limitations": [
            "PnL is the D01 assumed overlay, not venue or broker PnL.",
            "Funding remains 0 because no settlement boundary is modeled.",
            "USD is an explicit 1:1 USDC accounting proxy.",
            "This is not profitability, TESTNET, SHADOW, or LIVE evidence.",
            "This is not 24/7 service evidence.",
        ],
    }


def orders_artifact(paper: Mapping[str, object]) -> dict[str, object]:
    """Copy observed PAPER order intents. Empty is valid; invented orders are not."""

    business = _require_object(paper.get("business"), field_name="business")
    execution = _require_object(paper.get("execution"), field_name="execution")
    orders = _require_list_of_objects(
        business.get("order_intents"),
        field_name="business.order_intents",
    )
    if execution.get("venue_orders_submitted") is not False:
        raise ValueError("Cockpit order artifacts refuse a venue-submission claim.")
    return {
        "schema": COCKPIT_ARTIFACT_SCHEMA,
        "kind": "orders",
        "path_contract": COURSE1_PATH_CONTRACT_ID,
        "mode": "PAPER",
        "venue_orders_submitted": False,
        "order_count": len(orders),
        "orders": orders,
        "limitations": [
            "These are sandbox PAPER intents, not Hyperliquid venue orders.",
            "Signing and LIVE submission are out of scope.",
        ],
    }


def fills_artifact(paper: Mapping[str, object]) -> dict[str, object]:
    """Copy observed PAPER fills. Empty is valid; invented fills are not."""

    business = _require_object(paper.get("business"), field_name="business")
    fills = _require_list_of_objects(business.get("fills"), field_name="business.fills")
    return {
        "schema": COCKPIT_ARTIFACT_SCHEMA,
        "kind": "fills",
        "path_contract": COURSE1_PATH_CONTRACT_ID,
        "mode": "PAPER",
        "fill_count": len(fills),
        "fills": fills,
        "limitations": [
            "These are sandbox PAPER fills, not venue-authoritative fills.",
            "D22-B reconciliation is not implemented.",
        ],
    }


def capture_health_artifact(stream: Mapping[str, object]) -> dict[str, object]:
    """Project soak stream health. This is not a 24/7 collector heartbeat."""

    status = _require_text(stream.get("status"), field_name="stream.status")
    if status not in {"COMPLETED_FLAT", "BOUNDED_TIMEOUT", "RISK_REJECTED"}:
        raise ValueError("Capture-health status is outside the COURSE-1 soak bound.")
    acknowledged = stream.get("subscriptions_acknowledged")
    if type(acknowledged) is not list or any(type(item) is not str for item in acknowledged):
        raise TypeError("subscriptions_acknowledged must be a list of strings.")
    return {
        "schema": COCKPIT_ARTIFACT_SCHEMA,
        "kind": "capture-health",
        "path_contract": COURSE1_PATH_CONTRACT_ID,
        "feed": _require_text(stream.get("feed"), field_name="stream.feed"),
        "status": status,
        "credentialless": stream.get("credentialless") is True,
        "twenty_four_seven": False,
        "trade_count": _require_int(stream.get("trade_count"), field_name="stream.trade_count"),
        "bbo_count": _require_int(stream.get("bbo_count"), field_name="stream.bbo_count"),
        "adapter_rejected_count": _require_int(
            stream.get("adapter_rejected_count"),
            field_name="stream.adapter_rejected_count",
        ),
        "risk_rejections": _require_int(
            stream.get("risk_rejections"),
            field_name="stream.risk_rejections",
        ),
        "subscriptions_acknowledged": list(cast(list[str], acknowledged)),
        "limitations": [
            "Bounded soak health only; not 24/7 feed reliability.",
            "Public Hyperliquid trades/BBO only; no credentials or signing.",
            "Adapter-rejected ticks are stale/future/precision skips, not silent fills.",
        ],
    }


def project_cockpit_artifacts(
    *,
    paper: Mapping[str, object],
    stream: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """Return the create-only Cockpit files derived from soak paper/stream."""

    return {
        COURSE1_PAPER_POSITION_NAME: paper_position_artifact(paper),
        COURSE1_PAPER_PNL_NAME: paper_pnl_artifact(paper),
        COURSE1_ORDERS_NAME: orders_artifact(paper),
        COURSE1_FILLS_NAME: fills_artifact(paper),
        COURSE1_CAPTURE_HEALTH_NAME: capture_health_artifact(stream),
    }
