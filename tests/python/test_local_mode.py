"""Tests for the Phase 0B local PAPER-mode policy."""

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError, require_local_paper_mode


class PaperString(str):
    """A string subclass that must not pass the exact-type safety check."""


def test_none_defaults_to_paper() -> None:
    assert require_local_paper_mode(None) == "PAPER"


def test_exact_builtin_paper_string_is_accepted() -> None:
    assert require_local_paper_mode("PAPER") == "PAPER"


@pytest.mark.parametrize(
    "raw_mode",
    [
        pytest.param("", id="empty-string"),
        pytest.param(" ", id="space"),
        pytest.param("\t", id="tab"),
        pytest.param("\n", id="newline"),
        pytest.param("paper", id="lowercase"),
        pytest.param("Paper", id="titlecase"),
        pytest.param("pApEr", id="mixed-case"),
        pytest.param(" PAPER ", id="surrounding-whitespace"),
        pytest.param("PAPER\n", id="trailing-newline"),
        pytest.param("BACKTEST", id="backtest"),
        pytest.param("SHADOW", id="shadow"),
        pytest.param("TESTNET", id="testnet"),
        pytest.param("LIVE", id="live"),
        pytest.param("UNKNOWN", id="unknown"),
        pytest.param(b"PAPER", id="bytes"),
        pytest.param(bytearray(b"PAPER"), id="bytearray"),
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param(0, id="zero"),
        pytest.param(1, id="one"),
        pytest.param(1.0, id="float"),
        pytest.param([], id="list"),
        pytest.param({}, id="dict"),
        pytest.param((), id="tuple"),
        pytest.param(set(), id="set"),
        pytest.param(["PAPER"], id="paper-list"),
        pytest.param({"PAPER": True}, id="paper-dict"),
        pytest.param(PaperString("PAPER"), id="string-subclass"),
        pytest.param(object(), id="object"),
    ],
)
def test_all_other_values_fail_closed(raw_mode: object) -> None:
    with pytest.raises(UnsafeTradingModeError):
        require_local_paper_mode(raw_mode)


def test_error_message_does_not_include_received_value() -> None:
    invalid_mode = "DO_NOT_INCLUDE_RAW_MODE"

    with pytest.raises(UnsafeTradingModeError) as exc_info:
        require_local_paper_mode(invalid_mode)

    assert invalid_mode not in str(exc_info.value)


def test_reserved_environment_name_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "LIVE")

    assert require_local_paper_mode(None) == "PAPER"
