"""Dormant outer-envelope-v3 market-data contracts.

Nothing in this module is imported by the active schema-v2 producers.  The
contracts describe the future atomic cutover boundary and perform only pure,
locally checkable validation over explicitly supplied values.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self

from hyperliquid_bot.contracts import Instrument, TradeEvent, Venue
from hyperliquid_bot.data_provenance import (
    MAX_CANONICAL_IDENTIFIER_LENGTH,
    MAX_COVERAGE_TRANSITION_REFERENCES,
    MAX_DECODED_EVENTS_PER_RAW_RECORD,
    MAX_NORMALIZATION_EVIDENCE_ITEMS,
    MAX_NORMALIZATION_OUTCOME_ITEMS,
    MAX_SOURCE_SEQUENCE_RANGES,
    MAX_SOURCE_TIME_FACTS,
    MAX_UNSIGNED_64,
    AdapterFeedBindingId,
    CollectorRunId,
    ConnectionSessionId,
    CorrelationId,
    CoverageDomain,
    CoverageEvidenceKind,
    CoverageReason,
    CoverageScope,
    CoverageScopeId,
    CoverageStatus,
    CoverageTransitionId,
    EventActivationRequirement,
    EventCoverage,
    FeedProductId,
    FeedProductIdentity,
    InstrumentMetadataObservationId,
    InstrumentSpecificationId,
    NormalizationFailureCategory,
    NormalizationRunId,
    PublicSourceSelector,
    PublicSourceSelectorKind,
    RawMarketDataRecord,
    RawRecordId,
    SourceEventId,
    SourceSequenceRange,
    SourceTimeFact,
    SourceTimeRole,
    SourceTransactionId,
    SubscriptionAttemptId,
    SubscriptionAttemptIdentity,
    SubscriptionAttemptStatus,
    SubscriptionPlanId,
    SubscriptionSpecId,
    SubscriptionSpecIdentity,
    canonical_json_array,
    canonical_utc_datetime,
    parse_canonical_json_array,
    require_collection_size,
    require_sha256,
    sha256_hex,
)
from hyperliquid_bot.instrument_metadata import ResolvedInstrumentMetadata

MARKET_EVENT_ENVELOPE_SCHEMA_VERSION: Final = 3
TRADE_EVENT_FAMILY_SCHEMA_VERSION: Final = 2

_ADAPTER_FEED_BINDING_ID_VERSION: Final = "adapter-feed-binding-v1"
_LOGICAL_SOURCE_KEY_ID_VERSION: Final = "logical-source-key-v1"
_OBSERVATION_KEY_ID_VERSION: Final = "observation-key-v1"
_MATERIALIZATION_KEY_ID_VERSION: Final = "materialization-key-v1"
_RAW_EVENT_NORMALIZATION_SCOPE_BINDING_VERSION: Final = "raw-event-normalization-scope-binding-v1"
_RAW_FRAME_NORMALIZATION_SCOPE_BINDING_VERSION: Final = "raw-frame-normalization-scope-binding-v1"
_RAW_EVENT_NORMALIZATION_OUTCOME_ID_VERSION: Final = "raw-event-normalization-outcome-v1"
_NORMALIZATION_OUTCOME_CONTENT_VERSION: Final = "normalization-outcome-content-v1"
_NORMALIZATION_OUTCOME_ID_VERSION: Final = "normalization-outcome-v1"


def _require_text(
    value: object,
    *,
    field_name: str,
    maximum_length: int = 256,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    if len(value) > maximum_length:
        raise ValueError(f"{field_name} exceeds its maximum length.")
    if not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{field_name} must be non-empty printable text without outer whitespace.")
    return value


def _require_non_negative_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    if value > MAX_UNSIGNED_64:
        raise ValueError(f"{field_name} exceeds the supported unsigned-64 bound.")
    return value


def _require_utc(value: object, *, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a built-in datetime.")
    canonical_utc_datetime(value, field_name=field_name)
    return value.replace(tzinfo=UTC)


def _parse_canonical_identifier(
    value: object,
    *,
    field_name: str,
    version_tag: str,
) -> tuple[object, ...]:
    components = parse_canonical_json_array(
        value,
        field_name=field_name,
        maximum_length=MAX_CANONICAL_IDENTIFIER_LENGTH,
    )
    if not components or components[0] != version_tag:
        raise ValueError(f"{field_name} must use version tag {version_tag!r}.")
    return components


def _require_exact_tuple[T](
    values: object,
    *,
    item_type: type[T],
    field_name: str,
    maximum_items: int,
) -> tuple[T, ...]:
    if type(values) is not tuple:
        raise TypeError(f"{field_name} must be a built-in tuple.")
    require_collection_size(
        values,
        field_name=field_name,
        maximum_items=maximum_items,
    )
    if any(type(value) is not item_type for value in values):
        raise TypeError(f"every {field_name} member must be a {item_type.__name__}.")
    return values


@dataclass(frozen=True, slots=True)
class LogicalSourceKeyId:
    """Canonical identifier for a logical source event within a feed product."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="logical_source_key_id",
            version_tag=_LOGICAL_SOURCE_KEY_ID_VERSION,
        )
        if len(components) != 3 or any(type(item) is not str for item in components[1:]):
            raise ValueError("logical_source_key_id has invalid components.")
        assert type(components[1]) is str
        assert type(components[2]) is str
        FeedProductId(components[1])
        SourceEventId(components[2])


@dataclass(frozen=True, slots=True)
class ObservationKeyId:
    """Canonical identifier for one indexed observation inside one raw record."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="observation_key_id",
            version_tag=_OBSERVATION_KEY_ID_VERSION,
        )
        if (
            len(components) != 3
            or type(components[1]) is not str
            or type(components[2]) is not int
            or components[2] < 0
            or components[2] > MAX_UNSIGNED_64
        ):
            raise ValueError("observation_key_id has invalid components.")
        RawRecordId(components[1])


@dataclass(frozen=True, slots=True)
class MaterializationKeyId:
    """Canonical identifier for one versioned normalization materialization."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="materialization_key_id",
            version_tag=_MATERIALIZATION_KEY_ID_VERSION,
        )
        if (
            len(components) != 6
            or type(components[1]) is not str
            or type(components[2]) is not str
            or type(components[3]) is not str
            or type(components[4]) is not int
            or components[4] <= 0
            or components[4] > MAX_UNSIGNED_64
            or type(components[5]) is not str
        ):
            raise ValueError("materialization_key_id has invalid components.")
        NormalizationRunId(components[1])
        ObservationKeyId(components[2])
        if components[3] not in {item.value for item in EventFamily}:
            raise ValueError("materialization_key_id has an unsupported event family.")
        if components[5] not in {item.value for item in PayloadType}:
            raise ValueError("materialization_key_id has an unsupported payload type.")


def _validate_normalization_scope_binding_components(
    value: object,
    *,
    observation_key_id: ObservationKeyId,
) -> None:
    if type(value) is not tuple or len(value) != 11:
        raise ValueError("normalization scope binding has invalid components.")
    if value[0] != _RAW_EVENT_NORMALIZATION_SCOPE_BINDING_VERSION:
        raise ValueError("normalization scope binding has an unexpected version tag.")
    if any(type(value[index]) is not str for index in (1, 2, 3, 5, 6, 7, 8, 10)):
        raise ValueError("normalization scope binding has invalid text components.")
    assert type(value[1]) is str
    assert type(value[2]) is str
    assert type(value[3]) is str
    assert type(value[5]) is str
    assert type(value[6]) is str
    assert type(value[7]) is str
    assert type(value[8]) is str
    assert type(value[10]) is str
    raw_record_id = RawRecordId(value[1])
    require_sha256(value[2], field_name="full_record_integrity_sha256")
    subscription_plan_id = SubscriptionPlanId(value[3])
    raw_event_index = _require_non_negative_int(value[4], field_name="raw_event_index")
    subscription_spec_id = SubscriptionSpecId(value[5])
    subscription_attempt_id = SubscriptionAttemptId(value[6])
    attempt_status = SubscriptionAttemptStatus(value[7])
    require_sha256(value[8], field_name="canonical_instrument_id_sha256")
    selector_components = value[9]
    if type(selector_components) is not tuple or len(selector_components) != 3:
        raise ValueError("normalization scope binding selector has invalid components.")
    if selector_components[0] != "public-source-selector-v1":
        raise ValueError("normalization scope binding selector has an unexpected version tag.")
    if type(selector_components[1]) is not str or type(selector_components[2]) is not str:
        raise ValueError("normalization scope binding selector has invalid text components.")
    selector_kind = PublicSourceSelectorKind(selector_components[1])
    PublicSourceSelector(
        selector_kind,
        selector_components[2],
    )
    coverage_scope_id = CoverageScopeId(value[10])

    observation_components = json.loads(observation_key_id.value)
    raw_components = json.loads(raw_record_id.value)
    plan_components = json.loads(subscription_plan_id.value)
    spec_components = json.loads(subscription_spec_id.value)
    attempt_components = json.loads(subscription_attempt_id.value)
    scope_components = json.loads(coverage_scope_id.value)
    if (
        observation_components[1] != raw_record_id.value
        or observation_components[2] != raw_event_index
    ):
        raise ValueError("normalization scope binding must match the observation key.")
    if not (raw_components[1] == plan_components[1] == spec_components[1] == scope_components[2]):
        raise ValueError("normalization scope binding feed identities must match.")
    if scope_components[1] != CoverageDomain.SILVER_NORMALIZATION.value:
        raise ValueError("normalization scope binding requires Silver-normalization coverage.")
    if (
        attempt_components[1] != raw_components[3]
        or attempt_components[2] != subscription_spec_id.value
    ):
        raise ValueError("normalization scope binding attempt must match raw session and spec.")
    del attempt_status


@dataclass(frozen=True, slots=True)
class RawEventNormalizationOutcomeId:
    """Canonical identifier for one index-specific normalization outcome."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="raw_event_normalization_outcome_id",
            version_tag=_RAW_EVENT_NORMALIZATION_OUTCOME_ID_VERSION,
        )
        if (
            len(components) != 7
            or type(components[1]) is not str
            or type(components[2]) is not tuple
            or type(components[3]) is not str
            or (components[4] is not None and type(components[4]) is not str)
            or (components[5] is not None and type(components[5]) is not str)
            or (components[6] is not None and type(components[6]) is not str)
        ):
            raise ValueError("raw_event_normalization_outcome_id has invalid components.")
        observation_key_id = ObservationKeyId(components[1])
        _validate_normalization_scope_binding_components(
            components[2],
            observation_key_id=observation_key_id,
        )
        if components[3] not in {item.value for item in RawEventDisposition}:
            raise ValueError("raw event outcome has an unsupported disposition.")
        if components[4] is not None:
            LogicalSourceKeyId(components[4])
        if components[5] is not None:
            MaterializationKeyId(components[5])
        if components[6] is not None:
            if components[6] not in {item.value for item in NormalizationEvidence}:
                raise ValueError("raw event outcome has unsupported evidence.")


@dataclass(frozen=True, slots=True)
class NormalizationOutcomeId:
    """Canonical identifier for one complete frame-normalization outcome."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="normalization_outcome_id",
            version_tag=_NORMALIZATION_OUTCOME_ID_VERSION,
        )
        if (
            len(components) != 8
            or type(components[1]) is not str
            or type(components[2]) is not str
            or type(components[3]) is not str
            or type(components[4]) is not str
            or type(components[5]) is not str
            or (components[6] is not None and type(components[6]) is not int)
            or type(components[7]) is not str
        ):
            raise ValueError("normalization_outcome_id has invalid components.")
        if components[6] is not None and (
            components[6] < 0 or components[6] > MAX_DECODED_EVENTS_PER_RAW_RECORD
        ):
            raise ValueError("normalization_outcome_id decoded count is outside its bound.")
        NormalizationRunId(components[1])
        RawRecordId(components[2])
        _require_text(components[3], field_name="normalizer_version")
        _require_text(components[4], field_name="normalizer_commit")
        if components[5] not in {item.value for item in FrameNormalizationStatus}:
            raise ValueError("normalization_outcome_id has an unsupported frame status.")
        require_sha256(components[7], field_name="normalization_outcome_content_sha256")


class EventFamily(StrEnum):
    """Implemented venue-neutral event families."""

    TRADE = "trade"


class PayloadType(StrEnum):
    """Implemented concrete payload discriminators."""

    TRADE = "trade"


@dataclass(frozen=True, slots=True)
class AdapterFeedBinding:
    """Pure catalog binding between an adapter implementation and a feed product.

    ID preimage, in exact order::

        ["adapter-feed-binding-v1", adapter_code, feed_product_id, venue,
         event_activation_requirement]
    """

    adapter_code: str
    feed_product: FeedProductIdentity
    venue: Venue
    event_activation_requirement: EventActivationRequirement
    adapter_feed_binding_id: AdapterFeedBindingId = field(init=False)

    def __post_init__(self) -> None:
        adapter_code = _require_text(self.adapter_code, field_name="adapter_code")
        if type(self.feed_product) is not FeedProductIdentity:
            raise TypeError("feed_product must be a FeedProductIdentity.")
        if type(self.venue) is not Venue:
            raise TypeError("venue must be a Venue.")
        if self.feed_product.venue is not self.venue:
            raise ValueError("adapter venue must match feed-product venue.")
        if type(self.event_activation_requirement) is not EventActivationRequirement:
            raise TypeError("event_activation_requirement must be an EventActivationRequirement.")
        canonical = canonical_json_array(
            (
                _ADAPTER_FEED_BINDING_ID_VERSION,
                adapter_code,
                self.feed_product.feed_product_id.value,
                self.venue.value,
                self.event_activation_requirement.value,
            )
        )
        object.__setattr__(
            self,
            "adapter_feed_binding_id",
            AdapterFeedBindingId(canonical),
        )


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Immutable source facts, separate from collector observation facts."""

    source_event_id: SourceEventId
    source_transaction_id: SourceTransactionId | None
    source_time_facts: tuple[SourceTimeFact, ...]
    source_sequence_ranges: tuple[SourceSequenceRange, ...]

    def __post_init__(self) -> None:
        if type(self.source_event_id) is not SourceEventId:
            raise TypeError("source_event_id must be a SourceEventId.")
        if self.source_transaction_id is not None and (
            type(self.source_transaction_id) is not SourceTransactionId
        ):
            raise TypeError("source_transaction_id must be a SourceTransactionId or None.")
        time_facts = _require_exact_tuple(
            self.source_time_facts,
            item_type=SourceTimeFact,
            field_name="source_time_facts",
            maximum_items=MAX_SOURCE_TIME_FACTS,
        )
        sequence_ranges = _require_exact_tuple(
            self.source_sequence_ranges,
            item_type=SourceSequenceRange,
            field_name="source_sequence_ranges",
            maximum_items=MAX_SOURCE_SEQUENCE_RANGES,
        )
        if len(set(time_facts)) != len(time_facts):
            raise ValueError("source_time_facts must not contain duplicates.")
        if len(set(sequence_ranges)) != len(sequence_ranges):
            raise ValueError("source_sequence_ranges must not contain duplicates.")


@dataclass(frozen=True, slots=True)
class ObservationProvenance:
    """Exact collector and normalizer observation facts for one materialization."""

    feed_product_id: FeedProductId
    collector_run_id: CollectorRunId
    connection_session_id: ConnectionSessionId
    subscription_plan_id: SubscriptionPlanId
    subscription_spec_id: SubscriptionSpecId
    subscription_attempt_id: SubscriptionAttemptId
    raw_record_id: RawRecordId
    raw_event_index: int
    received_time: datetime
    received_monotonic_ns: int
    collector_version: str
    collector_commit: str
    normalization_run_id: NormalizationRunId
    normalizer_version: str
    normalizer_commit: str

    def __post_init__(self) -> None:
        expected_types: tuple[tuple[object, type[object], str], ...] = (
            (self.feed_product_id, FeedProductId, "feed_product_id"),
            (self.collector_run_id, CollectorRunId, "collector_run_id"),
            (
                self.connection_session_id,
                ConnectionSessionId,
                "connection_session_id",
            ),
            (self.subscription_plan_id, SubscriptionPlanId, "subscription_plan_id"),
            (self.subscription_spec_id, SubscriptionSpecId, "subscription_spec_id"),
            (
                self.subscription_attempt_id,
                SubscriptionAttemptId,
                "subscription_attempt_id",
            ),
            (self.raw_record_id, RawRecordId, "raw_record_id"),
            (self.normalization_run_id, NormalizationRunId, "normalization_run_id"),
        )
        for value, expected_type, field_name in expected_types:
            if type(value) is not expected_type:
                raise TypeError(f"{field_name} must be a {expected_type.__name__}.")
        _require_non_negative_int(self.raw_event_index, field_name="raw_event_index")
        received_time = _require_utc(self.received_time, field_name="received_time")
        object.__setattr__(self, "received_time", received_time)
        _require_non_negative_int(
            self.received_monotonic_ns,
            field_name="received_monotonic_ns",
        )
        _require_text(self.collector_version, field_name="collector_version")
        _require_text(self.collector_commit, field_name="collector_commit")
        _require_text(self.normalizer_version, field_name="normalizer_version")
        _require_text(self.normalizer_commit, field_name="normalizer_commit")

        session_components = json.loads(self.connection_session_id.value)
        plan_components = json.loads(self.subscription_plan_id.value)
        spec_components = json.loads(self.subscription_spec_id.value)
        attempt_components = json.loads(self.subscription_attempt_id.value)
        raw_components = json.loads(self.raw_record_id.value)
        if session_components[1] != self.collector_run_id.value:
            raise ValueError("connection session must belong to collector run.")
        if (
            plan_components[1] != self.feed_product_id.value
            or spec_components[1] != self.feed_product_id.value
            or raw_components[1] != self.feed_product_id.value
        ):
            raise ValueError("plan, spec and raw record must belong to the observation feed.")
        if (
            attempt_components[1] != self.connection_session_id.value
            or attempt_components[2] != self.subscription_spec_id.value
        ):
            raise ValueError(
                "subscription attempt must belong to the observation session and spec."
            )
        if (
            raw_components[2] != self.collector_run_id.value
            or raw_components[3] != self.connection_session_id.value
        ):
            raise ValueError("raw record must belong to the observation run and session.")


@dataclass(frozen=True, slots=True)
class LogicalSourceKey:
    """Feed-scoped source-event key.

    ID preimage, in exact order::

        ["logical-source-key-v1", feed_product_id, source_event_id]
    """

    feed_product_id: FeedProductId
    source_event_id: SourceEventId
    logical_source_key_id: LogicalSourceKeyId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.source_event_id) is not SourceEventId:
            raise TypeError("source_event_id must be a SourceEventId.")
        canonical = canonical_json_array(
            (
                _LOGICAL_SOURCE_KEY_ID_VERSION,
                self.feed_product_id.value,
                self.source_event_id.value,
            )
        )
        object.__setattr__(self, "logical_source_key_id", LogicalSourceKeyId(canonical))


@dataclass(frozen=True, slots=True)
class ObservationKey:
    """Raw-record and wire-index observation key.

    ID preimage, in exact order::

        ["observation-key-v1", raw_record_id, raw_event_index]
    """

    raw_record_id: RawRecordId
    raw_event_index: int
    observation_key_id: ObservationKeyId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        raw_event_index = _require_non_negative_int(
            self.raw_event_index,
            field_name="raw_event_index",
        )
        canonical = canonical_json_array(
            (
                _OBSERVATION_KEY_ID_VERSION,
                self.raw_record_id.value,
                raw_event_index,
            )
        )
        object.__setattr__(self, "observation_key_id", ObservationKeyId(canonical))


@dataclass(frozen=True, slots=True)
class MaterializationKey:
    """Versioned key for one normalized observation materialization.

    ID preimage, in exact order::

        [
          "materialization-key-v1",
          normalization_run_id,
          observation_key_id,
          event_family,
          event_family_schema_version,
          payload_type
        ]
    """

    normalization_run_id: NormalizationRunId
    observation_key: ObservationKey
    event_family: EventFamily
    event_family_schema_version: int
    payload_type: PayloadType
    materialization_key_id: MaterializationKeyId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        if type(self.observation_key) is not ObservationKey:
            raise TypeError("observation_key must be an ObservationKey.")
        if type(self.event_family) is not EventFamily:
            raise TypeError("event_family must be an EventFamily.")
        family_version = _require_non_negative_int(
            self.event_family_schema_version,
            field_name="event_family_schema_version",
        )
        if family_version == 0:
            raise ValueError("event_family_schema_version must be positive.")
        if type(self.payload_type) is not PayloadType:
            raise TypeError("payload_type must be a PayloadType.")
        canonical = canonical_json_array(
            (
                _MATERIALIZATION_KEY_ID_VERSION,
                self.normalization_run_id.value,
                self.observation_key.observation_key_id.value,
                self.event_family.value,
                family_version,
                self.payload_type.value,
            )
        )
        object.__setattr__(
            self,
            "materialization_key_id",
            MaterializationKeyId(canonical),
        )


@dataclass(frozen=True, slots=True)
class DecodedEventSubscriptionBinding:
    """Caller-supplied decoded-index binding to one structured wire attempt."""

    raw_event_index: int
    subscription_spec: SubscriptionSpecIdentity
    subscription_attempt: SubscriptionAttemptIdentity

    def __post_init__(self) -> None:
        _require_non_negative_int(self.raw_event_index, field_name="raw_event_index")
        if type(self.subscription_spec) is not SubscriptionSpecIdentity:
            raise TypeError("subscription_spec must be a SubscriptionSpecIdentity.")
        if type(self.subscription_attempt) is not SubscriptionAttemptIdentity:
            raise TypeError("subscription_attempt must be a SubscriptionAttemptIdentity.")
        if (
            self.subscription_attempt.subscription_spec.subscription_spec_id
            != self.subscription_spec.subscription_spec_id
        ):
            raise ValueError("subscription attempt must belong to subscription spec.")


@dataclass(frozen=True, slots=True)
class DecodedWirePayloadContext:
    """Pure decoded-item count and per-index subscription interpretation."""

    raw_record_id: RawRecordId
    decoded_event_count: int
    event_bindings: tuple[DecodedEventSubscriptionBinding, ...]

    def __post_init__(self) -> None:
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        decoded_count = _require_non_negative_int(
            self.decoded_event_count,
            field_name="decoded_event_count",
        )
        if decoded_count > MAX_DECODED_EVENTS_PER_RAW_RECORD:
            raise ValueError("decoded_event_count exceeds the per-record bound.")
        event_bindings = _require_exact_tuple(
            self.event_bindings,
            item_type=DecodedEventSubscriptionBinding,
            field_name="event_bindings",
            maximum_items=MAX_DECODED_EVENTS_PER_RAW_RECORD,
        )
        indexes = tuple(binding.raw_event_index for binding in event_bindings)
        if indexes != tuple(range(decoded_count)):
            raise ValueError(
                "event_bindings must contain every decoded index exactly once in wire order."
            )

    def binding_for_index(self, raw_event_index: int) -> DecodedEventSubscriptionBinding:
        """Return the explicitly supplied binding for one validated decoded index."""

        index = _require_non_negative_int(raw_event_index, field_name="raw_event_index")
        if index >= self.decoded_event_count:
            raise ValueError("raw_event_index is outside the decoded wire payload.")
        return self.event_bindings[index]


@dataclass(frozen=True, slots=True, init=False)
class RawEventNormalizationScopeBinding:
    """Factory-validated raw index, subscription, instrument and coverage scope.

    Exact nested canonical row::

        ["raw-event-normalization-scope-binding-v1", raw_record_id,
         full_record_integrity_sha256, subscription_plan_id, raw_event_index,
         subscription_spec_id, subscription_attempt_id, attempt_status,
         canonical_instrument_id_sha256,
         ["public-source-selector-v1", selector_kind, selector_value],
         coverage_scope_id]

    The canonical instrument text and public selector are retained on the value
    but hidden from ordinary representations. Construction derives the event's
    instrument through the raw record's validated subscription plan rather than
    accepting a caller-selected instrument association.
    """

    raw_record_id: RawRecordId
    full_record_integrity_sha256: str
    subscription_plan_id: SubscriptionPlanId
    raw_event_index: int
    subscription_spec_id: SubscriptionSpecId
    subscription_attempt_id: SubscriptionAttemptId
    attempt_status: SubscriptionAttemptStatus
    canonical_instrument_id: str = field(repr=False)
    canonical_instrument_id_sha256: str
    source_selector: PublicSourceSelector = field(repr=False)
    coverage_scope_id: CoverageScopeId

    def __init__(self) -> None:
        raise TypeError("use RawEventNormalizationScopeBinding.from_raw_record().")

    @classmethod
    def from_raw_record(
        cls,
        *,
        raw_record: RawMarketDataRecord,
        decoded_wire_payload: DecodedWirePayloadContext,
        raw_event_index: int,
        source_selector: PublicSourceSelector,
        coverage_scope: CoverageScope,
    ) -> Self:
        """Derive an exact event scope from one validated raw record and plan."""

        if cls is not RawEventNormalizationScopeBinding:
            raise TypeError("normalization scope bindings do not support subclass construction.")
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        if type(decoded_wire_payload) is not DecodedWirePayloadContext:
            raise TypeError("decoded_wire_payload must be a DecodedWirePayloadContext.")
        if type(source_selector) is not PublicSourceSelector:
            raise TypeError("source_selector must be a PublicSourceSelector.")
        if type(coverage_scope) is not CoverageScope:
            raise TypeError("coverage_scope must be a CoverageScope.")
        if decoded_wire_payload.raw_record_id != raw_record.raw_record_id:
            raise ValueError("decoded payload must belong to the supplied raw record.")
        decoded_binding = decoded_wire_payload.binding_for_index(raw_event_index)

        matching_snapshots = tuple(
            snapshot
            for snapshot in raw_record.subscription_attempt_snapshots
            if snapshot.subscription_spec.subscription_spec_id
            == decoded_binding.subscription_spec.subscription_spec_id
            and snapshot.subscription_attempt.subscription_attempt_id
            == decoded_binding.subscription_attempt.subscription_attempt_id
        )
        if len(matching_snapshots) != 1:
            raise ValueError("decoded event attempt must exist exactly once in the raw record.")
        if (
            decoded_binding.subscription_attempt.connection_session.connection_session_id
            != raw_record.connection_session.connection_session_id
        ):
            raise ValueError("decoded event attempt must belong to the raw-record session.")

        plan_bindings = tuple(
            binding
            for binding in raw_record.subscription_plan.instrument_bindings
            if binding.subscription_spec_id
            == decoded_binding.subscription_spec.subscription_spec_id
            and binding.source_selector == source_selector
        )
        if len(plan_bindings) != 1:
            raise ValueError(
                "raw-record plan must bind the decoded spec and selector to exactly one instrument."
            )
        plan_binding = plan_bindings[0]
        if coverage_scope.domain is not CoverageDomain.SILVER_NORMALIZATION:
            raise ValueError("event outcome scope must be Silver-normalization coverage.")
        if coverage_scope.feed_product_id != raw_record.feed_product.feed_product_id:
            raise ValueError("event outcome scope must use the raw-record feed product.")
        if decoded_binding.subscription_spec.subscription_spec_id not in (
            coverage_scope.subscription_spec_ids
        ):
            raise ValueError("event outcome scope must contain the decoded subscription spec.")
        if plan_binding.canonical_instrument_id not in coverage_scope.canonical_instrument_ids:
            raise ValueError("event outcome scope must contain the selector-bound instrument.")

        normalization_bindings = tuple(
            binding
            for binding in raw_record.subscription_plan.normalization_bindings
            if binding.subscription_spec_id
            == decoded_binding.subscription_spec.subscription_spec_id
            and binding.adapter_profile == plan_binding.adapter_profile
            and binding.event_family == coverage_scope.event_family
            and binding.event_family_schema_version == coverage_scope.event_family_schema_version
            and binding.payload_type == coverage_scope.payload_type
        )
        if len(normalization_bindings) != 1:
            raise ValueError("event outcome scope must match one exact plan normalization binding.")

        canonical_instrument_id_sha256 = sha256_hex(
            plan_binding.canonical_instrument_id.encode("utf-8"),
            field_name="canonical_instrument_id",
        )
        instance = cls.__new__(cls)
        object.__setattr__(instance, "raw_record_id", raw_record.raw_record_id)
        object.__setattr__(
            instance,
            "full_record_integrity_sha256",
            raw_record.full_record_integrity_sha256,
        )
        object.__setattr__(
            instance,
            "subscription_plan_id",
            raw_record.subscription_plan.subscription_plan_id,
        )
        object.__setattr__(instance, "raw_event_index", raw_event_index)
        object.__setattr__(
            instance,
            "subscription_spec_id",
            decoded_binding.subscription_spec.subscription_spec_id,
        )
        object.__setattr__(
            instance,
            "subscription_attempt_id",
            decoded_binding.subscription_attempt.subscription_attempt_id,
        )
        object.__setattr__(
            instance,
            "attempt_status",
            matching_snapshots[0].attempt_status,
        )
        object.__setattr__(
            instance,
            "canonical_instrument_id",
            plan_binding.canonical_instrument_id,
        )
        object.__setattr__(
            instance,
            "canonical_instrument_id_sha256",
            canonical_instrument_id_sha256,
        )
        object.__setattr__(instance, "source_selector", source_selector)
        object.__setattr__(instance, "coverage_scope_id", coverage_scope.coverage_scope_id)
        return instance

    def canonical_components(self) -> tuple[object, ...]:
        """Return the exact versioned nested row used by the outcome ID."""

        return (
            _RAW_EVENT_NORMALIZATION_SCOPE_BINDING_VERSION,
            self.raw_record_id.value,
            self.full_record_integrity_sha256,
            self.subscription_plan_id.value,
            self.raw_event_index,
            self.subscription_spec_id.value,
            self.subscription_attempt_id.value,
            self.attempt_status.value,
            self.canonical_instrument_id_sha256,
            self.source_selector.canonical_components(),
            self.coverage_scope_id.value,
        )


@dataclass(frozen=True, slots=True, init=False)
class RawFrameNormalizationScopeBinding:
    """Factory-validated pre-index raw-record and normalization-scope lineage."""

    raw_record_id: RawRecordId
    full_record_integrity_sha256: str
    subscription_plan_id: SubscriptionPlanId
    coverage_scope_id: CoverageScopeId

    def __init__(self) -> None:
        raise TypeError("use RawFrameNormalizationScopeBinding.from_raw_record().")

    @classmethod
    def from_raw_record(
        cls,
        *,
        raw_record: RawMarketDataRecord,
        coverage_scope: CoverageScope,
    ) -> Self:
        """Bind a pre-index failure to the complete relevant raw-plan scope."""

        if cls is not RawFrameNormalizationScopeBinding:
            raise TypeError("frame scope bindings do not support subclass construction.")
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        if type(coverage_scope) is not CoverageScope:
            raise TypeError("coverage_scope must be a CoverageScope.")
        if coverage_scope.domain is not CoverageDomain.SILVER_NORMALIZATION:
            raise ValueError("frame outcome scope must be Silver-normalization coverage.")
        if coverage_scope.feed_product_id != raw_record.feed_product.feed_product_id:
            raise ValueError("frame outcome scope must use the raw-record feed product.")

        plan = raw_record.subscription_plan
        relevant_normalization_bindings = tuple(
            binding
            for binding in plan.normalization_bindings
            if binding.event_family == coverage_scope.event_family
            and binding.event_family_schema_version == coverage_scope.event_family_schema_version
            and binding.payload_type == coverage_scope.payload_type
        )
        expected_spec_ids = tuple(
            sorted(
                {binding.subscription_spec_id for binding in relevant_normalization_bindings},
                key=lambda item: item.value,
            )
        )
        if not expected_spec_ids or coverage_scope.subscription_spec_ids != expected_spec_ids:
            raise ValueError(
                "frame outcome scope contains a spec outside or missing from the complete "
                "relevant subscription-plan scope."
            )
        expected_instrument_ids = tuple(
            sorted(
                {
                    binding.canonical_instrument_id
                    for binding in plan.instrument_bindings
                    if binding.subscription_spec_id in expected_spec_ids
                }
            )
        )
        if coverage_scope.canonical_instrument_ids != expected_instrument_ids:
            raise ValueError(
                "frame outcome scope has an instrument outside or missing from the complete "
                "relevant subscription-plan scope."
            )
        for spec_id in expected_spec_ids:
            matching = tuple(
                binding
                for binding in relevant_normalization_bindings
                if binding.subscription_spec_id == spec_id
            )
            if len(matching) != 1:
                raise ValueError(
                    "frame outcome scope must match each plan normalization binding exactly."
                )

        instance = cls.__new__(cls)
        object.__setattr__(instance, "raw_record_id", raw_record.raw_record_id)
        object.__setattr__(
            instance,
            "full_record_integrity_sha256",
            raw_record.full_record_integrity_sha256,
        )
        object.__setattr__(
            instance,
            "subscription_plan_id",
            plan.subscription_plan_id,
        )
        object.__setattr__(instance, "coverage_scope_id", coverage_scope.coverage_scope_id)
        return instance

    def canonical_components(self) -> tuple[str, str, str, str, str]:
        """Return the exact versioned row included in frame-outcome content."""

        return (
            _RAW_FRAME_NORMALIZATION_SCOPE_BINDING_VERSION,
            self.raw_record_id.value,
            self.full_record_integrity_sha256,
            self.subscription_plan_id.value,
            self.coverage_scope_id.value,
        )


@dataclass(frozen=True, slots=True, init=False)
class NormalizationContext:
    """Consistent normalized context derived from exactly one raw record.

    Capture-side values cannot be supplied independently.  The only public
    constructor is :meth:`from_raw_record`.
    """

    adapter_feed_binding: AdapterFeedBinding
    decoded_wire_payload: DecodedWirePayloadContext
    source_provenance: SourceProvenance
    observation_provenance: ObservationProvenance
    resolved_instrument_metadata: ResolvedInstrumentMetadata
    event_coverage: EventCoverage

    def __init__(self) -> None:
        raise TypeError("use NormalizationContext.from_raw_record().")

    @classmethod
    def from_raw_record(
        cls,
        *,
        raw_record: RawMarketDataRecord,
        adapter_feed_binding: AdapterFeedBinding,
        decoded_wire_payload: DecodedWirePayloadContext,
        raw_event_index: int,
        source_provenance: SourceProvenance,
        normalization_run_id: NormalizationRunId,
        normalizer_version: str,
        normalizer_commit: str,
        resolved_instrument_metadata: ResolvedInstrumentMetadata,
        event_coverage: EventCoverage,
    ) -> Self:
        """Derive all capture-side provenance and validate supplied relationships."""

        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        if type(adapter_feed_binding) is not AdapterFeedBinding:
            raise TypeError("adapter_feed_binding must be an AdapterFeedBinding.")
        if type(decoded_wire_payload) is not DecodedWirePayloadContext:
            raise TypeError("decoded_wire_payload must be a DecodedWirePayloadContext.")
        if type(source_provenance) is not SourceProvenance:
            raise TypeError("source_provenance must be a SourceProvenance.")
        if type(normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        normalizer_version = _require_text(
            normalizer_version,
            field_name="normalizer_version",
        )
        normalizer_commit = _require_text(
            normalizer_commit,
            field_name="normalizer_commit",
        )
        if type(resolved_instrument_metadata) is not ResolvedInstrumentMetadata:
            raise TypeError("resolved_instrument_metadata must be a ResolvedInstrumentMetadata.")
        if type(event_coverage) is not EventCoverage:
            raise TypeError("event_coverage must be an EventCoverage.")

        if decoded_wire_payload.raw_record_id != raw_record.raw_record_id:
            raise ValueError("decoded payload must belong to the supplied raw record.")
        binding = decoded_wire_payload.binding_for_index(raw_event_index)
        if adapter_feed_binding.feed_product != raw_record.feed_product:
            raise ValueError("adapter binding must match the raw-record feed product.")
        instrument = resolved_instrument_metadata.specification.instrument
        if instrument.venue is not raw_record.feed_product.venue:
            raise ValueError("feed-product venue must match resolved instrument venue.")
        if adapter_feed_binding.venue is not instrument.venue:
            raise ValueError("adapter venue must match resolved instrument venue.")
        if resolved_instrument_metadata.raw_received_time != raw_record.received_time:
            raise ValueError("resolved metadata must use the raw-record received time.")

        matching_snapshots = tuple(
            snapshot
            for snapshot in raw_record.subscription_attempt_snapshots
            if snapshot.subscription_spec.subscription_spec_id
            == binding.subscription_spec.subscription_spec_id
            and snapshot.subscription_attempt.subscription_attempt_id
            == binding.subscription_attempt.subscription_attempt_id
        )
        if len(matching_snapshots) != 1:
            raise ValueError(
                "decoded event subscription lineage must exist exactly once in the raw record."
            )
        if (
            adapter_feed_binding.event_activation_requirement
            is EventActivationRequirement.ACKNOWLEDGED
            and matching_snapshots[0].attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED
        ):
            raise ValueError("event materialization requires an acknowledged subscription attempt.")
        if (
            binding.subscription_attempt.connection_session.connection_session_id
            != raw_record.connection_session.connection_session_id
        ):
            raise ValueError("subscription attempt must belong to the raw-record session.")
        if binding.subscription_spec.feed_product_id != raw_record.feed_product.feed_product_id:
            raise ValueError("subscription spec must belong to the raw-record feed product.")
        if (
            raw_record.subscription_plan.adapter_feed_binding_id
            != adapter_feed_binding.adapter_feed_binding_id
        ):
            raise ValueError("subscription plan must use the selected adapter binding.")

        selected_instrument_bindings = tuple(
            plan_binding
            for plan_binding in raw_record.subscription_plan.instrument_bindings
            if plan_binding.subscription_spec_id == binding.subscription_spec.subscription_spec_id
            and plan_binding.canonical_instrument_id == instrument.canonical_instrument_id
            and plan_binding.instrument_native_symbol == instrument.native_symbol
        )
        if len(selected_instrument_bindings) != 1:
            raise ValueError(
                "subscription plan must bind the selected spec to the resolved instrument."
            )
        selected_normalization_bindings = tuple(
            plan_binding
            for plan_binding in raw_record.subscription_plan.normalization_bindings
            if plan_binding.subscription_spec_id == binding.subscription_spec.subscription_spec_id
            and plan_binding.adapter_profile == adapter_feed_binding.adapter_code
            and plan_binding.event_family == EventFamily.TRADE.value
            and plan_binding.event_family_schema_version == TRADE_EVENT_FAMILY_SCHEMA_VERSION
            and plan_binding.payload_type == PayloadType.TRADE.value
        )
        if len(selected_normalization_bindings) != 1:
            raise ValueError(
                "subscription plan must contain the exact adapter and trade-family binding."
            )

        trade_execution_facts = tuple(
            fact
            for fact in source_provenance.source_time_facts
            if fact.role is SourceTimeRole.TRADE_EXECUTION_TIME
        )
        if len(trade_execution_facts) != 1:
            raise ValueError(
                "trade-family-v2 requires exactly one trade-execution source-time fact."
            )
        if trade_execution_facts[0].utc_value != resolved_instrument_metadata.event_time:
            raise ValueError(
                "trade-execution source time must equal the selected metadata event time."
            )

        selected_spec_id = binding.subscription_spec.subscription_spec_id
        canonical_instrument_id = instrument.canonical_instrument_id
        for coverage_reference in (
            event_coverage.bronze_ingress,
            event_coverage.silver_normalization,
        ):
            scope = coverage_reference.scope
            if coverage_reference.epoch.collector_run_id != raw_record.collector_run_id:
                raise ValueError(
                    "event coverage epoch must belong to the raw-record collector run."
                )
            if scope.feed_product_id != raw_record.feed_product.feed_product_id:
                raise ValueError("event coverage must use the raw-record feed product.")
            if selected_spec_id not in scope.subscription_spec_ids:
                raise ValueError("event coverage must include the event subscription spec.")
            if canonical_instrument_id not in scope.canonical_instrument_ids:
                raise ValueError("event coverage must include the resolved instrument.")
            if scope.event_family != EventFamily.TRADE.value:
                raise ValueError("event coverage must describe the trade event family.")
            if scope.event_family_schema_version != TRADE_EVENT_FAMILY_SCHEMA_VERSION:
                raise ValueError("event coverage must describe trade-family schema version 2.")
            if scope.payload_type != PayloadType.TRADE.value:
                raise ValueError("event coverage must describe the trade payload type.")

        observation_provenance = ObservationProvenance(
            feed_product_id=raw_record.feed_product.feed_product_id,
            collector_run_id=raw_record.collector_run_id,
            connection_session_id=raw_record.connection_session.connection_session_id,
            subscription_plan_id=raw_record.subscription_plan.subscription_plan_id,
            subscription_spec_id=binding.subscription_spec.subscription_spec_id,
            subscription_attempt_id=(binding.subscription_attempt.subscription_attempt_id),
            raw_record_id=raw_record.raw_record_id,
            raw_event_index=raw_event_index,
            received_time=raw_record.received_time,
            received_monotonic_ns=raw_record.received_monotonic_ns,
            collector_version=raw_record.collector_version,
            collector_commit=raw_record.collector_commit,
            normalization_run_id=normalization_run_id,
            normalizer_version=normalizer_version,
            normalizer_commit=normalizer_commit,
        )
        instance = cls.__new__(cls)
        object.__setattr__(instance, "adapter_feed_binding", adapter_feed_binding)
        object.__setattr__(instance, "decoded_wire_payload", decoded_wire_payload)
        object.__setattr__(instance, "source_provenance", source_provenance)
        object.__setattr__(instance, "observation_provenance", observation_provenance)
        object.__setattr__(
            instance,
            "resolved_instrument_metadata",
            resolved_instrument_metadata,
        )
        object.__setattr__(instance, "event_coverage", event_coverage)
        return instance


@dataclass(frozen=True, slots=True)
class MarketEventEnvelopeV3:
    """Dormant mandatory outer-envelope-v3 binding for trade-family-v2."""

    normalization_context: NormalizationContext = field(repr=False)
    event: TradeEvent
    envelope_schema_version: int = MARKET_EVENT_ENVELOPE_SCHEMA_VERSION
    event_family: EventFamily = EventFamily.TRADE
    event_family_schema_version: int = TRADE_EVENT_FAMILY_SCHEMA_VERSION
    payload_type: PayloadType = PayloadType.TRADE
    correlation_id: CorrelationId | None = None
    instrument: Instrument = field(init=False)
    instrument_specification_id: InstrumentSpecificationId = field(init=False)
    instrument_metadata_observation_id: InstrumentMetadataObservationId = field(init=False)
    event_time: datetime = field(init=False)
    source_provenance: SourceProvenance = field(init=False)
    observation_provenance: ObservationProvenance = field(init=False)
    event_coverage: EventCoverage = field(init=False)
    logical_source_key: LogicalSourceKey = field(init=False)
    observation_key: ObservationKey = field(init=False)
    materialization_key: MaterializationKey = field(init=False)

    def __post_init__(self) -> None:
        if type(self.normalization_context) is not NormalizationContext:
            raise TypeError("normalization_context must be a NormalizationContext.")
        if type(self.event) is not TradeEvent:
            raise TypeError("event must be a TradeEvent.")
        if type(self.envelope_schema_version) is not int:
            raise TypeError("envelope_schema_version must be a built-in integer.")
        if self.envelope_schema_version != MARKET_EVENT_ENVELOPE_SCHEMA_VERSION:
            raise ValueError("envelope_schema_version is not supported.")
        if type(self.event_family) is not EventFamily:
            raise TypeError("event_family must be an EventFamily.")
        if self.event_family is not EventFamily.TRADE:
            raise ValueError("only the trade event family is implemented.")
        if type(self.event_family_schema_version) is not int:
            raise TypeError("event_family_schema_version must be a built-in integer.")
        if self.event_family_schema_version != TRADE_EVENT_FAMILY_SCHEMA_VERSION:
            raise ValueError("trade event-family schema version is not supported.")
        if type(self.payload_type) is not PayloadType:
            raise TypeError("payload_type must be a PayloadType.")
        if self.payload_type is not PayloadType.TRADE:
            raise ValueError("only the trade payload type is implemented.")
        if self.correlation_id is not None and type(self.correlation_id) is not CorrelationId:
            raise TypeError("correlation_id must be a CorrelationId or None.")

        context = self.normalization_context
        instrument = context.resolved_instrument_metadata.specification.instrument
        event_time = context.resolved_instrument_metadata.event_time
        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(
            self,
            "instrument_specification_id",
            context.resolved_instrument_metadata.specification.instrument_specification_id,
        )
        object.__setattr__(
            self,
            "instrument_metadata_observation_id",
            context.resolved_instrument_metadata.observation.instrument_metadata_observation_id,
        )
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "source_provenance", context.source_provenance)
        object.__setattr__(
            self,
            "observation_provenance",
            context.observation_provenance,
        )
        object.__setattr__(self, "event_coverage", context.event_coverage)

        logical_source_key = LogicalSourceKey(
            feed_product_id=context.observation_provenance.feed_product_id,
            source_event_id=context.source_provenance.source_event_id,
        )
        observation_key = ObservationKey(
            raw_record_id=context.observation_provenance.raw_record_id,
            raw_event_index=context.observation_provenance.raw_event_index,
        )
        materialization_key = MaterializationKey(
            normalization_run_id=(context.observation_provenance.normalization_run_id),
            observation_key=observation_key,
            event_family=self.event_family,
            event_family_schema_version=self.event_family_schema_version,
            payload_type=self.payload_type,
        )
        object.__setattr__(self, "logical_source_key", logical_source_key)
        object.__setattr__(self, "observation_key", observation_key)
        object.__setattr__(self, "materialization_key", materialization_key)


class RawEventDisposition(StrEnum):
    """Closed index-specific normalization disposition."""

    MATERIALIZED_NEW = "materialized_new"
    EXACT_DUPLICATE_SUPPRESSED = "exact_duplicate_suppressed"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"
    REJECTED = "rejected"
    NOT_MATERIALIZED_FRAME_ABORTED = "not_materialized_frame_aborted"


class FrameNormalizationStatus(StrEnum):
    """Closed frame-level normalization status."""

    CONTROL_NO_EVENT = "control_no_event"
    VALID_EMPTY_MARKET_FRAME = "valid_empty_market_frame"
    MATERIALIZED = "materialized"
    DUPLICATES_ONLY = "duplicates_only"
    MIXED_SUCCESS = "mixed_success"
    REJECTED_BEFORE_INDEXING = "rejected_before_indexing"
    REJECTED_AFTER_INDEXING = "rejected_after_indexing"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"


class NormalizationEvidence(StrEnum):
    """Bounded sanitized normalization evidence categories."""

    PROTOCOL_REJECTION = "protocol_rejection"
    DECODER_REJECTION = "decoder_rejection"
    UNKNOWN_INSTRUMENT = "unknown_instrument"
    METADATA_UNAVAILABLE = "metadata_unavailable"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"
    FRAME_ATOMIC_ABORT = "frame_atomic_abort"
    LOCAL_CONTRACT_FAILURE = "local_contract_failure"


_PREINDEX_REJECTION_EVIDENCE_ALLOWLIST: Final[frozenset[NormalizationEvidence]] = frozenset(
    {
        NormalizationEvidence.PROTOCOL_REJECTION,
        NormalizationEvidence.DECODER_REJECTION,
        NormalizationEvidence.UNKNOWN_INSTRUMENT,
        NormalizationEvidence.METADATA_UNAVAILABLE,
        NormalizationEvidence.PROVENANCE_MISMATCH,
        NormalizationEvidence.LOCAL_CONTRACT_FAILURE,
    }
)


_NORMALIZATION_FAILURE_TO_FRAME_EVIDENCE: Final = {
    NormalizationFailureCategory.PROTOCOL_REJECTION: NormalizationEvidence.PROTOCOL_REJECTION,
    NormalizationFailureCategory.DECODER_REJECTION: NormalizationEvidence.DECODER_REJECTION,
    NormalizationFailureCategory.UNKNOWN_INSTRUMENT: NormalizationEvidence.UNKNOWN_INSTRUMENT,
    NormalizationFailureCategory.METADATA_UNAVAILABLE: NormalizationEvidence.METADATA_UNAVAILABLE,
    NormalizationFailureCategory.LOCAL_CONTRACT_FAILURE: (
        NormalizationEvidence.LOCAL_CONTRACT_FAILURE
    ),
}


@dataclass(frozen=True, slots=True)
class RawEventNormalizationOutcome:
    """Index-specific normalization audit outcome.

    ID preimage, in exact order::

        [
          "raw-event-normalization-outcome-v1",
          observation_key_id,
          raw-event-normalization-scope-binding-v1 row,
          disposition,
          logical_source_key_id-or-null,
          materialization_key_id-or-null,
          evidence-or-null
        ]
    """

    observation_key: ObservationKey
    normalization_scope_binding: RawEventNormalizationScopeBinding
    disposition: RawEventDisposition
    logical_source_key: LogicalSourceKey | None = None
    materialization_key: MaterializationKey | None = None
    evidence: NormalizationEvidence | None = None
    raw_event_normalization_outcome_id: RawEventNormalizationOutcomeId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.observation_key) is not ObservationKey:
            raise TypeError("observation_key must be an ObservationKey.")
        if type(self.normalization_scope_binding) is not RawEventNormalizationScopeBinding:
            raise TypeError(
                "normalization_scope_binding must be a RawEventNormalizationScopeBinding."
            )
        if (
            self.normalization_scope_binding.raw_record_id != self.observation_key.raw_record_id
            or self.normalization_scope_binding.raw_event_index
            != self.observation_key.raw_event_index
        ):
            raise ValueError("normalization scope binding must match the observation key.")
        if type(self.disposition) is not RawEventDisposition:
            raise TypeError("disposition must be a RawEventDisposition.")
        if (
            self.disposition is not RawEventDisposition.REJECTED
            and self.normalization_scope_binding.attempt_status
            is not SubscriptionAttemptStatus.ACKNOWLEDGED
        ):
            raise ValueError(
                "non-rejected event outcomes require an acknowledged subscription attempt."
            )
        if self.logical_source_key is not None and (
            type(self.logical_source_key) is not LogicalSourceKey
        ):
            raise TypeError("logical_source_key must be a LogicalSourceKey or None.")
        if self.materialization_key is not None and (
            type(self.materialization_key) is not MaterializationKey
        ):
            raise TypeError("materialization_key must be a MaterializationKey or None.")
        if self.evidence is not None and type(self.evidence) is not NormalizationEvidence:
            raise TypeError("evidence must be a NormalizationEvidence or None.")

        if self.disposition is RawEventDisposition.MATERIALIZED_NEW:
            if self.logical_source_key is None or self.materialization_key is None:
                raise ValueError("materialized outcomes require logical and materialization keys.")
            if self.evidence is not None:
                raise ValueError("materialized outcomes cannot carry failure evidence.")
        elif self.disposition is RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED:
            if self.logical_source_key is None or self.materialization_key is not None:
                raise ValueError(
                    "duplicate outcomes require a logical key and no materialization key."
                )
            if self.evidence is not None:
                raise ValueError("duplicate outcomes cannot carry failure evidence.")
        else:
            if self.materialization_key is not None:
                raise ValueError("aborted or rejected outcomes cannot materialize events.")
            if self.evidence is None:
                raise ValueError("aborted or rejected outcomes require bounded evidence.")
            if self.disposition is RawEventDisposition.SOURCE_EVENT_CONFLICT:
                if self.logical_source_key is None:
                    raise ValueError("source conflicts require the conflicting logical source key.")
                if self.evidence is not NormalizationEvidence.SOURCE_EVENT_CONFLICT:
                    raise ValueError("source conflicts require source-conflict evidence.")
            elif self.evidence is NormalizationEvidence.SOURCE_EVENT_CONFLICT:
                raise ValueError("source-conflict evidence requires a source-conflict disposition.")
            if (
                self.disposition is RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED
                and self.logical_source_key is None
            ):
                raise ValueError("frame-aborted candidates require their known logical source key.")
            if self.disposition is RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED:
                if self.evidence is not NormalizationEvidence.FRAME_ATOMIC_ABORT:
                    raise ValueError(
                        "frame-aborted candidates require frame-atomic-abort evidence."
                    )
            elif self.evidence is NormalizationEvidence.FRAME_ATOMIC_ABORT:
                raise ValueError("frame-atomic-abort evidence requires an aborted disposition.")

        if self.materialization_key is not None and (
            self.materialization_key.observation_key != self.observation_key
        ):
            raise ValueError("materialization key must use the same observation key.")
        if self.logical_source_key is not None:
            raw_components = json.loads(self.observation_key.raw_record_id.value)
            if self.logical_source_key.feed_product_id.value != raw_components[1]:
                raise ValueError(
                    "logical source key must use the observation raw-record feed product."
                )

        canonical = canonical_json_array(
            (
                _RAW_EVENT_NORMALIZATION_OUTCOME_ID_VERSION,
                self.observation_key.observation_key_id.value,
                self.normalization_scope_binding.canonical_components(),
                self.disposition.value,
                (
                    self.logical_source_key.logical_source_key_id.value
                    if self.logical_source_key is not None
                    else None
                ),
                (
                    self.materialization_key.materialization_key_id.value
                    if self.materialization_key is not None
                    else None
                ),
                self.evidence.value if self.evidence is not None else None,
            )
        )
        object.__setattr__(
            self,
            "raw_event_normalization_outcome_id",
            RawEventNormalizationOutcomeId(canonical),
        )


@dataclass(frozen=True, slots=True)
class NormalizationOutcome:
    """Frame-atomic normalization outcome with a closed status matrix.

    ID preimage, in exact order::

        [
          "normalization-outcome-v1",
          normalization_run_id,
          raw_record_id,
          normalizer_version,
          normalizer_commit,
          frame_status,
          decoded_event_count-or-null,
          normalization_outcome_content_sha256
        ]

    The digest is SHA-256 over this exact bounded-content preimage::

        [
          "normalization-outcome-content-v1",
          ordered-index-outcome-ids,
          ordered-committed-materialization-key-ids,
          sorted-evidence-codes,
          sorted-coverage-transition-ids,
          pre-index-frame-scope-binding-or-null
        ]
    """

    normalization_run_id: NormalizationRunId
    raw_record_id: RawRecordId
    normalizer_version: str
    normalizer_commit: str
    frame_status: FrameNormalizationStatus
    decoded_event_count: int | None
    raw_event_outcomes: tuple[RawEventNormalizationOutcome, ...]
    committed_materialization_keys: tuple[MaterializationKey, ...]
    preindex_scope_binding: RawFrameNormalizationScopeBinding | None = None
    evidence: tuple[NormalizationEvidence, ...] = ()
    coverage_transition_ids: tuple[CoverageTransitionId, ...] = ()
    normalization_outcome_content_sha256: str = field(init=False)
    normalization_outcome_id: NormalizationOutcomeId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        normalizer_version = _require_text(
            self.normalizer_version,
            field_name="normalizer_version",
        )
        normalizer_commit = _require_text(
            self.normalizer_commit,
            field_name="normalizer_commit",
        )
        if type(self.frame_status) is not FrameNormalizationStatus:
            raise TypeError("frame_status must be a FrameNormalizationStatus.")
        if self.decoded_event_count is not None:
            decoded_count = _require_non_negative_int(
                self.decoded_event_count,
                field_name="decoded_event_count",
            )
            if decoded_count > MAX_DECODED_EVENTS_PER_RAW_RECORD:
                raise ValueError("decoded_event_count exceeds the per-record bound.")
        outcomes = _require_exact_tuple(
            self.raw_event_outcomes,
            item_type=RawEventNormalizationOutcome,
            field_name="raw_event_outcomes",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        materializations = _require_exact_tuple(
            self.committed_materialization_keys,
            item_type=MaterializationKey,
            field_name="committed_materialization_keys",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        if self.preindex_scope_binding is not None and (
            type(self.preindex_scope_binding) is not RawFrameNormalizationScopeBinding
        ):
            raise TypeError(
                "preindex_scope_binding must be a RawFrameNormalizationScopeBinding or None."
            )
        if self.frame_status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING:
            if self.preindex_scope_binding is None:
                raise ValueError(
                    "pre-index rejection requires the complete raw-frame scope binding."
                )
            if self.preindex_scope_binding.raw_record_id != self.raw_record_id:
                raise ValueError("pre-index scope binding must match the outcome raw record.")
        elif self.preindex_scope_binding is not None:
            raise ValueError("pre-index scope binding is valid only for pre-index rejection.")
        evidence = _require_exact_tuple(
            self.evidence,
            item_type=NormalizationEvidence,
            field_name="evidence",
            maximum_items=MAX_NORMALIZATION_EVIDENCE_ITEMS,
        )
        transition_ids = _require_exact_tuple(
            self.coverage_transition_ids,
            item_type=CoverageTransitionId,
            field_name="coverage_transition_ids",
            maximum_items=MAX_COVERAGE_TRANSITION_REFERENCES,
        )
        if tuple(sorted(evidence, key=lambda item: item.value)) != evidence:
            raise ValueError("evidence must be sorted by canonical code.")
        if len(set(evidence)) != len(evidence):
            raise ValueError("evidence must be unique.")
        if tuple(sorted(transition_ids, key=lambda item: item.value)) != transition_ids:
            raise ValueError("coverage_transition_ids must be sorted by canonical ID.")
        if len(set(transition_ids)) != len(transition_ids):
            raise ValueError("coverage_transition_ids must be unique.")

        raw_components = json.loads(self.raw_record_id.value)
        raw_feed_product_id = FeedProductId(raw_components[1])
        raw_collector_run_id = raw_components[2]
        for transition_id in transition_ids:
            transition_components = json.loads(transition_id.value)
            scope_components = json.loads(transition_components[1])
            epoch_components = json.loads(transition_components[2])
            evidence_components = json.loads(transition_components[7])
            if CoverageDomain(scope_components[1]) is not CoverageDomain.SILVER_NORMALIZATION:
                raise ValueError(
                    "NormalizationOutcome accepts only Silver-normalization transitions."
                )
            if scope_components[2] != raw_feed_product_id.value:
                raise ValueError("coverage transition feed must match the frame raw record.")
            if (
                epoch_components[1] != transition_components[1]
                or epoch_components[2] != raw_collector_run_id
            ):
                raise ValueError(
                    "coverage transition scope and collector run must match the frame raw record."
                )
            evidence_kind = CoverageEvidenceKind(evidence_components[4])
            if evidence_kind is CoverageEvidenceKind.NORMALIZATION_FAILURE:
                if (
                    transition_components[5] != CoverageStatus.CONFIRMED_INCOMPLETE.value
                    or transition_components[6]
                    != CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE.value
                ):
                    raise ValueError(
                        "normalization-failure transition has incompatible status or reason."
                    )
                source_components = evidence_components[5]
                normalization_failure_components = json.loads(source_components[1])
                if normalization_failure_components[1] != self.raw_record_id.value:
                    raise ValueError(
                        "normalization-failure coverage must match the outcome raw record."
                    )
                if normalization_failure_components[2] != self.normalization_run_id.value:
                    raise ValueError(
                        "normalization-failure coverage must match the outcome normalization run."
                    )
                failure_index = normalization_failure_components[3]
                failure_source_event_id = normalization_failure_components[4]
                failure_category = NormalizationFailureCategory(normalization_failure_components[5])
                expected_frame_evidence = _NORMALIZATION_FAILURE_TO_FRAME_EVIDENCE[failure_category]
                if expected_frame_evidence not in evidence:
                    raise ValueError(
                        "normalization-failure coverage must match frame failure evidence."
                    )
                if failure_index is None:
                    if self.frame_status is not FrameNormalizationStatus.REJECTED_BEFORE_INDEXING:
                        raise ValueError(
                            "index-free normalization failure requires pre-index frame rejection."
                        )
                    if (
                        self.preindex_scope_binding is None
                        or self.preindex_scope_binding.raw_record_id != self.raw_record_id
                        or self.preindex_scope_binding.coverage_scope_id.value
                        != transition_components[1]
                    ):
                        raise ValueError(
                            "pre-index normalization failure requires the exact raw frame scope."
                        )
                else:
                    matching_failure_outcomes = tuple(
                        outcome
                        for outcome in outcomes
                        if outcome.observation_key.raw_event_index == failure_index
                        and outcome.disposition is RawEventDisposition.REJECTED
                        and outcome.evidence is expected_frame_evidence
                        and outcome.normalization_scope_binding.coverage_scope_id.value
                        == transition_components[1]
                    )
                    if (
                        self.frame_status is not FrameNormalizationStatus.REJECTED_AFTER_INDEXING
                        or len(matching_failure_outcomes) != 1
                    ):
                        raise ValueError(
                            "indexed normalization failure must match one rejected frame index."
                        )
                    matching_source_event_id = (
                        matching_failure_outcomes[0].logical_source_key.source_event_id.value
                        if matching_failure_outcomes[0].logical_source_key is not None
                        else None
                    )
                    if failure_source_event_id != matching_source_event_id:
                        raise ValueError(
                            "indexed normalization failure source identity must exactly match "
                            "the rejected frame index."
                        )
            elif evidence_kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT:
                if (
                    transition_components[5] != CoverageStatus.CONFIRMED_INCOMPLETE.value
                    or transition_components[6] != CoverageReason.SOURCE_EVENT_CONFLICT.value
                ):
                    raise ValueError(
                        "source-conflict transition has incompatible status or reason."
                    )
                source_components = evidence_components[5]
                if source_components[3] != self.raw_record_id.value:
                    raise ValueError("source-conflict coverage must match the outcome raw record.")
                conflict_index = source_components[4]
                conflict_source_event_id = source_components[2]
                matching_conflict_outcomes = tuple(
                    outcome
                    for outcome in outcomes
                    if outcome.observation_key.raw_event_index == conflict_index
                    and outcome.disposition is RawEventDisposition.SOURCE_EVENT_CONFLICT
                    and outcome.evidence is NormalizationEvidence.SOURCE_EVENT_CONFLICT
                    and outcome.normalization_scope_binding.coverage_scope_id.value
                    == transition_components[1]
                    and outcome.logical_source_key is not None
                    and outcome.logical_source_key.source_event_id.value == conflict_source_event_id
                )
                if (
                    self.frame_status is not FrameNormalizationStatus.SOURCE_EVENT_CONFLICT
                    or len(matching_conflict_outcomes) != 1
                ):
                    raise ValueError(
                        "source-conflict coverage must match one conflicting frame index."
                    )
            else:
                raise ValueError(
                    "NormalizationOutcome transition evidence must be a normalization failure "
                    "or source-event conflict."
                )

        indexes = tuple(outcome.observation_key.raw_event_index for outcome in outcomes)
        if indexes != tuple(sorted(set(indexes))):
            raise ValueError("raw event outcome indexes must be unique and increasing.")
        for outcome in outcomes:
            if outcome.observation_key.raw_record_id != self.raw_record_id:
                raise ValueError("every index outcome must belong to the frame raw record.")
            if outcome.logical_source_key is not None and (
                outcome.logical_source_key.feed_product_id != raw_feed_product_id
            ):
                raise ValueError("every logical source key must use the frame feed product.")
            if outcome.materialization_key is not None and (
                outcome.materialization_key.normalization_run_id != self.normalization_run_id
            ):
                raise ValueError(
                    "every materialization must belong to the outcome normalization run."
                )
        committed_from_items = tuple(
            outcome.materialization_key
            for outcome in outcomes
            if outcome.materialization_key is not None
        )
        if committed_from_items != materializations:
            raise ValueError(
                "committed materialization keys must exactly match materialized items."
            )
        if len(set(materializations)) != len(materializations):
            raise ValueError("committed materialization keys must be unique.")

        self._validate_closed_matrix(outcomes, materializations, evidence)

        content_canonical = canonical_json_array(
            (
                _NORMALIZATION_OUTCOME_CONTENT_VERSION,
                tuple(outcome.raw_event_normalization_outcome_id.value for outcome in outcomes),
                tuple(key.materialization_key_id.value for key in materializations),
                tuple(item.value for item in evidence),
                tuple(item.value for item in transition_ids),
                (
                    self.preindex_scope_binding.canonical_components()
                    if self.preindex_scope_binding is not None
                    else None
                ),
            )
        )
        content_sha256 = sha256_hex(
            content_canonical.encode("utf-8"),
            field_name="normalization_outcome_content",
        )
        canonical = canonical_json_array(
            (
                _NORMALIZATION_OUTCOME_ID_VERSION,
                self.normalization_run_id.value,
                self.raw_record_id.value,
                normalizer_version,
                normalizer_commit,
                self.frame_status.value,
                self.decoded_event_count,
                content_sha256,
            )
        )
        object.__setattr__(
            self,
            "normalization_outcome_content_sha256",
            content_sha256,
        )
        object.__setattr__(
            self,
            "normalization_outcome_id",
            NormalizationOutcomeId(canonical),
        )

    def _validate_closed_matrix(
        self,
        outcomes: tuple[RawEventNormalizationOutcome, ...],
        materializations: tuple[MaterializationKey, ...],
        evidence: tuple[NormalizationEvidence, ...],
    ) -> None:
        status = self.frame_status
        decoded_count = self.decoded_event_count
        dispositions = tuple(outcome.disposition for outcome in outcomes)

        if status is FrameNormalizationStatus.CONTROL_NO_EVENT:
            if decoded_count is not None or outcomes or materializations or evidence:
                raise ValueError("control/no-event outcome must contain no market results.")
            return
        if status is FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME:
            if decoded_count != 0 or outcomes or materializations or evidence:
                raise ValueError("valid empty market frame must contain no event results.")
            return
        if status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING:
            if decoded_count is not None or outcomes or materializations or not evidence:
                raise ValueError("pre-index rejection requires evidence and no decoded indexes.")
            if any(item not in _PREINDEX_REJECTION_EVIDENCE_ALLOWLIST for item in evidence):
                raise ValueError(
                    "pre-index rejection evidence is not in the explicit closed allowlist."
                )
            return

        if decoded_count is None or decoded_count <= 0:
            raise ValueError("indexed outcomes require a positive decoded_event_count.")
        indexes = tuple(outcome.observation_key.raw_event_index for outcome in outcomes)
        if indexes != tuple(range(decoded_count)):
            raise ValueError("indexed outcomes must cover the supplied decoded context.")
        expected_evidence = tuple(
            sorted(
                {outcome.evidence for outcome in outcomes if outcome.evidence is not None},
                key=lambda item: item.value,
            )
        )
        if evidence != expected_evidence:
            raise ValueError(
                "indexed frame evidence must equal the canonical union of index evidence."
            )

        if status is FrameNormalizationStatus.MATERIALIZED:
            if not dispositions or any(
                disposition is not RawEventDisposition.MATERIALIZED_NEW
                for disposition in dispositions
            ):
                raise ValueError("materialized frame requires only new materializations.")
            if evidence:
                raise ValueError("successful frames cannot carry failure evidence.")
            return
        if status is FrameNormalizationStatus.DUPLICATES_ONLY:
            if not dispositions or any(
                disposition is not RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED
                for disposition in dispositions
            ):
                raise ValueError("duplicate-only frame requires only exact duplicates.")
            if materializations or evidence:
                raise ValueError("duplicate-only frames cannot materialize or fail.")
            return
        if status is FrameNormalizationStatus.MIXED_SUCCESS:
            allowed = {
                RawEventDisposition.MATERIALIZED_NEW,
                RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
            }
            if set(dispositions) != allowed or evidence:
                raise ValueError("mixed success requires both new and duplicate outcomes.")
            return
        if status is FrameNormalizationStatus.REJECTED_AFTER_INDEXING:
            allowed = {
                RawEventDisposition.REJECTED,
                RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
                RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
            }
            if (
                not dispositions
                or any(disposition not in allowed for disposition in dispositions)
                or RawEventDisposition.REJECTED not in dispositions
                or materializations
                or not evidence
            ):
                raise ValueError("indexed rejection violates the closed outcome matrix.")
            return
        if status is FrameNormalizationStatus.SOURCE_EVENT_CONFLICT:
            allowed = {
                RawEventDisposition.SOURCE_EVENT_CONFLICT,
                RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
                RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
            }
            if (
                not dispositions
                or any(disposition not in allowed for disposition in dispositions)
                or RawEventDisposition.SOURCE_EVENT_CONFLICT not in dispositions
                or materializations
                or NormalizationEvidence.SOURCE_EVENT_CONFLICT not in evidence
            ):
                raise ValueError("source-conflict frame violates the closed outcome matrix.")
            return
        raise AssertionError("unhandled frame normalization status")
