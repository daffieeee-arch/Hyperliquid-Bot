"""Fail-closed errors for the historical archive ETL."""

from __future__ import annotations


class HistEtlError(Exception):
    """A pipeline failure that maps to a process exit code.

    Exit 2 is a data/integrity failure. Exit 3 is the disk guard.
    """

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
