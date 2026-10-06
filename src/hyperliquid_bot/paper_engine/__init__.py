"""PAPER-only strategy and risk engine.

Public surface for a deterministic target-position strategy, a touch/trade
fill simulator, and a create-only run ledger. No exchange order client is
imported from this package.
"""

from hyperliquid_bot.paper_engine.engine import (
    LIMITATIONS,
    KillSwitch,
    PaperEngine,
    PaperEngineConfig,
    decimal_text,
)
from hyperliquid_bot.paper_engine.errors import (
    PaperEngineError,
    PaperTapeError,
    RunAlreadyExistsError,
)
from hyperliquid_bot.paper_engine.events import (
    BarEvent,
    BboEvent,
    MarketEvent,
    MarkEvent,
    TradeEvent,
    aggregate_trade_bars,
    bbo_mid,
)
from hyperliquid_bot.paper_engine.execution import (
    HYPERLIQUID_PERP_BASE_MAKER_FEE_RATE,
    HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE,
    quote_taker_fill,
)
from hyperliquid_bot.paper_engine.ledger import read_health
from hyperliquid_bot.paper_engine.replay import load_hyperliquid_parquet_tape
from hyperliquid_bot.paper_engine.strategy import (
    NonProductionReferenceStrategy,
    PaperStrategy,
    StrategyView,
    TargetPosition,
)
from hyperliquid_bot.paper_engine.time_display import format_amsterdam

__all__ = [
    "HYPERLIQUID_PERP_BASE_MAKER_FEE_RATE",
    "HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE",
    "LIMITATIONS",
    "BarEvent",
    "BboEvent",
    "KillSwitch",
    "MarkEvent",
    "MarketEvent",
    "NonProductionReferenceStrategy",
    "PaperEngine",
    "PaperEngineConfig",
    "PaperEngineError",
    "PaperStrategy",
    "PaperTapeError",
    "RunAlreadyExistsError",
    "StrategyView",
    "TargetPosition",
    "TradeEvent",
    "aggregate_trade_bars",
    "bbo_mid",
    "decimal_text",
    "format_amsterdam",
    "load_hyperliquid_parquet_tape",
    "quote_taker_fill",
    "read_health",
]
