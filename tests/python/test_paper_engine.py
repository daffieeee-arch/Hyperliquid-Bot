"""PAPER engine tests: risk, kill switch, fees, replay, and no venue client."""

from __future__ import annotations

import ast
import importlib
import json
import pkgutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from hyperliquid_bot.paper_engine import (
    HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE,
    BboEvent,
    MarketEvent,
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
from hyperliquid_bot.paper_engine.execution import FillQuote, PositionState, apply_fill
from hyperliquid_bot.paper_engine.precision import adverse_price
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
        config=config,
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
        config=PaperEngineConfig(max_position_quantity=Decimal("0.05")),
    )
    position_engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    position_engine.close()
    assert position_engine.position_quantity == Decimal("0")
    assert _objects(_state(position_engine)["risk_rejections"])[0]["reason"] == "max_position"

    notional_engine = _engine(
        tmp_path,
        "maxnot001",
        strategy=ScriptedStrategy((Decimal("0.00020"),)),
        config=PaperEngineConfig(max_notional_usdc=Decimal("15")),
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
        config=PaperEngineConfig(max_position_quantity=Decimal("1")),
    )
    engine.on_event(_bbo(ns=0, bid="99999", ask="100000"))
    engine.close()
    assert engine.position_quantity == Decimal("0.125")
    fills = _objects(_state(engine)["fills"])
    assert fills[0]["quantity"] == "0.125"
    assert fills[0]["fee_usdc"] == "5.625"
    assert Decimal(str(fills[0]["quantity"])) < Decimal("1")


def test_daily_loss_halts_entries_but_allows_flatten(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "dailyloss1",
        strategy=ScriptedStrategy((Decimal("0.1"), Decimal("0.2"), Decimal("0"))),
        config=PaperEngineConfig(risk_limits=_loose_loss_limits(drawdown="0.50", daily="0.01")),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000_000, bid="80000", ask="80001", ordinal=2))
    assert engine.position_quantity == Decimal("0.1")
    assert engine.kill_switch == "HALT_NEW"
    engine.on_event(_bbo(ns=2_000_000_000, bid="80000", ask="80001", ordinal=3))
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
        config=PaperEngineConfig(risk_limits=_loose_loss_limits(drawdown="0.01", daily="0.50")),
    )
    engine.on_event(_bbo(ns=0, bid="100000", ask="100001", ordinal=1))
    engine.on_event(_bbo(ns=1_000_000_000, bid="80000", ask="80001", ordinal=2))
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
        config=PaperEngineConfig(stale_after_ns=5_000_000_000),
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
        config=PaperEngineConfig(stale_after_ns=5_000_000_000),
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
