"""Deterministic PAPER sizing and hard-limit tests."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from hyperliquid_bot.paper_risk import (
    DEFAULT_PAPER_RISK_LIMITS,
    PAPER_HARD_LIMIT_GATES,
    REASON_ASSET_CONCENTRATION,
    REASON_AVERAGING_DOWN,
    REASON_DAILY_LOSS,
    REASON_DRAWDOWN_KILL,
    REASON_MAX_GROSS,
    REASON_MAX_LEVERAGE,
    REASON_MAX_NET,
    REASON_MAX_POSITIONS,
    REASON_RISK_BASED_SIZE,
    REASON_STRATEGY_CONCENTRATION,
    REASON_VENUE_CONCENTRATION,
    REASON_WEEKLY_LOSS,
    PaperOrderIntent,
    PaperPortfolioSnapshot,
    PaperRiskLimits,
    account_risk_budget_usdc,
    evaluate_paper_hard_limits,
    extend_d01_risk_decision,
    paper_snapshot_for_bounded_book,
    require_paper_trading_mode,
    size_paper_notional_usdc,
    size_paper_quantity,
)

DOCS_EQUITY = Decimal("50000")
DOCS_RISK_FRACTION = Decimal("0.003")
DOCS_STOP = Decimal("0.02")
DOCS_NOTIONAL = Decimal("7500")
D01_EQUITY = Decimal("100000")
D01_PRICE = Decimal("78047.0")
D01_QUANTITY = Decimal("0.00013")
D01_STOP = Decimal("0.02")
SIZE_INCREMENT = Decimal("0.00001")


def _flat_snapshot(equity: Decimal = D01_EQUITY) -> PaperPortfolioSnapshot:
    return paper_snapshot_for_bounded_book(
        equity_usdc=equity,
        price=D01_PRICE,
        current_position=Decimal("0"),
    )


def _entry(*, quantity: Decimal, reduce_only: bool = False) -> PaperOrderIntent:
    return PaperOrderIntent(
        side="BUY",
        quantity=quantity,
        price=D01_PRICE,
        reduce_only=reduce_only,
        stop_distance_fraction=D01_STOP,
    )


def test_documented_stop_based_notional_example() -> None:
    assert account_risk_budget_usdc(
        equity_usdc=DOCS_EQUITY,
        risk_per_trade_fraction=DOCS_RISK_FRACTION,
    ) == Decimal("150")
    assert (
        size_paper_notional_usdc(
            equity_usdc=DOCS_EQUITY,
            stop_distance_fraction=DOCS_STOP,
            risk_per_trade_fraction=DOCS_RISK_FRACTION,
        )
        == DOCS_NOTIONAL
    )


def test_volatility_widens_stop_and_reduces_size_never_increases() -> None:
    base = size_paper_notional_usdc(
        equity_usdc=DOCS_EQUITY,
        stop_distance_fraction=DOCS_STOP,
        risk_per_trade_fraction=DOCS_RISK_FRACTION,
    )
    stressed = size_paper_notional_usdc(
        equity_usdc=DOCS_EQUITY,
        stop_distance_fraction=DOCS_STOP,
        risk_per_trade_fraction=DOCS_RISK_FRACTION,
        volatility_multiple=Decimal("2"),
    )
    assert stressed == base / 2
    with pytest.raises(ValueError, match="volatility_multiple"):
        size_paper_notional_usdc(
            equity_usdc=DOCS_EQUITY,
            stop_distance_fraction=DOCS_STOP,
            volatility_multiple=Decimal("0.5"),
        )


def test_quantity_rounds_down_and_never_exceeds_notional() -> None:
    quantity = size_paper_quantity(
        notional_usdc=DOCS_NOTIONAL,
        price=D01_PRICE,
        increment=SIZE_INCREMENT,
    )
    assert quantity == Decimal("0.09609")
    assert quantity * D01_PRICE <= DOCS_NOTIONAL
    assert (quantity + SIZE_INCREMENT) * D01_PRICE > DOCS_NOTIONAL


def test_d01_smoke_entry_stays_within_paper_limits() -> None:
    decision = evaluate_paper_hard_limits(
        snapshot=_flat_snapshot(),
        intent=_entry(quantity=D01_QUANTITY),
        trading_mode="PAPER",
    )
    assert decision.approved is True
    assert decision.reasons == ()
    assert decision.proposed_notional_usdc == D01_QUANTITY * D01_PRICE
    assert decision.proposed_notional_usdc < decision.sized_notional_usdc
    assert decision.projected_leverage < Decimal("1")


def test_risk_sized_entry_is_approved_when_inside_hard_caps() -> None:
    notional = size_paper_notional_usdc(
        equity_usdc=D01_EQUITY,
        stop_distance_fraction=D01_STOP,
    )
    quantity = size_paper_quantity(
        notional_usdc=notional,
        price=D01_PRICE,
        increment=SIZE_INCREMENT,
    )
    decision = evaluate_paper_hard_limits(
        snapshot=_flat_snapshot(),
        intent=_entry(quantity=quantity),
        trading_mode=None,
    )
    assert quantity > 0
    assert decision.approved is True
    assert decision.proposed_notional_usdc <= decision.sized_notional_usdc


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda intent, snapshot, limits: (
                _entry(quantity=Decimal("1")),
                snapshot,
                limits,
            ),
            REASON_RISK_BASED_SIZE,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                snapshot,
                PaperRiskLimits(max_paper_leverage=Decimal("0.0000001")),
            ),
            REASON_MAX_LEVERAGE,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                snapshot,
                PaperRiskLimits(max_gross_exposure_fraction=Decimal("0.0000001")),
            ),
            REASON_MAX_GROSS,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                snapshot,
                PaperRiskLimits(max_net_exposure_fraction=Decimal("0.0000001")),
            ),
            REASON_MAX_NET,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                snapshot,
                PaperRiskLimits(max_asset_concentration_fraction=Decimal("0.0000001")),
            ),
            REASON_ASSET_CONCENTRATION,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                snapshot,
                PaperRiskLimits(max_strategy_concentration_fraction=Decimal("0.0000001")),
            ),
            REASON_STRATEGY_CONCENTRATION,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                snapshot,
                PaperRiskLimits(max_venue_concentration_fraction=Decimal("0.0000001")),
            ),
            REASON_VENUE_CONCENTRATION,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                paper_snapshot_for_bounded_book(
                    equity_usdc=D01_EQUITY,
                    price=D01_PRICE,
                    current_position=Decimal("0"),
                    daily_pnl_usdc=Decimal("-1000"),
                ),
                limits,
            ),
            REASON_DAILY_LOSS,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                paper_snapshot_for_bounded_book(
                    equity_usdc=D01_EQUITY,
                    price=D01_PRICE,
                    current_position=Decimal("0"),
                    weekly_pnl_usdc=Decimal("-3000"),
                ),
                limits,
            ),
            REASON_WEEKLY_LOSS,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                paper_snapshot_for_bounded_book(
                    equity_usdc=Decimal("93000"),
                    price=D01_PRICE,
                    current_position=Decimal("0"),
                    peak_equity_usdc=D01_EQUITY,
                ),
                limits,
            ),
            REASON_DRAWDOWN_KILL,
        ),
        (
            lambda intent, snapshot, limits: (
                intent,
                paper_snapshot_for_bounded_book(
                    equity_usdc=D01_EQUITY,
                    price=D01_PRICE,
                    current_position=D01_QUANTITY,
                ),
                limits,
            ),
            REASON_AVERAGING_DOWN,
        ),
    ],
)
def test_each_hard_limit_breach_fails_closed(mutate: object, reason: str) -> None:
    intent, snapshot, limits = mutate(  # type: ignore[operator]
        _entry(quantity=D01_QUANTITY),
        _flat_snapshot(),
        DEFAULT_PAPER_RISK_LIMITS,
    )
    decision = evaluate_paper_hard_limits(
        snapshot=snapshot,
        intent=intent,
        limits=limits,
        trading_mode="PAPER",
    )
    assert decision.approved is False
    assert reason in decision.reasons


def test_max_simultaneous_positions_rejects_an_additional_name() -> None:
    snapshot = PaperPortfolioSnapshot(
        equity_usdc=D01_EQUITY,
        peak_equity_usdc=D01_EQUITY,
        daily_pnl_usdc=Decimal("0"),
        weekly_pnl_usdc=Decimal("0"),
        open_position_count=3,
        gross_exposure_usdc=Decimal("30"),
        net_exposure_usdc=Decimal("30"),
        asset_exposure_usdc=Decimal("0"),
        strategy_exposure_usdc=Decimal("30"),
        venue_exposure_usdc=Decimal("30"),
    )
    decision = evaluate_paper_hard_limits(
        snapshot=snapshot,
        intent=_entry(quantity=D01_QUANTITY),
        trading_mode="PAPER",
    )
    assert decision.approved is False
    assert REASON_MAX_POSITIONS in decision.reasons


def test_reduce_only_exit_is_allowed_when_entry_halts_are_already_breached() -> None:
    snapshot = paper_snapshot_for_bounded_book(
        equity_usdc=Decimal("90000"),
        price=D01_PRICE,
        current_position=D01_QUANTITY,
        daily_pnl_usdc=Decimal("-2000"),
        weekly_pnl_usdc=Decimal("-4000"),
        peak_equity_usdc=D01_EQUITY,
    )
    decision = evaluate_paper_hard_limits(
        snapshot=snapshot,
        intent=PaperOrderIntent(
            side="SELL",
            quantity=D01_QUANTITY,
            price=D01_PRICE,
            reduce_only=True,
            stop_distance_fraction=D01_STOP,
        ),
        trading_mode="PAPER",
    )
    assert decision.approved is True
    assert decision.reasons == ()


def test_extend_never_relaxes_a_d01_rejection() -> None:
    d01_rejected = {
        "approved": False,
        "reasons": ["entry notional exceeds the hard USDC exposure cap"],
        "quantity_btc": str(D01_QUANTITY),
    }
    extended = extend_d01_risk_decision(
        d01_rejected,
        snapshot=_flat_snapshot(),
        intent=_entry(quantity=D01_QUANTITY),
        trading_mode="PAPER",
    )
    assert extended["approved"] is False
    reasons = extended["reasons"]
    assert isinstance(reasons, list)
    assert "entry notional exceeds the hard USDC exposure cap" in reasons
    paper = extended["paper_risk"]
    assert isinstance(paper, dict)
    assert paper["approved"] is True


def test_extend_can_reject_after_d01_approval() -> None:
    d01_approved = {"approved": True, "reasons": []}
    extended = extend_d01_risk_decision(
        d01_approved,
        snapshot=_flat_snapshot(),
        intent=_entry(quantity=Decimal("1")),
        trading_mode="PAPER",
    )
    assert extended["approved"] is False
    reasons = extended["reasons"]
    assert isinstance(reasons, list)
    assert REASON_RISK_BASED_SIZE in reasons


@pytest.mark.parametrize(
    "raw_mode",
    ["LIVE", "TESTNET", "SHADOW", "paper", "Paper", " PAPER ", ""],
)
def test_paper_risk_refuses_to_run_or_flip_non_paper_modes(raw_mode: str) -> None:
    with pytest.raises(UnsafeTradingModeError):
        require_paper_trading_mode(raw_mode)
    with pytest.raises(UnsafeTradingModeError):
        evaluate_paper_hard_limits(
            snapshot=_flat_snapshot(),
            intent=_entry(quantity=D01_QUANTITY),
            trading_mode=raw_mode,
        )


def test_module_does_not_write_trading_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADING_MODE", raising=False)
    require_paper_trading_mode(None)
    evaluate_paper_hard_limits(
        snapshot=_flat_snapshot(),
        intent=_entry(quantity=D01_QUANTITY),
        trading_mode=None,
    )
    assert "TRADING_MODE" not in os.environ
    assert require_paper_trading_mode("PAPER") == "PAPER"


def test_invalid_portfolio_state_fails_closed() -> None:
    with pytest.raises(ValueError, match="equity_usdc"):
        paper_snapshot_for_bounded_book(
            equity_usdc=Decimal("0"),
            price=D01_PRICE,
            current_position=Decimal("0"),
        )
    with pytest.raises(ValueError, match="inconsistent"):
        paper_snapshot_for_bounded_book(
            equity_usdc=D01_EQUITY,
            price=D01_PRICE,
            current_position=Decimal("0"),
            peak_equity_usdc=Decimal("1"),
        )
    with pytest.raises(ValueError, match="stop_distance"):
        size_paper_notional_usdc(equity_usdc=D01_EQUITY, stop_distance_fraction=Decimal("0"))


def test_documented_gates_are_explicit() -> None:
    assert PAPER_HARD_LIMIT_GATES == (
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
    encoded = DEFAULT_PAPER_RISK_LIMITS.to_dict()
    assert encoded["trading_mode"] == "PAPER"
    assert "LIVE" not in encoded.values()
