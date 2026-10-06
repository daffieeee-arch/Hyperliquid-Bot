"""Hyperliquid perp price and size increments used by the PAPER fill model.

Official rules, reviewed 2026-10-06:

- Prices have at most 5 significant figures, and at most
  ``MAX_DECIMALS - szDecimals`` decimal places. ``MAX_DECIMALS`` is 6 for
  perps. Integer prices are always allowed.
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size
- Sizes are multiples of ``10 ** -szDecimals``. BTC perp ``szDecimals`` in the
  ``meta`` universe example is 5.
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals
- Perp orders must have a minimum value of $10, except an exact reduce-only
  close.
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/error-responses

The PAPER engine rounds size down and rounds a fill price in the adverse
direction so a simulated fill is never better than the venue grid.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Final

PERP_MAX_DECIMALS: Final = 6
PRICE_SIGNIFICANT_FIGURES: Final = 5
HYPERLIQUID_MIN_ORDER_NOTIONAL_USDC: Final = Decimal("10")
BTC_PERP_SZ_DECIMALS: Final = 5


def perp_max_price_decimals(sz_decimals: int) -> int:
    """Return ``6 - szDecimals`` for a linear perp."""

    if type(sz_decimals) is not int or sz_decimals < 0 or sz_decimals > PERP_MAX_DECIMALS:
        raise ValueError("sz_decimals must be an integer from 0 through 6.")
    return PERP_MAX_DECIMALS - sz_decimals


def size_increment(sz_decimals: int) -> Decimal:
    """Return the lot size ``10 ** -szDecimals``."""

    if type(sz_decimals) is not int or sz_decimals < 0 or sz_decimals > 18:
        raise ValueError("sz_decimals must be an integer from 0 through 18.")
    return Decimal(10) ** (-sz_decimals)


def floor_size(quantity: Decimal, increment: Decimal) -> Decimal:
    """Round quantity down to ``increment``. Never round up."""

    if type(quantity) is not Decimal or not quantity.is_finite() or quantity < 0:
        raise ValueError("quantity must be a finite non-negative decimal.")
    if type(increment) is not Decimal or not increment.is_finite() or increment <= 0:
        raise ValueError("increment must be a finite positive decimal.")
    steps = (quantity / increment).to_integral_value(rounding=ROUND_DOWN)
    return steps * increment


def price_quantum(price: Decimal, *, max_decimals: int) -> Decimal:
    """Return the local adverse-rounding grid for a positive price.

    The binding constraint is the coarser of the 5-significant-figure quantum
    and the decimal-place quantum. Integers are always valid, so the quantum
    is never coarser than 1.
    """

    if type(price) is not Decimal or not price.is_finite() or price <= 0:
        raise ValueError("price must be a finite positive decimal.")
    if type(max_decimals) is not int or max_decimals < 0 or max_decimals > 18:
        raise ValueError("max_decimals must be an integer from 0 through 18.")
    exponent = price.adjusted()
    sig_quantum = Decimal(10) ** (exponent - (PRICE_SIGNIFICANT_FIGURES - 1))
    decimal_quantum = Decimal(1) if max_decimals == 0 else Decimal(10) ** (-max_decimals)
    quantum = sig_quantum if sig_quantum > decimal_quantum else decimal_quantum
    if quantum > 1:
        return Decimal(1)
    return quantum


def adverse_price(price: Decimal, *, side: str, max_decimals: int) -> Decimal:
    """Move ``price`` to a valid grid point that does not improve the fill."""

    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be exactly BUY or SELL.")
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    return _round_to_grid(price, rounding=rounding, max_decimals=max_decimals)


def protective_price(price: Decimal, *, side: str, max_decimals: int) -> Decimal:
    """Move a limit ``price`` to a valid grid point that does not widen its band.

    A BUY limit rounds down and a SELL limit rounds up, so the rounded limit
    never admits a fill worse than the unrounded band allowed.
    """

    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be exactly BUY or SELL.")
    rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
    return _round_to_grid(price, rounding=rounding, max_decimals=max_decimals)


def _round_to_grid(price: Decimal, *, rounding: str, max_decimals: int) -> Decimal:
    quantum = price_quantum(price, max_decimals=max_decimals)
    rounded = (price / quantum).to_integral_value(rounding=rounding) * quantum
    if rounded <= 0:
        raise ValueError("price rounded to a non-positive value.")
    if price_quantum(rounded, max_decimals=max_decimals) == quantum:
        return rounded
    # Rounding crossed a power of ten, which changes the grid: round again.
    return _round_to_grid(rounded, rounding=rounding, max_decimals=max_decimals)
