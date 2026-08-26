"""Storage-neutral asynchronous acceptance boundaries for market-data audit values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .data_provenance import RawMarketDataRecord, RawRecordId
from .market_event_v3 import NormalizationOutcome, NormalizationOutcomeId

_DESTINATION_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._:-]{0,127})")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class SinkDestinationId:
    """Bounded caller-declared non-secret identity for one sink destination."""

    value: str = field(repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("sink destination IDs do not support subclass definition.")

    def __post_init__(self) -> None:
        if type(self) is not SinkDestinationId:
            raise TypeError("sink destination IDs do not support subclass construction.")
        if type(self.value) is not str:
            raise TypeError("sink destination ID must be a built-in string.")
        if _DESTINATION_ID_PATTERN.fullmatch(self.value) is None:
            raise ValueError("sink destination ID must use the bounded public code grammar.")


@dataclass(frozen=True, slots=True)
class RawRecordAcceptance:
    """A sink accepted ownership of one complete immutable Bronze record.

    Acceptance isn't a claim of durable persistence.
    """

    raw_record_id: RawRecordId
    full_record_integrity_sha256: str = field(repr=False)
    destination_id: SinkDestinationId

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("raw record acceptances do not support subclass definition.")

    def __post_init__(self) -> None:
        if type(self) is not RawRecordAcceptance:
            raise TypeError("raw record acceptances do not support subclass construction.")
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        if type(self.full_record_integrity_sha256) is not str:
            raise TypeError("full_record_integrity_sha256 must be a built-in string.")
        if _SHA256_PATTERN.fullmatch(self.full_record_integrity_sha256) is None:
            raise ValueError("full_record_integrity_sha256 must be a lowercase SHA-256 digest.")
        if type(self.destination_id) is not SinkDestinationId:
            raise TypeError("destination_id must be a SinkDestinationId.")


@dataclass(frozen=True, slots=True)
class NormalizationOutcomeAcceptance:
    """A sink accepted ownership of one complete immutable normalization outcome.

    Acceptance isn't a claim of durable persistence.
    """

    normalization_outcome_id: NormalizationOutcomeId
    destination_id: SinkDestinationId

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("outcome acceptances do not support subclass definition.")

    def __post_init__(self) -> None:
        if type(self) is not NormalizationOutcomeAcceptance:
            raise TypeError("outcome acceptances do not support subclass construction.")
        if type(self.normalization_outcome_id) is not NormalizationOutcomeId:
            raise TypeError("normalization_outcome_id must be a NormalizationOutcomeId.")
        if type(self.destination_id) is not SinkDestinationId:
            raise TypeError("destination_id must be a SinkDestinationId.")


class RawRecordRejected(Exception):
    """Closed argumentless signal that a raw record definitely wasn't accepted."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("raw record rejection does not support subclass definition.")

    def __init__(self) -> None:
        super().__init__()


class NormalizationOutcomeRejected(Exception):
    """Closed argumentless signal that an outcome definitely wasn't accepted."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("outcome rejection does not support subclass definition.")

    def __init__(self) -> None:
        super().__init__()


class SinkFailureCategory(StrEnum):
    """Bounded sink failure categories safe for outward lifecycle reporting."""

    RAW_EXPLICIT_REJECTION = "raw-explicit-rejection"
    RAW_ACCEPTANCE_TIMEOUT = "raw-acceptance-timeout"
    RAW_ACCEPTANCE_AMBIGUOUS = "raw-acceptance-ambiguous"
    RAW_ACCEPTANCE_INVALID = "raw-acceptance-invalid"
    OUTCOME_EXPLICIT_REJECTION = "outcome-explicit-rejection"
    OUTCOME_ACCEPTANCE_TIMEOUT = "outcome-acceptance-timeout"
    OUTCOME_ACCEPTANCE_AMBIGUOUS = "outcome-acceptance-ambiguous"
    OUTCOME_ACCEPTANCE_INVALID = "outcome-acceptance-invalid"
    OUTCOME_CLOSE_TIMEOUT = "outcome-close-timeout"
    OUTCOME_CLOSE_FAILURE = "outcome-close-failure"
    RAW_CLOSE_TIMEOUT = "raw-close-timeout"
    RAW_CLOSE_FAILURE = "raw-close-failure"


@runtime_checkable
class RawRecordSink(Protocol):
    """Mandatory bounded ownership boundary for immutable Bronze records.

    In-process implementations must be cancellation-cooperative. Collector
    deadlines stop waiting without treating a late result as acceptance, but
    Python cannot forcibly terminate arbitrary coroutine code.
    """

    @property
    def destination_id(self) -> SinkDestinationId: ...

    async def accept(self, record: RawMarketDataRecord) -> RawRecordAcceptance: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class NormalizationOutcomeSink(Protocol):
    """Mandatory bounded ownership boundary for frame normalization outcomes.

    In-process implementations must be cancellation-cooperative. Collector
    deadlines stop waiting without treating a late result as acceptance, but
    Python cannot forcibly terminate arbitrary coroutine code.
    """

    @property
    def destination_id(self) -> SinkDestinationId: ...

    async def accept(
        self,
        outcome: NormalizationOutcome,
    ) -> NormalizationOutcomeAcceptance: ...

    async def aclose(self) -> None: ...
