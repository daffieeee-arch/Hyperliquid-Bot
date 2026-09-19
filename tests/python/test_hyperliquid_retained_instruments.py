"""Deterministic tests for Hyperliquid BTC/ETH/SOL retained instrument configs."""

from __future__ import annotations

import pytest

from hyperliquid_bot.contracts import InstrumentType, Venue
from hyperliquid_bot.hyperliquid_raw_research import HyperliquidRawResearchCollector
from hyperliquid_bot.hyperliquid_retained_instruments import (
    HYPERLIQUID_MAINNET_WEBSOCKET_URL,
    HYPERLIQUID_OPTIONAL_ADDON_COINS,
    HYPERLIQUID_REQUIRED_COIN,
    HYPERLIQUID_RETAINED_CHANNELS,
    HyperliquidInstrumentConfigError,
    build_hyperliquid_retained_plan,
    hyperliquid_retained_instrument,
    official_subscription,
    parse_addon_coins,
    phase_a_default_hyperliquid_plan,
)
from hyperliquid_bot.raw_research import RawResearchRecord


def test_official_btc_eth_sol_subscribe_payloads_match_docs() -> None:
    for coin in (HYPERLIQUID_REQUIRED_COIN, *HYPERLIQUID_OPTIONAL_ADDON_COINS):
        instrument = hyperliquid_retained_instrument(coin)
        assert instrument.instrument.venue is Venue.HYPERLIQUID
        assert instrument.instrument.instrument_type is InstrumentType.PERPETUAL
        assert instrument.instrument.native_symbol == coin
        assert instrument.product == f"{coin}-PERP"
        assert instrument.channels == HYPERLIQUID_RETAINED_CHANNELS
        for channel in HYPERLIQUID_RETAINED_CHANNELS:
            payload = official_subscription(coin, channel).payload_bytes.decode("utf-8")
            assert payload == (
                '{"method":"subscribe","subscription":{"type":"'
                + channel
                + '","coin":"'
                + coin
                + '"}}'
            )


def test_eth_and_sol_are_optional_addons_not_btc_replacements() -> None:
    btc = hyperliquid_retained_instrument("BTC")
    eth = hyperliquid_retained_instrument("ETH")
    sol = hyperliquid_retained_instrument("SOL")
    assert btc.role == "required"
    assert eth.role == "optional_addon"
    assert sol.role == "optional_addon"
    with pytest.raises(HyperliquidInstrumentConfigError, match="optional add-ons"):
        hyperliquid_retained_instrument("ETH", role="required")
    with pytest.raises(HyperliquidInstrumentConfigError, match="required"):
        hyperliquid_retained_instrument("BTC", role="optional_addon")


def test_phase_a_default_plan_keeps_btc_and_defers_eth_sol() -> None:
    plan = phase_a_default_hyperliquid_plan()
    assert plan.websocket_url == HYPERLIQUID_MAINNET_WEBSOCKET_URL
    assert plan.started_coins == ("BTC",)
    assert plan.deferred_addon_coins == ("ETH", "SOL")
    assert plan.addon_start_policy == "deferred_at_start"
    assert {item.identity for item in plan.subscriptions} == {
        ("trades", "BTC"),
        ("bbo", "BTC"),
        ("l2Book", "BTC"),
        ("activeAssetCtx", "BTC"),
    }


def test_enabling_addons_never_drops_btc_channels() -> None:
    plan = build_hyperliquid_retained_plan(addon_coins="ETH,SOL", enable_addons=True)
    assert plan.started_coins == ("BTC", "ETH", "SOL")
    coins_by_channel = {
        channel: {coin for kind, coin in plan.expected_subscription_identities if kind == channel}
        for channel in HYPERLIQUID_RETAINED_CHANNELS
    }
    for channel in HYPERLIQUID_RETAINED_CHANNELS:
        assert coins_by_channel[channel] == {"BTC", "ETH", "SOL"}
    assert official_subscription("BTC", "trades") in plan.subscriptions


def test_default_collector_plan_is_btc_only() -> None:
    class _Sink:
        async def append(self, record: RawResearchRecord) -> None:
            del record

        async def aclose(self) -> None:
            return None

    collector = HyperliquidRawResearchCollector(_Sink())
    assert collector._instrument_plan.started_coins == ("BTC",)
    assert "ETH" not in collector._instrument_plan.started_coins


def test_parse_addon_coins_refuses_btc_and_unknowns() -> None:
    assert parse_addon_coins("ETH,SOL") == ("ETH", "SOL")
    assert parse_addon_coins("") == ()
    with pytest.raises(HyperliquidInstrumentConfigError, match="cannot appear as an add-on"):
        parse_addon_coins("BTC,ETH")
    with pytest.raises(HyperliquidInstrumentConfigError, match="subset"):
        parse_addon_coins("DOGE")


def test_optional_candles_off_by_default_and_official_1m_payload() -> None:
    from hyperliquid_bot.hyperliquid_retained_instruments import official_candle_subscription

    default = build_hyperliquid_retained_plan()
    assert default.include_candles is False
    assert "candle" not in default.expected_channels
    assert default.feed_name == "hyperliquid-public-btc-perp-trades-bbo-l2-ctx"

    with_candles = build_hyperliquid_retained_plan(include_candles=True, candle_interval="1m")
    assert with_candles.include_candles is True
    assert ("candle", "BTC:1m") in with_candles.expected_subscription_identities
    payload = official_candle_subscription("BTC", "1m").payload_bytes.decode("utf-8")
    assert payload == (
        '{"method":"subscribe","subscription":{"type":"candle","coin":"BTC","interval":"1m"}}'
    )
    assert with_candles.feed_name.endswith("-candles-1m")
    with pytest.raises(HyperliquidInstrumentConfigError, match="include_candles"):
        build_hyperliquid_retained_plan(include_candles=False, candle_interval="5m")
