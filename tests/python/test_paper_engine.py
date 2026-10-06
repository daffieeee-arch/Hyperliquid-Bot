"""PAPER engine tests: risk, kill switch, fees, replay, and no venue client."""

from __future__ import annotations

import ast
import importlib
import json
import pkgutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from hyperliquid_bot.paper_engine import (
    HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE,
    BboEvent,
    MarketEvent,
    MarkEvent,
    NonProductionReferenceStrategy,
    PaperEngine,
    PaperEngineConfig,
    RunAlreadyExistsError,
    StrategyView,
    TargetPosition,
    TradeEvent,
    aggregate_trade_bars,
    bbo_mid,
    format_amsterdam,
    load_hyperliquid_parquet_tape,
    quote_taker_fill,
    read_health,
)
from hyperliquid_bot.paper_engine.engine import DEFAULT_PAPER_LATENCY_NS
from hyperliquid_bot.paper_engine.errors import PaperEngineError
from hyperliquid_bot.paper_engine.execution import FillQuote, PositionState, apply_fill
from hyperliquid_bot.paper_engine.precision import adverse_price, protective_price
from hyperliquid_bot.paper_risk import PaperRiskLimits

CREATED = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
INSTRUMENT = "BTC-PERP"
VENUE = "hyperliquid"


class ScriptedStrategy:
    """Test strategy with an explicit target on each successive event."""

    def __init__(self, targets: tuple[Decimal, ...]) -> None:
        if not targets:
            raise ValueError("targets must be non-empty.")
        self._targets = targets
        self._index = 0

    @property
    def strategy_id(self) -> str:
        return "test-scripted"

    @property
    def configuration_version(self) -> str:
        return "test-v1"

    @property
    def production_eligible(self) -> bool:
        return False

    @property
    def label(self) -> str:
        return "test script"

    def on_market(self, event: MarketEvent, view: StrategyView) -> TargetPosition | None:
        del event, view
        if self._index >= len(self._targets):
            target = self._targets[-1]
        else:
            target = self._targets[self._index]
            self._index += 1
        return TargetPosition(
            instrument_id=INSTRUMENT,
            target_quantity=target,
            reason="scripted",
        )


def _bbo(
    *,
    ns: int,
    bid: str | None,
    ask: str | None,
    bid_size: str | None = "1",
    ask_size: str | None = "1",
    ordinal: int = 1,
) -> BboEvent:
    return BboEvent(
        venue=VENUE,
        instrument_id=INSTRUMENT,
        event_time_utc=CREATED + timedelta(microseconds=ns // 1000),
        received_utc_ns=ns,
        source_event_id=f"bbo-{ordinal}",
        bid_price=None if bid is None else Decimal(bid),
        bid_size=None if bid_size is None else Decimal(bid_size),
        ask_price=None if ask is None else Decimal(ask),
        ask_size=None if ask_size is None else Decimal(ask_size),
        message_ordinal=ordinal,
    )


def _trade(*, ns: int, price: str, size: str, side: str = "SELL", ordinal: int = 1) -> TradeEvent:
    return TradeEvent(
        venue=VENUE,
        instrument_id=INSTRUMENT,
        event_time_utc=CREATED + timedelta(microseconds=ns // 1000),
        received_utc_ns=ns,
        source_event_id=f"trade-{ordinal}",
        price=Decimal(price),
        quantity=Decimal(size),
        aggressor_side=side,
        message_ordinal=ordinal,
    )


def _bbo_at(
    *,
    ns: int,
    event_time: datetime,
    bid: str,
    ask: str,
    ordinal: int,
) -> BboEvent:
    return BboEvent(
        venue=VENUE,
        instrument_id=INSTRUMENT,
        event_time_utc=event_time,
        received_utc_ns=ns,
        source_event_id=f"bbo-{ordinal}",
        bid_price=Decimal(bid),
        bid_size=Decimal("1"),
        ask_price=Decimal(ask),
        ask_size=Decimal("1"),
        message_ordinal=ordinal,
    )


def _mark(*, ns: int, price: str, ordinal: int = 1) -> MarkEvent:
    return MarkEvent(
        venue=VENUE,
        instrument_id=INSTRUMENT,
        event_time_utc=CREATED + timedelta(microseconds=ns // 1000),
        received_utc_ns=ns,
        source_event_id=f"mark-{ordinal}",
        mark_price=Decimal(price),
        message_ordinal=ordinal,
    )


def _instant(**overrides: Any) -> PaperEngineConfig:
    """Zero latency, opted in: for tests that are not about latency."""

    return PaperEngineConfig(latency_ns=0, allow_zero_latency=True, **overrides)


def _engine(
    root: Path,
    run_id: str,
    *,
    strategy: ScriptedStrategy | NonProductionReferenceStrategy | None = None,
    config: PaperEngineConfig | None = None,
) -> PaperEngine:
    return PaperEngine(
        store_root=root,
        run_id=run_id,
        created_at_utc=CREATED,
        config=_instant() if config is None else config,
        strategy=strategy,
    )


def _loose_loss_limits(*, drawdown: str, daily: str) -> PaperRiskLimits:
    return PaperRiskLimits(
        drawdown_kill_fraction=Decimal(drawdown),
        daily_loss_fraction=Decimal(daily),
        weekly_loss_fraction=Decimal("0.50"),
    )


def test_amsterdam_labels_cest_and_cet() -> None:
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert format_amsterdam(summer, field_name="summer") == "2026-07-15 14:00:00 CEST"
    assert format_amsterdam(winter, field_name="winter") == "2026-01-15 13:00:00 CET"


def test_reference_strategy_is_non_production_and_flat(tmp_path: Path) -> None:
    strategy = NonProductionReferenceStrategy()
    assert strategy.production_eligible is False
    assert "NON-PRODUCTION" in strategy.label
    engine = _engine(tmp_path, "flatref01", strategy=strategy)
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001"))
    engine.close()
    health = read_health(engine.health_path)
    assert health["production_eligible"] is False
    assert health["strategy_id"] == "nonprod-reference-flat"
    assert health["position_quantity"] == "0"
    assert health["fill_count"] == 0
    assert health["venue_orders_submitted"] is False
    assert _text(health["observed_at_local"]) == "2026-07-15 14:00:00 CEST"
    assert health["timezone"] == "Europe/Amsterdam"
    assert health["resume"] is False


def test_adverse_price_and_exact_fee_slippage() -> None:
    assert adverse_price(
        Decimal("100000.4"),
        side="BUY",
        max_decimals=1,
    ) == Decimal("100001")
    assert adverse_price(
        Decimal("100000.4"),
        side="SELL",
        max_decimals=1,
    ) == Decimal("100000")
    assert adverse_price(
        Decimal("1234.56"),
        side="BUY",
        max_decimals=5,
    ) == Decimal("1234.6")
    assert adverse_price(Decimal("9999.96"), side="BUY", max_decimals=1) == Decimal("10000")

    plain = quote_taker_fill(
        side="BUY",
        quantity=Decimal("0.00010"),
        touch_price=Decimal("100000"),
        touch_size=Decimal("1"),
        slippage_fraction=Decimal("0"),
        taker_fee_rate=HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE,
        size_increment=Decimal("0.00001"),
        max_price_decimals=1,
        source="bbo",
    )
    assert plain is not None
    assert plain.price == Decimal("100000")
    assert plain.fee_usdc == Decimal("100000") * Decimal("0.00010") * Decimal("0.00045")
    assert plain.fee_usdc == Decimal("0.0045")

    slipped = quote_taker_fill(
        side="BUY",
        quantity=Decimal("0.00010"),
        touch_price=Decimal("100000"),
        touch_size=Decimal("1"),
        slippage_fraction=Decimal("0.0001"),
        taker_fee_rate=HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE,
        size_increment=Decimal("0.00001"),
        max_price_decimals=1,
        source="bbo",
    )
    assert slipped is not None
    assert slipped.price == Decimal("100010")
    assert slipped.fee_usdc == Decimal("100010") * Decimal("0.00010") * Decimal("0.00045")


def test_partial_fill_caps_at_touch_size(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "partial01",
        strategy=ScriptedStrategy((Decimal("0.00010"),)),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000", ask_size="0.00005", bid_size="1"))
    engine.close()
    state = _state(engine)
    assert state["position_quantity"] == "0.00005"
    orders = _objects(state["orders"])
    assert orders[0]["status"] == "PARTIALLY_FILLED"
    assert orders[0]["filled_quantity"] == "0.00005"
    fills = _objects(state["fills"])
    assert fills[0]["source"] == "bbo"
    assert fills[0]["venue_fill"] is False


def test_latency_fills_on_the_later_book(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "latency01",
        strategy=ScriptedStrategy((Decimal("0.00010"),)),
        config=PaperEngineConfig(latency_ns=1_000_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="99990", ask="100000", ordinal=1))
    engine.on_event(_bbo(ns=500_000_000, bid="100000", ask="100010", ordinal=2))
    assert engine.position_quantity == Decimal("0")
    engine.on_event(_bbo(ns=1_000_000_000, bid="100010", ask="100020", ordinal=3))
    engine.close()
    fills = _objects(_state(engine)["fills"])
    assert len(fills) == 1
    assert fills[0]["price"] == "100020"
    assert fills[0]["received_utc_ns"] == 1_000_000_000


def test_trade_fill_when_the_book_is_absent(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "tradefill1",
        strategy=ScriptedStrategy((Decimal("0.00010"),)),
    )
    engine.on_event(_trade(ns=0, price="100000", size="0.5", side="SELL"))
    engine.close()
    fills = _objects(_state(engine)["fills"])
    assert fills[0]["source"] == "trade"
    assert fills[0]["price"] == "100000"
    health = read_health(engine.health_path)
    assert health["mark_price"] is None
    assert health["mark_source"] is None


def test_missing_and_crossed_prices_do_not_invent_a_mid(tmp_path: Path) -> None:
    assert bbo_mid(_bbo(ns=0, bid="100000", ask=None, ask_size=None)) is None
    assert bbo_mid(_bbo(ns=0, bid="100", ask="100")) is None

    engine = _engine(
        tmp_path,
        "missing01",
        strategy=ScriptedStrategy((Decimal("0.00010"), Decimal("0.00020"))),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000, bid="100000", ask=None, ask_size=None, ordinal=2))
    engine.on_event(_trade(ns=2_000_000, price="111111", size="0.2", ordinal=3))
    engine.close()
    health = read_health(engine.health_path)
    assert health["mark_price"] is None
    assert health["mark_source"] is None
    assert health["unrealized_pnl_usdc"] is None
    assert health["kill_reason"] == "missing_price"
    reasons = [item["reason"] for item in _objects(_state(engine)["risk_rejections"])]
    assert "halt_new" in reasons
    assert engine.position_quantity == Decimal("0.00010")


def test_max_position_and_max_notional_reject(tmp_path: Path) -> None:
    position_engine = _engine(
        tmp_path,
        "maxpos001",
        strategy=ScriptedStrategy((Decimal("1"),)),
        config=_instant(max_position_quantity=Decimal("0.05")),
    )
    position_engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    position_engine.close()
    assert position_engine.position_quantity == Decimal("0")
    assert _objects(_state(position_engine)["risk_rejections"])[0]["reason"] == "max_position"

    notional_engine = _engine(
        tmp_path,
        "maxnot001",
        strategy=ScriptedStrategy((Decimal("0.00020"),)),
        config=_instant(max_notional_usdc=Decimal("15")),
    )
    notional_engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    notional_engine.close()
    assert notional_engine.position_quantity == Decimal("0")
    assert _objects(_state(notional_engine)["risk_rejections"])[0]["reason"] == "max_notional"


def test_per_trade_sizing_clips_down_and_fee_matches(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "sized0001",
        strategy=ScriptedStrategy((Decimal("1"),)),
        config=_instant(max_position_quantity=Decimal("1")),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    engine.close()
    # 0.25% of 100k over a 2% stop is 12,500 USDC, sized at the 101,000 limit
    # price (ask + 1% band) so the hard limits hold at the worst admissible fill.
    assert engine.position_quantity == Decimal("0.12376")
    fills = _objects(_state(engine)["fills"])
    assert fills[0]["quantity"] == "0.12376"
    assert fills[0]["price"] == "100000"
    assert fills[0]["fee_usdc"] == "5.5692"
    assert Decimal(str(fills[0]["quantity"])) < Decimal("1")


def test_clipped_order_with_latency_fills_instead_of_rearming(tmp_path: Path) -> None:
    # A clipped working order must survive the same target on later events.
    # Otherwise it is cancelled and re-armed on every event and never fills
    # while events arrive faster than the configured latency.
    engine = _engine(
        tmp_path,
        "cliplat01",
        strategy=ScriptedStrategy((Decimal("1"),)),
        config=PaperEngineConfig(latency_ns=250_000_000),
    )
    for index in range(10):
        engine.on_event(_bbo(ns=index * 100_000_000, bid="99999", ask="100000", ordinal=index + 1))
    engine.close()
    state = _state(engine)
    assert engine.position_quantity == Decimal("0.12376")
    orders = _objects(state["orders"])
    assert [order["status"] for order in orders] == ["FILLED"]
    assert _objects(state["fills"])[0]["received_utc_ns"] == 300_000_000


def test_repeated_block_is_one_timestamped_rejection(tmp_path: Path) -> None:
    # After the first fill every event asks to add to the open position, which
    # paper_risk blocks. That is one rejection, recorded when it starts, and it
    # is already on disk before close() so a crash cannot lose it.
    engine = _engine(
        tmp_path,
        "repeatrej1",
        strategy=ScriptedStrategy((Decimal("1"),)),
    )
    for index in range(3):
        engine.on_event(_bbo(ns=index * 1_000_000, bid="99999", ask="100000", ordinal=index + 1))
    ledger = _ledger(tmp_path / "repeatrej1")
    assert [row["received_utc_ns"] for row in ledger if row["type"] == "risk_rejected"] == [
        1_000_000
    ]
    for index in range(3, 300):
        engine.on_event(_bbo(ns=index * 1_000_000, bid="99999", ask="100000", ordinal=index + 1))
    engine.close()
    assert engine.position_quantity == Decimal("0.12376")
    state = _state(engine)
    rejections = _objects(state["risk_rejections"])
    assert len(rejections) == 1
    assert rejections[0]["reason"] == "paper_risk"
    assert rejections[0]["received_utc_ns"] == 1_000_000
    assert state["risk_rejection_count"] == 1
    assert read_health(engine.health_path)["risk_rejection_count"] == 1
    rejected = [row for row in _ledger(tmp_path / "repeatrej1") if row["type"] == "risk_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["received_utc_ns"] == 1_000_000


def test_block_is_recorded_again_after_it_clears(tmp_path: Path) -> None:
    # "0.12376" is the risk-sized position, so that target clears the block.
    targets = tuple(Decimal(text) for text in ("1", "1", "1", "0.12376", "1", "1"))
    engine = _engine(tmp_path, "reblock01", strategy=ScriptedStrategy(targets))
    for index in range(len(targets)):
        engine.on_event(_bbo(ns=index * 1_000_000, bid="99999", ask="100000", ordinal=index + 1))
    engine.close()
    rejections = _objects(_state(engine)["risk_rejections"])
    assert [row["received_utc_ns"] for row in rejections] == [1_000_000, 4_000_000]
    assert _state(engine)["risk_rejection_count"] == 2


def test_kill_flatten_replaces_strategy_exit_waiting_on_latency(tmp_path: Path) -> None:
    # A strategy exit identical to the flatten is still waiting out its
    # latency when the drawdown kill fires. The kill flatten is zero-latency,
    # so it must replace that order and close on the same event.
    engine = _engine(
        tmp_path,
        "killlat01",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0"))),
        config=PaperEngineConfig(
            latency_ns=250_000_000,
            risk_limits=_loose_loss_limits(drawdown="0.001", daily="0.50"),
        ),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=300_000_000, bid="100000", ask="100001", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    engine.on_event(_bbo(ns=400_000_000, bid="99000", ask="99001", ordinal=3))
    assert engine.kill_switch == "FLATTEN_HALT"
    assert engine.position_quantity == Decimal("0")
    engine.close()
    orders = _objects(_state(engine)["orders"])
    assert [(order["side"], order["status"], order["reason"]) for order in orders] == [
        ("BUY", "FILLED", "scripted"),
        ("SELL", "CANCELED", "replaced"),
        ("SELL", "FILLED", "kill-flatten"),
    ]
    assert read_health(engine.health_path)["flatten_blocked_missing_price"] is False


def test_waiting_entry_is_cancelled_when_a_kill_switch_is_set(tmp_path: Path) -> None:
    # A quiet feed trips the stale-data kill while a flat run still has an
    # entry waiting out its latency. The entry is cancelled at that moment.
    engine = _engine(
        tmp_path,
        "haltwait1",
        strategy=ScriptedStrategy((Decimal("0.1"),)),
        config=PaperEngineConfig(latency_ns=250_000_000, stale_after_ns=1_000_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000", ordinal=1))
    engine.on_clock(now_utc_ns=2_000_000_000, now_utc=CREATED + timedelta(seconds=2))
    assert engine.kill_switch == "FLATTEN_HALT"
    engine.close()
    assert engine.position_quantity == Decimal("0")
    state = _state(engine)
    assert state["open_order"] is None
    assert [(order["status"], order["reason"]) for order in _objects(state["orders"])] == [
        ("CANCELED", "halted")
    ]


class _RaisingAfterFirstCall(ScriptedStrategy):
    def on_market(self, event: MarketEvent, view: StrategyView) -> TargetPosition | None:
        if self._index >= 1:
            raise RuntimeError("strategy failure")
        return super().on_market(event, view)


def test_exception_mid_event_fails_the_run_closed(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "raising01",
        strategy=_RaisingAfterFirstCall((Decimal("0.1"),)),
        config=PaperEngineConfig(latency_ns=250_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000", ordinal=1))
    with pytest.raises(RuntimeError, match="strategy failure"):
        engine.on_event(_bbo(ns=300_000_000, bid="99999", ask="100000", ordinal=2))
    types = [row["type"] for row in _ledger(tmp_path / "raising01")]
    assert types == ["order_accepted", "fill", "order_completed", "stop_set"]
    health = read_health(engine.health_path)
    assert health["status"] == "FAILED"
    assert health["position_quantity"] == "0.1"
    assert health["fill_count"] == 1
    assert health["ledger_write_failed"] is False
    with pytest.raises(PaperEngineError, match="failed"):
        engine.on_event(_bbo(ns=400_000_000, bid="99999", ask="100000", ordinal=3))
    engine.close()
    closed = read_health(engine.health_path)
    assert closed["status"] == "FAILED"
    assert closed["run_closed"] is True
    assert closed["observed_at_utc"] == health["observed_at_utc"]


def test_failed_ledger_write_is_not_retried_into_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(
        tmp_path,
        "ioerror01",
        strategy=ScriptedStrategy((Decimal("0.1"),)),
    )

    def failing_fsync(descriptor: int) -> None:
        del descriptor
        raise OSError("disk failure")

    monkeypatch.setattr("os.fsync", failing_fsync)
    with pytest.raises(OSError, match="disk failure"):
        engine.on_event(_bbo(ns=0, bid="99999", ask="100000", ordinal=1))
    monkeypatch.undo()
    types = [row["type"] for row in _ledger(tmp_path / "ioerror01")]
    assert types == ["order_accepted", "fill", "order_completed", "stop_set"]
    health = read_health(engine.health_path)
    assert health["status"] == "FAILED"
    assert health["ledger_write_failed"] is True
    engine.close()
    assert read_health(engine.health_path)["status"] == "FAILED"
    assert len(_ledger(tmp_path / "ioerror01")) == 4


@pytest.mark.parametrize(("durable", "expect_fsync"), [(True, True), (False, False)])
def test_durable_ledger_controls_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    durable: bool,
    expect_fsync: bool,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("os.fsync", lambda descriptor: calls.append(descriptor))
    engine = _engine(
        tmp_path,
        "durable01",
        strategy=ScriptedStrategy((Decimal("0.1"),)),
        config=_instant(durable_ledger=durable),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    engine.close()
    assert bool(calls) is expect_fsync
    types = [row["type"] for row in _ledger(tmp_path / "durable01")]
    assert types == ["order_accepted", "fill", "order_completed", "stop_set"]


def test_state_keeps_recent_records_and_full_counts(tmp_path: Path) -> None:
    targets = tuple(Decimal("0.01") if index % 2 == 0 else Decimal("0") for index in range(240))
    engine = _engine(tmp_path, "bounded01", strategy=ScriptedStrategy(targets))
    for index in range(240):
        engine.on_event(_bbo(ns=index * 1_000_000, bid="99999", ask="100000", ordinal=index + 1))
    engine.close()
    state = _state(engine)
    assert state["schema"] == "paper-engine-state-v2"
    assert state["recent_record_limit"] == 100
    assert state["order_count"] == 240
    assert state["fill_count"] == 240
    assert len(_objects(state["orders"])) == 100
    assert len(_objects(state["fills"])) == 100
    assert _objects(state["orders"])[-1]["side"] == "SELL"
    health = read_health(engine.health_path)
    assert health["order_count"] == 240
    assert health["fill_count"] == 240
    ledger = _ledger(tmp_path / "bounded01")
    assert sum(1 for row in ledger if row["type"] == "order_completed") == 240
    assert sum(1 for row in ledger if row["type"] == "fill") == 240


def test_daily_loss_halts_entries_but_allows_flatten(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "dailyloss1",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0.2"), Decimal("0"))),
        config=_instant(risk_limits=_loose_loss_limits(drawdown="0.50", daily="0.001")),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000_000, bid="99000", ask="99001", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    assert engine.kill_switch == "HALT_NEW"
    engine.on_event(_bbo(ns=2_000_000_000, bid="99000", ask="99001", ordinal=3))
    engine.close()
    health = read_health(engine.health_path)
    assert health["kill_reason"] == "daily_loss"
    assert health["position_quantity"] == "0"
    assert [item["side"] for item in _objects(_state(engine)["orders"])] == ["BUY", "SELL"]
    reasons = [item["reason"] for item in _objects(_state(engine)["risk_rejections"])]
    assert "halt_new" in reasons


def test_drawdown_kill_switch_flattens_and_halts(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "drawdown1",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0.2"))),
        config=_instant(risk_limits=_loose_loss_limits(drawdown="0.001", daily="0.50")),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000_000, bid="99000", ask="99001", ordinal=2))
    engine.close()
    health = read_health(engine.health_path)
    assert health["kill_switch"] == "FLATTEN_HALT"
    assert health["kill_reason"] == "drawdown"
    assert health["position_quantity"] == "0"
    assert [item["side"] for item in _objects(_state(engine)["orders"])] == ["BUY", "SELL"]
    assert health["venue_orders_submitted"] is False


def test_stale_data_flattens_and_halts(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "stale0001",
        strategy=ScriptedStrategy((Decimal("0.00010"), Decimal("0.00010"))),
        config=_instant(stale_after_ns=5_000_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    assert engine.position_quantity == Decimal("0.00010")
    engine.on_event(_bbo(ns=6_000_000_000, bid="100000", ask="100001", ordinal=2))
    engine.close()
    health = read_health(engine.health_path)
    assert health["kill_reason"] == "stale_data"
    assert health["stale"] is True
    assert health["position_quantity"] == "0"
    assert health["status"] == "FLATTEN_HALT"


def test_on_clock_stale_uses_last_touch_not_a_synthetic_mid(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "clock0001",
        strategy=ScriptedStrategy((Decimal("0.00010"),)),
        config=_instant(stale_after_ns=5_000_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001"))
    engine.on_clock(now_utc_ns=5_000_000_001, now_utc=CREATED + timedelta(seconds=6))
    engine.close()
    health = read_health(engine.health_path)
    assert health["position_quantity"] == "0"
    fills = _objects(_state(engine)["fills"])
    assert [item["side"] for item in fills] == ["BUY", "SELL"]
    assert fills[1]["price"] == "100000"
    assert _text(health["observed_at_local"]).endswith("CEST")


def test_latency_defaults_above_zero_and_zero_needs_an_opt_in() -> None:
    assert PaperEngineConfig().latency_ns == DEFAULT_PAPER_LATENCY_NS
    assert DEFAULT_PAPER_LATENCY_NS > 0
    with pytest.raises(PaperEngineError, match="allow_zero_latency"):
        PaperEngineConfig(latency_ns=0)
    assert _instant().latency_ns == 0


def test_config_keeps_slippage_inside_the_bands_and_the_stop_below_one() -> None:
    with pytest.raises(PaperEngineError, match="entry_price_band_fraction"):
        _instant(slippage_fraction=Decimal("0.01"))
    with pytest.raises(PaperEngineError, match="exit_price_band_fraction"):
        _instant(
            slippage_fraction=Decimal("0.02"),
            entry_price_band_fraction=Decimal("0.03"),
            exit_price_band_fraction=Decimal("0.02"),
        )
    with pytest.raises(PaperEngineError, match="effective stop distance"):
        _instant(stop_distance_fraction=Decimal("0.5"), volatility_multiple=Decimal("2"))


def test_protective_price_never_widens_the_band() -> None:
    assert protective_price(Decimal("101001.01"), side="BUY", max_decimals=1) == Decimal("101001")
    assert protective_price(Decimal("98999.99"), side="SELL", max_decimals=1) == Decimal("99000")
    assert protective_price(Decimal("1234.56"), side="BUY", max_decimals=5) == Decimal("1234.5")


def test_stop_exits_a_long_once_the_mark_crosses_the_sizing_stop(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stoplong1", strategy=ScriptedStrategy((Decimal("0.1"),)))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    # Entry 100001, 2% stop distance (the sizing assumption) -> stop 98000.98.
    engine.on_event(_bbo(ns=1_000_000, bid="98200", ask="98201", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    engine.on_event(_bbo(ns=2_000_000, bid="97990", ask="97991", ordinal=3))
    engine.on_event(_bbo(ns=3_000_000, bid="97990", ask="97991", ordinal=4))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    assert engine.kill_switch == "NONE"
    ledger = _ledger(tmp_path / "stoplong1")
    stop_set = [row for row in ledger if row["type"] == "stop_set"]
    assert len(stop_set) == 1
    assert stop_set[0]["stop_price"] == "98000.98"
    assert stop_set[0]["average_entry_price"] == "100001"
    assert stop_set[0]["stop_distance_fraction"] == "0.02"
    triggered = [row for row in ledger if row["type"] == "stop_triggered"]
    assert len(triggered) == 1
    assert triggered[0]["mark_price"] == "97990.5"
    assert triggered[0]["mark_source"] == "bbo_mid"
    assert triggered[0]["received_utc_ns"] == 2_000_000
    state = _state(engine)
    assert [(row["side"], row["status"], row["reason"]) for row in _objects(state["orders"])] == [
        ("BUY", "FILLED", "scripted"),
        ("SELL", "FILLED", "stop-exit"),
    ]
    assert _objects(state["fills"])[1]["price"] == "97990"
    # The strategy keeps asking for the long it was stopped out of: one block.
    assert [row["reason"] for row in _objects(state["risk_rejections"])] == ["stop_lockout"]
    health = read_health(engine.health_path)
    assert health["status"] == "COMPLETED"
    assert health["stop_price"] is None
    assert health["stop_exit_pending"] is False
    assert health["stop_lockout"] == "long"


def test_partial_stop_exit_takes_each_quote_once_and_retries_until_flat(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stoppart1", strategy=ScriptedStrategy((Decimal("0.1"),)))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    # Only 0.04 BTC is bid at the stop-out quote: the exit takes it once, not
    # again for the second stop check of the same event.
    engine.on_event(_bbo(ns=1_000_000, bid="97990", ask="97991", bid_size="0.04", ordinal=2))
    assert engine.position_quantity == Decimal("0.06")
    assert read_health(engine.health_path)["stop_exit_pending"] is True
    engine.on_event(_bbo(ns=2_000_000, bid="97980", ask="97981", bid_size="0.04", ordinal=3))
    assert engine.position_quantity == Decimal("0.02")
    engine.on_event(_bbo(ns=3_000_000, bid="97970", ask="97971", bid_size="0.04", ordinal=4))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    exits = [row for row in _objects(_state(engine)["orders"]) if row["reason"] == "stop-exit"]
    assert [row["filled_quantity"] for row in exits] == ["0.04", "0.04", "0.02"]
    assert read_health(engine.health_path)["stop_exit_pending"] is False


def test_stop_exits_a_short_when_the_mark_rises_through_the_stop(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stopshort", strategy=ScriptedStrategy((Decimal("-0.1"),)))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    assert engine.position_quantity == Decimal("-0.1")
    # Short entry 100000 -> stop 102000; the mid 102050.5 is above it.
    engine.on_event(_bbo(ns=1_000_000, bid="102050", ask="102051", ordinal=2))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    state = _state(engine)
    assert [(row["side"], row["reason"]) for row in _objects(state["orders"])] == [
        ("SELL", "scripted"),
        ("BUY", "stop-exit"),
    ]
    assert _objects(state["fills"])[1]["price"] == "102051"
    assert read_health(engine.health_path)["stop_lockout"] == "short"


def test_stop_lockout_clears_when_the_target_goes_flat(tmp_path: Path) -> None:
    targets = tuple(Decimal(text) for text in ("0.1", "0.1", "0.1", "0", "0.1"))
    engine = _engine(tmp_path, "lockout01", strategy=ScriptedStrategy(targets))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    for index in range(1, 5):
        engine.on_event(_bbo(ns=index * 1_000_000, bid="97990", ask="97991", ordinal=index + 1))
    engine.close()
    state = _state(engine)
    rejections = _objects(state["risk_rejections"])
    assert [(row["reason"], row["received_utc_ns"]) for row in rejections] == [
        ("stop_lockout", 1_000_000)
    ]
    cleared = [
        row for row in _ledger(tmp_path / "lockout01") if row["type"] == "stop_lockout_cleared"
    ]
    assert [row["received_utc_ns"] for row in cleared] == [3_000_000]
    assert [row["side"] for row in _objects(state["orders"])] == ["BUY", "SELL", "BUY"]
    assert engine.position_quantity == Decimal("0.1")
    health = read_health(engine.health_path)
    assert health["stop_lockout"] is None
    # The re-entry at 97991 carries its own stop: 97991 * 0.98.
    assert health["stop_price"] == "96031.18"


def test_stop_triggers_on_a_fresher_venue_mark(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path, "stopmark1", strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0")))
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_mark(ns=1_000_000, price="97000", ordinal=2))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    ledger = _ledger(tmp_path / "stopmark1")
    triggered = [row for row in ledger if row["type"] == "stop_triggered"]
    assert triggered[0]["mark_source"] == "venue_mark"
    assert triggered[0]["mark_price"] == "97000"
    # The exit is an IOC at the real touch, not at the mark or the stop.
    assert _objects(_state(engine)["fills"])[1]["price"] == "100000"


def test_strategy_exit_beyond_its_band_does_not_fill_and_the_stop_still_closes(
    tmp_path: Path,
) -> None:
    engine = _engine(
        tmp_path,
        "exitband1",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0"))),
        config=PaperEngineConfig(latency_ns=250_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=300_000_000, bid="100000", ask="100001", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    # The strategy exit was priced at bid 100000 with a 10% band (limit 90000);
    # by the time it is eligible the bid is 85000, so the IOC does not fill.
    engine.on_event(_bbo(ns=600_000_000, bid="85000", ask="85001", ordinal=3))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    orders = _objects(_state(engine)["orders"])
    assert [(row["side"], row["status"], row["reason"]) for row in orders] == [
        ("BUY", "FILLED", "scripted"),
        ("SELL", "CANCELED", "scripted"),
        ("SELL", "FILLED", "stop-exit"),
    ]
    assert orders[1]["unfilled_reason"] == "price_band"
    assert orders[1]["limit_price"] == "90000"


def test_entry_fill_beyond_its_band_is_cancelled(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "entryband",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0"))),
        config=PaperEngineConfig(latency_ns=250_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000", ordinal=1))
    # Limit 101000 (ask + 1%); the ask jumped to 101500 before the order arrived.
    engine.on_event(_bbo(ns=300_000_000, bid="101499", ask="101500", ordinal=2))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    orders = _objects(_state(engine)["orders"])
    assert orders[0]["status"] == "CANCELED"
    assert orders[0]["unfilled_reason"] == "price_band"
    assert orders[0]["limit_price"] == "101000"
    assert _objects(_state(engine)["fills"]) == []


def test_hard_limits_hold_at_the_worst_admissible_price(tmp_path: Path) -> None:
    # 0.0001 BTC is 10.00 USDC at the ask but 10.10 at the 101000 limit, so a
    # 10.05 notional cap must reject it even though the touch alone would pass.
    engine = _engine(
        tmp_path,
        "worstcase",
        strategy=ScriptedStrategy((Decimal("0.0001"),)),
        config=_instant(max_notional_usdc=Decimal("10.05")),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    assert _objects(_state(engine)["risk_rejections"])[0]["reason"] == "max_notional"


def test_short_entry_is_sized_at_the_same_risk_price_as_a_long(tmp_path: Path) -> None:
    # A SELL limit only floors the fill price, so a short is sized at the bid
    # plus the band, like a long at the ask plus the band: 12500 / 101000.
    engine = _engine(tmp_path, "shortsize", strategy=ScriptedStrategy((Decimal("-1"),)))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001"))
    engine.close()
    assert engine.position_quantity == Decimal("-0.12376")


def test_config_rejects_a_stop_not_wider_than_slippage() -> None:
    with pytest.raises(PaperEngineError, match="exceed slippage_fraction"):
        _instant(stop_distance_fraction=Decimal("0.005"), slippage_fraction=Decimal("0.005"))
    # A tight stop with a wide entry band is a valid choice.
    _instant(stop_distance_fraction=Decimal("0.015"), entry_price_band_fraction=Decimal("0.02"))


def test_stop_ignores_a_trade_printed_before_the_position_opened(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "oldtrade1",
        strategy=ScriptedStrategy((Decimal("0"), Decimal("0.1"))),
    )
    engine.on_event(_trade(ns=0, price="97000", size="0.5", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000, bid="100000", ask="100001", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    # One-sided book and no venue mark: the only trade is older than the stop.
    engine.on_event(_bbo(ns=2_000_000, bid="99990", ask=None, ask_size=None, ordinal=3))
    engine.close()
    assert engine.position_quantity == Decimal("0.1")
    assert not [row for row in _ledger(tmp_path / "oldtrade1") if row["type"] == "stop_triggered"]


def test_stop_fallback_follows_processing_order_not_receive_stamps(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "tradeorder", strategy=ScriptedStrategy((Decimal("0.1"),)))
    engine.on_event(_bbo(ns=2_000_000, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=3_000_000, bid="99990", ask=None, ask_size=None, ordinal=2))
    # Processed after the stop was set, though stamped earlier by its feed.
    engine.on_event(_trade(ns=1_000_000, price="97000", size="0.5", ordinal=3))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    triggered = [row for row in _ledger(tmp_path / "tradeorder") if row["type"] == "stop_triggered"]
    assert triggered[0]["mark_source"] == "last_trade"


def test_each_new_quote_is_fresh_liquidity_but_a_mark_is_not(tmp_path: Path) -> None:
    # PAPER fills have no market impact across quote updates: a new BBO
    # message offers its displayed size again, a mark-only event does not.
    engine = _engine(tmp_path, "freshquote", strategy=ScriptedStrategy((Decimal("0.1"),)))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000, bid="97990", ask="97991", bid_size="0.04", ordinal=2))
    assert engine.position_quantity == Decimal("0.06")
    engine.on_event(_mark(ns=2_000_000, price="97990", ordinal=3))
    assert engine.position_quantity == Decimal("0.06")
    engine.on_event(_bbo(ns=3_000_000, bid="97990", ask="97995", bid_size="0.04", ordinal=4))
    engine.close()
    assert engine.position_quantity == Decimal("0.02")


def test_zero_displayed_size_blocks_a_flatten_as_missing_price(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "zerosize1",
        strategy=ScriptedStrategy((Decimal("0.1"),)),
        config=_instant(risk_limits=_loose_loss_limits(drawdown="0.001", daily="0.50")),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000, bid="99000", ask="99001", bid_size="0", ordinal=2))
    assert engine.kill_switch == "FLATTEN_HALT"
    assert engine.position_quantity == Decimal("0.1")
    assert read_health(engine.health_path)["flatten_blocked_missing_price"] is True
    engine.close()


def test_used_up_quote_is_not_filled_twice(tmp_path: Path) -> None:
    # The strategy exit takes the 0.04 BTC bid; the stop that fires on the
    # same quote must not take that bid again, nor on a mark-only event.
    engine = _engine(
        tmp_path,
        "depleted1",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0"))),
        config=PaperEngineConfig(latency_ns=250_000_000),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=300_000_000, bid="100000", ask="100001", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    engine.on_event(_bbo(ns=600_000_000, bid="97990", ask="97991", bid_size="0.04", ordinal=3))
    assert engine.position_quantity == Decimal("0.06")
    assert read_health(engine.health_path)["stop_exit_pending"] is True
    engine.on_event(_mark(ns=700_000_000, price="97990", ordinal=4))
    assert engine.position_quantity == Decimal("0.06")
    engine.on_event(_bbo(ns=800_000_000, bid="97980", ask="97981", ordinal=5))
    engine.close()
    assert engine.position_quantity == Decimal("0")


class _SilentAfterTargets(ScriptedStrategy):
    """Returns no target once its script is used up, so nothing is resubmitted."""

    def on_market(self, event: MarketEvent, view: StrategyView) -> TargetPosition | None:
        if self._index >= len(self._targets):
            return None
        return super().on_market(event, view)


def test_band_blocked_exit_is_not_reported_as_a_missing_price(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "bandflag1",
        strategy=_SilentAfterTargets((Decimal("0.1"), Decimal("0"))),
        config=PaperEngineConfig(latency_ns=250_000_000, exit_price_band_fraction=Decimal("0.005")),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=300_000_000, bid="100000", ask="100001", ordinal=2))
    # The exit's limit is 99500; a 1% drop is beyond it but above the stop.
    engine.on_event(_bbo(ns=600_000_000, bid="99000", ask="99001", ordinal=3))
    assert engine.position_quantity == Decimal("0.1")
    orders = _objects(_state(engine)["orders"])
    assert orders[-1]["unfilled_reason"] == "price_band"
    assert read_health(engine.health_path)["flatten_blocked_missing_price"] is False
    engine.close()


def test_stop_falls_back_to_the_last_trade_without_any_mark(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stoptrade", strategy=ScriptedStrategy((Decimal("0.1"),)))
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    # One-sided book: no BBO mid and no venue mark, so only trades are left.
    engine.on_event(_bbo(ns=1_000_000, bid="98500", ask=None, ask_size=None, ordinal=2))
    engine.on_event(_trade(ns=2_000_000, price="97000", size="0.5", ordinal=3))
    engine.close()
    assert engine.position_quantity == Decimal("0")
    triggered = [row for row in _ledger(tmp_path / "stoptrade") if row["type"] == "stop_triggered"]
    assert triggered[0]["mark_source"] == "last_trade"
    assert triggered[0]["mark_price"] == "97000"


def test_loss_windows_follow_event_time_when_created_after_the_tape(tmp_path: Path) -> None:
    # A replay is created after the tape it plays; the windows must still
    # roll on the tape's own days.
    engine = PaperEngine(
        store_root=tmp_path,
        run_id="replayday",
        created_at_utc=CREATED + timedelta(days=30),
        config=_instant(
            stale_after_ns=2 * 86_400_000_000_000,
            risk_limits=_loose_loss_limits(drawdown="0.50", daily="0.001"),
        ),
        strategy=ScriptedStrategy(tuple(Decimal(text) for text in ("0.1", "0", "0.1"))),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000_000, bid="99000", ask="99001", ordinal=2))
    assert engine.kill_switch == "HALT_NEW"
    engine.on_event(_bbo(ns=13 * 3_600_000_000_000, bid="99000", ask="99001", ordinal=3))
    assert engine.kill_switch == "NONE"
    assert engine.position_quantity == Decimal("0.1")
    engine.close()


def test_daily_loss_halt_lifts_at_the_next_utc_day_and_never_rolls_back(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "dailyreset",
        strategy=ScriptedStrategy(tuple(Decimal(text) for text in ("0.1", "0", "0.1", "0.1"))),
        config=_instant(
            stale_after_ns=2 * 86_400_000_000_000,
            risk_limits=_loose_loss_limits(drawdown="0.50", daily="0.001"),
        ),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    # A 1% drop breaches the 0.1% daily guard; the exit is still allowed.
    engine.on_event(_bbo(ns=1_000_000_000, bid="99000", ask="99001", ordinal=2))
    assert engine.kill_switch == "HALT_NEW"
    assert engine.position_quantity == Decimal("0")
    engine.on_event(_bbo(ns=2_000_000_000, bid="99000", ask="99001", ordinal=3))
    assert engine.position_quantity == Decimal("0")
    # 13h later is 01:00 UTC the next day: a new day, a new baseline.
    next_day_ns = 13 * 3_600_000_000_000
    engine.on_event(_bbo(ns=next_day_ns, bid="99000", ask="99001", ordinal=4))
    assert engine.kill_switch == "NONE"
    assert engine.position_quantity == Decimal("0.1")
    # A late event stamped the previous day must not reset the new baseline:
    # a 1% drop against today's start breaches the guard again.
    engine.on_event(
        _bbo_at(
            ns=next_day_ns + 1_000_000_000,
            event_time=CREATED + timedelta(hours=11, minutes=59),
            bid="98000",
            ask="98001",
            ordinal=5,
        )
    )
    assert engine.kill_switch == "HALT_NEW"
    engine.close()
    switches = [row for row in _ledger(tmp_path / "dailyreset") if row["type"] == "kill_switch"]
    assert [(row["state"], row["reason"]) for row in switches] == [
        ("HALT_NEW", "daily_loss"),
        ("NONE", "daily_loss_window_reset"),
        ("HALT_NEW", "daily_loss"),
    ]
    rejections = _objects(_state(engine)["risk_rejections"])
    assert [row["reason"] for row in rejections] == ["halt_new"]


def test_weekly_loss_halt_lifts_only_at_the_next_iso_week(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "weekreset",
        strategy=ScriptedStrategy(tuple(Decimal(text) for text in ("0.1", "0", "0.1", "0.1"))),
        config=_instant(
            stale_after_ns=10 * 86_400_000_000_000,
            risk_limits=PaperRiskLimits(
                drawdown_kill_fraction=Decimal("0.50"),
                daily_loss_fraction=Decimal("0.50"),
                weekly_loss_fraction=Decimal("0.001"),
            ),
        ),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000_000, bid="99000", ask="99001", ordinal=2))
    assert engine.kill_switch == "HALT_NEW"
    # Thursday: a new day does not lift a weekly halt.
    engine.on_event(_bbo(ns=24 * 3_600_000_000_000, bid="99000", ask="99001", ordinal=3))
    assert engine.kill_switch == "HALT_NEW"
    assert engine.position_quantity == Decimal("0")
    # Wednesday 12:00 + 108h is Monday 00:00 UTC: a new ISO week.
    engine.on_event(_bbo(ns=108 * 3_600_000_000_000, bid="99000", ask="99001", ordinal=4))
    assert engine.kill_switch == "NONE"
    assert engine.position_quantity == Decimal("0.1")
    engine.close()
    switches = [row for row in _ledger(tmp_path / "weekreset") if row["type"] == "kill_switch"]
    assert [(row["state"], row["reason"]) for row in switches] == [
        ("HALT_NEW", "weekly_loss"),
        ("NONE", "weekly_loss_window_reset"),
    ]


def test_linear_perp_pnl_identity() -> None:
    opened = apply_fill(
        PositionState(
            cash_usdc=Decimal("100000"),
            position_quantity=Decimal("0"),
            average_entry_price=Decimal("0"),
            realized_pnl_usdc=Decimal("0"),
            fees_usdc=Decimal("0"),
        ),
        FillQuote(
            side="BUY",
            quantity=Decimal("0.1"),
            price=Decimal("100000"),
            fee_usdc=Decimal("4.5"),
            source="bbo",
        ),
    )
    closed = apply_fill(
        opened,
        FillQuote(
            side="SELL",
            quantity=Decimal("0.1"),
            price=Decimal("100010"),
            fee_usdc=Decimal("4.50045"),
            source="bbo",
        ),
    )
    assert closed.position_quantity == Decimal("0")
    assert closed.realized_pnl_usdc == Decimal("1.0")
    assert closed.fees_usdc == Decimal("9.00045")
    assert closed.cash_usdc == Decimal("100000") + Decimal("1.0") - Decimal("9.00045")


def test_create_only_run_id_never_resumes(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "createonly1")
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001"))
    engine.close()
    ledger = (tmp_path / "createonly1" / "ledger.jsonl").read_text(encoding="utf-8")
    with pytest.raises(RunAlreadyExistsError):
        _engine(tmp_path, "createonly1")
    assert (tmp_path / "createonly1" / "ledger.jsonl").read_text(encoding="utf-8") == ledger


def test_live_mode_fails_closed() -> None:
    with pytest.raises(UnsafeTradingModeError):
        PaperEngineConfig(trading_mode="LIVE")


def test_bar_does_not_invent_a_fill_price(tmp_path: Path) -> None:
    trades = (
        _trade(ns=0, price="100000", size="0.1", ordinal=1),
        _trade(ns=100, price="100002", size="0.2", ordinal=2),
    )
    bars = aggregate_trade_bars(trades, interval_ns=1_000_000_000)
    assert len(bars) == 1
    assert bars[0].open_price == Decimal("100000")
    assert bars[0].close_price == Decimal("100002")
    assert bars[0].high_price == Decimal("100002")
    assert bars[0].volume == Decimal("0.3")
    engine = _engine(
        tmp_path,
        "baronly01",
        strategy=ScriptedStrategy((Decimal("0.00010"),)),
    )
    engine.on_event(bars[0])
    engine.close()
    assert engine.position_quantity == Decimal("0")
    assert _objects(_state(engine)["risk_rejections"])[0]["reason"] == "missing_price"


def test_toy_replay_is_deterministic_and_ends_flat(tmp_path: Path) -> None:
    events = _toy_tape()
    first = _run_toy(tmp_path, "toyreplay1", events)
    second = _run_toy(tmp_path, "toyreplay2", events)
    assert _normalize(_state(first), "toyreplay1") == _normalize(_state(second), "toyreplay2")
    health = read_health(first.health_path)
    assert health["strategy_id"] == "nonprod-reference-toy"
    assert health["production_eligible"] is False
    assert health["position_quantity"] == "0"
    assert "NON-PRODUCTION" in str(health["strategy_label"])
    state = _state(first)
    assert state["realized_pnl_usdc"] == "-0.0001"
    assert state["fees_usdc"] == "0.009000045"
    assert _text(health["observed_at_local"]).endswith("CEST")


def test_parquet_replay_matches_the_in_memory_tape(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "tape"
    _write_hyperliquid_parquet(parquet_dir)
    loaded = load_hyperliquid_parquet_tape((parquet_dir,))
    assert [type(event) for event in loaded] == [BboEvent, BboEvent, TradeEvent]
    memory = _run_toy(tmp_path, "memreplay1", loaded)
    parquet = _engine(
        tmp_path,
        "pqreplay01",
        strategy=NonProductionReferenceStrategy(mode="toy"),
    )
    parquet.run_parquet((parquet_dir,))
    parquet.close()
    assert _normalize(_state(memory), "memreplay1") == _normalize(_state(parquet), "pqreplay01")


def test_package_does_not_import_an_exchange_order_client() -> None:
    import hyperliquid_bot.paper_engine as package

    forbidden = (
        "eth_account",
        "nautilus_trader",
        "websockets",
        "hyperliquid.exchange",
        "hyperliquid_bot.hyperliquid_ws_client",
        "hyperliquid_bot.hyperliquid_capture",
    )
    root = Path(package.__file__).resolve().parent
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "private_key" not in source
        assert "api.hyperliquid.xyz/exchange" not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
            for module_name in modules:
                for prefix in forbidden:
                    assert not module_name.startswith(prefix), module_name
    for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        imported = importlib.import_module(module_info.name)
        for value in vars(imported).values():
            module_name = getattr(value, "__module__", "")
            if type(module_name) is not str:
                continue
            for prefix in forbidden:
                assert not module_name.startswith(prefix)


def _toy_tape() -> tuple[BboEvent, BboEvent]:
    return (
        _bbo(ns=0, bid="100000", ask="100001", ordinal=1),
        _bbo(ns=1_000_000_000, bid="100000", ask="100001", ordinal=2),
    )


def _run_toy(root: Path, run_id: str, events: tuple[MarketEvent, ...]) -> PaperEngine:
    engine = _engine(
        root,
        run_id,
        strategy=NonProductionReferenceStrategy(mode="toy"),
    )
    engine.run_events(events)
    engine.close()
    return engine


def _text(value: object) -> str:
    if type(value) is not str:
        raise AssertionError("expected text.")
    return value


def _state(engine: PaperEngine) -> dict[str, object]:
    payload = json.loads(engine.state_path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise AssertionError("state file must be an object.")
    return payload


def _ledger(run_dir: Path) -> list[dict[str, object]]:
    lines = (run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    return _objects([json.loads(line) for line in lines])


def _objects(value: object) -> list[dict[str, object]]:
    if type(value) is not list:
        raise AssertionError("expected a list.")
    rows: list[dict[str, object]] = []
    for item in value:
        if type(item) is not dict:
            raise AssertionError("expected a list of objects.")
        rows.append(item)
    return rows


def _normalize(state: dict[str, object], run_id: str) -> str:
    return json.dumps(state, sort_keys=True).replace(run_id, "RUN")


def _write_hyperliquid_parquet(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows = (
        _raw_row(
            1,
            0,
            "bbo",
            {
                "channel": "bbo",
                "data": {
                    "coin": "BTC",
                    "time": 1_784_000_000_000,
                    "bbo": [
                        {"px": "100000", "sz": "1", "n": 1},
                        {"px": "100001", "sz": "1", "n": 1},
                    ],
                },
            },
        ),
        _raw_row(
            2,
            1_000_000_000,
            "bbo",
            {
                "channel": "bbo",
                "data": {
                    "coin": "BTC",
                    "time": 1_784_000_001_000,
                    "bbo": [
                        {"px": "100000", "sz": "1", "n": 1},
                        {"px": "100001", "sz": "1", "n": 1},
                    ],
                },
            },
        ),
        _raw_row(
            3,
            1_500_000_000,
            "trades",
            {
                "channel": "trades",
                "data": [
                    {
                        "coin": "BTC",
                        "side": "A",
                        "px": "100000",
                        "sz": "0.01",
                        "time": 1_784_000_001_500,
                        "tid": 9,
                    }
                ],
            },
        ),
    )
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE raw_segment (
                schema_version INTEGER NOT NULL,
                venue VARCHAR NOT NULL,
                product VARCHAR NOT NULL,
                channel VARCHAR NOT NULL,
                session_id VARCHAR NOT NULL,
                message_ordinal BIGINT NOT NULL,
                received_utc_ns BIGINT NOT NULL,
                received_monotonic_ns BIGINT NOT NULL,
                direction VARCHAR NOT NULL,
                frame_type VARCHAR NOT NULL,
                payload_encoding VARCHAR NOT NULL,
                payload_bytes BLOB NOT NULL,
                payload_sha256 VARCHAR NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO raw_segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        destination = (directory / "part-000001.parquet").as_posix().replace("'", "''")
        connection.execute(
            f"COPY raw_segment TO '{destination}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.close()


def _raw_row(
    ordinal: int,
    received_utc_ns: int,
    channel: str,
    payload: dict[str, object],
) -> tuple[object, ...]:
    body = json.dumps(payload).encode("utf-8")
    return (
        1,
        "hyperliquid",
        "BTC-PERP",
        channel,
        "session-test",
        ordinal,
        received_utc_ns,
        received_utc_ns,
        "inbound",
        "text",
        "utf-8-json",
        body,
        "0" * 64,
    )
