"""Offline observation boundary, deliberately incapable of granting account authority.

The observations describe an injected, bounded response in a fragment exercise.
They do not authenticate a venue response or establish pagination/history coverage.
No production risk consumer uses this fit-gate evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ReportKind = Literal["order", "orders", "fills", "positions"]
ObservationState = Literal["UNKNOWN", "BOUNDED_EMPTY", "BOUNDED_NOT_FOUND", "BOUNDED_NONEMPTY"]
REPORT_TYPES = {
    "order": "OrderStatusReport",
    "orders": "OrderStatusReport",
    "fills": "FillReport",
    "positions": "PositionStatusReport",
}


@dataclass(frozen=True)
class ReportScope:
    invocation_id: str
    environment: str
    account_id: str
    venue: str
    instrument_id: str
    kind: ReportKind
    query: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class ReportObservation:
    scope: ReportScope
    request_count: int
    transport_completed: bool
    adapter_completed: bool
    bounded_response_complete: bool
    raw_count: int | None
    returned_count: int | None
    receipt_counts: tuple[int, ...]
    errors: tuple[str, ...]
    report_types: tuple[str, ...]
    report_identities: tuple[tuple[str, str], ...]
    report_order_ids: tuple[tuple[str | None, str | None], ...]


@dataclass(frozen=True)
class ReportAssessment:
    state: ObservationState
    reason: str
    account_truth: str = field(default="UNKNOWN", init=False)
    history_coverage: str = field(default="UNKNOWN", init=False)
    authoritative_flat: bool = field(default=False, init=False)
    new_risk_allowed: bool = field(default=False, init=False)
    decision: str = field(default="HOLD", init=False)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value.isascii()
        and not any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
    )


def _scope_valid(scope: ReportScope) -> bool:
    if type(scope) is not ReportScope:
        return False
    if not all(
        _text(value)
        for value in (
            scope.invocation_id,
            scope.account_id,
            scope.instrument_id,
            scope.environment,
            scope.venue,
            scope.kind,
        )
    ):
        return False
    if scope.environment != "PAPER" or scope.venue != "HYPERLIQUID":
        return False
    if type(scope.kind) is not str or scope.kind not in REPORT_TYPES:
        return False
    if not scope.instrument_id.endswith(".HYPERLIQUID") or type(scope.query) is not tuple:
        return False
    for item in scope.query:
        if type(item) is not tuple or len(item) != 2 or not _text(item[0]):
            return False
        if item[1] is not None and not _text(item[1]):
            return False
    if scope.kind != "order":
        return scope.query == (("instrument_id", scope.instrument_id),)
    return (
        len(scope.query) == 2
        and tuple(key for key, _value in scope.query) == ("venue_order_id", "client_order_id")
        and any(value is not None for _key, value in scope.query)
    )


def assess_report(
    expected: ReportScope,
    observation: ReportObservation | None,
) -> ReportAssessment:
    """Accept only consistent invocation evidence, never infer flat/new-risk permission."""
    if not _scope_valid(expected) or type(observation) is not ReportObservation:
        return ReportAssessment("UNKNOWN", "missing-or-invalid-observation")
    if not _scope_valid(observation.scope) or observation.scope != expected:
        return ReportAssessment("UNKNOWN", "scope-mismatch")
    if type(observation.request_count) is not int or observation.request_count != 1:
        return ReportAssessment("UNKNOWN", "request-count-unproved")
    if any(
        type(flag) is not bool or flag is not True
        for flag in (
            observation.transport_completed,
            observation.adapter_completed,
            observation.bounded_response_complete,
        )
    ):
        return ReportAssessment("UNKNOWN", "completion-unproved")
    if type(observation.errors) is not tuple or observation.errors:
        return ReportAssessment("UNKNOWN", "adapter-error")
    if any(
        type(count) is not int or count < 0
        for count in (observation.raw_count, observation.returned_count)
    ):
        return ReportAssessment("UNKNOWN", "counts-unproved")
    if observation.raw_count != observation.returned_count:
        return ReportAssessment("UNKNOWN", "response-return-count-mismatch")
    if type(observation.receipt_counts) is not tuple or any(
        type(count) is not int or count < 0 for count in observation.receipt_counts
    ):
        return ReportAssessment("UNKNOWN", "receipt-unproved")
    expected_receipts = () if expected.kind == "order" else (observation.returned_count,)
    if observation.receipt_counts != expected_receipts:
        return ReportAssessment("UNKNOWN", "receipt-count-mismatch")
    count = observation.returned_count
    if expected.kind == "order" and count not in (0, 1):
        return ReportAssessment("UNKNOWN", "single-report-count-mismatch")
    if (
        type(observation.report_types) is not tuple
        or len(observation.report_types) != count
        or any(
            type(name) is not str or name != REPORT_TYPES[expected.kind]
            for name in observation.report_types
        )
        or type(observation.report_identities) is not tuple
        or len(observation.report_identities) != count
        or any(
            type(identity) is not tuple
            or len(identity) != 2
            or not all(_text(value) for value in identity)
            or identity != (expected.account_id, expected.instrument_id)
            for identity in observation.report_identities
        )
    ):
        return ReportAssessment("UNKNOWN", "returned-report-scope-mismatch")
    if (
        type(observation.report_order_ids) is not tuple
        or len(observation.report_order_ids) != count
        or any(
            type(identifiers) is not tuple
            or len(identifiers) != 2
            or any(value is not None and not _text(value) for value in identifiers)
            for identifiers in observation.report_order_ids
        )
    ):
        return ReportAssessment("UNKNOWN", "returned-order-identity-unproved")
    if expected.kind == "order" and any(
        expected_value is not None and expected_value != returned_value
        for identifiers in observation.report_order_ids
        for (_key, expected_value), returned_value in zip(expected.query, identifiers, strict=True)
    ):
        return ReportAssessment("UNKNOWN", "returned-order-identity-mismatch")
    if count:
        return ReportAssessment("BOUNDED_NONEMPTY", "consistent-bounded-observation")
    state: ObservationState = "BOUNDED_NOT_FOUND" if expected.kind == "order" else "BOUNDED_EMPTY"
    return ReportAssessment(state, "consistent-bounded-observation")
