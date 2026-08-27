"""Market-data contracts for normalization audit and the dormant v3 boundary.

``NormalizationOutcome`` is active in the schema-v2 capture path.  The strict
coverage and delivery contracts added for the next lifecycle slice, plus
``MarketEventEnvelopeV3`` and ``NormalizationContext``, remain dormant.  All
contracts here perform only pure, locally checkable validation over explicitly
supplied values.
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
    CoverageEvidence,
    CoverageEvidenceId,
    CoverageEvidenceKind,
    CoverageFanoutKind,
    CoverageFanoutProof,
    CoverageMutationBatch,
    CoverageMutationBatchId,
    CoverageMutationNoOp,
    CoverageReason,
    CoverageScope,
    CoverageScopeId,
    CoverageStateReference,
    CoverageStatus,
    CoverageTransition,
    CoverageTransitionId,
    DeliveryAttemptId,
    DeliveryAttemptIdentity,
    DeliveryBatchId,
    EventActivationRequirement,
    EventCoverage,
    ExactIdentifiedRejectionTarget,
    FeedProductId,
    FeedProductIdentity,
    InstrumentMetadataObservationId,
    InstrumentSpecificationId,
    NormalizationFailureCategory,
    NormalizationFailureEvidenceSource,
    NormalizationOutcomeEvidenceSource,
    NormalizationRunId,
    PublicSourceSelector,
    PublicSourceSelectorKind,
    RawCoverageFanoutBinding,
    RawCoverageFanoutBindingId,
    RawMarketDataRecord,
    RawRecordId,
    RoutedCoverageTarget,
    SourceEventConflictEvidenceSource,
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
    require_code,
    require_collection_size,
    require_sha256,
    sha256_hex,
)
from hyperliquid_bot.data_provenance import (
    NormalizationOutcomeId as NormalizationOutcomeId,
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
_TYPED_NORMALIZATION_OUTCOME_CONTENT_VERSION: Final = "normalization-outcome-content-v2"
_NORMALIZATION_OUTCOME_SINK_FAILURE_COVERAGE_BINDING_VERSION: Final = (
    "normalization-outcome-sink-failure-coverage-binding-v1"
)
_FRAME_ATOMIC_ABORT_EVIDENCE_ID_VERSION: Final = "frame-atomic-abort-evidence-v1"
_FRAME_ATOMIC_ABORT_CAUSE_CONTENT_VERSION: Final = "frame-atomic-abort-primary-cause-content-v1"
_NORMALIZATION_SOURCE_CONFLICT_BINDING_ID_VERSION: Final = (
    "normalization-source-conflict-binding-v1"
)
_NORMALIZATION_COVERAGE_LINEAGE_ID_VERSION: Final = "normalization-coverage-lineage-v2"
_NORMALIZATION_COVERAGE_LINEAGE_CONTENT_VERSION: Final = "normalization-coverage-lineage-content-v2"
_DELIVERY_ITEM_COMMITMENT_ID_VERSION: Final = "delivery-item-commitment-v1"
_DELIVERY_ITEM_COMMITMENTS_CONTENT_VERSION: Final = "delivery-item-commitments-v1"
_DELIVERY_BATCH_CONTENT_VERSION: Final = "normalization-delivery-batch-content-v1"
_DELIVERY_BATCH_COMMITMENT_ID_VERSION: Final = "normalization-delivery-batch-commitment-v1"
_OUTCOME_DELIVERY_ATTEMPT_BINDING_ID_VERSION: Final = "normalization-delivery-attempt-binding-v1"
_DELIVERY_OUTCOME_ID_VERSION: Final = "normalization-delivery-outcome-v1"
_DELIVERY_COMMIT_ACCEPTANCE_ID_VERSION: Final = "delivery-commit-acceptance-v1"
_DELIVERY_COMMIT_FAILURE_ID_VERSION: Final = "delivery-commit-failure-v1"


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
class NormalizationOutcomeSinkFailureCoverageBindingId:
    """Bound one concrete outcome to one exact prepared sink-failure mutation.

    Exact preimage::

        ["normalization-outcome-sink-failure-coverage-binding-v1",
         normalization_outcome_id, coverage_mutation_batch_id,
         raw_coverage_fanout_binding_id, coverage_evidence_kind]

    High-cardinality scope and event membership remains content-addressed by
    the referenced outcome, mutation batch and raw fan-out binding.
    """

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="normalization_outcome_sink_failure_coverage_binding_id",
            version_tag=_NORMALIZATION_OUTCOME_SINK_FAILURE_COVERAGE_BINDING_VERSION,
        )
        if len(components) != 5 or any(type(components[index]) is not str for index in range(1, 5)):
            raise ValueError("outcome sink-failure coverage binding ID has invalid components.")
        assert type(components[1]) is str
        assert type(components[2]) is str
        assert type(components[3]) is str
        assert type(components[4]) is str
        NormalizationOutcomeId(components[1])
        CoverageMutationBatchId(components[2])
        RawCoverageFanoutBindingId(components[3])
        kind = CoverageEvidenceKind(components[4])
        if kind not in {
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        }:
            raise ValueError("outcome sink-failure binding has an unsupported evidence kind.")


@dataclass(frozen=True, slots=True)
class FrameAtomicAbortEvidenceId:
    """Content-addressed identity for one frame-aborted indexed candidate."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="frame_atomic_abort_evidence_id",
            version_tag=_FRAME_ATOMIC_ABORT_EVIDENCE_ID_VERSION,
        )
        if (
            len(components) != 8
            or type(components[1]) is not str
            or type(components[2]) is not str
            or type(components[3]) is not int
            or components[3] < 0
            or components[3] > MAX_UNSIGNED_64
            or type(components[4]) is not str
            or type(components[5]) is not str
            or type(components[6]) is not int
            or components[6] <= 0
            or components[6] > MAX_NORMALIZATION_OUTCOME_ITEMS
            or type(components[7]) is not str
        ):
            raise ValueError("frame_atomic_abort_evidence_id has invalid components.")
        RawRecordId(components[1])
        NormalizationRunId(components[2])
        SourceEventId(components[4])
        CoverageScopeId(components[5])
        require_sha256(components[7], field_name="primary_cause_content_sha256")


@dataclass(frozen=True, slots=True)
class NormalizationSourceConflictBindingId:
    """Upper-layer identity binding legacy conflict evidence to a normalization run."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="normalization_source_conflict_binding_id",
            version_tag=_NORMALIZATION_SOURCE_CONFLICT_BINDING_ID_VERSION,
        )
        if len(components) != 3 or type(components[1]) is not str or type(components[2]) is not str:
            raise ValueError("normalization_source_conflict_binding_id has invalid components.")
        evidence_id = CoverageEvidenceId(components[1])
        evidence_components = parse_canonical_json_array(
            evidence_id.value,
            field_name="coverage_evidence_id",
        )
        if evidence_components[4] != CoverageEvidenceKind.SOURCE_EVENT_CONFLICT.value:
            raise ValueError("normalization source conflict binding requires conflict evidence.")
        NormalizationRunId(components[2])


@dataclass(frozen=True, slots=True)
class NormalizationCoverageLineageId:
    """Content-addressed identity for complete typed frame coverage lineage."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="normalization_coverage_lineage_id",
            version_tag=_NORMALIZATION_COVERAGE_LINEAGE_ID_VERSION,
        )
        if len(components) != 9:
            raise ValueError("normalization_coverage_lineage_id has invalid components.")
        batch_id = components[1]
        (
            primary_count,
            abort_count,
            conflict_binding_count,
            transition_count,
            no_op_count,
            state_count,
        ) = components[2:8]
        content_sha256 = components[8]
        if (
            type(batch_id) is not str
            or type(primary_count) is not int
            or type(abort_count) is not int
            or type(transition_count) is not int
            or type(conflict_binding_count) is not int
            or type(no_op_count) is not int
            or type(state_count) is not int
            or primary_count < 0
            or abort_count < 0
            or transition_count < 0
            or conflict_binding_count < 0
            or no_op_count < 0
            or state_count <= 0
            or primary_count > MAX_NORMALIZATION_OUTCOME_ITEMS * 2
            or abort_count > MAX_NORMALIZATION_OUTCOME_ITEMS
            or transition_count > MAX_COVERAGE_TRANSITION_REFERENCES
            or conflict_binding_count > MAX_NORMALIZATION_OUTCOME_ITEMS
            or no_op_count > MAX_COVERAGE_TRANSITION_REFERENCES
            or state_count > MAX_COVERAGE_TRANSITION_REFERENCES
            or type(content_sha256) is not str
        ):
            raise ValueError("normalization_coverage_lineage_id has invalid components.")
        CoverageMutationBatchId(batch_id)
        require_sha256(
            content_sha256,
            field_name="normalization_coverage_lineage_content_sha256",
        )


class DeliveryKnowledgeStatus(StrEnum):
    """What is known about one bounded output-queue acceptance attempt."""

    ACCEPTED = "accepted"
    DEFINITELY_NOT_ACCEPTED = "definitely-not-accepted"
    ACCEPTANCE_UNCERTAIN = "acceptance-uncertain"


class DeliveryOutcomeReason(StrEnum):
    """Closed sanitized reason independent of delivery knowledge status."""

    OUTPUT_QUEUE_ACCEPTANCE = "output-queue-acceptance"
    QUEUE_CAPACITY_TIMEOUT = "queue-capacity-timeout"
    CANCELLATION_BEFORE_LINEARIZATION = "cancellation-before-linearization"
    EXPLICIT_REJECTION = "explicit-rejection"
    LOCAL_CONTRACT_FAILURE = "local-contract-failure"
    AMBIGUOUS_COMPLETION = "ambiguous-completion"


@dataclass(frozen=True, slots=True)
class DeliveryDestinationId:
    """Caller-supplied bounded public identity for one delivery destination."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            require_code(self.value, field_name="delivery_destination_id"),
        )


@dataclass(frozen=True, slots=True)
class DeliveryItemCommitmentId:
    """Canonical commitment to one materialization and its serialized event digest."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="delivery_item_commitment_id",
            version_tag=_DELIVERY_ITEM_COMMITMENT_ID_VERSION,
        )
        if len(components) != 3 or type(components[1]) is not str or type(components[2]) is not str:
            raise ValueError("delivery_item_commitment_id has invalid components.")
        MaterializationKeyId(components[1])
        require_sha256(components[2], field_name="event_content_sha256")


@dataclass(frozen=True, slots=True)
class DeliveryBatchCommitmentId:
    """Canonical proof-neutral commitment to one outcome's complete event batch."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="delivery_batch_commitment_id",
            version_tag=_DELIVERY_BATCH_COMMITMENT_ID_VERSION,
        )
        if (
            len(components) != 5
            or type(components[1]) is not str
            or type(components[2]) is not str
            or type(components[3]) is not int
            or components[3] <= 0
            or components[3] > MAX_NORMALIZATION_OUTCOME_ITEMS
            or type(components[4]) is not str
        ):
            raise ValueError("delivery_batch_commitment_id has invalid components.")
        legacy_batch_id = DeliveryBatchId(components[1])
        legacy_components = parse_canonical_json_array(
            legacy_batch_id.value,
            field_name="legacy_delivery_batch_id",
        )
        if legacy_components[1] != components[3]:
            raise ValueError("delivery batch commitment and legacy locator counts disagree.")
        outcome_id = NormalizationOutcomeId(components[2])
        outcome_components = parse_canonical_json_array(
            outcome_id.value,
            field_name="normalization_outcome_id",
        )
        if outcome_components[5] not in {
            FrameNormalizationStatus.MATERIALIZED.value,
            FrameNormalizationStatus.MIXED_SUCCESS.value,
        }:
            raise ValueError("delivery batch requires a successful materializing outcome.")
        require_sha256(components[4], field_name="delivery_batch_content_sha256")


@dataclass(frozen=True, slots=True)
class OutcomeDeliveryAttemptBindingId:
    """Canonical binding of a legacy attempt locator to one batch commitment."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="outcome_delivery_attempt_binding_id",
            version_tag=_OUTCOME_DELIVERY_ATTEMPT_BINDING_ID_VERSION,
        )
        if len(components) != 3 or type(components[1]) is not str or type(components[2]) is not str:
            raise ValueError("outcome_delivery_attempt_binding_id has invalid components.")
        legacy_attempt_id = DeliveryAttemptId(components[1])
        batch_commitment_id = DeliveryBatchCommitmentId(components[2])
        attempt_components = parse_canonical_json_array(
            legacy_attempt_id.value,
            field_name="legacy_delivery_attempt_id",
        )
        batch_components = parse_canonical_json_array(
            batch_commitment_id.value,
            field_name="delivery_batch_commitment_id",
        )
        if attempt_components[2] != batch_components[1]:
            raise ValueError("delivery attempt and batch commitment legacy locators disagree.")
        if attempt_components[3] != 0:
            raise ValueError("the current schema-v2 output queue permits only attempt ordinal 0.")


@dataclass(frozen=True, slots=True)
class DeliveryOutcomeId:
    """Canonical identity for bounded delivery knowledge and sanitized reason."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="delivery_outcome_id",
            version_tag=_DELIVERY_OUTCOME_ID_VERSION,
        )
        if (
            len(components) != 6
            or type(components[1]) is not str
            or type(components[2]) is not str
            or type(components[3]) is not str
            or type(components[4]) is not str
            or type(components[5]) is not int
            or components[5] < 0
            or components[5] > MAX_UNSIGNED_64
        ):
            raise ValueError("delivery_outcome_id has invalid components.")
        OutcomeDeliveryAttemptBindingId(components[1])
        if components[2] not in {item.value for item in DeliveryKnowledgeStatus}:
            raise ValueError("delivery outcome has an unsupported knowledge status.")
        if components[3] not in {item.value for item in DeliveryOutcomeReason}:
            raise ValueError("delivery outcome has an unsupported reason.")
        _validate_delivery_status_reason(
            DeliveryKnowledgeStatus(components[2]),
            DeliveryOutcomeReason(components[3]),
        )
        parsed: datetime | None = None
        try:
            parsed = datetime.fromisoformat(components[4].replace("Z", "+00:00"))
        except ValueError:
            pass
        if parsed is None:
            raise ValueError("delivery outcome observed_at is not canonical UTC.")
        if canonical_utc_datetime(parsed, field_name="observed_at") != components[4]:
            raise ValueError("delivery outcome observed_at is not canonical UTC.")


@dataclass(frozen=True, slots=True)
class DeliveryCommitAcceptanceId:
    """Canonical identity for the only positive delivery-commit proof."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="delivery_commit_acceptance_id",
            version_tag=_DELIVERY_COMMIT_ACCEPTANCE_ID_VERSION,
        )
        if len(components) != 3 or type(components[1]) is not str or type(components[2]) is not str:
            raise ValueError("delivery_commit_acceptance_id has invalid components.")
        outcome_id = DeliveryOutcomeId(components[1])
        batch_id = DeliveryBatchCommitmentId(components[2])
        outcome_components = parse_canonical_json_array(
            outcome_id.value,
            field_name="delivery_outcome_id",
        )
        if outcome_components[2] != DeliveryKnowledgeStatus.ACCEPTED.value:
            raise ValueError("delivery commit acceptance requires accepted delivery knowledge.")
        attempt_components = parse_canonical_json_array(
            outcome_components[1],
            field_name="outcome_delivery_attempt_binding_id",
        )
        if attempt_components[2] != batch_id.value:
            raise ValueError("delivery commit acceptance outcome and batch disagree.")


@dataclass(frozen=True, slots=True)
class DeliveryCommitFailureId:
    """Canonical non-acceptance or uncertainty result without event-batch content."""

    value: str

    def __post_init__(self) -> None:
        components = _parse_canonical_identifier(
            self.value,
            field_name="delivery_commit_failure_id",
            version_tag=_DELIVERY_COMMIT_FAILURE_ID_VERSION,
        )
        if len(components) != 2 or type(components[1]) is not str:
            raise ValueError("delivery_commit_failure_id has invalid components.")
        outcome_id = DeliveryOutcomeId(components[1])
        outcome_components = parse_canonical_json_array(
            outcome_id.value,
            field_name="delivery_outcome_id",
        )
        if outcome_components[2] == DeliveryKnowledgeStatus.ACCEPTED.value:
            raise ValueError("accepted delivery outcome cannot be represented as failure.")


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
    coverage_scope: CoverageScope = field(repr=False)

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
        object.__setattr__(instance, "coverage_scope", coverage_scope)
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
        for committed_coverage_state in (
            event_coverage.bronze_ingress,
            event_coverage.silver_normalization,
        ):
            coverage_reference = committed_coverage_state.state_reference.reference
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
    NormalizationFailureCategory.PROVENANCE_MISMATCH: (NormalizationEvidence.PROVENANCE_MISMATCH),
    NormalizationFailureCategory.LOCAL_CONTRACT_FAILURE: (
        NormalizationEvidence.LOCAL_CONTRACT_FAILURE
    ),
}


@dataclass(frozen=True, slots=True)
class FrameAtomicAbortEvidenceSource:
    """Outcome-only evidence for one known candidate aborted by frame atomicity.

    This value deliberately never creates a coverage transition. Its complete
    primary-cause content is retained and content-addressed so the enclosing
    typed lineage can prove which normalization failures or source conflicts
    caused this otherwise valid candidate not to materialize.

    ID preimage, in exact order::

        ["frame-atomic-abort-evidence-v1", raw_record_id,
         normalization_run_id, raw_event_index, source_event_id,
         identified_coverage_scope_id, primary_cause_count,
         primary_cause_content_sha256]

    Cause-content preimage::

        ["frame-atomic-abort-primary-cause-content-v1",
         sorted_primary_coverage_evidence_ids]
    """

    raw_record_id: RawRecordId = field(repr=False)
    normalization_run_id: NormalizationRunId
    raw_event_index: int
    source_event_id: SourceEventId = field(repr=False)
    identified_coverage_scope_id: CoverageScopeId
    primary_cause_coverage_evidence_ids: tuple[CoverageEvidenceId, ...] = field(repr=False)
    primary_cause_content_sha256: str = field(init=False)
    frame_atomic_abort_evidence_id: FrameAtomicAbortEvidenceId = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        if type(self.normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        index = _require_non_negative_int(self.raw_event_index, field_name="raw_event_index")
        if type(self.source_event_id) is not SourceEventId:
            raise TypeError("source_event_id must be a SourceEventId.")
        if type(self.identified_coverage_scope_id) is not CoverageScopeId:
            raise TypeError("identified_coverage_scope_id must be a CoverageScopeId.")
        causes = _require_exact_tuple(
            self.primary_cause_coverage_evidence_ids,
            item_type=CoverageEvidenceId,
            field_name="primary_cause_coverage_evidence_ids",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        if not causes:
            raise ValueError("frame atomic abort evidence requires at least one primary cause.")
        if tuple(sorted(causes, key=lambda item: item.value)) != causes:
            raise ValueError("primary cause evidence IDs must be sorted by canonical ID.")
        if len(set(causes)) != len(causes):
            raise ValueError("primary cause evidence IDs must be unique.")
        for cause in causes:
            components = parse_canonical_json_array(
                cause.value,
                field_name="primary_cause_coverage_evidence_id",
            )
            kind_component = components[4]
            if type(kind_component) is not str:
                raise ValueError("primary cause evidence has invalid kind.")
            kind = CoverageEvidenceKind(kind_component)
            if kind not in {
                CoverageEvidenceKind.NORMALIZATION_FAILURE,
                CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
            }:
                raise ValueError(
                    "frame atomic abort causes must be normalization failures or source conflicts."
                )
            source_components = components[5]
            if type(source_components) is not tuple:
                raise ValueError("primary cause evidence has invalid source components.")
            if kind is CoverageEvidenceKind.NORMALIZATION_FAILURE:
                failure_components = parse_canonical_json_array(
                    source_components[1],
                    field_name="normalization_failure_evidence_id",
                )
                if (
                    failure_components[1] != self.raw_record_id.value
                    or failure_components[2] != self.normalization_run_id.value
                    or failure_components[3] is None
                    or failure_components[3] == index
                ):
                    raise ValueError(
                        "frame atomic abort cause must match the frame and name another indexed "
                        "item."
                    )
            else:
                if (
                    source_components[3] != self.raw_record_id.value
                    or source_components[4] == index
                ):
                    raise ValueError(
                        "frame atomic abort cause must match the frame and name another index."
                    )

        cause_content = canonical_json_array(
            (
                _FRAME_ATOMIC_ABORT_CAUSE_CONTENT_VERSION,
                tuple(cause.value for cause in causes),
            )
        )
        cause_sha256 = sha256_hex(
            cause_content.encode("utf-8"),
            field_name="frame_atomic_abort_primary_cause_content",
        )
        identifier = FrameAtomicAbortEvidenceId(
            canonical_json_array(
                (
                    _FRAME_ATOMIC_ABORT_EVIDENCE_ID_VERSION,
                    self.raw_record_id.value,
                    self.normalization_run_id.value,
                    index,
                    self.source_event_id.value,
                    self.identified_coverage_scope_id.value,
                    len(causes),
                    cause_sha256,
                )
            )
        )
        object.__setattr__(self, "primary_cause_content_sha256", cause_sha256)
        object.__setattr__(self, "frame_atomic_abort_evidence_id", identifier)

    @classmethod
    def from_stored(
        cls,
        *,
        raw_record_id: RawRecordId,
        normalization_run_id: NormalizationRunId,
        raw_event_index: int,
        source_event_id: SourceEventId,
        identified_coverage_scope_id: CoverageScopeId,
        primary_cause_coverage_evidence_ids: tuple[CoverageEvidenceId, ...],
        expected_primary_cause_content_sha256: str,
        expected_evidence_id: FrameAtomicAbortEvidenceId,
    ) -> Self:
        """Recompute stored abort evidence and reject any digest or ID mismatch."""

        require_sha256(
            expected_primary_cause_content_sha256,
            field_name="expected_primary_cause_content_sha256",
        )
        if type(expected_evidence_id) is not FrameAtomicAbortEvidenceId:
            raise TypeError("expected_evidence_id must be a FrameAtomicAbortEvidenceId.")
        value = cls(
            raw_record_id,
            normalization_run_id,
            raw_event_index,
            source_event_id,
            identified_coverage_scope_id,
            primary_cause_coverage_evidence_ids,
        )
        if (
            value.primary_cause_content_sha256 != expected_primary_cause_content_sha256
            or value.frame_atomic_abort_evidence_id != expected_evidence_id
        ):
            raise ValueError("stored frame atomic abort evidence does not match its content.")
        return value


@dataclass(frozen=True, slots=True)
class NormalizationSourceConflictBinding:
    """Bind one legacy v1 conflict evidence record to its exact normalization run.

    ID preimage::

        ["normalization-source-conflict-binding-v1",
         coverage_evidence_id, normalization_run_id]
    """

    coverage_evidence: CoverageEvidence = field(repr=False)
    normalization_run_id: NormalizationRunId
    normalization_source_conflict_binding_id: NormalizationSourceConflictBindingId = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.coverage_evidence) is not CoverageEvidence:
            raise TypeError("coverage_evidence must be a CoverageEvidence.")
        if (
            self.coverage_evidence.kind is not CoverageEvidenceKind.SOURCE_EVENT_CONFLICT
            or type(self.coverage_evidence.source) is not SourceEventConflictEvidenceSource
        ):
            raise ValueError("normalization source conflict binding requires conflict evidence.")
        if type(self.normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        object.__setattr__(
            self,
            "normalization_source_conflict_binding_id",
            NormalizationSourceConflictBindingId(
                canonical_json_array(
                    (
                        _NORMALIZATION_SOURCE_CONFLICT_BINDING_ID_VERSION,
                        self.coverage_evidence.coverage_evidence_id.value,
                        self.normalization_run_id.value,
                    )
                )
            ),
        )

    @classmethod
    def from_stored(
        cls,
        *,
        coverage_evidence: CoverageEvidence,
        normalization_run_id: NormalizationRunId,
        expected_binding_id: NormalizationSourceConflictBindingId,
    ) -> Self:
        if type(expected_binding_id) is not NormalizationSourceConflictBindingId:
            raise TypeError("expected_binding_id must be a NormalizationSourceConflictBindingId.")
        value = cls(coverage_evidence, normalization_run_id)
        if value.normalization_source_conflict_binding_id != expected_binding_id:
            raise ValueError("stored normalization source conflict binding does not match.")
        return value


@dataclass(frozen=True, slots=True)
class NormalizationCoverageLineage:
    """One prepared event-scoped mutation batch plus outcome-only abort evidence.

    A prepared batch isn't a commit acceptance. Its transitions, no-ops, result
    state references, and primary evidence are derived here and cannot be
    independently supplied. Frame-atomic-abort evidence remains outcome-only.

    Content preimage, in exact order::

        ["normalization-coverage-lineage-content-v2",
         coverage_mutation_batch_id,
         raw_coverage_fanout_binding_id,
         sorted_primary_coverage_evidence_ids,
         sorted_frame_atomic_abort_evidence_ids,
         sorted_normalization_source_conflict_binding_ids,
         sorted_coverage_transition_ids,
         sorted_coverage_no_op_rows,
         sorted_resulting_state_reference_ids]

    ID preimage::

        ["normalization-coverage-lineage-v2", coverage_mutation_batch_id,
         primary_evidence_count, frame_abort_evidence_count,
         source_conflict_binding_count, transition_count, no_op_count,
         resulting_state_count, content_sha256]
    """

    coverage_mutation_batch: CoverageMutationBatch = field(repr=False)
    frame_atomic_abort_evidence: tuple[FrameAtomicAbortEvidenceSource, ...] = field(repr=False)
    raw_fanout_binding: RawCoverageFanoutBinding = field(repr=False)
    additional_primary_evidence: tuple[CoverageEvidence, ...] = field(
        default=(),
        repr=False,
    )
    source_conflict_bindings: tuple[NormalizationSourceConflictBinding, ...] = field(
        default=(),
        repr=False,
    )
    primary_evidence: tuple[CoverageEvidence, ...] = field(init=False, repr=False)
    coverage_transitions: tuple[CoverageTransition, ...] = field(init=False, repr=False)
    coverage_no_ops: tuple[CoverageMutationNoOp, ...] = field(init=False, repr=False)
    resulting_state_references: tuple[CoverageStateReference, ...] = field(
        init=False,
        repr=False,
    )
    canonical_content: str = field(init=False, repr=False)
    content_sha256: str = field(init=False)
    normalization_coverage_lineage_id: NormalizationCoverageLineageId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.coverage_mutation_batch) is not CoverageMutationBatch:
            raise TypeError("coverage_mutation_batch must be a prepared CoverageMutationBatch.")
        batch = self.coverage_mutation_batch
        if type(self.raw_fanout_binding) is not RawCoverageFanoutBinding:
            raise TypeError("raw_fanout_binding must be a RawCoverageFanoutBinding.")
        if batch.raw_fanout_binding != self.raw_fanout_binding:
            raise ValueError("normalization lineage requires the batch's exact raw fanout binding.")
        if (
            self.raw_fanout_binding.coverage_fanout_proof_id
            != batch.fanout_proof.coverage_fanout_proof_id
            or self.raw_fanout_binding.subscription_plan_id
            != batch.fanout_proof.subscription_plan_id
            or self.raw_fanout_binding.connection_session_id
            != batch.fanout_proof.connection_session_id
        ):
            raise ValueError("raw fanout binding must match the prepared coverage batch.")
        if batch.fanout_proof.kind not in {
            CoverageFanoutKind.EXACT_ROUTED_EVENT,
            CoverageFanoutKind.EXACT_ROUTED_EVENTS,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
            CoverageFanoutKind.ALL_POSSIBLY_ACTIVE,
        }:
            raise ValueError("frame outcomes reject lifecycle-only coverage fanout.")
        if any(
            scope.domain is not CoverageDomain.SILVER_NORMALIZATION
            for scope in batch.fanout_proof.target_scopes
        ):
            raise ValueError("frame outcome coverage targets must all be Silver normalization.")
        if (
            batch.fanout_proof.kind
            in {
                CoverageFanoutKind.EXACT_ROUTED_EVENT,
                CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
            }
            and len(batch.fanout_proof.target_scopes) != 1
        ):
            raise ValueError("a singular exact frame mutation must have one target.")
        aborts = _require_exact_tuple(
            self.frame_atomic_abort_evidence,
            item_type=FrameAtomicAbortEvidenceSource,
            field_name="frame_atomic_abort_evidence",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        additional = _require_exact_tuple(
            self.additional_primary_evidence,
            item_type=CoverageEvidence,
            field_name="additional_primary_evidence",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        if (
            tuple(sorted(additional, key=lambda item: item.coverage_evidence_id.value))
            != additional
        ):
            raise ValueError("additional primary evidence must be sorted by canonical ID.")
        conflict_bindings = _require_exact_tuple(
            self.source_conflict_bindings,
            item_type=NormalizationSourceConflictBinding,
            field_name="source_conflict_bindings",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        if (
            tuple(
                sorted(
                    conflict_bindings,
                    key=lambda item: item.normalization_source_conflict_binding_id.value,
                )
            )
            != conflict_bindings
        ):
            raise ValueError("source conflict bindings must be sorted by canonical ID.")
        selected_primary = tuple(
            sorted(
                (
                    *(item.initial_evidence for item in batch.initializations),
                    *(item.evidence for item in batch.transitions),
                    *(item.request.evidence for item in batch.no_ops),
                ),
                key=lambda item: item.coverage_evidence_id.value,
            )
        )
        primary = tuple(
            sorted(
                (*selected_primary, *additional),
                key=lambda item: item.coverage_evidence_id.value,
            )
        )
        transitions = batch.transitions
        no_ops = batch.no_ops
        resulting_states = batch.resulting_state_references
        target_count = len(batch.fanout_proof.target_scopes)
        if len(selected_primary) != target_count or len(resulting_states) != target_count:
            raise ValueError("prepared lineage requires one selected cause and result per target.")
        if (
            tuple(
                sorted(
                    aborts,
                    key=lambda item: item.frame_atomic_abort_evidence_id.value,
                )
            )
            != aborts
        ):
            raise ValueError("frame atomic abort evidence must be sorted by canonical ID.")
        primary_ids = tuple(item.coverage_evidence_id for item in primary)
        abort_ids = tuple(item.frame_atomic_abort_evidence_id for item in aborts)
        transition_ids = tuple(item.coverage_transition_id for item in transitions)
        no_op_rows = tuple(item.canonical_row for item in no_ops)
        conflict_binding_ids = tuple(
            item.normalization_source_conflict_binding_id for item in conflict_bindings
        )
        state_ids = tuple(item.coverage_state_reference_id for item in resulting_states)
        if len(set(abort_ids)) != len(abort_ids):
            raise ValueError("frame atomic abort evidence must be unique.")
        if len(set(primary_ids)) != len(primary_ids):
            raise ValueError("primary evidence must be unique.")
        if len(set(conflict_binding_ids)) != len(conflict_binding_ids):
            raise ValueError("source conflict bindings must be unique.")
        target_scope_ids = {scope.coverage_scope_id for scope in batch.fanout_proof.target_scopes}
        selected_by_scope = {evidence.coverage_scope_id: evidence for evidence in selected_primary}
        for evidence in primary:
            if evidence.kind not in {
                CoverageEvidenceKind.NORMALIZATION_FAILURE,
                CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
            }:
                raise ValueError(
                    "typed normalization lineage accepts only primary failure or conflict evidence."
                )
            if evidence.coverage_scope_id not in target_scope_ids:
                raise ValueError("primary evidence must belong to a prepared target scope.")
            if evidence.epoch != selected_by_scope[evidence.coverage_scope_id].epoch:
                raise ValueError("primary evidence must use its selected target epoch and run.")
        for selected in selected_primary:
            matching = tuple(
                evidence
                for evidence in primary
                if evidence.coverage_scope_id == selected.coverage_scope_id
            )
            if not matching or selected.coverage_evidence_id != min(
                (item.coverage_evidence_id for item in matching),
                key=lambda item: item.value,
            ):
                raise ValueError(
                    "prepared mutation must select the canonical-lowest cause per target."
                )
        expected_conflicts = tuple(
            evidence
            for evidence in primary
            if evidence.kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT
        )
        if tuple(item.coverage_evidence for item in conflict_bindings) != expected_conflicts:
            raise ValueError("every conflict cause requires one exact normalization-run binding.")
        expected_cause_ids = primary_ids
        for abort in aborts:
            if abort.primary_cause_coverage_evidence_ids != expected_cause_ids:
                raise ValueError(
                    "every frame atomic abort must bind the complete primary cause set."
                )

        content = canonical_json_array(
            (
                _NORMALIZATION_COVERAGE_LINEAGE_CONTENT_VERSION,
                batch.coverage_mutation_batch_id.value,
                self.raw_fanout_binding.raw_coverage_fanout_binding_id.value,
                tuple(item.value for item in primary_ids),
                tuple(item.value for item in abort_ids),
                tuple(item.value for item in conflict_binding_ids),
                tuple(item.value for item in transition_ids),
                no_op_rows,
                tuple(item.value for item in state_ids),
            )
        )
        content_sha256 = sha256_hex(
            content.encode("utf-8"),
            field_name="normalization_coverage_lineage_content",
        )
        identifier = NormalizationCoverageLineageId(
            canonical_json_array(
                (
                    _NORMALIZATION_COVERAGE_LINEAGE_ID_VERSION,
                    batch.coverage_mutation_batch_id.value,
                    len(primary),
                    len(aborts),
                    len(conflict_bindings),
                    len(transitions),
                    len(no_ops),
                    len(resulting_states),
                    content_sha256,
                )
            )
        )
        object.__setattr__(self, "primary_evidence", primary)
        object.__setattr__(self, "coverage_transitions", transitions)
        object.__setattr__(self, "coverage_no_ops", no_ops)
        object.__setattr__(self, "resulting_state_references", resulting_states)
        object.__setattr__(self, "canonical_content", content)
        object.__setattr__(self, "content_sha256", content_sha256)
        object.__setattr__(self, "normalization_coverage_lineage_id", identifier)

    @classmethod
    def from_stored(
        cls,
        *,
        coverage_mutation_batch: CoverageMutationBatch,
        frame_atomic_abort_evidence: tuple[FrameAtomicAbortEvidenceSource, ...],
        raw_fanout_binding: RawCoverageFanoutBinding,
        additional_primary_evidence: tuple[CoverageEvidence, ...] = (),
        source_conflict_bindings: tuple[NormalizationSourceConflictBinding, ...] = (),
        expected_canonical_content: str,
        expected_lineage_id: NormalizationCoverageLineageId,
    ) -> Self:
        """Recompute complete prepared lineage and reject stored-content mismatch."""

        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        if type(expected_lineage_id) is not NormalizationCoverageLineageId:
            raise TypeError("expected_lineage_id must be a NormalizationCoverageLineageId.")
        value = cls(
            coverage_mutation_batch,
            frame_atomic_abort_evidence,
            raw_fanout_binding,
            additional_primary_evidence,
            source_conflict_bindings,
        )
        if (
            value.canonical_content != expected_canonical_content
            or value.normalization_coverage_lineage_id != expected_lineage_id
        ):
            raise ValueError("stored normalization coverage lineage does not match its content.")
        return value


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


def _binding_matches_identified_rejection_target(
    binding: RawEventNormalizationScopeBinding,
    target: ExactIdentifiedRejectionTarget,
    scope_id: CoverageScopeId,
) -> bool:
    snapshot = target.attempt_snapshot
    return (
        binding.coverage_scope_id == scope_id
        and binding.subscription_spec_id == snapshot.subscription_spec.subscription_spec_id
        and binding.subscription_attempt_id == snapshot.subscription_attempt.subscription_attempt_id
        and binding.attempt_status is snapshot.attempt_status
        and binding.source_selector == target.source_selector
        and binding.canonical_instrument_id == target.canonical_instrument_id
    )


def _binding_matches_acknowledged_route_target(
    binding: RawEventNormalizationScopeBinding,
    target: "RoutedCoverageTarget",
    scope_id: CoverageScopeId,
) -> bool:
    snapshot = target.acknowledged_snapshot
    return (
        binding.coverage_scope_id == scope_id
        and binding.subscription_spec_id == snapshot.subscription_spec.subscription_spec_id
        and binding.subscription_attempt_id == snapshot.subscription_attempt.subscription_attempt_id
        and binding.attempt_status is SubscriptionAttemptStatus.ACKNOWLEDGED
        and binding.canonical_instrument_id == target.canonical_instrument_id
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

    A legacy transition-empty outcome retains the exact 3B1B digest preimage::

        [
          "normalization-outcome-content-v1",
          ordered-index-outcome-ids,
          ordered-committed-materialization-key-ids,
          sorted-evidence-codes,
          sorted-coverage-transition-ids,
          pre-index-frame-scope-binding-or-null
        ]

    An outcome with complete typed coverage lineage instead uses::

        [
          "normalization-outcome-content-v2",
          ordered-index-outcome-ids,
          ordered-committed-materialization-key-ids,
          sorted-evidence-codes,
          normalization-coverage-lineage-id,
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
    coverage_lineage: NormalizationCoverageLineage | None = None
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
        if self.coverage_lineage is not None and (
            type(self.coverage_lineage) is not NormalizationCoverageLineage
        ):
            raise TypeError("coverage_lineage must be a NormalizationCoverageLineage or None.")
        if self.coverage_lineage is None:
            if transition_ids:
                raise ValueError(
                    "legacy normalization outcomes must be transition-empty; "
                    "typed transitions require coverage_lineage."
                )
        else:
            if self.coverage_lineage.raw_fanout_binding.raw_record_id != self.raw_record_id:
                raise ValueError("typed coverage lineage must bind the outcome raw record.")
            typed_transition_ids = tuple(
                transition.coverage_transition_id
                for transition in self.coverage_lineage.coverage_transitions
            )
            if transition_ids:
                raise ValueError("typed transition IDs are derived and cannot be supplied.")
            transition_ids = typed_transition_ids
            object.__setattr__(self, "coverage_transition_ids", transition_ids)

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
                    assert self.coverage_lineage is not None
                    selected_scope_ids = {
                        scope.coverage_scope_id.value
                        for scope in (
                            self.coverage_lineage.coverage_mutation_batch.fanout_proof.target_scopes
                        )
                    }
                    if (
                        self.preindex_scope_binding is None
                        or self.preindex_scope_binding.raw_record_id != self.raw_record_id
                        or transition_components[1] not in selected_scope_ids
                    ):
                        raise ValueError(
                            "pre-index normalization failure requires a selected raw frame scope."
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

        if self.coverage_lineage is not None:
            self._validate_typed_coverage_lineage(outcomes)

        common_content = (
            tuple(outcome.raw_event_normalization_outcome_id.value for outcome in outcomes),
            tuple(key.materialization_key_id.value for key in materializations),
            tuple(item.value for item in evidence),
        )
        scope_binding_content = (
            self.preindex_scope_binding.canonical_components()
            if self.preindex_scope_binding is not None
            else None
        )
        if self.coverage_lineage is None:
            content_canonical = canonical_json_array(
                (
                    _NORMALIZATION_OUTCOME_CONTENT_VERSION,
                    *common_content,
                    (),
                    scope_binding_content,
                )
            )
        else:
            content_canonical = canonical_json_array(
                (
                    _TYPED_NORMALIZATION_OUTCOME_CONTENT_VERSION,
                    *common_content,
                    self.coverage_lineage.normalization_coverage_lineage_id.value,
                    scope_binding_content,
                )
            )
        content_sha256 = sha256_hex(
            content_canonical.encode("utf-8"),
            field_name="normalization_outcome_content",
        )
        canonical = canonical_json_array(
            (
                NormalizationOutcomeId.VERSION_TAG,
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

    @classmethod
    def legacy_transition_empty(
        cls,
        *,
        normalization_run_id: NormalizationRunId,
        raw_record_id: RawRecordId,
        normalizer_version: str,
        normalizer_commit: str,
        frame_status: FrameNormalizationStatus,
        decoded_event_count: int | None,
        raw_event_outcomes: tuple[RawEventNormalizationOutcome, ...],
        committed_materialization_keys: tuple[MaterializationKey, ...],
        preindex_scope_binding: RawFrameNormalizationScopeBinding | None = None,
        evidence: tuple[NormalizationEvidence, ...] = (),
    ) -> Self:
        """Construct the exact transition-empty 3B1B identity for compatibility."""

        return cls(
            normalization_run_id=normalization_run_id,
            raw_record_id=raw_record_id,
            normalizer_version=normalizer_version,
            normalizer_commit=normalizer_commit,
            frame_status=frame_status,
            decoded_event_count=decoded_event_count,
            raw_event_outcomes=raw_event_outcomes,
            committed_materialization_keys=committed_materialization_keys,
            preindex_scope_binding=preindex_scope_binding,
            evidence=evidence,
            coverage_transition_ids=(),
            coverage_lineage=None,
        )

    @classmethod
    def from_stored_typed(
        cls,
        *,
        normalization_run_id: NormalizationRunId,
        raw_record_id: RawRecordId,
        normalizer_version: str,
        normalizer_commit: str,
        frame_status: FrameNormalizationStatus,
        decoded_event_count: int | None,
        raw_event_outcomes: tuple[RawEventNormalizationOutcome, ...],
        committed_materialization_keys: tuple[MaterializationKey, ...],
        coverage_lineage: NormalizationCoverageLineage,
        preindex_scope_binding: RawFrameNormalizationScopeBinding | None = None,
        evidence: tuple[NormalizationEvidence, ...] = (),
        expected_content_sha256: str,
        expected_outcome_id: NormalizationOutcomeId,
    ) -> Self:
        """Recompute one typed outcome without accepting caller-supplied derived IDs."""

        if type(coverage_lineage) is not NormalizationCoverageLineage:
            raise TypeError("coverage_lineage must be a NormalizationCoverageLineage.")
        require_sha256(expected_content_sha256, field_name="expected_content_sha256")
        if type(expected_outcome_id) is not NormalizationOutcomeId:
            raise TypeError("expected_outcome_id must be a NormalizationOutcomeId.")
        value = cls(
            normalization_run_id=normalization_run_id,
            raw_record_id=raw_record_id,
            normalizer_version=normalizer_version,
            normalizer_commit=normalizer_commit,
            frame_status=frame_status,
            decoded_event_count=decoded_event_count,
            raw_event_outcomes=raw_event_outcomes,
            committed_materialization_keys=committed_materialization_keys,
            preindex_scope_binding=preindex_scope_binding,
            evidence=evidence,
            coverage_transition_ids=(),
            coverage_lineage=coverage_lineage,
        )
        if (
            value.normalization_outcome_content_sha256 != expected_content_sha256
            or value.normalization_outcome_id != expected_outcome_id
        ):
            raise ValueError("stored typed normalization outcome doesn't match its content.")
        return value

    def _validate_typed_coverage_lineage(
        self,
        outcomes: tuple[RawEventNormalizationOutcome, ...],
    ) -> None:
        """Bind every typed evidence record to exactly one frame disposition."""

        lineage = self.coverage_lineage
        assert lineage is not None
        primary = lineage.primary_evidence
        aborts = lineage.frame_atomic_abort_evidence
        conflict_bindings = lineage.source_conflict_bindings
        expected_raw_integrity = lineage.raw_fanout_binding.full_record_integrity_sha256
        if self.preindex_scope_binding is not None:
            if self.preindex_scope_binding.full_record_integrity_sha256 != expected_raw_integrity:
                raise ValueError(
                    "typed pre-index lineage must bind the exact raw full-record content."
                )
        elif any(
            outcome.normalization_scope_binding.full_record_integrity_sha256
            != expected_raw_integrity
            for outcome in outcomes
        ):
            raise ValueError("typed indexed lineage must bind the exact raw full-record content.")
        if any(
            binding.normalization_run_id != self.normalization_run_id
            for binding in conflict_bindings
        ):
            raise ValueError("source conflict binding must match the outcome normalization run.")

        if self.frame_status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING:
            if self.preindex_scope_binding is None or aborts:
                raise ValueError(
                    "typed pre-index coverage lineage requires its frame scope and no aborts."
                )
            if (
                lineage.coverage_mutation_batch.fanout_proof.kind
                is not CoverageFanoutKind.ALL_POSSIBLY_ACTIVE
            ):
                raise ValueError("typed pre-index lineage requires complete Silver fanout.")
            aggregate = self.preindex_scope_binding.coverage_scope
            targets = lineage.coverage_mutation_batch.fanout_proof.target_scopes
            selected_spec_ids = {
                spec_id for target in targets for spec_id in target.subscription_spec_ids
            }
            selected_instrument_ids = {
                instrument_id
                for target in targets
                for instrument_id in target.canonical_instrument_ids
            }
            if (
                lineage.coverage_mutation_batch.fanout_proof.subscription_plan_id
                != self.preindex_scope_binding.subscription_plan_id
                or not targets
                or any(
                    (
                        target.feed_product_id,
                        target.event_family,
                        target.event_family_schema_version,
                        target.payload_type,
                    )
                    != (
                        aggregate.feed_product_id,
                        aggregate.event_family,
                        aggregate.event_family_schema_version,
                        aggregate.payload_type,
                    )
                    for target in targets
                )
                or not selected_spec_ids.issubset(set(aggregate.subscription_spec_ids))
                or not selected_instrument_ids.issubset(set(aggregate.canonical_instrument_ids))
            ):
                raise ValueError(
                    "typed pre-index targets must be the non-empty possibly-active slice of the "
                    "aggregate frame scope."
                )
            matched_frame_evidence: list[NormalizationEvidence] = []
            for coverage_evidence in primary:
                source = coverage_evidence.source
                if (
                    type(source) is not NormalizationFailureEvidenceSource
                    or source.raw_record_id != self.raw_record_id
                    or source.normalization_run_id != self.normalization_run_id
                    or source.raw_event_index is not None
                    or source.source_event_id is not None
                    or source.identified_coverage_scope_id != coverage_evidence.coverage_scope_id
                ):
                    raise ValueError(
                        "typed pre-index evidence must bind the exact frame scope and no index."
                    )
                matched_frame_evidence.append(
                    _NORMALIZATION_FAILURE_TO_FRAME_EVIDENCE[source.category]
                )
            if tuple(sorted(set(matched_frame_evidence), key=lambda item: item.value)) != (
                self.evidence
            ):
                raise ValueError(
                    "typed pre-index evidence must exactly explain the frame evidence union."
                )
            return

        fanout = lineage.coverage_mutation_batch.fanout_proof
        if fanout.kind not in {
            CoverageFanoutKind.EXACT_ROUTED_EVENT,
            CoverageFanoutKind.EXACT_ROUTED_EVENTS,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
        }:
            raise ValueError("indexed typed lineage requires exact routed-event fanout.")

        identified_rejection_fanout = fanout.kind in {
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
        }
        if identified_rejection_fanout and self.frame_status is not (
            FrameNormalizationStatus.REJECTED_AFTER_INDEXING
        ):
            raise ValueError("identified rejection lineage requires an indexed rejected frame.")
        matched_rejection_scope_ids: set[CoverageScopeId] = set()
        matched_acknowledged_scope_ids: set[CoverageScopeId] = set()

        matched_primary_ids: list[CoverageEvidenceId] = []
        for outcome in outcomes:
            binding = outcome.normalization_scope_binding
            is_acknowledged = binding.attempt_status is SubscriptionAttemptStatus.ACKNOWLEDGED
            if identified_rejection_fanout and not is_acknowledged:
                route_matches = tuple(
                    scope_id
                    for target, scope_id in zip(
                        fanout.identified_rejection_targets,
                        fanout.identified_rejection_scope_ids,
                        strict=True,
                    )
                    if _binding_matches_identified_rejection_target(binding, target, scope_id)
                )
                if len(route_matches) != 1:
                    raise ValueError(
                        "every non-ACK rejected index must match one exact identified route."
                    )
                if (
                    outcome.disposition is not RawEventDisposition.REJECTED
                    or outcome.evidence is not NormalizationEvidence.PROVENANCE_MISMATCH
                ):
                    raise ValueError(
                        "non-ACK identified routes permit only rejected provenance mismatches."
                    )
                matched_rejection_scope_ids.add(route_matches[0])
            elif identified_rejection_fanout and (
                outcome.disposition is RawEventDisposition.REJECTED
            ):
                route_matches = tuple(
                    scope_id
                    for target, scope_id in zip(
                        fanout.acknowledged_routed_targets,
                        fanout.acknowledged_routed_scope_ids,
                        strict=True,
                    )
                    if _binding_matches_acknowledged_route_target(binding, target, scope_id)
                )
                if len(route_matches) != 1:
                    raise ValueError(
                        "every acknowledged primary rejection must match one exact routed target."
                    )
                matched_acknowledged_scope_ids.add(route_matches[0])
            if outcome.disposition is RawEventDisposition.REJECTED:
                matches = tuple(
                    evidence
                    for evidence in primary
                    if type(evidence.source) is NormalizationFailureEvidenceSource
                    and evidence.source.raw_record_id == self.raw_record_id
                    and evidence.source.normalization_run_id == self.normalization_run_id
                    and evidence.source.raw_event_index == outcome.observation_key.raw_event_index
                    and evidence.source.identified_coverage_scope_id
                    == outcome.normalization_scope_binding.coverage_scope_id
                    and (
                        evidence.source.source_event_id.value
                        if evidence.source.source_event_id is not None
                        else None
                    )
                    == (
                        outcome.logical_source_key.source_event_id.value
                        if outcome.logical_source_key is not None
                        else None
                    )
                    and _NORMALIZATION_FAILURE_TO_FRAME_EVIDENCE[evidence.source.category]
                    is outcome.evidence
                    and (
                        not identified_rejection_fanout
                        or is_acknowledged
                        or evidence.source.category
                        is NormalizationFailureCategory.PROVENANCE_MISMATCH
                    )
                )
                if len(matches) != 1:
                    raise ValueError(
                        "every rejected index requires one exact typed failure evidence record."
                    )
                matched_primary_ids.append(matches[0].coverage_evidence_id)
            elif outcome.disposition is RawEventDisposition.SOURCE_EVENT_CONFLICT:
                matches = tuple(
                    evidence
                    for evidence in primary
                    if type(evidence.source) is SourceEventConflictEvidenceSource
                    and evidence.source.raw_record_id == self.raw_record_id
                    and evidence.source.raw_event_index == outcome.observation_key.raw_event_index
                    and evidence.source.identified_coverage_scope_id
                    == outcome.normalization_scope_binding.coverage_scope_id
                    and outcome.logical_source_key is not None
                    and evidence.source.source_event_id
                    == outcome.logical_source_key.source_event_id
                )
                if len(matches) != 1:
                    raise ValueError(
                        "every source-conflict index requires one exact typed conflict evidence "
                        "record."
                    )
                if not any(
                    binding.coverage_evidence == matches[0]
                    and binding.normalization_run_id == self.normalization_run_id
                    for binding in conflict_bindings
                ):
                    raise ValueError(
                        "source-conflict index requires its exact normalization-run binding."
                    )
                matched_primary_ids.append(matches[0].coverage_evidence_id)
            elif outcome.disposition is RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED:
                abort_matches = tuple(
                    abort
                    for abort in aborts
                    if abort.raw_record_id == self.raw_record_id
                    and abort.normalization_run_id == self.normalization_run_id
                    and abort.raw_event_index == outcome.observation_key.raw_event_index
                    and abort.identified_coverage_scope_id
                    == outcome.normalization_scope_binding.coverage_scope_id
                    and outcome.logical_source_key is not None
                    and abort.source_event_id == outcome.logical_source_key.source_event_id
                )
                if len(abort_matches) != 1:
                    raise ValueError(
                        "every frame-aborted index requires one exact outcome-only abort evidence "
                        "record."
                    )
            elif outcome.disposition in {
                RawEventDisposition.MATERIALIZED_NEW,
                RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
            }:
                continue
            else:  # pragma: no cover - closed enum exhaustiveness
                raise AssertionError("unhandled raw-event disposition")

        if identified_rejection_fanout:
            if matched_rejection_scope_ids != set(fanout.identified_rejection_scope_ids):
                raise ValueError(
                    "identified rejection lineage contains an orphan or missing non-ACK route."
                )
            if matched_acknowledged_scope_ids != set(fanout.acknowledged_routed_scope_ids):
                raise ValueError(
                    "identified rejection lineage contains an orphan acknowledged route."
                )

        if len(matched_primary_ids) != len(primary) or set(matched_primary_ids) != {
            item.coverage_evidence_id for item in primary
        }:
            raise ValueError("typed coverage lineage contains orphan primary evidence.")
        aborted_indexes = {
            outcome.observation_key.raw_event_index
            for outcome in outcomes
            if outcome.disposition is RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED
        }
        if len(aborts) != len(aborted_indexes):
            raise ValueError("typed coverage lineage contains orphan frame-abort evidence.")

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


@dataclass(frozen=True, slots=True, init=False)
class NormalizationOutcomeSinkFailureCoverageBinding:
    """Verified aggregate binding of one outcome to its post-sink-failure batch.

    A lower-layer :class:`CoverageMutationBatch` and its individual evidence
    rows don't expose the concrete outcome's decoded scope bindings.  This
    upper-layer aggregate is therefore the sole complete proof that an outcome
    sink failure targets exactly the scopes and subscription attempts identified
    by that outcome.  It remains dormant until Phase 1A-3B1C-2.
    """

    normalization_outcome: NormalizationOutcome = field(repr=False)
    coverage_mutation_batch: CoverageMutationBatch = field(repr=False)
    evidence_kind: CoverageEvidenceKind
    normalization_outcome_sink_failure_coverage_binding_id: (
        NormalizationOutcomeSinkFailureCoverageBindingId
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "use NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch()."
        )

    @classmethod
    def from_outcome_and_batch(
        cls,
        *,
        normalization_outcome: NormalizationOutcome,
        coverage_mutation_batch: CoverageMutationBatch,
    ) -> Self:
        """Validate exact concrete-outcome, fan-out and raw-record lineage."""

        if cls is not NormalizationOutcomeSinkFailureCoverageBinding:
            raise TypeError("outcome sink-failure coverage bindings don't support subclassing.")
        if type(normalization_outcome) is not NormalizationOutcome:
            raise TypeError("normalization_outcome must be a NormalizationOutcome.")
        if type(coverage_mutation_batch) is not CoverageMutationBatch:
            raise TypeError("coverage_mutation_batch must be a CoverageMutationBatch.")
        raw_binding = coverage_mutation_batch.raw_fanout_binding
        if type(raw_binding) is not RawCoverageFanoutBinding:
            raise ValueError("outcome sink-failure coverage requires exact raw fan-out binding.")
        fanout = coverage_mutation_batch.fanout_proof
        if raw_binding.raw_record_id != normalization_outcome.raw_record_id:
            raise ValueError("outcome sink-failure coverage must bind the exact raw record.")

        target_scope_ids = tuple(scope.coverage_scope_id for scope in fanout.target_scopes)
        evidence_by_scope = _outcome_sink_failure_evidence_by_scope(coverage_mutation_batch)
        if tuple(evidence_by_scope) != target_scope_ids:
            raise ValueError("outcome sink-failure evidence must cover every exact fan-out target.")
        sources = tuple(evidence.source for evidence in evidence_by_scope.values())
        if any(type(source) is not NormalizationOutcomeEvidenceSource for source in sources):
            raise ValueError("outcome sink-failure batch requires its exact typed evidence source.")
        typed_sources = tuple(
            source for source in sources if type(source) is NormalizationOutcomeEvidenceSource
        )
        if len(typed_sources) != len(sources):  # pragma: no cover - guarded above
            raise AssertionError("unhandled outcome sink-failure evidence source")
        if any(
            source.normalization_outcome_id != normalization_outcome.normalization_outcome_id
            for source in typed_sources
        ):
            raise ValueError("outcome sink-failure evidence must cite the concrete outcome.")
        evidence_kinds = {evidence.kind for evidence in evidence_by_scope.values()}
        if len(evidence_kinds) != 1:
            raise ValueError("outcome sink-failure batch requires one exact failure knowledge.")
        evidence_kind = next(iter(evidence_kinds))
        if evidence_kind not in {
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        }:
            raise ValueError("outcome sink-failure batch has an unsupported evidence kind.")

        _validate_concrete_outcome_fanout(
            normalization_outcome=normalization_outcome,
            fanout=fanout,
            raw_binding=raw_binding,
        )
        identifier = NormalizationOutcomeSinkFailureCoverageBindingId(
            canonical_json_array(
                (
                    _NORMALIZATION_OUTCOME_SINK_FAILURE_COVERAGE_BINDING_VERSION,
                    normalization_outcome.normalization_outcome_id.value,
                    coverage_mutation_batch.coverage_mutation_batch_id.value,
                    raw_binding.raw_coverage_fanout_binding_id.value,
                    evidence_kind.value,
                )
            )
        )
        value = object.__new__(cls)
        object.__setattr__(value, "normalization_outcome", normalization_outcome)
        object.__setattr__(value, "coverage_mutation_batch", coverage_mutation_batch)
        object.__setattr__(value, "evidence_kind", evidence_kind)
        object.__setattr__(
            value,
            "normalization_outcome_sink_failure_coverage_binding_id",
            identifier,
        )
        return value

    @classmethod
    def from_stored(
        cls,
        *,
        normalization_outcome: NormalizationOutcome,
        coverage_mutation_batch: CoverageMutationBatch,
        expected_binding_id: NormalizationOutcomeSinkFailureCoverageBindingId,
    ) -> Self:
        """Recompute the complete binding and reject a persisted ID mismatch."""

        if type(expected_binding_id) is not NormalizationOutcomeSinkFailureCoverageBindingId:
            raise TypeError(
                "expected_binding_id must be a NormalizationOutcomeSinkFailureCoverageBindingId."
            )
        value = cls.from_outcome_and_batch(
            normalization_outcome=normalization_outcome,
            coverage_mutation_batch=coverage_mutation_batch,
        )
        if value.normalization_outcome_sink_failure_coverage_binding_id != expected_binding_id:
            raise ValueError("stored outcome sink-failure coverage binding doesn't match.")
        return value


def _outcome_sink_failure_evidence_by_scope(
    batch: CoverageMutationBatch,
) -> dict[CoverageScopeId, CoverageEvidence]:
    evidence = (
        tuple(initialization.initial_evidence for initialization in batch.initializations)
        + tuple(transition.evidence for transition in batch.transitions)
        + tuple(no_op.request.evidence for no_op in batch.no_ops)
    )
    if len(evidence) != len(batch.fanout_proof.target_scopes):
        raise ValueError("outcome sink-failure batch must decide every fan-out target once.")
    by_scope = {item.coverage_scope_id: item for item in evidence}
    if len(by_scope) != len(evidence):
        raise ValueError("outcome sink-failure evidence must be unique by target scope.")
    return {
        scope.coverage_scope_id: by_scope[scope.coverage_scope_id]
        for scope in batch.fanout_proof.target_scopes
        if scope.coverage_scope_id in by_scope
    }


def _validate_concrete_outcome_fanout(
    *,
    normalization_outcome: NormalizationOutcome,
    fanout: CoverageFanoutProof,
    raw_binding: RawCoverageFanoutBinding,
) -> None:
    status = normalization_outcome.frame_status
    if status in {
        FrameNormalizationStatus.CONTROL_NO_EVENT,
        FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME,
    }:
        raise ValueError("control and valid-empty outcomes cannot degrade market coverage.")
    if status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING:
        if fanout.kind is not CoverageFanoutKind.ALL_POSSIBLY_ACTIVE:
            raise ValueError(
                "pre-index outcome sink failure requires complete active-plan fan-out."
            )
        preindex = normalization_outcome.preindex_scope_binding
        if type(preindex) is not RawFrameNormalizationScopeBinding:
            raise ValueError("pre-index outcome requires its exact frame-scope binding.")
        if (
            preindex.raw_record_id != raw_binding.raw_record_id
            or preindex.full_record_integrity_sha256 != raw_binding.full_record_integrity_sha256
            or preindex.subscription_plan_id != fanout.subscription_plan_id
        ):
            raise ValueError("pre-index outcome scope must match the exact raw fan-out binding.")
        aggregate = preindex.coverage_scope
        for target in fanout.target_scopes:
            if (
                target.domain is not CoverageDomain.SILVER_NORMALIZATION
                or (
                    target.feed_product_id,
                    target.event_family,
                    target.event_family_schema_version,
                    target.payload_type,
                )
                != (
                    aggregate.feed_product_id,
                    aggregate.event_family,
                    aggregate.event_family_schema_version,
                    aggregate.payload_type,
                )
                or not set(target.subscription_spec_ids).issubset(aggregate.subscription_spec_ids)
                or not set(target.canonical_instrument_ids).issubset(
                    aggregate.canonical_instrument_ids
                )
            ):
                raise ValueError("pre-index outcome fan-out is outside its complete frame scope.")
        return

    identified_rejection_fanout = fanout.kind in {
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
    }
    if fanout.kind not in {
        CoverageFanoutKind.EXACT_ROUTED_EVENT,
        CoverageFanoutKind.EXACT_ROUTED_EVENTS,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
    }:
        raise ValueError("indexed outcome sink failure requires exact routed-event fan-out.")
    if identified_rejection_fanout and (
        status is not FrameNormalizationStatus.REJECTED_AFTER_INDEXING
        or normalization_outcome.coverage_lineage is None
    ):
        raise ValueError(
            "identified rejection sink failure requires typed indexed rejection lineage."
        )
    scope_bindings = tuple(
        outcome.normalization_scope_binding for outcome in normalization_outcome.raw_event_outcomes
    )
    if any(
        binding.raw_record_id != raw_binding.raw_record_id
        or binding.full_record_integrity_sha256 != raw_binding.full_record_integrity_sha256
        or binding.subscription_plan_id != fanout.subscription_plan_id
        for binding in scope_bindings
    ):
        raise ValueError("indexed outcome scopes must match the exact raw fan-out binding.")
    expected_scope_ids = tuple(
        sorted(
            {binding.coverage_scope_id for binding in scope_bindings},
            key=lambda item: item.value,
        )
    )
    actual_scope_ids = tuple(scope.coverage_scope_id for scope in fanout.target_scopes)
    if actual_scope_ids != expected_scope_ids:
        raise ValueError("indexed outcome fan-out must equal its exact decoded scope union.")
    expected_attempt_ids = tuple(
        sorted(
            {binding.subscription_attempt_id for binding in scope_bindings},
            key=lambda item: item.value,
        )
    )
    if fanout.selected_attempt_ids != expected_attempt_ids:
        raise ValueError("indexed outcome fan-out must equal its exact decoded attempt union.")
    if identified_rejection_fanout:
        matched_rejection_scope_ids: set[CoverageScopeId] = set()
        matched_acknowledged_scope_ids: set[CoverageScopeId] = set()
        for outcome in normalization_outcome.raw_event_outcomes:
            outcome_binding = outcome.normalization_scope_binding
            if outcome_binding.attempt_status is SubscriptionAttemptStatus.ACKNOWLEDGED:
                route_matches = tuple(
                    scope_id
                    for target, scope_id in zip(
                        fanout.acknowledged_routed_targets,
                        fanout.acknowledged_routed_scope_ids,
                        strict=True,
                    )
                    if _binding_matches_acknowledged_route_target(
                        outcome_binding,
                        target,
                        scope_id,
                    )
                )
                if len(route_matches) != 1:
                    raise ValueError(
                        "acknowledged indexed outcome must match one exact sink-fanout route."
                    )
                matched_acknowledged_scope_ids.add(route_matches[0])
                continue
            route_matches = tuple(
                scope_id
                for target, scope_id in zip(
                    fanout.identified_rejection_targets,
                    fanout.identified_rejection_scope_ids,
                    strict=True,
                )
                if _binding_matches_identified_rejection_target(
                    outcome_binding,
                    target,
                    scope_id,
                )
            )
            if (
                len(route_matches) != 1
                or outcome.disposition is not RawEventDisposition.REJECTED
                or outcome.evidence is not NormalizationEvidence.PROVENANCE_MISMATCH
            ):
                raise ValueError(
                    "non-ACK sink-fanout routes require exact rejected provenance mismatches."
                )
            matched_rejection_scope_ids.add(route_matches[0])
        if matched_rejection_scope_ids != set(fanout.identified_rejection_scope_ids):
            raise ValueError("sink-failure fanout omits or adds an identified rejection route.")
        if matched_acknowledged_scope_ids != set(fanout.acknowledged_routed_scope_ids):
            raise ValueError("sink-failure fanout omits or adds an acknowledged route.")


def _validate_delivery_status_reason(
    status: DeliveryKnowledgeStatus,
    reason: DeliveryOutcomeReason,
) -> None:
    allowed = {
        DeliveryKnowledgeStatus.ACCEPTED: {
            DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE,
        },
        DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED: {
            DeliveryOutcomeReason.QUEUE_CAPACITY_TIMEOUT,
            DeliveryOutcomeReason.CANCELLATION_BEFORE_LINEARIZATION,
            DeliveryOutcomeReason.EXPLICIT_REJECTION,
            DeliveryOutcomeReason.LOCAL_CONTRACT_FAILURE,
        },
        DeliveryKnowledgeStatus.ACCEPTANCE_UNCERTAIN: {
            DeliveryOutcomeReason.AMBIGUOUS_COMPLETION,
        },
    }
    if reason not in allowed[status]:
        raise ValueError("delivery knowledge status and reason are inconsistent.")


@dataclass(frozen=True, slots=True)
class DeliveryItemCommitment:
    """Commit one materialization key to its exact serialized event-content digest.

    ID preimage::

        ["delivery-item-commitment-v1", materialization_key_id,
         event_content_sha256]
    """

    materialization_key_id: MaterializationKeyId
    event_content_sha256: str = field(repr=False)
    delivery_item_commitment_id: DeliveryItemCommitmentId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.materialization_key_id) is not MaterializationKeyId:
            raise TypeError("materialization_key_id must be a MaterializationKeyId.")
        digest = require_sha256(
            self.event_content_sha256,
            field_name="event_content_sha256",
        )
        object.__setattr__(
            self,
            "delivery_item_commitment_id",
            DeliveryItemCommitmentId(
                canonical_json_array(
                    (
                        _DELIVERY_ITEM_COMMITMENT_ID_VERSION,
                        self.materialization_key_id.value,
                        digest,
                    )
                )
            ),
        )

    @classmethod
    def from_stored(
        cls,
        *,
        materialization_key_id: MaterializationKeyId,
        event_content_sha256: str,
        expected_commitment_id: DeliveryItemCommitmentId,
    ) -> Self:
        if type(expected_commitment_id) is not DeliveryItemCommitmentId:
            raise TypeError("expected_commitment_id must be a DeliveryItemCommitmentId.")
        value = cls(materialization_key_id, event_content_sha256)
        if value.delivery_item_commitment_id != expected_commitment_id:
            raise ValueError("stored delivery item commitment does not match its content.")
        return value


@dataclass(frozen=True, slots=True)
class DeliveryBatchCommitment:
    """Commit exact event content above the byte-compatible v1 batch locator.

    Exact ordered-item-content preimage::

        ["delivery-item-commitments-v1",
         [[materialization_key_id, event_content_sha256], ...]]

    Exact batch-content preimage::

        ["normalization-delivery-batch-content-v1",
         normalization_outcome_id,
         [[materialization_key_id, event_content_sha256], ...],
         item_count, SHA256(exact_ordered_item_content)]

    Commitment ID preimage::

        ["normalization-delivery-batch-commitment-v1", legacy_delivery_batch_id,
         normalization_outcome_id, item_count, SHA256(exact_batch_content)]

    ``DeliveryBatchId`` remains a proof-neutral locator over ordered
    materialization-key text. This upper commitment composes that locator and
    adds the exact outcome and serialized event-content binding.
    """

    normalization_outcome: NormalizationOutcome = field(repr=False)
    item_commitments: tuple[DeliveryItemCommitment, ...] = field(repr=False)
    legacy_delivery_batch_id: DeliveryBatchId = field(init=False)
    item_count: int = field(init=False)
    canonical_content: str = field(init=False, repr=False)
    aggregate_content_sha256: str = field(init=False)
    batch_content_sha256: str = field(init=False)
    delivery_batch_commitment_id: DeliveryBatchCommitmentId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.normalization_outcome) is not NormalizationOutcome:
            raise TypeError("normalization_outcome must be a NormalizationOutcome.")
        if self.normalization_outcome.frame_status not in {
            FrameNormalizationStatus.MATERIALIZED,
            FrameNormalizationStatus.MIXED_SUCCESS,
        }:
            raise ValueError("delivery batch requires a successful materializing outcome.")
        items = _require_exact_tuple(
            self.item_commitments,
            item_type=DeliveryItemCommitment,
            field_name="item_commitments",
            maximum_items=MAX_NORMALIZATION_OUTCOME_ITEMS,
        )
        if not items:
            raise ValueError("delivery batch requires at least one materialized event.")
        expected_keys = tuple(
            key.materialization_key_id
            for key in self.normalization_outcome.committed_materialization_keys
        )
        actual_keys = tuple(item.materialization_key_id for item in items)
        if actual_keys != expected_keys:
            raise ValueError(
                "delivery item commitments must exactly match outcome order and content."
            )
        if len(set(actual_keys)) != len(actual_keys):
            raise ValueError("delivery item commitments must be unique by materialization key.")
        item_rows = tuple(
            (item.materialization_key_id.value, item.event_content_sha256) for item in items
        )
        ordered_item_content = canonical_json_array(
            (_DELIVERY_ITEM_COMMITMENTS_CONTENT_VERSION, item_rows)
        )
        aggregate_digest = sha256_hex(
            ordered_item_content.encode("utf-8"),
            field_name="delivery_item_commitments_content",
        )
        content = canonical_json_array(
            (
                _DELIVERY_BATCH_CONTENT_VERSION,
                self.normalization_outcome.normalization_outcome_id.value,
                item_rows,
                len(items),
                aggregate_digest,
            )
        )
        content_digest = sha256_hex(
            content.encode("utf-8"),
            field_name="delivery_batch_content",
        )
        legacy_locator = DeliveryAttemptIdentity(
            destination_id="normalization-delivery-commitment",
            materialization_key_canonical_texts=tuple(item[0] for item in item_rows),
            attempt_ordinal=0,
        )
        identifier = DeliveryBatchCommitmentId(
            canonical_json_array(
                (
                    _DELIVERY_BATCH_COMMITMENT_ID_VERSION,
                    legacy_locator.delivery_batch_id.value,
                    self.normalization_outcome.normalization_outcome_id.value,
                    len(items),
                    content_digest,
                )
            )
        )
        object.__setattr__(self, "legacy_delivery_batch_id", legacy_locator.delivery_batch_id)
        object.__setattr__(self, "item_count", len(items))
        object.__setattr__(self, "canonical_content", content)
        object.__setattr__(self, "aggregate_content_sha256", aggregate_digest)
        object.__setattr__(self, "batch_content_sha256", content_digest)
        object.__setattr__(self, "delivery_batch_commitment_id", identifier)

    @classmethod
    def from_stored(
        cls,
        *,
        normalization_outcome: NormalizationOutcome,
        item_commitments: tuple[DeliveryItemCommitment, ...],
        expected_legacy_delivery_batch_id: DeliveryBatchId,
        expected_canonical_content: str,
        expected_aggregate_content_sha256: str,
        expected_batch_content_sha256: str,
        expected_commitment_id: DeliveryBatchCommitmentId,
    ) -> Self:
        if type(expected_legacy_delivery_batch_id) is not DeliveryBatchId:
            raise TypeError("expected_legacy_delivery_batch_id must be a DeliveryBatchId.")
        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        require_sha256(
            expected_aggregate_content_sha256,
            field_name="expected_aggregate_content_sha256",
        )
        require_sha256(
            expected_batch_content_sha256,
            field_name="expected_batch_content_sha256",
        )
        if type(expected_commitment_id) is not DeliveryBatchCommitmentId:
            raise TypeError("expected_commitment_id must be a DeliveryBatchCommitmentId.")
        value = cls(normalization_outcome, item_commitments)
        if (
            value.legacy_delivery_batch_id != expected_legacy_delivery_batch_id
            or value.canonical_content != expected_canonical_content
            or value.aggregate_content_sha256 != expected_aggregate_content_sha256
            or value.batch_content_sha256 != expected_batch_content_sha256
            or value.delivery_batch_commitment_id != expected_commitment_id
        ):
            raise ValueError("stored delivery batch commitment does not match its content.")
        return value


@dataclass(frozen=True, slots=True)
class OutcomeDeliveryAttemptBinding:
    """Bind a legacy v1 attempt locator to one exact batch commitment.

    Binding ID preimage::

        ["normalization-delivery-attempt-binding-v1", legacy_delivery_attempt_id,
         delivery_batch_commitment_id]

    The current schema-v2 bounded output queue permits only ordinal zero.
    """

    destination_id: DeliveryDestinationId
    delivery_batch_commitment: DeliveryBatchCommitment = field(repr=False)
    attempt_ordinal: int = 0
    legacy_delivery_attempt: DeliveryAttemptIdentity = field(init=False, repr=False)
    outcome_delivery_attempt_binding_id: OutcomeDeliveryAttemptBindingId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.destination_id) is not DeliveryDestinationId:
            raise TypeError("destination_id must be a DeliveryDestinationId.")
        if type(self.delivery_batch_commitment) is not DeliveryBatchCommitment:
            raise TypeError("delivery_batch_commitment must be a DeliveryBatchCommitment.")
        ordinal = _require_non_negative_int(
            self.attempt_ordinal,
            field_name="attempt_ordinal",
        )
        if ordinal != 0:
            raise ValueError("the current schema-v2 output queue permits only attempt ordinal 0.")
        legacy_attempt = DeliveryAttemptIdentity(
            destination_id=self.destination_id.value,
            materialization_key_canonical_texts=tuple(
                item.materialization_key_id.value
                for item in self.delivery_batch_commitment.item_commitments
            ),
            attempt_ordinal=ordinal,
        )
        if (
            legacy_attempt.delivery_batch_id
            != self.delivery_batch_commitment.legacy_delivery_batch_id
        ):
            raise ValueError("legacy attempt and batch commitment locators disagree.")
        object.__setattr__(self, "legacy_delivery_attempt", legacy_attempt)
        object.__setattr__(
            self,
            "outcome_delivery_attempt_binding_id",
            OutcomeDeliveryAttemptBindingId(
                canonical_json_array(
                    (
                        _OUTCOME_DELIVERY_ATTEMPT_BINDING_ID_VERSION,
                        legacy_attempt.delivery_attempt_id.value,
                        self.delivery_batch_commitment.delivery_batch_commitment_id.value,
                    )
                )
            ),
        )

    @classmethod
    def from_stored(
        cls,
        *,
        destination_id: DeliveryDestinationId,
        delivery_batch_commitment: DeliveryBatchCommitment,
        attempt_ordinal: int,
        expected_legacy_delivery_attempt_id: DeliveryAttemptId,
        expected_binding_id: OutcomeDeliveryAttemptBindingId,
    ) -> Self:
        if type(expected_legacy_delivery_attempt_id) is not DeliveryAttemptId:
            raise TypeError("expected_legacy_delivery_attempt_id must be a DeliveryAttemptId.")
        if type(expected_binding_id) is not OutcomeDeliveryAttemptBindingId:
            raise TypeError("expected_binding_id must be an OutcomeDeliveryAttemptBindingId.")
        value = cls(destination_id, delivery_batch_commitment, attempt_ordinal)
        if (
            value.legacy_delivery_attempt.delivery_attempt_id != expected_legacy_delivery_attempt_id
            or value.outcome_delivery_attempt_binding_id != expected_binding_id
        ):
            raise ValueError("stored outcome delivery attempt binding does not match its content.")
        return value


@dataclass(frozen=True, slots=True, init=False)
class OutcomeDeliveryResult:
    """Sanitized immutable delivery knowledge without retaining event-batch content.

    ID preimage::

        ["normalization-delivery-outcome-v1", outcome_delivery_attempt_binding_id,
         knowledge_status, reason, observed_at, observed_monotonic_ns]
    """

    outcome_delivery_attempt_binding_id: OutcomeDeliveryAttemptBindingId
    knowledge_status: DeliveryKnowledgeStatus
    reason: DeliveryOutcomeReason
    observed_at: datetime
    observed_monotonic_ns: int
    delivery_outcome_id: DeliveryOutcomeId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("delivery outcomes are created only by commit result factories.")


def _construct_nonaccepted_delivery_outcome(
    *,
    attempt: OutcomeDeliveryAttemptBinding,
    knowledge_status: DeliveryKnowledgeStatus,
    reason: DeliveryOutcomeReason,
    observed_at: datetime,
    observed_monotonic_ns: int,
) -> OutcomeDeliveryResult:
    if type(attempt) is not OutcomeDeliveryAttemptBinding:
        raise TypeError("attempt must be an OutcomeDeliveryAttemptBinding.")
    if type(knowledge_status) is not DeliveryKnowledgeStatus:
        raise TypeError("knowledge_status must be a DeliveryKnowledgeStatus.")
    if type(reason) is not DeliveryOutcomeReason:
        raise TypeError("reason must be a DeliveryOutcomeReason.")
    if knowledge_status is DeliveryKnowledgeStatus.ACCEPTED:
        raise ValueError("accepted delivery knowledge requires commit acceptance.")
    _validate_delivery_status_reason(knowledge_status, reason)
    observed_text = canonical_utc_datetime(observed_at, field_name="observed_at")
    monotonic = _require_non_negative_int(
        observed_monotonic_ns,
        field_name="observed_monotonic_ns",
    )
    value = object.__new__(OutcomeDeliveryResult)
    object.__setattr__(
        value,
        "outcome_delivery_attempt_binding_id",
        attempt.outcome_delivery_attempt_binding_id,
    )
    object.__setattr__(value, "knowledge_status", knowledge_status)
    object.__setattr__(value, "reason", reason)
    object.__setattr__(value, "observed_at", observed_at.replace(tzinfo=UTC))
    object.__setattr__(value, "observed_monotonic_ns", monotonic)
    object.__setattr__(
        value,
        "delivery_outcome_id",
        DeliveryOutcomeId(
            canonical_json_array(
                (
                    _DELIVERY_OUTCOME_ID_VERSION,
                    attempt.outcome_delivery_attempt_binding_id.value,
                    knowledge_status.value,
                    reason.value,
                    observed_text,
                    monotonic,
                )
            )
        ),
    )
    return value


@dataclass(frozen=True, slots=True, init=False)
class DeliveryCommitAcceptance:
    """The sole proof that a complete composite event batch was accepted."""

    delivery_outcome: OutcomeDeliveryResult
    delivery_batch_commitment: DeliveryBatchCommitment = field(repr=False)
    delivery_commit_acceptance_id: DeliveryCommitAcceptanceId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use DeliveryCommitAcceptance.from_linearized().")

    @classmethod
    def from_linearized(
        cls,
        *,
        attempt: OutcomeDeliveryAttemptBinding,
        observed_at: datetime,
        observed_monotonic_ns: int,
    ) -> Self:
        """Atomically construct accepted knowledge and its composite-batch proof."""

        if cls is not DeliveryCommitAcceptance:
            raise TypeError("delivery commit acceptances do not support subclass factories.")
        if type(attempt) is not OutcomeDeliveryAttemptBinding:
            raise TypeError("attempt must be an OutcomeDeliveryAttemptBinding.")
        observed_text = canonical_utc_datetime(observed_at, field_name="observed_at")
        monotonic = _require_non_negative_int(
            observed_monotonic_ns,
            field_name="observed_monotonic_ns",
        )
        outcome = object.__new__(OutcomeDeliveryResult)
        object.__setattr__(
            outcome,
            "outcome_delivery_attempt_binding_id",
            attempt.outcome_delivery_attempt_binding_id,
        )
        object.__setattr__(outcome, "knowledge_status", DeliveryKnowledgeStatus.ACCEPTED)
        object.__setattr__(outcome, "reason", DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE)
        object.__setattr__(outcome, "observed_at", observed_at.replace(tzinfo=UTC))
        object.__setattr__(outcome, "observed_monotonic_ns", monotonic)
        object.__setattr__(
            outcome,
            "delivery_outcome_id",
            DeliveryOutcomeId(
                canonical_json_array(
                    (
                        _DELIVERY_OUTCOME_ID_VERSION,
                        attempt.outcome_delivery_attempt_binding_id.value,
                        DeliveryKnowledgeStatus.ACCEPTED.value,
                        DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE.value,
                        observed_text,
                        monotonic,
                    )
                )
            ),
        )
        value = object.__new__(cls)
        object.__setattr__(value, "delivery_outcome", outcome)
        object.__setattr__(
            value,
            "delivery_batch_commitment",
            attempt.delivery_batch_commitment,
        )
        object.__setattr__(
            value,
            "delivery_commit_acceptance_id",
            DeliveryCommitAcceptanceId(
                canonical_json_array(
                    (
                        _DELIVERY_COMMIT_ACCEPTANCE_ID_VERSION,
                        outcome.delivery_outcome_id.value,
                        attempt.delivery_batch_commitment.delivery_batch_commitment_id.value,
                    )
                )
            ),
        )
        return value

    @classmethod
    def from_stored(
        cls,
        *,
        attempt: OutcomeDeliveryAttemptBinding,
        observed_at: datetime,
        observed_monotonic_ns: int,
        expected_outcome_id: DeliveryOutcomeId,
        expected_acceptance_id: DeliveryCommitAcceptanceId,
    ) -> Self:
        if type(expected_outcome_id) is not DeliveryOutcomeId:
            raise TypeError("expected_outcome_id must be a DeliveryOutcomeId.")
        if type(expected_acceptance_id) is not DeliveryCommitAcceptanceId:
            raise TypeError("expected_acceptance_id must be a DeliveryCommitAcceptanceId.")
        value = cls.from_linearized(
            attempt=attempt,
            observed_at=observed_at,
            observed_monotonic_ns=observed_monotonic_ns,
        )
        if (
            value.delivery_outcome.delivery_outcome_id != expected_outcome_id
            or value.delivery_commit_acceptance_id != expected_acceptance_id
        ):
            raise ValueError("stored delivery commit acceptance does not match its content.")
        return value


@dataclass(frozen=True, slots=True, init=False)
class DeliveryCommitFailure:
    """Non-acceptance or uncertainty result that retains no composite event batch."""

    delivery_outcome: OutcomeDeliveryResult
    delivery_commit_failure_id: DeliveryCommitFailureId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use DeliveryCommitFailure.from_attempt().")

    @classmethod
    def from_attempt(
        cls,
        *,
        attempt: OutcomeDeliveryAttemptBinding,
        knowledge_status: DeliveryKnowledgeStatus,
        reason: DeliveryOutcomeReason,
        observed_at: datetime,
        observed_monotonic_ns: int,
    ) -> Self:
        if cls is not DeliveryCommitFailure:
            raise TypeError("delivery commit failures do not support subclass factories.")
        if knowledge_status is DeliveryKnowledgeStatus.ACCEPTED:
            raise ValueError("accepted delivery knowledge requires commit acceptance.")
        outcome = _construct_nonaccepted_delivery_outcome(
            attempt=attempt,
            knowledge_status=knowledge_status,
            reason=reason,
            observed_at=observed_at,
            observed_monotonic_ns=observed_monotonic_ns,
        )
        value = object.__new__(cls)
        object.__setattr__(value, "delivery_outcome", outcome)
        object.__setattr__(
            value,
            "delivery_commit_failure_id",
            DeliveryCommitFailureId(
                canonical_json_array(
                    (
                        _DELIVERY_COMMIT_FAILURE_ID_VERSION,
                        outcome.delivery_outcome_id.value,
                    )
                )
            ),
        )
        return value

    @classmethod
    def from_stored(
        cls,
        *,
        attempt: OutcomeDeliveryAttemptBinding,
        knowledge_status: DeliveryKnowledgeStatus,
        reason: DeliveryOutcomeReason,
        observed_at: datetime,
        observed_monotonic_ns: int,
        expected_outcome_id: DeliveryOutcomeId,
        expected_failure_id: DeliveryCommitFailureId,
    ) -> Self:
        if type(expected_outcome_id) is not DeliveryOutcomeId:
            raise TypeError("expected_outcome_id must be a DeliveryOutcomeId.")
        if type(expected_failure_id) is not DeliveryCommitFailureId:
            raise TypeError("expected_failure_id must be a DeliveryCommitFailureId.")
        value = cls.from_attempt(
            attempt=attempt,
            knowledge_status=knowledge_status,
            reason=reason,
            observed_at=observed_at,
            observed_monotonic_ns=observed_monotonic_ns,
        )
        if (
            value.delivery_outcome.delivery_outcome_id != expected_outcome_id
            or value.delivery_commit_failure_id != expected_failure_id
        ):
            raise ValueError("stored delivery commit failure does not match its content.")
        return value
