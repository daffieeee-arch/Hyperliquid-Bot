"""Read one exact, local sandbox position; this is not venue reconciliation."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from hyperliquid_bot.paper_risk import PaperPositionKey, PaperSignedPosition


def sandbox_position_truth(strategy: Any, *, epoch: str) -> PaperSignedPosition | None:
    """Read the pinned sandbox cache without constructing another order store.

    A fresh owner epoch invalidates pre-restart decisions. Missing or conflicting
    cache/fill truth is unavailable, rather than an invented flat/zero-pending
    state. All orders in the account/instrument/strategy scope are considered,
    including partially filled orders and orders still awaiting acknowledgement.
    """

    require_local_paper_mode(None)
    if type(epoch) is not str or not epoch or epoch != epoch.strip() or not epoch.isprintable():
        return None
    try:
        instrument_id = strategy.config.instrument_id
        account = strategy.cache.account_for_venue(instrument_id.venue)
        if account is None:
            return None
        account_id = account.id
        key = PaperPositionKey(
            environment="PAPER",
            account_id=account_id.value,
            venue_id=instrument_id.venue.value,
            instrument_id=instrument_id.value,
            strategy_id=strategy.id.value,
        )
        if any(
            type(value) is not str or not value or value != value.strip() or not value.isprintable()
            for value in (key.account_id, key.venue_id, key.instrument_id, key.strategy_id)
        ):
            return None
        positions = strategy.cache.positions_open(
            instrument_id=instrument_id,
            strategy_id=strategy.id,
            account_id=account_id,
        )
        signed = Decimal("0")
        position_rows: list[tuple[str, str]] = []
        seen_positions: set[str] = set()
        for position in positions:
            if (
                position.account_id != account_id
                or position.instrument_id != instrument_id
                or position.strategy_id != strategy.id
            ):
                continue
            if (
                type(position.is_long) is not bool
                or type(position.is_short) is not bool
                or position.id.value in seen_positions
            ):
                return None
            seen_positions.add(position.id.value)
            quantity = Decimal(str(position.quantity))
            if (
                not quantity.is_finite()
                or quantity <= 0
                or position.account_id != account_id
                or position.instrument_id != instrument_id
                or position.strategy_id != strategy.id
                or position.is_long == position.is_short
            ):
                return None
            signed += quantity if position.is_long else -quantity
            position_rows.append(
                (position.id.value, str(quantity if position.is_long else -quantity))
            )
        if signed != strategy.signed_position_quantity:
            return None
        pending = Decimal("0")
        order_rows: list[tuple[str, str, str, str]] = []
        seen: set[str] = set()
        for order in strategy.cache.orders(
            instrument_id=instrument_id,
            strategy_id=strategy.id,
        ):
            if order.instrument_id != instrument_id or order.strategy_id != strategy.id:
                continue
            if order.account_id is None:
                return None
            if order.account_id != account_id:
                continue
            if type(order.is_closed) is not bool:
                return None
            if (
                order.client_order_id.value in seen
                or order.account_id != account_id
                or order.instrument_id != instrument_id
                or order.strategy_id != strategy.id
            ):
                return None
            seen.add(order.client_order_id.value)
            filled = Decimal(str(order.filled_qty))
            leaves = Decimal(str(order.leaves_qty))
            total = Decimal(str(order.quantity))
            if (
                not all(value.is_finite() for value in (filled, leaves, total))
                or filled < 0
                or leaves < 0
                or total <= 0
                or filled + leaves != total
            ):
                return None
            if not order.is_closed:
                pending += leaves
            order_rows.append(
                (order.client_order_id.value, str(order.status), str(filled), str(leaves))
            )
        version = hashlib.sha256(
            json.dumps(
                {"epoch": epoch, "positions": sorted(position_rows), "orders": sorted(order_rows)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return PaperSignedPosition(
            key=key,
            quantity=signed,
            version=version,
            pending_order_quantity=pending,
            reconciled=True,
        )
    except (AttributeError, TypeError, ValueError, ArithmeticError):
        return None
