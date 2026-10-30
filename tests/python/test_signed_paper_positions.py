"""Signed account/asset truth, pending reductions and reconciliation boundaries."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from vertical_slices.paper_position import sandbox_position_truth

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from hyperliquid_bot.paper_risk import (
    REASON_POSITION_CHANGED,
    REASON_POSITION_CONFLICT,
    REASON_POSITION_IDENTITY,
    REASON_POSITION_PENDING,
    REASON_POSITION_UNKNOWN,
    REASON_REDUCE_ONLY_DIRECTION,
    REASON_REDUCE_ONLY_FLAT,
    REASON_REDUCE_ONLY_OVERSIZE,
    PaperOrderIntent,
    PaperPortfolioSnapshot,
    PaperPositionKey,
    PaperSignedPosition,
    evaluate_paper_hard_limits,
    paper_snapshot_for_bounded_book,
)

KEY = PaperPositionKey("PAPER", "paper-account-a", "hyperliquid", "BTC-PERP", "strategy-a")


def position(quantity: str = "2", **changes: Any) -> PaperSignedPosition:
    return replace(
        PaperSignedPosition(KEY, Decimal(quantity), "owner-a:position-v1", Decimal("0"), True),
        **changes,
    )


def intent(state: PaperSignedPosition, quantity: str = "2", **changes: Any) -> PaperOrderIntent:
    return replace(
        PaperOrderIntent(
            side="SELL" if state.quantity is not None and state.quantity > 0 else "BUY",
            quantity=Decimal(quantity),
            price=Decimal("1000"),
            reduce_only=True,
            asset_id=KEY.instrument_id,
            venue_id=KEY.venue_id,
            strategy_id=KEY.strategy_id,
            position_key=state.key,
            position_version=state.version,
        ),
        **changes,
    )


def snapshot(state: PaperSignedPosition) -> PaperPortfolioSnapshot:
    return paper_snapshot_for_bounded_book(
        equity_usdc=Decimal("100000"),
        price=Decimal("1000"),
        current_position=state.quantity if state.quantity is not None else Decimal("2"),
        position=state,
    )


@pytest.mark.parametrize("signed", ["2", "-2"])
@pytest.mark.parametrize("quantity", ["0.5", "2"])
def test_only_real_reductions_preserve_other_asset_aggregates(signed: str, quantity: str) -> None:
    state = position(signed)
    # The other asset points in the opposite direction: portfolio-net cannot
    # identify this asset's sign, and flattening this asset leaves another open.
    long = Decimal(signed) > 0
    book = replace(
        snapshot(state),
        gross_exposure_usdc=Decimal("7000"),
        net_exposure_usdc=Decimal("-3000" if long else "3000"),
        open_position_count=2,
        strategy_exposure_usdc=Decimal("7000"),
        venue_exposure_usdc=Decimal("7000"),
    )
    decision = evaluate_paper_hard_limits(snapshot=book, intent=intent(state, quantity))
    reduced = Decimal(quantity) * 1000
    assert decision.approved is True
    assert decision.projected_gross_exposure_usdc == Decimal("7000") - reduced
    assert decision.projected_net_exposure_usdc == book.net_exposure_usdc + (
        -reduced if long else reduced
    )
    assert decision.projected_open_position_count == (1 if quantity == "2" else 2)


@pytest.mark.parametrize("signed", ["2", "-2"])
def test_wrong_direction_flat_and_overclose_are_rejected_without_clipping(signed: str) -> None:
    state = position(signed)
    wrong = intent(state, side="BUY" if Decimal(signed) > 0 else "SELL")
    assert (
        REASON_REDUCE_ONLY_DIRECTION
        in evaluate_paper_hard_limits(snapshot=snapshot(state), intent=wrong).reasons
    )
    oversized = evaluate_paper_hard_limits(
        snapshot=snapshot(state), intent=intent(state, "2.00001")
    )
    assert REASON_REDUCE_ONLY_OVERSIZE in oversized.reasons
    assert oversized.projected_gross_exposure_usdc == Decimal("2000")
    flat = position("0")
    assert (
        REASON_REDUCE_ONLY_FLAT
        in evaluate_paper_hard_limits(snapshot=snapshot(flat), intent=intent(flat)).reasons
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", "paper-account-b"),
        ("venue_id", "another-venue"),
        ("instrument_id", "ETH-PERP"),
        ("strategy_id", "strategy-b"),
    ],
)
def test_wrong_ownership_never_gets_entry_limit_exemptions(field: str, value: str) -> None:
    state = position()
    key = replace(KEY, **{field: value})
    order = intent(state, position_key=key)
    result = evaluate_paper_hard_limits(snapshot=snapshot(state), intent=order)
    assert result.approved is False
    assert REASON_POSITION_IDENTITY in result.reasons


@pytest.mark.parametrize("mode", ["TESTNET", "SHADOW", "LIVE", "paper", ""])
def test_position_environment_does_not_enable_another_mode(mode: str) -> None:
    state = position(key=replace(KEY, environment=mode))
    with pytest.raises(UnsafeTradingModeError):
        evaluate_paper_hard_limits(snapshot=snapshot(state), intent=intent(state))


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": None},
        {"version": None},
        {"pending_order_quantity": None},
        {"reconciled": False},
    ],
)
def test_unknown_truth_is_not_a_fresh_flat_or_zero_pending_position(
    changes: dict[str, Any],
) -> None:
    state = replace(position(), **changes)
    result = evaluate_paper_hard_limits(snapshot=snapshot(state), intent=intent(state))
    assert result.approved is False
    assert REASON_POSITION_UNKNOWN in result.reasons


def test_pending_partial_fill_and_restart_require_new_reconciled_decisions() -> None:
    state = position(pending_order_quantity=Decimal("1"))
    assert (
        REASON_POSITION_PENDING
        in evaluate_paper_hard_limits(snapshot=snapshot(state), intent=intent(state, "0.5")).reasons
    )
    reconciled = position("1", version="owner-a:after-partial-cancel")
    old_order = intent(state, "1")
    assert (
        REASON_POSITION_CHANGED
        in evaluate_paper_hard_limits(snapshot=snapshot(reconciled), intent=old_order).reasons
    )
    assert (
        REASON_REDUCE_ONLY_OVERSIZE
        in evaluate_paper_hard_limits(
            snapshot=snapshot(reconciled), intent=intent(reconciled, "2")
        ).reasons
    )
    assert evaluate_paper_hard_limits(
        snapshot=snapshot(reconciled), intent=intent(reconciled, "1")
    ).approved
    restarted = replace(reconciled, version="owner-b:reconciled-after-restart")
    assert (
        REASON_POSITION_CHANGED
        in evaluate_paper_hard_limits(
            snapshot=snapshot(restarted), intent=intent(reconciled, "1")
        ).reasons
    )
    assert evaluate_paper_hard_limits(
        snapshot=snapshot(restarted), intent=intent(restarted, "1")
    ).approved


@pytest.mark.parametrize("field", ["quantity", "pending_order_quantity"])
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_nonfinite_position_or_pending_values_fail_closed(field: str, value: str) -> None:
    state = position()
    changes: dict[str, Any] = {field: Decimal(value)}
    broken = replace(state, **changes)
    with pytest.raises(ValueError):
        evaluate_paper_hard_limits(
            snapshot=replace(snapshot(state), position=broken), intent=intent(state)
        )


def test_missing_identity_and_conflicting_asset_exposure_cannot_prove_reduction() -> None:
    state = position()
    assert (
        REASON_POSITION_IDENTITY
        in evaluate_paper_hard_limits(
            snapshot=snapshot(state), intent=intent(state, position_key=None)
        ).reasons
    )
    assert (
        REASON_POSITION_UNKNOWN
        in evaluate_paper_hard_limits(
            snapshot=replace(snapshot(state), position=None), intent=intent(state)
        ).reasons
    )
    broken = replace(snapshot(state), asset_exposure_usdc=Decimal("0"))
    assert (
        REASON_POSITION_CONFLICT
        in evaluate_paper_hard_limits(snapshot=broken, intent=intent(state)).reasons
    )


def sandbox(
    *, quantity: str = "2", pending: str = "0", account_present: bool = True
) -> SimpleNamespace:
    venue = SimpleNamespace(value="hyperliquid")
    instrument_id = SimpleNamespace(value="BTC-PERP", venue=venue)
    strategy_id = SimpleNamespace(value="strategy-a")
    account_id = SimpleNamespace(value="paper-account-a")
    signed_quantity = Decimal(quantity)
    cached_position = SimpleNamespace(
        quantity=abs(signed_quantity),
        account_id=account_id,
        instrument_id=instrument_id,
        strategy_id=strategy_id,
        is_long=signed_quantity > 0,
        is_short=signed_quantity < 0,
        id=SimpleNamespace(value="position-1"),
    )
    order = SimpleNamespace(
        quantity=Decimal("2"),
        filled_qty=Decimal("2") - Decimal(pending),
        leaves_qty=Decimal(pending),
        account_id=account_id,
        instrument_id=instrument_id,
        strategy_id=strategy_id,
        client_order_id=SimpleNamespace(value="exit-1"),
        is_closed=Decimal(pending) == 0,
        status="FILLED" if Decimal(pending) == 0 else "PARTIALLY_FILLED",
    )
    return SimpleNamespace(
        config=SimpleNamespace(instrument_id=instrument_id),
        id=strategy_id,
        signed_position_quantity=Decimal(quantity),
        cache=SimpleNamespace(
            account_for_venue=lambda _: SimpleNamespace(id=account_id) if account_present else None,
            positions_open=lambda **_: [cached_position] if signed_quantity else [],
            orders=lambda **_: [order],
        ),
    )


@pytest.mark.parametrize("signed", ["2", "-2"])
def test_sandbox_truth_uses_partial_remaining_and_fresh_owner_epoch(signed: str) -> None:
    one = sandbox(quantity=signed, pending="0.5")
    two = sandbox(quantity=signed, pending="0.5")
    first = sandbox_position_truth(one, epoch="owner-1")
    same = sandbox_position_truth(two, epoch="owner-1")
    assert first == same
    assert first is not None
    assert first.quantity == Decimal(signed)
    assert first.pending_order_quantity == Decimal("0.5")
    restarted = sandbox_position_truth(two, epoch="owner-2")
    assert restarted is not None and first.version != restarted.version
    settled = sandbox_position_truth(sandbox(quantity=signed), epoch="owner-1")
    assert settled is not None and settled.pending_order_quantity == 0
    assert settled.version != first.version
    # Ephemeral owner versions do not enter canonical PAPER business output.
    a = position(signed, version=first.version)
    b = position(signed, version=restarted.version)
    assert (
        evaluate_paper_hard_limits(snapshot=snapshot(a), intent=intent(a)).to_dict()
        == evaluate_paper_hard_limits(snapshot=snapshot(b), intent=intent(b)).to_dict()
    )


def test_sandbox_unknown_or_conflicting_cache_never_mints_usable_truth() -> None:
    assert sandbox_position_truth(sandbox(account_present=False), epoch="owner-1") is None
    broken = sandbox()
    broken.signed_position_quantity = Decimal("1")
    assert sandbox_position_truth(broken, epoch="owner-1") is None
    assert sandbox_position_truth(sandbox(), epoch="") is None


class _DecimalSubclass(Decimal):
    pass


@pytest.mark.parametrize(
    "signed,value",
    [
        ("0", False),
        ("0", 0.0),
        ("1", True),
        ("1", 1.0),
        ("-1", -1.0),
        ("0", None),
        ("1", "1"),
        ("0", Decimal("NaN")),
        ("1", Decimal("sNaN")),
        ("1", Decimal("Infinity")),
        ("-1", Decimal("-Infinity")),
        ("0", _DecimalSubclass("0")),
        ("1", _DecimalSubclass("1")),
    ],
)
def test_sandbox_callback_truth_requires_an_exact_finite_decimal(signed: str, value: Any) -> None:
    strategy = sandbox(quantity=signed)
    strategy.signed_position_quantity = value
    assert sandbox_position_truth(strategy, epoch="owner-1") is None


@pytest.mark.parametrize("signed", ["0", "1", "-1"])
def test_sandbox_exact_decimal_callback_truth_preserves_entries_and_reductions(signed: str) -> None:
    truth = sandbox_position_truth(sandbox(quantity=signed), epoch="owner-1")
    assert truth is not None
    assert truth.quantity == Decimal(signed)
    order = intent(truth, "1", reduce_only=Decimal(signed) != 0)
    assert evaluate_paper_hard_limits(snapshot=snapshot(truth), intent=order).approved


@pytest.mark.parametrize("opposite", [False, True])
def test_sandbox_netting_scope_never_hides_distinct_open_legs(opposite: bool) -> None:
    strategy = sandbox()
    first = strategy.cache.positions_open()[0]
    second = SimpleNamespace(
        **{
            **vars(first),
            "id": SimpleNamespace(value="position-2"),
            "is_long": not opposite,
            "is_short": opposite,
        }
    )
    strategy.cache.positions_open = lambda **_: [first, second]
    strategy.signed_position_quantity = Decimal("0" if opposite else "4")
    assert sandbox_position_truth(strategy, epoch="owner-1") is None


def test_sandbox_netting_guard_ignores_open_legs_outside_its_exact_scope() -> None:
    strategy = sandbox()
    first = strategy.cache.positions_open()[0]
    others = [
        SimpleNamespace(**{**vars(first), field: SimpleNamespace(value="unrelated")})
        for field in ("account_id", "instrument_id", "strategy_id")
    ]
    strategy.cache.positions_open = lambda **_: [first, *others]
    truth = sandbox_position_truth(strategy, epoch="owner-1")
    assert truth is not None and truth.quantity == Decimal("2")


def test_sandbox_nonterminal_order_with_zero_leaves_is_unknown_pending_truth() -> None:
    strategy = sandbox()
    order = strategy.cache.orders()[0]
    order.is_closed = False
    order.status = "PARTIALLY_FILLED"
    assert sandbox_position_truth(strategy, epoch="owner-1") is None


@pytest.mark.parametrize("terminal", [False, True])
def test_sandbox_valid_partial_or_terminal_cancel_preserves_remaining_truth(terminal: bool) -> None:
    strategy = sandbox(quantity="1.5", pending="0.5")
    order = strategy.cache.orders()[0]
    order.is_closed = terminal
    order.status = "CANCELED" if terminal else "PARTIALLY_FILLED"
    strategy.cache.orders = lambda **_: [order]
    truth = sandbox_position_truth(strategy, epoch="owner-1")
    assert truth is not None
    assert truth.quantity == Decimal("1.5")
    assert truth.pending_order_quantity == (0 if terminal else Decimal("0.5"))
    decision = evaluate_paper_hard_limits(snapshot=snapshot(truth), intent=intent(truth, "1.5"))
    assert decision.approved is terminal


def test_sandbox_pending_scope_excludes_unrelated_accounts_assets_and_strategies() -> None:
    scoped = sandbox(pending="0.5")
    target = scoped.cache.orders()[0]
    unrelated = [
        SimpleNamespace(**{**vars(target), field: SimpleNamespace(value="unrelated")})
        for field in ("account_id", "instrument_id", "strategy_id")
    ]
    scoped.cache.orders = lambda **_: [target, *unrelated]
    truth = sandbox_position_truth(scoped, epoch="owner-1")
    assert truth is not None
    assert truth.pending_order_quantity == Decimal("0.5")
    scoped.cache.orders = lambda **_: [target, target]
    assert sandbox_position_truth(scoped, epoch="owner-1") is None


@pytest.mark.parametrize("field", ["asset_id", "venue_id", "strategy_id"])
def test_intent_metadata_cannot_contradict_its_ownership_key(field: str) -> None:
    state = position()
    order = intent(state, **{field: "another-scope"})
    result = evaluate_paper_hard_limits(snapshot=snapshot(state), intent=order)
    assert result.approved is False
    assert REASON_POSITION_IDENTITY in result.reasons


@pytest.mark.parametrize("value", ["", " ", " account", "account\n", None, 123])
def test_malformed_account_identity_is_not_a_default_local_account(value: Any) -> None:
    state = position(key=replace(KEY, account_id=value))
    with pytest.raises(ValueError):
        evaluate_paper_hard_limits(snapshot=snapshot(state), intent=intent(state))


def test_missing_environment_does_not_fall_back_to_paper() -> None:
    state = position(key=replace(KEY, environment=None))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="environment"):
        evaluate_paper_hard_limits(snapshot=snapshot(state), intent=intent(state))


@pytest.mark.parametrize(
    "field,value",
    [
        ("pending_order_quantity", Decimal("-0.01")),
        ("pending_order_quantity", False),
        ("quantity", 2.0),
        ("quantity", True),
    ],
)
def test_malformed_position_numbers_are_not_normalized(field: str, value: Any) -> None:
    known = position()
    changes: dict[str, Any] = {field: value}
    bad = replace(known, **changes)
    with pytest.raises(ValueError):
        evaluate_paper_hard_limits(
            snapshot=replace(snapshot(known), position=bad), intent=intent(known)
        )


@pytest.mark.parametrize("net,count", [("-2000", 1), ("2000", 0), ("2000", 2)])
def test_conflicting_aggregate_sign_or_other_position_count_is_not_a_valid_book(
    net: str, count: int
) -> None:
    state = position()
    broken = replace(snapshot(state), net_exposure_usdc=Decimal(net), open_position_count=count)
    result = evaluate_paper_hard_limits(snapshot=broken, intent=intent(state))
    assert result.approved is False
    assert REASON_POSITION_CONFLICT in result.reasons
