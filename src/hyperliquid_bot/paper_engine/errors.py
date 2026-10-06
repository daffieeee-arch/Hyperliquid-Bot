"""Errors for the PAPER-only strategy engine.

This package never places venue orders. These errors are local invariant
failures, not exchange rejects.
"""

from __future__ import annotations


class PaperEngineError(ValueError):
    """A PAPER engine input or invariant failed closed."""


class RunAlreadyExistsError(PaperEngineError):
    """Raised when a run_id directory already exists.

    Run identifiers are create-only. The engine never resumes a prior ledger.
    """


class PaperTapeError(PaperEngineError):
    """Raised when a Parquet tape cannot be read as a deterministic event stream."""
