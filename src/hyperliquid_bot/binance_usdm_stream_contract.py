"""Fail-closed Binance USD-M combined-stream category split.

Official 2026 USDⓈ-M WebSocket routing (reviewed against Binance developer
docs and the 2026-03-06 system-upgrade notice) places high-frequency
``bookTicker`` / ``depth`` on ``/public`` and regular market streams
(``aggTrade``, ``markPrice``, ``forceOrder``, …) on ``/market``. Combined
streams must not mix those categories. A ``bookTicker`` subscription on
``/market`` is the #52 transport bug.

Schema basis (no live sockets in this module):

- https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice#public-high-frequency-public-data
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market
"""

from __future__ import annotations

from typing import Final

USDM_PUBLIC_PATH: Final = "/public/stream?"
USDM_MARKET_PATH: Final = "/market/stream?"
USDM_PUBLIC_HOST_PREFIX: Final = "wss://fstream.binance.com/public/stream?streams="
USDM_MARKET_HOST_PREFIX: Final = "wss://fstream.binance.com/market/stream?streams="

PUBLIC_CHANNELS: Final = frozenset({"bookTicker", "depth"})
MARKET_CHANNELS: Final = frozenset(
    {
        "aggTrade",
        "markPrice",
        "markPrice@1s",
        "forceOrder",
        "kline",
        "ticker",
    }
)


class UsdmStreamContractError(ValueError):
    """Raised when USD-M combined streams violate the official category split."""


def combined_stream_names(url: str) -> tuple[str, ...]:
    """Return combined-stream names from a Binance ``stream?streams=`` URL."""

    marker = "stream?streams="
    try:
        start = url.index(marker) + len(marker)
    except ValueError as exc:
        raise UsdmStreamContractError(f"USD-M URL is not a combined stream: {url!r}") from exc
    query = url[start:]
    streams, _, _rest = query.partition("&")
    names = tuple(part for part in streams.split("/") if part)
    if not names:
        raise UsdmStreamContractError(f"USD-M combined stream list is empty: {url!r}")
    return names


def _channel(stream: str) -> str:
    if "@" not in stream:
        raise UsdmStreamContractError(f"USD-M stream is missing an @channel: {stream!r}")
    return stream.split("@", 1)[1]


def require_usdm_combined_stream_split(*, public_url: str, market_url: str) -> None:
    """Fail closed unless USD-M ``bookTicker`` is on ``/public`` and not ``/market``."""

    if not public_url.startswith(USDM_PUBLIC_HOST_PREFIX):
        raise UsdmStreamContractError(
            "USD-M public socket must be wss://fstream.binance.com/public/stream?streams=…"
        )
    if not market_url.startswith(USDM_MARKET_HOST_PREFIX):
        raise UsdmStreamContractError(
            "USD-M market socket must be wss://fstream.binance.com/market/stream?streams=…"
        )
    if USDM_MARKET_PATH in public_url or "/market/" in public_url:
        raise UsdmStreamContractError("USD-M public socket must not use /market/")
    if USDM_PUBLIC_PATH in market_url or "/public/" in market_url:
        raise UsdmStreamContractError("USD-M market socket must not use /public/")

    public_streams = combined_stream_names(public_url)
    market_streams = combined_stream_names(market_url)
    public_channels = {_channel(stream) for stream in public_streams}
    market_channels = {_channel(stream) for stream in market_streams}

    if "bookTicker" in market_url or any(
        _channel(stream) == "bookTicker" for stream in market_streams
    ):
        raise UsdmStreamContractError(
            "USD-M bookTicker must use the official /public combined socket, not /market"
        )
    if public_channels - PUBLIC_CHANNELS:
        raise UsdmStreamContractError(
            f"USD-M /public streams are not in the official high-frequency set: {public_streams}"
        )
    if market_channels - MARKET_CHANNELS:
        raise UsdmStreamContractError(
            f"USD-M /market streams are not in the official regular-market set: {market_streams}"
        )
    if not public_channels.isdisjoint(MARKET_CHANNELS):
        raise UsdmStreamContractError("USD-M /public streams mix regular-market channels")
    if not market_channels.isdisjoint(PUBLIC_CHANNELS):
        raise UsdmStreamContractError("USD-M /market streams mix high-frequency public channels")
    if public_streams != ("btcusdt@bookTicker",):
        raise UsdmStreamContractError(
            f"DATA-1F usdm_public must be exactly btcusdt@bookTicker, got {public_streams}"
        )
