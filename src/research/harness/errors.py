"""Fail-closed errors for the research harness."""

from __future__ import annotations


class HarnessError(Exception):
    """A harness run stopped before a statistical label was earned."""

    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class SpecError(HarnessError):
    """The pre-registration document is missing, ambiguous, or invalid."""

    def __init__(self, message: str) -> None:
        super().__init__("spec", message)


class LockError(HarnessError):
    """The spec is not hash-locked, or the lock does not match the file."""

    def __init__(self, message: str) -> None:
        super().__init__("lock", message)


class IntegrityError(HarnessError):
    """The series failed a point-in-time or schema check."""
