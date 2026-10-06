"""PAPER-only backtest harness.

Import :func:`research.harness.run.execute` or use ``python -m research.harness``.
"""

from research.harness.run import execute, lock_spec

__all__ = ["execute", "lock_spec"]
