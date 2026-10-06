"""Strategy surface for the PAPER engine.

A strategy sees normalized events and the current paper book, and returns a
target position. It must be deterministic: the same events produce the same
targets. Strategies do not import venue clients and cannot submit orders.

``NonProductionReferenceStrategy`` is the only strategy shipped with the
engine. It has no researched edge. ``flat`` stays flat. ``toy`` takes one
fixed long and then flattens, so the fill path can be replayed. Neither mode
is eligible for promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from hyperliquid_bot.paper_engine.events import BboEvent, MarketEvent


@dataclass(frozen=True, slots=True)
class TargetPosition:
    """Desired signed position in base units. Zero is flat."""

    instrument_id: str
    target_quantity: Decimal
    reason: str

    def __post_init__(self) -> None:
        if type(self.instrument_id) is not str or not self.instrument_id:
            raise ValueError("instrument_id must be non-empty text.")
        if type(self.target_quantity) is not Decimal or not self.target_quantity.is_finite():
            raise ValueError("target_quantity must be a finite decimal.")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("reason must be non-empty text.")


@dataclass(frozen=True, slots=True)
class StrategyView:
    """Point-in-time paper state visible to the strategy. Prices may be missing."""

    position_quantity: Decimal
    bid_price: Decimal | None
    ask_price: Decimal | None
    mid_price: Decimal | None
    last_trade_price: Decimal | None
    venue_mark_price: Decimal | None
    last_bar_close: Decimal | None
    kill_switch: str


class PaperStrategy(Protocol):
    """Deterministic target-position strategy. PAPER execution is outside this type."""

    @property
    def strategy_id(self) -> str: ...

    @property
    def configuration_version(self) -> str: ...

    @property
    def production_eligible(self) -> bool: ...

    @property
    def label(self) -> str: ...

    def on_market(self, event: MarketEvent, view: StrategyView) -> TargetPosition | None: ...


class NonProductionReferenceStrategy:
    """No-edge placeholder. Not a signal and not a production strategy.

    ``mode='flat'`` always targets zero. ``mode='toy'`` targets a fixed long
    for a fixed number of BBO updates, then targets zero. The toy quantity is
    a constant; it does not depend on price, so the path has no edge.
    """

    def __init__(
        self,
        *,
        mode: str = "flat",
        instrument_id: str = "BTC-PERP",
        toy_quantity: Decimal = Decimal("0.00010"),
        arm_after_bbos: int = 1,
        hold_bbos: int = 1,
    ) -> None:
        if mode not in {"flat", "toy"}:
            raise ValueError("reference strategy mode must be 'flat' or 'toy'.")
        if type(instrument_id) is not str or not instrument_id:
            raise ValueError("instrument_id must be non-empty text.")
        if type(toy_quantity) is not Decimal or not toy_quantity.is_finite() or toy_quantity <= 0:
            raise ValueError("toy_quantity must be a finite positive decimal.")
        if type(arm_after_bbos) is not int or arm_after_bbos < 1:
            raise ValueError("arm_after_bbos must be a positive integer.")
        if type(hold_bbos) is not int or hold_bbos < 1:
            raise ValueError("hold_bbos must be a positive integer.")
        self._mode = mode
        self._instrument_id = instrument_id
        self._toy_quantity = toy_quantity
        self._arm_after_bbos = arm_after_bbos
        self._hold_bbos = hold_bbos
        self._bbo_count = 0

    @property
    def strategy_id(self) -> str:
        if self._mode == "flat":
            return "nonprod-reference-flat"
        return "nonprod-reference-toy"

    @property
    def configuration_version(self) -> str:
        return f"reference-v1-{self._mode}"

    @property
    def production_eligible(self) -> bool:
        return False

    @property
    def label(self) -> str:
        return "NON-PRODUCTION reference strategy with no researched edge"

    @property
    def mode(self) -> str:
        return self._mode

    def on_market(self, event: MarketEvent, view: StrategyView) -> TargetPosition | None:
        del view
        if self._mode == "flat":
            return TargetPosition(
                instrument_id=self._instrument_id,
                target_quantity=Decimal("0"),
                reason="reference-flat",
            )
        if isinstance(event, BboEvent):
            self._bbo_count += 1
        target = Decimal("0")
        reason = "toy-waiting"
        arm = self._arm_after_bbos
        hold_end = arm + self._hold_bbos
        if arm <= self._bbo_count < hold_end:
            target = self._toy_quantity
            reason = "toy-long-no-edge"
        elif self._bbo_count >= hold_end:
            reason = "toy-flat"
        return TargetPosition(
            instrument_id=self._instrument_id,
            target_quantity=target,
            reason=reason,
        )
