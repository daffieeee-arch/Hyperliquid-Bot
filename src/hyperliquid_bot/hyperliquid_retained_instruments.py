"""Explicit Hyperliquid retained-capture instrument configs.

Official mainnet public WebSocket (reviewed 2026-09-11):

- URL: ``wss://api.hyperliquid.xyz/ws``
- Subscribe: ``{"method":"subscribe","subscription":{"type":<channel>,"coin":<coin>}}``
- Channels used here: ``trades``, ``bbo``, ``l2Book``, ``activeAssetCtx``
- Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket
- Subscriptions: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- Timeouts: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats

Official examples use coin symbols ``BTC``, ``ETH``, and ``SOL``. ``activeAssetCtx``
for perps carries mark, oracle, funding, and open interest (``PerpsAssetCtx``).
These are optional add-on instruments, never silent BTC replacements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, cast

from .contracts import Instrument, InstrumentType, Venue

HYPERLIQUID_MAINNET_WEBSOCKET_URL: Final = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_REQUIRED_COIN: Final = "BTC"
HYPERLIQUID_OPTIONAL_ADDON_COINS: Final = ("ETH", "SOL")
HYPERLIQUID_RETAINED_CHANNELS: Final = ("trades", "bbo", "l2Book", "activeAssetCtx")
HYPERLIQUID_QUOTE_ASSET: Final = "USDC"
HYPERLIQUID_ADDON_START_POLICY_DEFERRED: Final = "deferred_at_start"
HYPERLIQUID_ADDON_START_POLICY_ENABLED: Final = "enabled"

InstrumentRole = Literal["required", "optional_addon"]
AddonStartPolicy = Literal["deferred_at_start", "enabled"]


class HyperliquidInstrumentConfigError(ValueError):
    """Raised when a retained Hyperliquid instrument plan is invalid."""


@dataclass(frozen=True, slots=True)
class HyperliquidRetainedInstrument:
    """One official-coin retained subscription set."""

    coin: str
    product: str
    role: InstrumentRole
    instrument: Instrument
    channels: tuple[str, ...] = HYPERLIQUID_RETAINED_CHANNELS

    def __post_init__(self) -> None:
        if self.coin not in (HYPERLIQUID_REQUIRED_COIN, *HYPERLIQUID_OPTIONAL_ADDON_COINS):
            raise HyperliquidInstrumentConfigError(
                f"unsupported Hyperliquid retained coin {self.coin!r}"
            )
        if self.channels != HYPERLIQUID_RETAINED_CHANNELS:
            raise HyperliquidInstrumentConfigError(
                "retained Hyperliquid channels must be trades, bbo, l2Book, activeAssetCtx"
            )
        if self.instrument.venue is not Venue.HYPERLIQUID:
            raise HyperliquidInstrumentConfigError("instrument venue must be Hyperliquid")
        if self.instrument.native_symbol != self.coin:
            raise HyperliquidInstrumentConfigError("native_symbol must match the official coin")
        if self.instrument.instrument_type is not InstrumentType.PERPETUAL:
            raise HyperliquidInstrumentConfigError("retained Hyperliquid instruments are perps")


@dataclass(frozen=True, slots=True)
class HyperliquidSubscription:
    """One official subscribe payload for a coin and channel."""

    coin: str
    channel: str
    payload_bytes: bytes

    @property
    def identity(self) -> tuple[str, str]:
        return (self.channel, self.coin)


@dataclass(frozen=True, slots=True)
class HyperliquidRetainedInstrumentPlan:
    """Resolved DATA-1A subscription plan. BTC is always subscribed when started."""

    required: HyperliquidRetainedInstrument
    addons: tuple[HyperliquidRetainedInstrument, ...]
    addon_start_policy: AddonStartPolicy
    websocket_url: str = HYPERLIQUID_MAINNET_WEBSOCKET_URL

    def __post_init__(self) -> None:
        if self.required.coin != HYPERLIQUID_REQUIRED_COIN or self.required.role != "required":
            raise HyperliquidInstrumentConfigError("required instrument must be BTC")
        addon_coins = tuple(item.coin for item in self.addons)
        if len(set(addon_coins)) != len(addon_coins):
            raise HyperliquidInstrumentConfigError("addon coins must be unique")
        if any(item.role != "optional_addon" for item in self.addons):
            raise HyperliquidInstrumentConfigError("ETH/SOL must be optional add-ons")
        if HYPERLIQUID_REQUIRED_COIN in addon_coins:
            raise HyperliquidInstrumentConfigError("BTC cannot be listed as an add-on")
        if self.addon_start_policy not in (
            HYPERLIQUID_ADDON_START_POLICY_DEFERRED,
            HYPERLIQUID_ADDON_START_POLICY_ENABLED,
        ):
            raise HyperliquidInstrumentConfigError("addon_start_policy is not a documented value")

    @property
    def started_instruments(self) -> tuple[HyperliquidRetainedInstrument, ...]:
        if self.addon_start_policy == HYPERLIQUID_ADDON_START_POLICY_DEFERRED:
            return (self.required,)
        return (self.required, *self.addons)

    @property
    def deferred_addon_coins(self) -> tuple[str, ...]:
        if self.addon_start_policy == HYPERLIQUID_ADDON_START_POLICY_DEFERRED:
            return tuple(item.coin for item in self.addons)
        return ()

    @property
    def started_coins(self) -> tuple[str, ...]:
        return tuple(item.coin for item in self.started_instruments)

    @property
    def subscriptions(self) -> tuple[HyperliquidSubscription, ...]:
        return tuple(
            official_subscription(instrument.coin, channel)
            for instrument in self.started_instruments
            for channel in instrument.channels
        )

    @property
    def expected_subscription_identities(self) -> frozenset[tuple[str, str]]:
        return frozenset(item.identity for item in self.subscriptions)

    @property
    def expected_channels(self) -> frozenset[str]:
        return frozenset(channel for channel, _coin in self.expected_subscription_identities)


def official_subscription(coin: str, channel: str) -> HyperliquidSubscription:
    """Build the official compact subscribe payload for one coin and channel."""

    if channel not in HYPERLIQUID_RETAINED_CHANNELS:
        raise HyperliquidInstrumentConfigError(f"unsupported Hyperliquid channel {channel!r}")
    if coin not in (HYPERLIQUID_REQUIRED_COIN, *HYPERLIQUID_OPTIONAL_ADDON_COINS):
        raise HyperliquidInstrumentConfigError(f"unsupported Hyperliquid retained coin {coin!r}")
    payload = (
        '{"method":"subscribe","subscription":{"type":"' + channel + '","coin":"' + coin + '"}}'
    )
    return HyperliquidSubscription(
        coin=coin,
        channel=channel,
        payload_bytes=payload.encode("utf-8"),
    )


def hyperliquid_perp_instrument(coin: str) -> Instrument:
    """Return the canonical Hyperliquid USDC-margined perpetual for ``coin``."""

    if coin not in (HYPERLIQUID_REQUIRED_COIN, *HYPERLIQUID_OPTIONAL_ADDON_COINS):
        raise HyperliquidInstrumentConfigError(f"unsupported Hyperliquid retained coin {coin!r}")
    return Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset=coin,
        quote_asset=HYPERLIQUID_QUOTE_ASSET,
        venue_market_id=coin,
        native_symbol=coin,
    )


def hyperliquid_retained_instrument(
    coin: str,
    *,
    role: InstrumentRole | None = None,
) -> HyperliquidRetainedInstrument:
    """Return the explicit retained config for BTC, ETH, or SOL."""

    resolved_role: InstrumentRole
    if role is not None:
        resolved_role = role
    elif coin == HYPERLIQUID_REQUIRED_COIN:
        resolved_role = "required"
    else:
        resolved_role = "optional_addon"
    if coin == HYPERLIQUID_REQUIRED_COIN and resolved_role != "required":
        raise HyperliquidInstrumentConfigError("BTC must remain the required DATA-1A instrument")
    if coin in HYPERLIQUID_OPTIONAL_ADDON_COINS and resolved_role != "optional_addon":
        raise HyperliquidInstrumentConfigError(
            "ETH and SOL are optional add-ons, not BTC replacements"
        )
    return HyperliquidRetainedInstrument(
        coin=coin,
        product=f"{coin}-PERP",
        role=resolved_role,
        instrument=hyperliquid_perp_instrument(coin),
    )


def parse_addon_coins(raw: object) -> tuple[str, ...]:
    """Parse an explicit add-on coin list. Empty means configured but start-deferred."""

    if raw is None:
        return ()
    if type(raw) is str:
        parts = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    elif type(raw) is list or type(raw) is tuple:
        values = cast(list[object] | tuple[object, ...], raw)
        parts = tuple(str(part).strip().upper() for part in values if str(part).strip())
    else:
        raise HyperliquidInstrumentConfigError("addon coins must be a string or sequence")
    if HYPERLIQUID_REQUIRED_COIN in parts:
        raise HyperliquidInstrumentConfigError("BTC is required and cannot appear as an add-on")
    unknown = tuple(part for part in parts if part not in HYPERLIQUID_OPTIONAL_ADDON_COINS)
    if unknown:
        raise HyperliquidInstrumentConfigError(
            f"addon coins must be a subset of {HYPERLIQUID_OPTIONAL_ADDON_COINS}, got {unknown}"
        )
    if len(set(parts)) != len(parts):
        raise HyperliquidInstrumentConfigError("addon coins must be unique")
    return parts


def build_hyperliquid_retained_plan(
    *,
    addon_coins: object = (),
    enable_addons: bool = False,
) -> HyperliquidRetainedInstrumentPlan:
    """Build a DATA-1A plan. BTC is always present. Add-ons default to deferred."""

    coins = parse_addon_coins(addon_coins)
    addons = tuple(hyperliquid_retained_instrument(coin) for coin in coins)
    policy: AddonStartPolicy = (
        HYPERLIQUID_ADDON_START_POLICY_ENABLED
        if enable_addons and addons
        else HYPERLIQUID_ADDON_START_POLICY_DEFERRED
    )
    plan = HyperliquidRetainedInstrumentPlan(
        required=hyperliquid_retained_instrument(HYPERLIQUID_REQUIRED_COIN),
        addons=addons,
        addon_start_policy=policy,
    )
    if HYPERLIQUID_REQUIRED_COIN not in plan.started_coins:
        raise HyperliquidInstrumentConfigError("plan dropped the required BTC channels")
    return plan


def phase_a_default_hyperliquid_plan() -> HyperliquidRetainedInstrumentPlan:
    """Phase A joint 72h default: BTC required; ETH+SOL configured but deferred."""

    return build_hyperliquid_retained_plan(
        addon_coins=HYPERLIQUID_OPTIONAL_ADDON_COINS,
        enable_addons=False,
    )
