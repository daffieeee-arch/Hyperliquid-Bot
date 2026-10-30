"""Deterministic, no-edge native engine artifacts for the cockpit contract tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hyperliquid_bot.paper_engine import (
    BboEvent,
    MarketEvent,
    PaperEngine,
    PaperEngineConfig,
    StrategyView,
    TargetPosition,
)
from hyperliquid_bot.paper_engine.time_display import datetime_to_utc_ns

CREATED = datetime(2026, 10, 1, 12, tzinfo=UTC)


class FixtureStrategy:
    strategy_id = "native-cockpit-fixture"
    configuration_version = "fixture-v1"
    production_eligible = False
    label = "DEMO only: scripted round trips with no researched edge"

    def __init__(self, round_trips: int) -> None:
        self._decisions = round_trips * 2
        self._seen = 0

    def on_market(self, event: MarketEvent, view: StrategyView) -> TargetPosition | None:
        del event, view
        self._seen += 1
        if self._seen > self._decisions:
            return None
        return TargetPosition(
            instrument_id="BTC-PERP",
            target_quantity=Decimal("0.0002") if self._seen % 2 else Decimal("0"),
            reason="fixture-only",
        )


def create_demo_run(root: Path, *, round_trips: int = 1) -> Path:
    if type(round_trips) is not int or not 1 <= round_trips <= 100:
        raise ValueError("round_trips must be between 1 and 100.")
    engine = PaperEngine(
        store_root=root,
        run_id="native-demo",
        created_at_utc=CREATED,
        strategy=FixtureStrategy(round_trips),
        config=PaperEngineConfig(
            source_commit="fixture-no-source-commit",
            image_digest="fixture-not-a-deployed-image",
            durable_ledger=False,
        ),
    )
    for ordinal in range(1, round_trips * 2 + 2):
        time = CREATED + timedelta(seconds=ordinal)
        engine.on_event(
            BboEvent(
                venue="hyperliquid",
                instrument_id="BTC-PERP",
                event_time_utc=time,
                received_utc_ns=datetime_to_utc_ns(time, field_name="fixture_time"),
                source_event_id=f"fixture-bbo-{ordinal}",
                bid_price=Decimal("79999"),
                ask_price=Decimal("80001"),
                bid_size=Decimal("1"),
                ask_size=Decimal("1"),
            )
        )
    engine.close()
    return engine.state_path.parent


if __name__ == "__main__":
    create_demo_run(Path(__file__).parent)
    create_demo_run(Path(__file__).parent / "history", round_trips=60)
