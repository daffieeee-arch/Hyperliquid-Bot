from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

import pytest

from research import fast_catchup as model
from research.fast_catchup import (
    CAP,
    COSTS,
    INITIAL,
    SECOND,
    Event,
    Intent,
    Level,
    Liquidity,
    Replay,
    limits,
)
from research.harness.errors import IntegrityError

D = Decimal
OFFSET = 1_794_600_600_000_000_000  # A fixed synthetic UTC hour minute10, no real observations.


def book(
    at: int,
    *,
    mid: str = "100000",
    size: str = "1",
    source: str = "hyperliquid",
    available: int | None = None,
    session: str = "s",
) -> Event:
    price = D(mid)
    return Event(
        source,
        session,
        at + 1,
        at,
        OFFSET + at,
        (OFFSET + at) // 1_000_000,
        at if available is None else available,
        f"{source}:{session}:{at}",
        "hl_book" if source == "hyperliquid" else "bn_book",
        (Level(price - 1, D(size)),),
        (Level(price + 1, D(size)),),
    )


def entry(*, side: int = 1, quantity: str = ".00099", decision: int = 0) -> Intent:
    return Intent(
        "entry",
        side,
        decision,
        decision + COSTS[0].latency,
        decision + COSTS[0].latency + SECOND,
        D(quantity),
        D("101000") if side == 1 else D("99000"),
        D("101000") if side == 1 else D("99000"),
        D("99000"),
        D("101000"),
        "fixture",
        "decision",
    )


@pytest.mark.parametrize("side", [-1, 1])
@pytest.mark.parametrize("cost", COSTS, ids=lambda x: x.name)
def test_cost_target_ceiling_proves_one_percent_margin_after_both_fees(
    side: int, cost: Any
) -> None:
    from hyperliquid_bot.paper_engine.precision import adverse_price

    charged, raw = limits(D("100000"), D("100001"), side, cost)
    exit_fill = adverse_price(
        (D("100000") * (1 - D(".0005")) * (1 - cost.friction))
        if side == 1
        else D("100001") * (1 + D(".0005")) * (1 + cost.friction),
        side="SELL" if side == 1 else "BUY",
        max_decimals=1,
    )
    pnl = side * (exit_fill - charged) - cost.fee * (charged + exit_fill)
    assert pnl / (charged / 10) >= D(".01")
    modeled = adverse_price(
        raw * (1 + side * cost.friction), side="BUY" if side == 1 else "SELL", max_decimals=1
    )
    assert side * (modeled - charged) <= 0


def test_liquidity_consumption_is_not_replenished_by_identical_snapshot() -> None:
    depth = Liquidity()
    original = book(0, size=".0005")
    depth.update(original)
    got = depth.quote(original, entry(), COSTS[0], consume=True)
    assert sum(x.fill.quantity for x in got) == D(".0005")
    depth.update(book(SECOND, size=".0005"))
    assert not depth.quote(book(SECOND, size=".0005"), entry(), COSTS[0])
    deeper = book(2 * SECOND, size=".0008")
    depth.update(deeper)
    assert sum(x.fill.quantity for x in depth.quote(deeper, entry(), COSTS[0])) == D(".0003")


def test_raw_limit_and_charged_limit_are_both_enforced_no_double_friction() -> None:
    depth = Liquidity()
    observed = book(SECOND)
    depth.update(observed)
    order = replace(entry(), raw_limit=D("100001"), charged_limit=D("100012"))
    got = depth.quote(observed, order, COSTS[0])
    assert got[0].reference_price == D("100001")
    assert got[0].fill.price == D("100012")
    assert not depth.quote(observed, replace(order, raw_limit=D("100000")), COSTS[0])
    assert not depth.quote(observed, replace(order, charged_limit=D("100011")), COSTS[0])


def harness() -> tuple[Replay, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    return Replay(COSTS[0], 0, 1000 * SECOND, OFFSET, (), rows.append), rows


def deliver(replay: Replay, event: Event) -> None:
    replay.advance(event.available_ns)
    replay.observe([event])


def test_delayed_old_raw_book_never_fills_a_new_order() -> None:
    replay, _ = harness()
    replay.pending = entry(decision=10 * SECOND)
    deliver(replay, book(10 * SECOND, available=11 * SECOND))
    assert replay.state.position_quantity == 0 and replay.pending is not None
    deliver(replay, book(11 * SECOND))
    assert replay.episode is not None and replay.episode.entry_ns == 11 * SECOND
    assert replay.episode.entry_notional <= CAP


def test_nonzero_subminimum_partial_is_owned_and_reduced_with_original_margin() -> None:
    replay, rows = harness()
    replay.pending = entry()
    deliver(replay, book(SECOND, size=".00001"))
    assert replay.episode is not None and replay.episode.entry_notional < 10
    assert replay.state.position_quantity == D(".00001")
    margin = replay.episode.margin
    assert replay.pending is not None and replay.pending.kind == "exit"
    deliver(replay, book(2 * SECOND))
    assert replay.state.position_quantity == 0
    assert replay.state.cash_usdc < INITIAL
    assert replay.episodes[0]["initial_reference_margin"] == margin
    assert replay.episodes[0]["net_margin_roi"] < 0
    assert len([r for r in rows if r["kind"] == "fill"]) == 2


def test_partial_exit_retries_have_delay_without_widening_limit_or_resetting_deadline() -> None:
    replay, _ = harness()
    replay.pending = entry()
    deliver(replay, book(SECOND))
    replay.exit("fixture_exit")
    assert replay.pending is not None
    original = replay.pending
    deliver(replay, book(2 * SECOND, size=".0001"))
    assert replay.state.position_quantity > 0
    assert replay.pending is not None
    assert replay.pending.deadline_ns == original.deadline_ns
    assert replay.pending.raw_limit == original.raw_limit
    assert replay.pending.due_ns == 2 * SECOND + COSTS[0].latency
    quantity = replay.state.position_quantity
    deliver(replay, book(2 * SECOND + 1, size=".0002"))
    assert replay.state.position_quantity == quantity
    replay.start = replay.now
    result = replay.run([])
    assert result["status"] == "FAILED_NULLACCOUNT"
    assert result["net_return"] is None and result["remaining_quantity"] > 0
    assert result["last_observed_state"]["fees_usdc"] > 0


def test_short_favorable_gap_cannot_upsize_quantity_or_break_c100_cap() -> None:
    replay, _ = harness()
    replay.pending = entry(side=-1)
    deliver(replay, book(SECOND, mid="102000"))
    assert replay.state.position_quantity == 0
    assert replay.counts["entry_nonfills"] == 1


@pytest.mark.parametrize("cost", COSTS, ids=lambda x: x.name)
@pytest.mark.parametrize("side", [-1, 1])
def test_owned_long_and_short_account_exit_charges_actual_fees_and_never_flips(
    cost: Any,
    side: int,
) -> None:
    rows: list[dict[str, Any]] = []
    replay = Replay(cost, 0, 1000 * SECOND, OFFSET, (), rows.append)
    replay.pending = replace(entry(side=side), due_ns=cost.latency)
    deliver(replay, book(SECOND))
    assert replay.episode is not None
    margin = replay.episode.margin
    fill_notional = replay.episode.entry_notional
    deliver(replay, book(2 * SECOND, mid="100500" if side == 1 else "99500"))
    assert replay.pending is not None and replay.pending.kind == "exit"
    deliver(replay, book(3 * SECOND, mid="100500" if side == 1 else "99500"))
    assert replay.episode is None and replay.state.position_quantity == 0
    assert replay.episodes[0]["net_margin_roi"] == (replay.state.cash_usdc - INITIAL) / margin
    assert margin == fill_notional / 10
    fee = sum(row["fill"]["fee_usdc"] for row in rows if row["kind"] == "fill")
    assert fee == replay.state.fees_usdc
    assert all(row["reduce_only"] for row in rows if row["kind"] == "risk")


def test_unqualified_ticks_do_not_unlock_an_episode_reentry() -> None:
    replay, _ = harness()
    replay.armed = False
    replay.false_ticks = 4
    replay.tick(SECOND)
    assert replay.false_ticks == 0 and not replay.armed


def tape() -> list[Event]:
    result: list[Event] = []
    for half in range(801):
        at = half * SECOND // 2
        hl = "100300" if at >= 307 * SECOND else "100000"
        bn = "100400" if at >= 305 * SECOND else "100000"
        result.extend((book(at, mid=hl), book(at, mid=bn, source="binance")))
        if 305 * SECOND <= at < 307 * SECOND:
            result.append(
                replace(
                    book(at, source="binance", session="flow"),
                    kind="flow",
                    bids=(),
                    asks=(),
                    signed_notional=D("1000"),
                )
            )
    return result


def test_complete_synthetic_account_carries_fees_and_reports_margin_not_account_roi() -> None:
    rows: list[dict[str, Any]] = []
    replay = Replay(COSTS[0], 0, 400 * SECOND, OFFSET, (), rows.append)
    result = replay.run(tape())
    assert result["status"] == "COMPLETE_CONDITIONAL_RESEARCH"
    assert len(result["episodes"]) == 1
    ep = result["episodes"][0]
    assert ep["net_margin_roi"] >= D(".01")
    assert ep["initial_reference_margin"] == ep["entry_fill_notional"] / 10
    assert result["net_return"] == ep["net_pnl"] / INITIAL
    assert result["last_observed_state"]["fees_usdc"] > 0
    assert result["full_UTC_day_return"] is None
    assert result["capacity_500_2500"] == "NOT_RUN_UNREGISTERED"
    assert len([r for r in rows if r["kind"] == "risk" and r["reduce_only"]]) > 0


def test_future_append_never_changes_preexisting_decisions_fills_or_equity() -> None:
    original = tape()
    rows1: list[dict[str, Any]] = []
    rows2: list[dict[str, Any]] = []
    first = Replay(COSTS[0], 0, 400 * SECOND, OFFSET, (), rows1.append)
    second = Replay(COSTS[0], 0, 420 * SECOND, OFFSET, (), rows2.append)
    first.run(original)
    second.run([*original, book(410 * SECOND, mid="50000")])
    # The fixed endpoint purge can differ near the declared end, so use an earlier prefix.
    assert [r for r in rows1 if r["availability_ns"] < 320 * SECOND] == [
        r for r in rows2 if r["availability_ns"] < 320 * SECOND
    ]


def test_grid_tie_barrier_does_not_use_equal_time_external_flow() -> None:
    replay, rows = harness()
    for event in tape():
        if event.available_ns < 305 * SECOND:
            deliver(replay, event)
            if event.source == "binance":
                replay.tick(event.available_ns + 1)
    replay.flow.clear()
    equal = replace(
        book(306 * SECOND, source="binance", session="flow"),
        kind="flow",
        signed_notional=D("100"),
        bids=(),
        asks=(),
    )
    # The run itself applies tick306 before releasing any event306.
    replay.now = 305 * SECOND
    replay.start = 306 * SECOND
    replay.end = 307 * SECOND
    replay.run([equal])
    assert not [r for r in rows if r["kind"] == "intent" and r["availability_ns"] == 306 * SECOND]


def test_source_gap_while_owned_is_unknown_not_a_flat_reset() -> None:
    replay, _ = harness()
    replay.pending = entry()
    deliver(replay, book(SECOND))
    before = asdict(replay.state)
    with pytest.raises(IntegrityError, match="unknown held"):
        deliver(replay, replace(book(2 * SECOND), kind="reset", reason="gap"))
    assert asdict(replay.state) == before and replay.episode is not None


def test_funding_settlement_with_owned_position_fails_without_zero_imputation() -> None:
    replay, _ = harness()
    replay.pending = entry()
    deliver(replay, book(SECOND))
    replay.funding_ms = ((OFFSET + 2 * SECOND) // 1_000_000,)
    replay.start = replay.now
    result = replay.run([book(2 * SECOND)])
    assert result["status"] == "FAILED_NULLACCOUNT"
    assert "funding" in result["failure"]
    assert result["remaining_quantity"] != 0


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("boundary", ["reset", "session"])
@pytest.mark.parametrize("owned", [False, True])
def test_entire_equal_time_safety_batch_precedes_any_fill(
    reverse: bool,
    boundary: str,
    owned: bool,
) -> None:
    replay, _ = harness()
    replay.pending = entry()
    if owned:
        deliver(replay, book(SECOND))
        replay.start = replay.now
    at = 2 * SECOND if owned else SECOND
    before = asdict(replay.state)
    observed = book(at)
    if boundary == "reset":
        unsafe = replace(book(at, source="binance"), kind="reset", reason="gap")
    else:
        replay.sessions["bn_book"] = "old"
        unsafe = book(at, source="binance", session="new")
    batch = [unsafe, observed] if reverse else [observed, unsafe]
    result = replay.run(batch)
    assert result["status"] == ("FAILED_NULLACCOUNT" if owned else "COMPLETE_CONDITIONAL_RESEARCH")
    assert result["last_observed_state"] == before
    if not owned:
        assert replay.pending is None and replay.book is None


@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_sessions_in_same_first_batch_are_a_boundary_before_fills(reverse: bool) -> None:
    replay, _ = harness()
    replay.pending = entry()
    events = [book(SECOND, session="old"), book(SECOND, session="new")]
    replay.advance(SECOND)
    replay.observe(events[::-1] if reverse else events)
    assert replay.pending is None and replay.state.position_quantity == 0
    assert replay.counts["source_resets"] == 1


@pytest.mark.parametrize("when", [0, 2 * SECOND, 2000 * SECOND])
def test_group_collection_stops_at_cap_plus_one_even_outside_envelope(
    monkeypatch: pytest.MonkeyPatch,
    when: int,
) -> None:
    monkeypatch.setattr(model, "MAX_EQUAL_TIME_EVENTS", 7)
    replay, _ = harness()
    replay.pending = entry()
    deliver(replay, book(SECOND))
    replay.start = replay.now
    before = asdict(replay.state)
    consumed = 0

    def oversized() -> Any:
        nonlocal consumed
        for _ in range(10000):
            consumed += 1
            yield book(when)

    result = replay.run(oversized())
    assert consumed == 8
    assert result["status"] == "FAILED_NULLACCOUNT" and "equal-time" in result["failure"]
    assert result["last_observed_state"] == before


@pytest.mark.parametrize("side", [-1, 1])
@pytest.mark.parametrize("would_trigger", ["target", "stop"])
def test_native_stale_quote_cannot_supply_close_roi_mark_or_exit_band(
    side: int,
    would_trigger: str,
) -> None:
    replay, rows = harness()
    replay.pending = entry(side=side)
    deliver(replay, book(SECOND))
    valid = replay.book
    before = asdict(replay.state)
    baseline_marks = len([row for row in rows if row["kind"] == "equity"])
    direction = side if would_trigger == "target" else -side
    invalid = book(2 * SECOND, mid="110000" if direction == 1 else "90000")
    invalid = replace(invalid, native_ms=invalid.native_ms - 3000, identity="stale-native")
    deliver(replay, invalid)
    assert replay.book is valid and replay.hl_blocked
    assert replay.pending is not None and replay.pending.reason == "stale_guard"
    assert valid is not None and replay.pending.decision_source == valid.identity
    assert asdict(replay.state) == before and replay.equity() is None
    assert len([row for row in rows if row["kind"] == "equity"]) == baseline_marks
    assert replay.episode is not None
    deliver(replay, book(3 * SECOND))
    assert not replay.hl_blocked and replay.state.position_quantity == 0


@pytest.mark.parametrize("reverse", [False, True])
def test_stale_observation_blocks_whole_tied_batch_and_cached_eligibility(reverse: bool) -> None:
    replay, _ = harness()
    deliver(replay, book(0))
    replay.pending = entry()
    valid = book(SECOND)
    stale = replace(valid, native_ms=valid.native_ms - 3000, identity="stale")
    replay.advance(SECOND)
    replay.observe([valid, stale] if reverse else [stale, valid])
    assert replay.state.position_quantity == 0 and replay.pending is None
    assert not replay.book_ready(SECOND) and replay.reference() is None
    deliver(replay, book(SECOND + 1))
    assert replay.book_ready(SECOND + 1)


@pytest.mark.parametrize(
    "error",
    [
        OSError("synthetic input read"),
        ZeroDivisionError("synthetic arithmetic"),
        OSError(),
        ArithmeticError(),
    ],
)
def test_input_or_arithmetic_failure_keeps_owned_state_and_terminal_audit(error: Exception) -> None:
    replay, rows = harness()
    replay.pending = entry()

    def broken() -> Any:
        yield book(SECOND)
        yield book(SECOND + 1)
        raise error

    result = replay.run(broken())
    assert result["status"] == "FAILED_NULLACCOUNT" and result["fatal_run_error"]
    assert result["remaining_quantity"] != 0 and result["net_return"] is None
    assert result["last_observed_state"]["fees_usdc"] > 0
    assert rows[-1]["kind"] == "failure" and str(error) in rows[-1]["reason"]


@pytest.mark.parametrize("reverse", [False, True])
def test_source_break_precedes_stale_price_guard_and_preserves_original_owner(
    reverse: bool,
) -> None:
    replay, _ = harness()
    replay.pending = entry()
    deliver(replay, book(SECOND))
    replay.start = replay.now
    before = asdict(replay.state)
    stale = book(2 * SECOND, mid="110000")
    stale = replace(stale, native_ms=stale.native_ms - 3000)
    reset = replace(book(2 * SECOND, source="binance"), kind="reset", reason="gap")
    result = replay.run([stale, reset] if reverse else [reset, stale])
    assert result["status"] == "FAILED_NULLACCOUNT" and "session/source gap" in result["failure"]
    assert result["last_observed_state"] == before and replay.pending is None
