"""PAPER-only risk-based sizing and hard portfolio limits.

This module extends the COURSE-1 / D01 smoke-risk path. It does not replace
``evaluate_order_risk``, relax a D01 rejection, or authorize LIVE / TESTNET /
SHADOW. Formulas and default fractions come from ``docs/RISK_MANAGEMENT.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final, Literal

from hyperliquid_bot.local_mode import require_local_paper_mode

PAPER_RISK_PER_TRADE_FRACTION: Final = Decimal("0.0025")
PAPER_MAX_SIMULTANEOUS_POSITIONS: Final = 3
PAPER_DAILY_LOSS_FRACTION: Final = Decimal("0.01")
PAPER_WEEKLY_LOSS_FRACTION: Final = Decimal("0.03")
PAPER_DRAWDOWN_KILL_FRACTION: Final = Decimal("0.07")
PAPER_MAX_LEVERAGE: Final = Decimal("1")
PAPER_MAX_GROSS_EXPOSURE_FRACTION: Final = Decimal("1")
PAPER_MAX_NET_EXPOSURE_FRACTION: Final = Decimal("1")
PAPER_MAX_ASSET_CONCENTRATION_FRACTION: Final = Decimal("0.50")
PAPER_MAX_STRATEGY_CONCENTRATION_FRACTION: Final = Decimal("0.50")
PAPER_MAX_VENUE_CONCENTRATION_FRACTION: Final = Decimal("1")
PAPER_DEFAULT_STOP_DISTANCE_FRACTION: Final = Decimal("0.02")

REASON_RISK_BASED_SIZE: Final = (
    "entry notional exceeds risk-based size (risk budget / stop distance)"
)
REASON_MAX_LEVERAGE: Final = "projected gross leverage exceeds the paper maximum"
REASON_MAX_GROSS: Final = "projected gross exposure exceeds the portfolio notional cap"
REASON_MAX_NET: Final = "projected net exposure exceeds the portfolio net cap"
REASON_MAX_POSITIONS: Final = "projected open position count exceeds the simultaneous-position cap"
REASON_ASSET_CONCENTRATION: Final = "projected asset concentration exceeds the portfolio cap"
REASON_STRATEGY_CONCENTRATION: Final = "projected strategy concentration exceeds the portfolio cap"
REASON_VENUE_CONCENTRATION: Final = "projected venue concentration exceeds the portfolio cap"
REASON_DAILY_LOSS: Final = "daily loss guard is already breached; new entries are blocked"
REASON_WEEKLY_LOSS: Final = "weekly loss guard is already breached; new entries are blocked"
REASON_DRAWDOWN_KILL: Final = (
    "portfolio drawdown kill threshold is already breached; new entries are blocked"
)
REASON_AVERAGING_DOWN: Final = (
    "averaging down is not permitted outside a predeclared reduce-only exit"
)

PAPER_HARD_LIMIT_GATES: Final = (
    "risk_based_size",
    "max_paper_leverage",
    "max_gross_exposure",
    "max_net_exposure",
    "max_simultaneous_positions",
    "max_asset_concentration",
    "max_strategy_concentration",
    "max_venue_concentration",
    "daily_loss_guard",
    "weekly_loss_guard",
    "drawdown_kill",
    "no_averaging_down",
)


def _require_positive_decimal(value: Decimal, *, field_name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive decimal.")
    return value


def _require_non_negative_decimal(value: Decimal, *, field_name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative decimal.")
    return value


def _require_finite_decimal(value: Decimal, *, field_name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal.")
    return value


def account_risk_budget_usdc(
    *,
    equity_usdc: Decimal,
    risk_per_trade_fraction: Decimal = PAPER_RISK_PER_TRADE_FRACTION,
) -> Decimal:
    """Return the per-trade loss budget: equity * risk-per-trade fraction."""

    _require_positive_decimal(equity_usdc, field_name="equity_usdc")
    _require_positive_decimal(risk_per_trade_fraction, field_name="risk_per_trade_fraction")
    if risk_per_trade_fraction >= 1:
        raise ValueError("risk_per_trade_fraction must be a fraction in (0, 1).")
    return equity_usdc * risk_per_trade_fraction


def effective_stop_distance_fraction(
    *,
    stop_distance_fraction: Decimal,
    volatility_multiple: Decimal = Decimal("1"),
) -> Decimal:
    """Widen stop distance when volatility rises; never tighten it automatically."""

    _require_positive_decimal(stop_distance_fraction, field_name="stop_distance_fraction")
    _require_positive_decimal(volatility_multiple, field_name="volatility_multiple")
    if volatility_multiple < 1:
        raise ValueError(
            "volatility_multiple must be >= 1; sizing must not increase automatically."
        )
    return stop_distance_fraction * volatility_multiple


def size_paper_notional_usdc(
    *,
    equity_usdc: Decimal,
    stop_distance_fraction: Decimal,
    risk_per_trade_fraction: Decimal = PAPER_RISK_PER_TRADE_FRACTION,
    volatility_multiple: Decimal = Decimal("1"),
) -> Decimal:
    """Return ``account_risk_budget / effective_stop_distance_fraction``."""

    budget = account_risk_budget_usdc(
        equity_usdc=equity_usdc,
        risk_per_trade_fraction=risk_per_trade_fraction,
    )
    stop = effective_stop_distance_fraction(
        stop_distance_fraction=stop_distance_fraction,
        volatility_multiple=volatility_multiple,
    )
    return budget / stop


def size_paper_quantity(
    *,
    notional_usdc: Decimal,
    price: Decimal,
    increment: Decimal,
) -> Decimal:
    """Convert notional to quantity, rounding down to ``increment``. Never round up."""

    _require_non_negative_decimal(notional_usdc, field_name="notional_usdc")
    _require_positive_decimal(price, field_name="price")
    _require_positive_decimal(increment, field_name="increment")
    raw_quantity = notional_usdc / price
    steps = (raw_quantity / increment).to_integral_value(rounding=ROUND_DOWN)
    return steps * increment


@dataclass(frozen=True, slots=True)
class PaperRiskLimits:
    """Explicit PAPER hard limits. Live limits require a separate approval path."""

    risk_per_trade_fraction: Decimal = PAPER_RISK_PER_TRADE_FRACTION
    max_simultaneous_positions: int = PAPER_MAX_SIMULTANEOUS_POSITIONS
    daily_loss_fraction: Decimal = PAPER_DAILY_LOSS_FRACTION
    weekly_loss_fraction: Decimal = PAPER_WEEKLY_LOSS_FRACTION
    drawdown_kill_fraction: Decimal = PAPER_DRAWDOWN_KILL_FRACTION
    max_paper_leverage: Decimal = PAPER_MAX_LEVERAGE
    max_gross_exposure_fraction: Decimal = PAPER_MAX_GROSS_EXPOSURE_FRACTION
    max_net_exposure_fraction: Decimal = PAPER_MAX_NET_EXPOSURE_FRACTION
    max_asset_concentration_fraction: Decimal = PAPER_MAX_ASSET_CONCENTRATION_FRACTION
    max_strategy_concentration_fraction: Decimal = PAPER_MAX_STRATEGY_CONCENTRATION_FRACTION
    max_venue_concentration_fraction: Decimal = PAPER_MAX_VENUE_CONCENTRATION_FRACTION

    def to_dict(self) -> dict[str, object]:
        return {
            "risk_per_trade_fraction": str(self.risk_per_trade_fraction),
            "max_simultaneous_positions": self.max_simultaneous_positions,
            "daily_loss_fraction": str(self.daily_loss_fraction),
            "weekly_loss_fraction": str(self.weekly_loss_fraction),
            "drawdown_kill_fraction": str(self.drawdown_kill_fraction),
            "max_paper_leverage": str(self.max_paper_leverage),
            "max_gross_exposure_fraction": str(self.max_gross_exposure_fraction),
            "max_net_exposure_fraction": str(self.max_net_exposure_fraction),
            "max_asset_concentration_fraction": str(self.max_asset_concentration_fraction),
            "max_strategy_concentration_fraction": str(self.max_strategy_concentration_fraction),
            "max_venue_concentration_fraction": str(self.max_venue_concentration_fraction),
            "trading_mode": "PAPER",
        }


DEFAULT_PAPER_RISK_LIMITS: Final = PaperRiskLimits()


@dataclass(frozen=True, slots=True)
class PaperPortfolioSnapshot:
    """Point-in-time PAPER book used by hard limits. Missing/invalid values fail closed."""

    equity_usdc: Decimal
    peak_equity_usdc: Decimal
    daily_pnl_usdc: Decimal
    weekly_pnl_usdc: Decimal
    open_position_count: int
    gross_exposure_usdc: Decimal
    net_exposure_usdc: Decimal
    asset_exposure_usdc: Decimal
    strategy_exposure_usdc: Decimal
    venue_exposure_usdc: Decimal


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    """One proposed PAPER order. Side must be the exact built-in BUY/SELL spelling."""

    side: str
    quantity: Decimal
    price: Decimal
    reduce_only: bool
    stop_distance_fraction: Decimal = PAPER_DEFAULT_STOP_DISTANCE_FRACTION
    volatility_multiple: Decimal = Decimal("1")
    asset_id: str = "BTC-USD-PERP"
    strategy_id: str = "D01SmokeStrategy"
    venue_id: str = "HYPERLIQUID"


@dataclass(frozen=True, slots=True)
class PaperRiskDecision:
    """Fail-closed PAPER portfolio decision. ``approved`` is false when any gate breaches."""

    approved: bool
    reasons: tuple[str, ...]
    sized_notional_usdc: Decimal
    proposed_notional_usdc: Decimal
    account_risk_budget_usdc: Decimal
    effective_stop_distance_fraction: Decimal
    projected_gross_exposure_usdc: Decimal
    projected_net_exposure_usdc: Decimal
    projected_leverage: Decimal
    projected_open_position_count: int
    limits: PaperRiskLimits

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "reasons": list(self.reasons),
            "sized_notional_usdc": str(self.sized_notional_usdc),
            "proposed_notional_usdc": str(self.proposed_notional_usdc),
            "account_risk_budget_usdc": str(self.account_risk_budget_usdc),
            "effective_stop_distance_fraction": str(self.effective_stop_distance_fraction),
            "projected_gross_exposure_usdc": str(self.projected_gross_exposure_usdc),
            "projected_net_exposure_usdc": str(self.projected_net_exposure_usdc),
            "projected_leverage": str(self.projected_leverage),
            "projected_open_position_count": self.projected_open_position_count,
            "limits": self.limits.to_dict(),
            "gates": list(PAPER_HARD_LIMIT_GATES),
            "trading_mode": "PAPER",
        }


def paper_snapshot_for_bounded_book(
    *,
    equity_usdc: Decimal,
    price: Decimal,
    current_position: Decimal,
    daily_pnl_usdc: Decimal = Decimal("0"),
    weekly_pnl_usdc: Decimal = Decimal("0"),
    peak_equity_usdc: Decimal | None = None,
) -> PaperPortfolioSnapshot:
    """Build a single-instrument snapshot for the bounded D01 / COURSE-1 / D22-A book."""

    _require_positive_decimal(equity_usdc, field_name="equity_usdc")
    _require_positive_decimal(price, field_name="price")
    _require_finite_decimal(current_position, field_name="current_position")
    _require_finite_decimal(daily_pnl_usdc, field_name="daily_pnl_usdc")
    _require_finite_decimal(weekly_pnl_usdc, field_name="weekly_pnl_usdc")
    peak = equity_usdc if peak_equity_usdc is None else peak_equity_usdc
    _require_positive_decimal(peak, field_name="peak_equity_usdc")
    if peak < equity_usdc:
        raise ValueError(
            "peak_equity_usdc is below current equity; portfolio state is inconsistent."
        )
    exposure = abs(current_position) * price
    return PaperPortfolioSnapshot(
        equity_usdc=equity_usdc,
        peak_equity_usdc=peak,
        daily_pnl_usdc=daily_pnl_usdc,
        weekly_pnl_usdc=weekly_pnl_usdc,
        open_position_count=0 if current_position == 0 else 1,
        gross_exposure_usdc=exposure,
        net_exposure_usdc=current_position * price,
        asset_exposure_usdc=exposure,
        strategy_exposure_usdc=exposure,
        venue_exposure_usdc=exposure,
    )


def _signed_quantity(side: str, quantity: Decimal) -> Decimal:
    if side == "BUY":
        return quantity
    return -quantity


def _validate_intent(intent: PaperOrderIntent) -> None:
    if type(intent.side) is not str or intent.side not in {"BUY", "SELL"}:
        raise ValueError("order side must be exactly BUY or SELL.")
    if type(intent.reduce_only) is not bool:
        raise TypeError("reduce_only must be a boolean.")
    _require_positive_decimal(intent.quantity, field_name="quantity")
    _require_positive_decimal(intent.price, field_name="price")
    _require_positive_decimal(intent.stop_distance_fraction, field_name="stop_distance_fraction")
    _require_positive_decimal(intent.volatility_multiple, field_name="volatility_multiple")
    if intent.volatility_multiple < 1:
        raise ValueError(
            "volatility_multiple must be >= 1; sizing must not increase automatically."
        )
    if type(intent.asset_id) is not str or not intent.asset_id:
        raise ValueError("asset_id must be non-empty text.")
    if type(intent.strategy_id) is not str or not intent.strategy_id:
        raise ValueError("strategy_id must be non-empty text.")
    if type(intent.venue_id) is not str or not intent.venue_id:
        raise ValueError("venue_id must be non-empty text.")


def _validate_snapshot(snapshot: PaperPortfolioSnapshot) -> None:
    _require_positive_decimal(snapshot.equity_usdc, field_name="equity_usdc")
    _require_positive_decimal(snapshot.peak_equity_usdc, field_name="peak_equity_usdc")
    if snapshot.peak_equity_usdc < snapshot.equity_usdc:
        raise ValueError(
            "peak_equity_usdc is below current equity; portfolio state is inconsistent."
        )
    _require_finite_decimal(snapshot.daily_pnl_usdc, field_name="daily_pnl_usdc")
    _require_finite_decimal(snapshot.weekly_pnl_usdc, field_name="weekly_pnl_usdc")
    if type(snapshot.open_position_count) is not int or snapshot.open_position_count < 0:
        raise ValueError("open_position_count must be a non-negative integer.")
    _require_non_negative_decimal(snapshot.gross_exposure_usdc, field_name="gross_exposure_usdc")
    _require_finite_decimal(snapshot.net_exposure_usdc, field_name="net_exposure_usdc")
    _require_non_negative_decimal(snapshot.asset_exposure_usdc, field_name="asset_exposure_usdc")
    _require_non_negative_decimal(
        snapshot.strategy_exposure_usdc, field_name="strategy_exposure_usdc"
    )
    _require_non_negative_decimal(snapshot.venue_exposure_usdc, field_name="venue_exposure_usdc")


def _validate_limits(limits: PaperRiskLimits) -> None:
    account_risk_budget_usdc(
        equity_usdc=Decimal("1"),
        risk_per_trade_fraction=limits.risk_per_trade_fraction,
    )
    if type(limits.max_simultaneous_positions) is not int or limits.max_simultaneous_positions < 1:
        raise ValueError("max_simultaneous_positions must be a positive integer.")
    for field_name, value in (
        ("daily_loss_fraction", limits.daily_loss_fraction),
        ("weekly_loss_fraction", limits.weekly_loss_fraction),
        ("drawdown_kill_fraction", limits.drawdown_kill_fraction),
        ("max_paper_leverage", limits.max_paper_leverage),
        ("max_gross_exposure_fraction", limits.max_gross_exposure_fraction),
        ("max_net_exposure_fraction", limits.max_net_exposure_fraction),
        ("max_asset_concentration_fraction", limits.max_asset_concentration_fraction),
        ("max_strategy_concentration_fraction", limits.max_strategy_concentration_fraction),
        ("max_venue_concentration_fraction", limits.max_venue_concentration_fraction),
    ):
        _require_positive_decimal(value, field_name=field_name)


def evaluate_paper_hard_limits(
    *,
    snapshot: PaperPortfolioSnapshot,
    intent: PaperOrderIntent,
    limits: PaperRiskLimits = DEFAULT_PAPER_RISK_LIMITS,
    trading_mode: object | None = None,
) -> PaperRiskDecision:
    """Reject a PAPER order that would breach documented sizing or portfolio limits."""

    require_local_paper_mode(trading_mode)
    _validate_snapshot(snapshot)
    _validate_intent(intent)
    _validate_limits(limits)

    proposed_notional = intent.quantity * intent.price
    signed_delta = _signed_quantity(intent.side, intent.quantity)
    current_signed_qty = (
        Decimal("0")
        if snapshot.gross_exposure_usdc == 0
        else snapshot.net_exposure_usdc / intent.price
    )
    projected_signed_qty = current_signed_qty + signed_delta
    if intent.reduce_only:
        projected_gross = abs(projected_signed_qty) * intent.price
        projected_net = projected_signed_qty * intent.price
        projected_asset = projected_gross
        projected_strategy = (
            snapshot.strategy_exposure_usdc - snapshot.asset_exposure_usdc + projected_asset
        )
        projected_venue = (
            snapshot.venue_exposure_usdc - snapshot.asset_exposure_usdc + projected_asset
        )
        projected_count = snapshot.open_position_count if projected_signed_qty != 0 else 0
    else:
        projected_gross = snapshot.gross_exposure_usdc + proposed_notional
        projected_net = snapshot.net_exposure_usdc + (signed_delta * intent.price)
        projected_asset = snapshot.asset_exposure_usdc + proposed_notional
        projected_strategy = snapshot.strategy_exposure_usdc + proposed_notional
        projected_venue = snapshot.venue_exposure_usdc + proposed_notional
        projected_count = snapshot.open_position_count + (
            1 if snapshot.asset_exposure_usdc == 0 else 0
        )

    budget = account_risk_budget_usdc(
        equity_usdc=snapshot.equity_usdc,
        risk_per_trade_fraction=limits.risk_per_trade_fraction,
    )
    stop = effective_stop_distance_fraction(
        stop_distance_fraction=intent.stop_distance_fraction,
        volatility_multiple=intent.volatility_multiple,
    )
    sized_notional = budget / stop
    leverage = projected_gross / snapshot.equity_usdc
    drawdown = (snapshot.peak_equity_usdc - snapshot.equity_usdc) / snapshot.peak_equity_usdc

    reasons: list[str] = []
    if not intent.reduce_only:
        if proposed_notional > sized_notional:
            reasons.append(REASON_RISK_BASED_SIZE)
        if leverage > limits.max_paper_leverage:
            reasons.append(REASON_MAX_LEVERAGE)
        if projected_gross > snapshot.equity_usdc * limits.max_gross_exposure_fraction:
            reasons.append(REASON_MAX_GROSS)
        if abs(projected_net) > snapshot.equity_usdc * limits.max_net_exposure_fraction:
            reasons.append(REASON_MAX_NET)
        if projected_count > limits.max_simultaneous_positions:
            reasons.append(REASON_MAX_POSITIONS)
        if projected_asset > snapshot.equity_usdc * limits.max_asset_concentration_fraction:
            reasons.append(REASON_ASSET_CONCENTRATION)
        if projected_strategy > snapshot.equity_usdc * limits.max_strategy_concentration_fraction:
            reasons.append(REASON_STRATEGY_CONCENTRATION)
        if projected_venue > snapshot.equity_usdc * limits.max_venue_concentration_fraction:
            reasons.append(REASON_VENUE_CONCENTRATION)
        if snapshot.daily_pnl_usdc <= -(snapshot.equity_usdc * limits.daily_loss_fraction):
            reasons.append(REASON_DAILY_LOSS)
        if snapshot.weekly_pnl_usdc <= -(snapshot.equity_usdc * limits.weekly_loss_fraction):
            reasons.append(REASON_WEEKLY_LOSS)
        if drawdown >= limits.drawdown_kill_fraction:
            reasons.append(REASON_DRAWDOWN_KILL)
        if snapshot.asset_exposure_usdc > 0:
            reasons.append(REASON_AVERAGING_DOWN)

    return PaperRiskDecision(
        approved=not reasons,
        reasons=tuple(reasons),
        sized_notional_usdc=sized_notional,
        proposed_notional_usdc=proposed_notional,
        account_risk_budget_usdc=budget,
        effective_stop_distance_fraction=stop,
        projected_gross_exposure_usdc=projected_gross,
        projected_net_exposure_usdc=projected_net,
        projected_leverage=leverage,
        projected_open_position_count=projected_count,
        limits=limits,
    )


def extend_d01_risk_decision(
    decision: Mapping[str, object],
    *,
    snapshot: PaperPortfolioSnapshot,
    intent: PaperOrderIntent,
    limits: PaperRiskLimits = DEFAULT_PAPER_RISK_LIMITS,
    trading_mode: object | None = None,
) -> dict[str, object]:
    """Extend a D01 smoke-risk decision. A D01 rejection is never relaxed."""

    if type(decision) is not dict:
        raise TypeError("D01 risk decision must be a dict.")
    d01_reasons = decision.get("reasons")
    if type(d01_reasons) is not list:
        raise TypeError("D01 risk decision reasons must be a list.")
    paper = evaluate_paper_hard_limits(
        snapshot=snapshot,
        intent=intent,
        limits=limits,
        trading_mode=trading_mode,
    )
    merged_reasons = [reason for reason in d01_reasons if type(reason) is str]
    merged_reasons.extend(paper.reasons)
    extended = dict(decision)
    extended["approved"] = decision.get("approved") is True and paper.approved
    extended["reasons"] = merged_reasons
    extended["paper_risk"] = paper.to_dict()
    return extended


def require_paper_trading_mode(
    trading_mode: object | None = None,
) -> Literal["PAPER"]:
    """Refuse every non-PAPER mode. This module cannot flip a run to LIVE."""

    return require_local_paper_mode(trading_mode)
