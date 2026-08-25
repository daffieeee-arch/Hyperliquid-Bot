"""Fail-closed local PAPER-mode policy.

Phase 0B provides only this dependency-free policy function. Phase 1A must wire it
into application startup and prove that it runs before adapters or network access.
"""

from typing import Literal


class UnsafeTradingModeError(ValueError):
    """Raised when local development is asked to use an unsafe trading mode."""


def require_local_paper_mode(raw_mode: object | None) -> Literal["PAPER"]:
    """Return PAPER only for the default or the exact built-in string value."""

    if raw_mode is None:
        return "PAPER"
    if type(raw_mode) is str and raw_mode == "PAPER":
        return "PAPER"
    raise UnsafeTradingModeError("Local trading mode must be exactly PAPER.")
