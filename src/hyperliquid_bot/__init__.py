"""Hyperliquid Bot foundation package."""

from .local_mode import UnsafeTradingModeError, require_local_paper_mode

__all__ = ["UnsafeTradingModeError", "require_local_paper_mode"]
