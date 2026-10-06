"""PAPER fill math. No venue client, no network, no synthetic mids.

Fills are aggressive (taker) against a real touch or a real trade print.

- A buy uses the ask. A sell uses the bid. If that side is missing, there is
  no fill.
- Slippage worsens the touch, then the price is rounded to the Hyperliquid
  perp grid in the adverse direction.
- Fee is ``fill_price * fill_quantity * taker_fee_rate``. The default rate is
  the published base-tier perp taker fee, 0.045% (0.00045). Maker is 0.015%.
  https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
- Size above the displayed touch (or the trade size) is a partial fill. The
  remainder is cancelled. This is an IOC touch model, not a queue model.
- Hyperliquid BTC-PERP is a linear USDC-margined contract: cash PnL is
  ``signed_quantity * (fill_price - average_entry)``. Funding is not settled.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from hyperliquid_bot.paper_engine.precision import adverse_price, floor_size

HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE: Final = Decimal("0.00045")
HYPERLIQUID_PERP_BASE_MAKER_FEE_RATE: Final = Decimal("0.00015")

FillSource = Literal["bbo", "trade"]
OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True, slots=True)
class FillQuote:
    """One deterministic fill. Fee is the exact decimal product, not a float."""

    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee_usdc: Decimal
    source: FillSource


@dataclass(frozen=True, slots=True)
class PositionState:
    """Linear-perp cash ledger. Fees reduce cash and are also tracked apart."""

    cash_usdc: Decimal
    position_quantity: Decimal
    average_entry_price: Decimal
    realized_pnl_usdc: Decimal
    fees_usdc: Decimal


def quote_taker_fill(
    *,
    side: str,
    quantity: Decimal,
    touch_price: Decimal | None,
    touch_size: Decimal | None,
    slippage_fraction: Decimal,
    taker_fee_rate: Decimal,
    size_increment: Decimal,
    max_price_decimals: int,
    source: FillSource,
) -> FillQuote | None:
    """Price a taker fill against one real touch or trade. Missing touch -> None."""

    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be exactly BUY or SELL.")
    if type(quantity) is not Decimal or not quantity.is_finite() or quantity <= 0:
        raise ValueError("quantity must be a finite positive decimal.")
    if touch_price is None or touch_size is None:
        return None
    if type(touch_price) is not Decimal or type(touch_size) is not Decimal:
        raise ValueError("touch price and size must be decimals when present.")
    if not touch_price.is_finite() or touch_price <= 0:
        return None
    if not touch_size.is_finite() or touch_size <= 0:
        return None
    if (
        type(slippage_fraction) is not Decimal
        or not slippage_fraction.is_finite()
        or slippage_fraction < 0
    ):
        raise ValueError("slippage_fraction must be a finite non-negative decimal.")
    if type(taker_fee_rate) is not Decimal or not taker_fee_rate.is_finite() or taker_fee_rate < 0:
        raise ValueError("taker_fee_rate must be a finite non-negative decimal.")
    order_side: OrderSide = "BUY" if side == "BUY" else "SELL"
    slipped = (
        touch_price * (Decimal(1) + slippage_fraction)
        if order_side == "BUY"
        else touch_price * (Decimal(1) - slippage_fraction)
    )
    if slipped <= 0:
        return None
    price = adverse_price(slipped, side=order_side, max_decimals=max_price_decimals)
    filled = floor_size(min(quantity, touch_size), size_increment)
    if filled <= 0:
        return None
    fee = price * filled * taker_fee_rate
    return FillQuote(
        side=order_side,
        quantity=filled,
        price=price,
        fee_usdc=fee,
        source=source,
    )


def apply_fill(state: PositionState, fill: FillQuote) -> PositionState:
    """Apply one linear-perp fill. Increasing, reducing, and flipping are explicit."""

    if fill.side == "BUY":
        signed = fill.quantity
        cash = state.cash_usdc - (fill.price * fill.quantity) - fill.fee_usdc
    else:
        signed = -fill.quantity
        cash = state.cash_usdc + (fill.price * fill.quantity) - fill.fee_usdc
    old_position = state.position_quantity
    new_position = old_position + signed
    realized = state.realized_pnl_usdc
    average = state.average_entry_price
    if old_position == 0 or _same_direction(old_position, signed):
        new_abs = abs(new_position)
        average = ((abs(old_position) * average) + (fill.quantity * fill.price)) / new_abs
    elif new_position == 0 or _same_direction(old_position, new_position):
        realized += _closed_pnl(
            closed_quantity=fill.quantity,
            fill_price=fill.price,
            average_entry=average,
            long_position=old_position > 0,
        )
    else:
        realized += _closed_pnl(
            closed_quantity=abs(old_position),
            fill_price=fill.price,
            average_entry=average,
            long_position=old_position > 0,
        )
        average = fill.price
    if new_position == 0:
        average = Decimal("0")
    return PositionState(
        cash_usdc=cash,
        position_quantity=new_position,
        average_entry_price=average,
        realized_pnl_usdc=realized,
        fees_usdc=state.fees_usdc + fill.fee_usdc,
    )


def unrealized_pnl(
    *,
    position_quantity: Decimal,
    average_entry_price: Decimal,
    mark_price: Decimal | None,
) -> Decimal | None:
    """Mark-to-market PnL. A missing mark stays missing; it is not the entry."""

    if position_quantity == 0:
        return Decimal("0")
    if mark_price is None:
        return None
    if type(mark_price) is not Decimal or not mark_price.is_finite() or mark_price <= 0:
        return None
    return position_quantity * (mark_price - average_entry_price)


def _same_direction(left: Decimal, right: Decimal) -> bool:
    return (left > 0 and right > 0) or (left < 0 and right < 0)


def _closed_pnl(
    *,
    closed_quantity: Decimal,
    fill_price: Decimal,
    average_entry: Decimal,
    long_position: bool,
) -> Decimal:
    direction = Decimal(1) if long_position else Decimal(-1)
    return closed_quantity * (fill_price - average_entry) * direction
