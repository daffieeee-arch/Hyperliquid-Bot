"""Tests for the Phase 1A-1 local composition root."""

from typing import Literal

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from hyperliquid_bot.startup import start_local_application


def test_missing_trading_mode_defaults_before_resolver_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_MODE", raising=False)
    application = object()
    calls: list[str] = []

    def resolver(mode: Literal["PAPER"]) -> str:
        calls.append(f"resolver:{mode}")
        return "resolved-paper-dependencies"

    def factory(resolved: str) -> object:
        calls.append(f"factory:{resolved}")
        return application

    result = start_local_application(resolver, factory)

    assert result is application
    assert calls == ["resolver:PAPER", "factory:resolved-paper-dependencies"]


def test_exact_paper_is_accepted_without_normalization() -> None:
    observed_modes: list[str] = []

    def resolver(mode: Literal["PAPER"]) -> None:
        observed_modes.append(mode)

    def factory(resolved: None) -> str:
        assert resolved is None
        return "application"

    result = start_local_application(
        resolver,
        factory,
        environment={"TRADING_MODE": "PAPER"},
    )

    assert result == "application"
    assert observed_modes == ["PAPER"]


@pytest.mark.parametrize(
    "unsafe_mode",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="space"),
        pytest.param("\t", id="tab"),
        pytest.param("paper", id="lowercase"),
        pytest.param("Paper", id="titlecase"),
        pytest.param(" PAPER ", id="surrounding-whitespace"),
        pytest.param("PAPER\n", id="trailing-newline"),
        pytest.param("BACKTEST", id="backtest"),
        pytest.param("SHADOW", id="shadow"),
        pytest.param("TESTNET", id="testnet"),
        pytest.param("LIVE", id="live"),
        pytest.param("UNKNOWN", id="unknown"),
    ],
)
def test_unsafe_mode_calls_neither_resolver_nor_factory(unsafe_mode: str) -> None:
    calls: list[str] = []

    def resolver(mode: Literal["PAPER"]) -> object:
        calls.append(f"resolver:{mode}")
        return object()

    def factory(resolved: object) -> object:
        calls.append("factory")
        return resolved

    with pytest.raises(UnsafeTradingModeError):
        start_local_application(
            resolver,
            factory,
            environment={"TRADING_MODE": unsafe_mode},
        )

    assert calls == []


def test_startup_error_does_not_include_rejected_raw_mode() -> None:
    rejected_mode = "DO_NOT_INCLUDE_REJECTED_MODE"

    def resolver(mode: Literal["PAPER"]) -> None:
        raise AssertionError(f"resolver must not be called: {mode}")

    def factory(resolved: None) -> None:
        raise AssertionError(f"factory must not be called: {resolved}")

    with pytest.raises(UnsafeTradingModeError) as exc_info:
        start_local_application(
            resolver,
            factory,
            environment={"TRADING_MODE": rejected_mode},
        )

    assert rejected_mode not in str(exc_info.value)
