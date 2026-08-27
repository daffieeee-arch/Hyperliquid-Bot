"""Pure tests for the dormant outer-envelope-v3 contract spine."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest

import hyperliquid_bot.market_event_v3 as market_event_v3_module
from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    Instrument,
    InstrumentType,
    TradeEvent,
    Venue,
)
from hyperliquid_bot.data_provenance import (
    BINANCE_MAINNET_SPOT_JSON_STREAMS,
    HYPERLIQUID_MAINNET_PUBLIC_TRADES,
    MAX_COVERAGE_TRANSITION_REFERENCES,
    MAX_DECODED_EVENTS_PER_RAW_RECORD,
    MAX_NORMALIZATION_EVIDENCE_ITEMS,
    MAX_NORMALIZATION_OUTCOME_ITEMS,
    MAX_SOURCE_SEQUENCE_RANGES,
    MAX_SOURCE_TIME_FACTS,
    MAX_UNSIGNED_64,
    AdapterFeedBindingId,
    CollectorRunId,
    CommittedCoverageState,
    ConnectionSessionIdentity,
    CorrelationId,
    CoverageCommitAcceptance,
    CoverageDomain,
    CoverageEpochIdentity,
    CoverageEvidence,
    CoverageEvidenceKind,
    CoverageEvidenceSource,
    CoverageFanoutProof,
    CoverageInitialization,
    CoverageMutationBatch,
    CoverageReason,
    CoverageReference,
    CoverageScope,
    CoverageStateReference,
    CoverageStatus,
    CoverageTargetCatalog,
    CoverageTransition,
    CoverageTransitionId,
    DeliveryAttemptId,
    DeliveryAttemptIdentity,
    DeliveryBatchId,
    EventActivationRequirement,
    EventCoverage,
    ExactIdentifiedRejectionTarget,
    FrameKind,
    InitialActivationEvidenceSource,
    InitialCoverageReason,
    InstrumentSubscriptionBinding,
    MetadataAuthorityId,
    NormalizationBinding,
    NormalizationFailureCategory,
    NormalizationFailureEvidenceSource,
    NormalizationOutcomeEvidenceSource,
    NormalizationRunId,
    PublicConnectionOption,
    PublicConnectionOptionKind,
    PublicEndpointProfile,
    PublicSourceSelector,
    PublicSourceSelectorKind,
    PublicSubscriptionParameter,
    PublicSubscriptionParameterKind,
    RawCoverageFanoutBinding,
    RawCoverageFanoutBindingId,
    RawMarketDataRecord,
    RawRecordEvidenceSource,
    RawRecordId,
    ReconnectEvidenceSource,
    RequestedCoverageMutation,
    RequestedCoverageTransition,
    RoutedCoverageTarget,
    SourceEventConflictEvidenceSource,
    SourceEventId,
    SourceSequenceBreakEvidenceSource,
    SourceSequenceRange,
    SourceSequenceRole,
    SourceTimeFact,
    SourceTimeRole,
    SourceTimeUnit,
    SourceTransactionId,
    SubscriptionAttemptIdentity,
    SubscriptionAttemptSnapshot,
    SubscriptionAttemptStatus,
    SubscriptionPlanIdentity,
    SubscriptionSpecIdentity,
    TransportAmbiguityEvidenceSource,
    prepare_coverage_mutation_batch,
    reduce_coverage,
)
from hyperliquid_bot.data_provenance import (
    NormalizationOutcomeId as ProvenanceNormalizationOutcomeId,
)
from hyperliquid_bot.instrument_metadata import (
    ContractForm,
    InstrumentMetadataObservation,
    InstrumentSpecification,
    MetadataEffectiveBasis,
    QuantityUnit,
    ResolvedInstrumentMetadata,
    select_instrument_metadata,
)
from hyperliquid_bot.market_event_v3 import (
    MARKET_EVENT_ENVELOPE_SCHEMA_VERSION,
    TRADE_EVENT_FAMILY_SCHEMA_VERSION,
    AdapterFeedBinding,
    DecodedEventSubscriptionBinding,
    DecodedWirePayloadContext,
    DeliveryBatchCommitment,
    DeliveryBatchCommitmentId,
    DeliveryCommitAcceptance,
    DeliveryCommitAcceptanceId,
    DeliveryCommitFailure,
    DeliveryCommitFailureId,
    DeliveryDestinationId,
    DeliveryItemCommitment,
    DeliveryItemCommitmentId,
    DeliveryKnowledgeStatus,
    DeliveryOutcomeId,
    DeliveryOutcomeReason,
    EventFamily,
    FrameAtomicAbortEvidenceId,
    FrameAtomicAbortEvidenceSource,
    FrameNormalizationStatus,
    LogicalSourceKey,
    LogicalSourceKeyId,
    MarketEventEnvelopeV3,
    MaterializationKey,
    MaterializationKeyId,
    NormalizationContext,
    NormalizationCoverageLineage,
    NormalizationCoverageLineageId,
    NormalizationEvidence,
    NormalizationOutcome,
    NormalizationOutcomeId,
    NormalizationOutcomeSinkFailureCoverageBinding,
    NormalizationOutcomeSinkFailureCoverageBindingId,
    NormalizationSourceConflictBinding,
    NormalizationSourceConflictBindingId,
    ObservationKey,
    ObservationKeyId,
    ObservationProvenance,
    OutcomeDeliveryAttemptBinding,
    OutcomeDeliveryAttemptBindingId,
    OutcomeDeliveryResult,
    PayloadType,
    RawEventDisposition,
    RawEventNormalizationOutcome,
    RawEventNormalizationOutcomeId,
    RawEventNormalizationScopeBinding,
    RawFrameNormalizationScopeBinding,
    SourceProvenance,
)


def _identified_preack_outcome(
    *,
    attempt_status: SubscriptionAttemptStatus = SubscriptionAttemptStatus.SENT,
    event_count: int = 1,
) -> tuple[
    RawMarketDataRecord,
    CoverageFanoutProof,
    CoverageMutationBatch,
    NormalizationOutcome,
]:
    raw_record, spec, attempt = _raw_record(attempt_status=attempt_status)
    snapshot = raw_record.subscription_attempt_snapshots[0]
    plan_binding = raw_record.subscription_plan.instrument_bindings[0]
    normalization_binding = raw_record.subscription_plan.normalization_bindings[0]
    target = ExactIdentifiedRejectionTarget(
        attempt_snapshot=snapshot,
        source_selector=plan_binding.source_selector,
        canonical_instrument_id=plan_binding.canonical_instrument_id,
        adapter_profile=plan_binding.adapter_profile,
        event_family=normalization_binding.event_family,
        event_family_schema_version=normalization_binding.event_family_schema_version,
        payload_type=normalization_binding.payload_type,
    )
    fanout = CoverageFanoutProof.exact_identified_rejections(
        plan=raw_record.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan),
        rejection_targets=(target,),
    )
    scope = fanout.target_scopes[0]
    normalization_run_id = NormalizationRunId("preack-normalization-run")
    epoch = CoverageEpochIdentity(
        scope,
        raw_record.collector_run_id,
        0,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    primary_evidence = tuple(
        CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
            NormalizationFailureEvidenceSource(
                raw_record.raw_record_id,
                normalization_run_id,
                index,
                SourceEventId(f"preack-source-{index}"),
                NormalizationFailureCategory.PROVENANCE_MISMATCH,
                scope.coverage_scope_id,
            ),
            scope,
            epoch,
            raw_record.received_time,
            raw_record.received_monotonic_ns,
        )
        for index in range(event_count)
    )
    ordered_evidence = tuple(
        sorted(primary_evidence, key=lambda item: item.coverage_evidence_id.value)
    )
    selected_evidence = ordered_evidence[0]
    raw_binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=fanout,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=(
            RequestedCoverageMutation(
                scope,
                epoch,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                selected_evidence,
            ),
        ),
        raw_fanout_binding=raw_binding,
    )
    indexed_outcomes = tuple(
        RawEventNormalizationOutcome(
            ObservationKey(raw_record.raw_record_id, index),
            _outcome_scope_binding(
                index,
                raw_record=raw_record,
                spec=spec,
                attempt=attempt,
                scope=scope,
            ),
            RawEventDisposition.REJECTED,
            LogicalSourceKey(
                raw_record.feed_product.feed_product_id,
                SourceEventId(f"preack-source-{index}"),
            ),
            evidence=NormalizationEvidence.PROVENANCE_MISMATCH,
        )
        for index in range(event_count)
    )
    lineage = NormalizationCoverageLineage(
        coverage_mutation_batch=batch,
        frame_atomic_abort_evidence=(),
        raw_fanout_binding=raw_binding,
        additional_primary_evidence=ordered_evidence[1:],
    )
    outcome = NormalizationOutcome(
        normalization_run_id=normalization_run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        decoded_event_count=event_count,
        raw_event_outcomes=indexed_outcomes,
        committed_materialization_keys=(),
        evidence=(NormalizationEvidence.PROVENANCE_MISMATCH,),
        coverage_lineage=lineage,
    )
    return raw_record, fanout, batch, outcome


@pytest.mark.parametrize(
    "status",
    (
        SubscriptionAttemptStatus.PENDING,
        SubscriptionAttemptStatus.SEND_STARTED,
        SubscriptionAttemptStatus.SENT,
    ),
)
def test_indexed_preack_provenance_rejection_has_exact_typed_coverage_lineage(
    status: SubscriptionAttemptStatus,
) -> None:
    raw_record, fanout, batch, outcome = _identified_preack_outcome(attempt_status=status)
    event = outcome.raw_event_outcomes[0]
    source = cast(
        NormalizationFailureEvidenceSource,
        cast(NormalizationCoverageLineage, outcome.coverage_lineage).primary_evidence[0].source,
    )

    assert outcome.frame_status is FrameNormalizationStatus.REJECTED_AFTER_INDEXING
    assert event.disposition is RawEventDisposition.REJECTED
    assert event.evidence is NormalizationEvidence.PROVENANCE_MISMATCH
    assert event.normalization_scope_binding.attempt_status is status
    assert source.category is NormalizationFailureCategory.PROVENANCE_MISMATCH
    assert source.raw_record_id == raw_record.raw_record_id
    assert source.raw_event_index == 0
    assert (
        source.source_event_id
        == cast(
            LogicalSourceKey,
            event.logical_source_key,
        ).source_event_id
    )
    assert batch.initializations[0].reference.status is CoverageStatus.CONFIRMED_INCOMPLETE
    assert fanout.target_scopes[0].coverage_scope_id == source.identified_coverage_scope_id


def test_same_preack_route_supports_multiple_exact_indexed_failure_evidence_rows() -> None:
    _raw, fanout, batch, outcome = _identified_preack_outcome(event_count=2)
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)

    assert len(fanout.target_scopes) == 1
    assert len(batch.initializations) == 1
    assert len(lineage.primary_evidence) == 2
    assert {
        cast(NormalizationFailureEvidenceSource, item.source).raw_event_index
        for item in lineage.primary_evidence
    } == {0, 1}
    assert len(outcome.raw_event_outcomes) == 2


def test_identified_preack_lineage_rejects_preindex_wrong_evidence_and_source_identity() -> None:
    raw, _fanout, _batch, outcome = _identified_preack_outcome()
    indexed = outcome.raw_event_outcomes[0]
    with pytest.raises(ValueError, match=r"indexed rejected frame|pre-index"):
        replace(
            outcome,
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            evidence=(NormalizationEvidence.PROVENANCE_MISMATCH,),
            preindex_scope_binding=_preindex_scope_binding(raw),
        )
    with pytest.raises(
        ValueError,
        match=r"typed failure evidence|evidence union|provenance mismatches",
    ):
        replace(
            outcome,
            raw_event_outcomes=(
                replace(indexed, evidence=NormalizationEvidence.DECODER_REJECTION),
            ),
            evidence=(NormalizationEvidence.DECODER_REJECTION,),
        )
    with pytest.raises(ValueError, match="typed failure evidence"):
        replace(
            outcome,
            raw_event_outcomes=(
                replace(
                    indexed,
                    logical_source_key=LogicalSourceKey(
                        raw.feed_product.feed_product_id,
                        SourceEventId("different-source-event"),
                    ),
                ),
            ),
        )


@pytest.mark.parametrize(
    "disposition,evidence",
    (
        (RawEventDisposition.MATERIALIZED_NEW, None),
        (RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED, None),
        (RawEventDisposition.SOURCE_EVENT_CONFLICT, NormalizationEvidence.SOURCE_EVENT_CONFLICT),
        (
            RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
            NormalizationEvidence.FRAME_ATOMIC_ABORT,
        ),
    ),
)
def test_nonack_identified_route_cannot_use_non_rejection_dispositions(
    disposition: RawEventDisposition,
    evidence: NormalizationEvidence | None,
) -> None:
    raw, _fanout, _batch, outcome = _identified_preack_outcome()
    original = outcome.raw_event_outcomes[0]
    materialization = (
        MaterializationKey(
            outcome.normalization_run_id,
            original.observation_key,
            EventFamily.TRADE,
            2,
            PayloadType.TRADE,
        )
        if disposition is RawEventDisposition.MATERIALIZED_NEW
        else None
    )
    with pytest.raises(ValueError, match="acknowledged subscription attempt"):
        RawEventNormalizationOutcome(
            original.observation_key,
            original.normalization_scope_binding,
            disposition,
            LogicalSourceKey(
                raw.feed_product.feed_product_id,
                SourceEventId("preack-non-rejection"),
            ),
            materialization,
            evidence,
        )


def _mixed_ack_preack_rejected_outcome(
    *,
    include_unrelated_sol: bool = False,
) -> tuple[
    RawMarketDataRecord,
    CoverageFanoutProof,
    NormalizationOutcome,
    RoutedCoverageTarget,
]:
    raw_record, btc_spec, eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record(
        include_unrelated_sol=include_unrelated_sol
    )
    original_snapshots = {
        item.subscription_spec.subscription_spec_id: item
        for item in raw_record.subscription_attempt_snapshots
    }
    btc_snapshot = replace(
        original_snapshots[btc_spec.subscription_spec_id],
        attempt_status=SubscriptionAttemptStatus.SENT,
    )
    eth_snapshot = original_snapshots[eth_spec.subscription_spec_id]
    raw_record = replace(
        raw_record,
        subscription_attempt_snapshots=tuple(
            sorted(
                (
                    btc_snapshot
                    if item.subscription_spec.subscription_spec_id == btc_spec.subscription_spec_id
                    else item
                    for item in raw_record.subscription_attempt_snapshots
                ),
                key=lambda item: (
                    item.subscription_spec.subscription_spec_id.value,
                    item.subscription_attempt.subscription_attempt_id.value,
                ),
            )
        ),
    )
    plan_bindings = {
        item.subscription_spec_id: item for item in raw_record.subscription_plan.instrument_bindings
    }
    btc_plan_binding = plan_bindings[btc_spec.subscription_spec_id]
    rejection_target = ExactIdentifiedRejectionTarget(
        btc_snapshot,
        btc_plan_binding.source_selector,
        btc_instrument.canonical_instrument_id,
        btc_plan_binding.adapter_profile,
        "trade",
        2,
        "trade",
    )
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    direct_fanout = CoverageFanoutProof.exact_identified_rejections(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        rejection_targets=(rejection_target,),
    )
    scopes = {
        item.canonical_instrument_ids[0]: item
        for item in catalog.scopes
        if item.domain is CoverageDomain.SILVER_NORMALIZATION
    }
    btc_scope = scopes[btc_instrument.canonical_instrument_id]
    eth_scope = scopes[eth_instrument.canonical_instrument_id]
    run_id = NormalizationRunId("mixed-preack-normalization-run")
    epoch = CoverageEpochIdentity(
        btc_scope,
        raw_record.collector_run_id,
        0,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    primary = CoverageEvidence(
        CoverageEvidenceKind.NORMALIZATION_FAILURE,
        NormalizationFailureEvidenceSource(
            raw_record.raw_record_id,
            run_id,
            0,
            SourceEventId("mixed-preack-btc-source"),
            NormalizationFailureCategory.PROVENANCE_MISMATCH,
            btc_scope.coverage_scope_id,
        ),
        btc_scope,
        epoch,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    raw_binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=direct_fanout,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=direct_fanout,
        current_state_references=(),
        requests=(
            RequestedCoverageMutation(
                btc_scope,
                epoch,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                primary,
            ),
        ),
        raw_fanout_binding=raw_binding,
    )
    rejected = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(
            0,
            raw_record=raw_record,
            spec=btc_spec,
            attempt=btc_snapshot.subscription_attempt,
            scope=btc_scope,
        ),
        RawEventDisposition.REJECTED,
        LogicalSourceKey(
            raw_record.feed_product.feed_product_id,
            SourceEventId("mixed-preack-btc-source"),
        ),
        evidence=NormalizationEvidence.PROVENANCE_MISMATCH,
    )
    eth_source_id = SourceEventId("mixed-ack-eth-source")
    abort = FrameAtomicAbortEvidenceSource(
        raw_record.raw_record_id,
        run_id,
        1,
        eth_source_id,
        eth_scope.coverage_scope_id,
        (primary.coverage_evidence_id,),
    )
    aborted = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 1),
        _outcome_scope_binding(
            1,
            raw_record=raw_record,
            spec=eth_spec,
            attempt=eth_snapshot.subscription_attempt,
            scope=eth_scope,
        ),
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        LogicalSourceKey(raw_record.feed_product.feed_product_id, eth_source_id),
        evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )
    lineage = NormalizationCoverageLineage(
        batch,
        (abort,),
        raw_binding,
    )
    outcome = NormalizationOutcome(
        run_id,
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        2,
        (rejected, aborted),
        (),
        evidence=tuple(
            sorted(
                {
                    NormalizationEvidence.PROVENANCE_MISMATCH,
                    NormalizationEvidence.FRAME_ATOMIC_ABORT,
                },
                key=lambda item: item.value,
            )
        ),
        coverage_lineage=lineage,
    )
    acknowledged_target = RoutedCoverageTarget(
        eth_snapshot,
        eth_instrument.canonical_instrument_id,
        "trade",
        2,
        "trade",
    )
    return raw_record, direct_fanout, outcome, acknowledged_target


def test_mixed_ack_preack_frame_degrades_only_rejected_scope_and_keeps_abort_atomic() -> None:
    _raw, direct_fanout, outcome, _acknowledged_target = _mixed_ack_preack_rejected_outcome()
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)

    assert len(direct_fanout.target_scopes) == 1
    assert len(lineage.coverage_mutation_batch.resulting_state_references) == 1
    assert outcome.raw_event_outcomes[0].disposition is RawEventDisposition.REJECTED
    assert outcome.raw_event_outcomes[1].disposition is (
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED
    )
    assert len(lineage.frame_atomic_abort_evidence) == 1
    assert outcome.committed_materialization_keys == ()


@pytest.mark.parametrize(
    "evidence_kind",
    (
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
    ),
)
def test_identified_rejection_outcome_sink_binding_requires_complete_mixed_union(
    evidence_kind: CoverageEvidenceKind,
) -> None:
    raw, direct_fanout, outcome, acknowledged_target = _mixed_ack_preack_rejected_outcome()
    full_fanout = CoverageFanoutProof.exact_identified_rejections(
        plan=raw.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw.subscription_plan),
        rejection_targets=direct_fanout.identified_rejection_targets,
        acknowledged_targets=(acknowledged_target,),
    )
    batch = _outcome_sink_failure_batch(
        raw_record=raw,
        outcome=outcome,
        fanout=full_fanout,
        kind=evidence_kind,
        current_state_references=(),
    )
    binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=outcome,
        coverage_mutation_batch=batch,
    )

    assert binding.evidence_kind is evidence_kind
    assert {item.coverage_scope_id for item in full_fanout.target_scopes} == {
        item.normalization_scope_binding.coverage_scope_id for item in outcome.raw_event_outcomes
    }
    assert full_fanout.selected_attempt_ids == tuple(
        sorted(
            {
                item.normalization_scope_binding.subscription_attempt_id
                for item in outcome.raw_event_outcomes
            },
            key=lambda item: item.value,
        )
    )

    subset_batch = _outcome_sink_failure_batch(
        raw_record=raw,
        outcome=outcome,
        fanout=direct_fanout,
        kind=evidence_kind,
        current_state_references=(),
    )
    with pytest.raises(ValueError, match="decoded scope union"):
        NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
            normalization_outcome=outcome,
            coverage_mutation_batch=subset_batch,
        )


def test_identified_rejection_sink_union_rejects_superset_duplicate_and_foreign_attempt() -> None:
    raw, direct_fanout, outcome, acknowledged_target = _mixed_ack_preack_rejected_outcome(
        include_unrelated_sol=True
    )
    outcome_spec_ids = {
        item.normalization_scope_binding.subscription_spec_id for item in outcome.raw_event_outcomes
    }
    unrelated_snapshot = next(
        item
        for item in raw.subscription_attempt_snapshots
        if item.subscription_spec.subscription_spec_id not in outcome_spec_ids
    )
    unrelated_binding = next(
        item
        for item in raw.subscription_plan.instrument_bindings
        if item.subscription_spec_id == unrelated_snapshot.subscription_spec.subscription_spec_id
    )
    unrelated_target = RoutedCoverageTarget(
        unrelated_snapshot,
        unrelated_binding.canonical_instrument_id,
        "trade",
        2,
        "trade",
    )
    superset_fanout = CoverageFanoutProof.exact_identified_rejections(
        plan=raw.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw.subscription_plan),
        rejection_targets=direct_fanout.identified_rejection_targets,
        acknowledged_targets=(acknowledged_target, unrelated_target),
    )
    superset_batch = _outcome_sink_failure_batch(
        raw_record=raw,
        outcome=outcome,
        fanout=superset_fanout,
        current_state_references=(),
    )
    with pytest.raises(ValueError, match="decoded scope union"):
        NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
            normalization_outcome=outcome,
            coverage_mutation_batch=superset_batch,
        )

    with pytest.raises(ValueError, match="unique by exact Silver scope"):
        CoverageFanoutProof.exact_identified_rejections(
            plan=raw.subscription_plan,
            catalog=CoverageTargetCatalog.from_subscription_plan(raw.subscription_plan),
            rejection_targets=direct_fanout.identified_rejection_targets,
            acknowledged_targets=(acknowledged_target, acknowledged_target),
        )

    foreign_snapshot = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(
            acknowledged_target.acknowledged_snapshot.subscription_attempt.connection_session,
            acknowledged_target.acknowledged_snapshot.subscription_spec,
            1,
        ),
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    foreign_target = replace(
        acknowledged_target,
        acknowledged_snapshot=foreign_snapshot,
    )
    foreign_fanout = CoverageFanoutProof.exact_identified_rejections(
        plan=raw.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw.subscription_plan),
        rejection_targets=direct_fanout.identified_rejection_targets,
        acknowledged_targets=(foreign_target,),
    )
    with pytest.raises(ValueError, match="raw snapshot"):
        _outcome_sink_failure_batch(
            raw_record=raw,
            outcome=outcome,
            fanout=foreign_fanout,
            current_state_references=(),
        )


_RECEIVED_TIME = datetime(2026, 8, 26, 12, 0, 0, 123456, tzinfo=UTC)
_EVENT_TIME = datetime(2026, 8, 26, 11, 59, 59, 654321, tzinfo=UTC)


def test_normalization_outcome_id_is_the_exact_lower_layer_reexport() -> None:
    assert NormalizationOutcomeId is ProvenanceNormalizationOutcomeId


def _expected_identifier(*components: object) -> str:
    return json.dumps(components, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _assert_bounded_exception_surface_excludes(error: BaseException, *markers: str) -> None:
    stack: list[object] = [error]
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        representation = repr(current)
        for marker in markers:
            assert marker not in representation
        if isinstance(current, BaseException):
            stack.extend(current.args)
            if current.__cause__ is not None:
                stack.append(current.__cause__)
            if current.__context__ is not None:
                stack.append(current.__context__)
            stack.extend(tuple(vars(current).values()))
            for attribute in ("object", "doc"):
                retained = getattr(current, attribute, None)
                if retained is not None:
                    stack.append(retained)
        elif type(current) is dict:
            stack.extend(current.keys())
            stack.extend(current.values())
        elif type(current) in (list, tuple, set, frozenset):
            stack.extend(
                cast(list[object] | tuple[object, ...] | set[object] | frozenset[object], current)
            )


def _instrument(*, native_symbol: str = "BTCUSDT") -> Instrument:
    return Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id="BTCUSDT",
        native_symbol=native_symbol,
    )


def _adapter_binding() -> AdapterFeedBinding:
    return AdapterFeedBinding(
        adapter_code="binance-spot-json-trade-v1",
        feed_product=BINANCE_MAINNET_SPOT_JSON_STREAMS,
        venue=Venue.BINANCE,
        event_activation_requirement=EventActivationRequirement.ACKNOWLEDGED,
    )


def _wire_spec(symbol: str = "BTCUSDT", *, request_id: int = 1) -> SubscriptionSpecIdentity:
    return SubscriptionSpecIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        wire_method="SUBSCRIBE",
        wire_subscription_type="@trade",
        wire_parameters=(
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.BINANCE_REQUEST_ID,
                request_id,
            ),
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.BINANCE_STREAMS,
                (f"{symbol.lower()}@trade",),
            ),
        ),
    )


def _raw_record(
    *,
    adapter_binding: AdapterFeedBinding | None = None,
    attempt_ordinal: int = 0,
    attempt_status: SubscriptionAttemptStatus = SubscriptionAttemptStatus.ACKNOWLEDGED,
) -> tuple[RawMarketDataRecord, SubscriptionSpecIdentity, SubscriptionAttemptIdentity]:
    adapter_binding = adapter_binding or _adapter_binding()
    spec = _wire_spec()
    run_id = CollectorRunId("collector-run-fixture")
    session = ConnectionSessionIdentity(
        collector_run_id=run_id,
        connection_ordinal=0,
    )
    plan = SubscriptionPlanIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        adapter_feed_binding_id=adapter_binding.adapter_feed_binding_id,
        subscription_specs=(spec,),
        instrument_bindings=(
            InstrumentSubscriptionBinding(
                instrument=_instrument(),
                source_selector=PublicSourceSelector(
                    PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                    "btcusdt@trade",
                ),
                subscription_spec=spec,
                adapter_profile=adapter_binding.adapter_code,
            ),
        ),
        normalization_bindings=(
            NormalizationBinding(
                subscription_spec_id=spec.subscription_spec_id,
                adapter_profile=adapter_binding.adapter_code,
                event_family="trade",
                event_family_schema_version=2,
                payload_type="trade",
            ),
        ),
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.BINANCE_PRODUCTION_SPOT,
            ),
        ),
    )
    attempt = SubscriptionAttemptIdentity(
        connection_session=session,
        subscription_spec=spec,
        attempt_ordinal=attempt_ordinal,
    )
    raw_record = RawMarketDataRecord(
        feed_product=BINANCE_MAINNET_SPOT_JSON_STREAMS,
        collector_run_id=run_id,
        connection_session=session,
        subscription_plan=plan,
        subscription_attempt_snapshots=(
            SubscriptionAttemptSnapshot(
                subscription_attempt=attempt,
                attempt_status=attempt_status,
            ),
        ),
        ingress_ordinal=4,
        frame_kind=FrameKind.TEXT,
        application_message_bytes=b'{"fixture":"trade"}',
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987654321,
        collector_version="collector-v1",
        collector_commit="collector-commit-fixture",
    )
    return raw_record, spec, attempt


def _multi_spec_raw_record(
    *,
    include_unrelated_sol: bool = False,
) -> tuple[
    RawMarketDataRecord,
    SubscriptionSpecIdentity,
    SubscriptionSpecIdentity,
    Instrument,
    Instrument,
]:
    adapter_binding = _adapter_binding()
    btc_spec = _wire_spec("BTCUSDT", request_id=1)
    eth_spec = _wire_spec("ETHUSDT", request_id=2)
    sol_spec = _wire_spec("SOLUSDT", request_id=3)
    selected_specs = (
        (btc_spec, eth_spec, sol_spec) if include_unrelated_sol else (btc_spec, eth_spec)
    )
    specs = tuple(sorted(selected_specs, key=lambda item: item.subscription_spec_id.value))
    btc_instrument = _instrument()
    eth_instrument = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="ETH",
        quote_asset="USDT",
        venue_market_id="ETHUSDT",
        native_symbol="ETHUSDT",
    )
    sol_instrument = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="SOL",
        quote_asset="USDT",
        venue_market_id="SOLUSDT",
        native_symbol="SOLUSDT",
    )
    instrument_by_spec_id = {
        btc_spec.subscription_spec_id: btc_instrument,
        eth_spec.subscription_spec_id: eth_instrument,
        sol_spec.subscription_spec_id: sol_instrument,
    }
    selector_by_spec_id = {
        btc_spec.subscription_spec_id: "btcusdt@trade",
        eth_spec.subscription_spec_id: "ethusdt@trade",
        sol_spec.subscription_spec_id: "solusdt@trade",
    }
    instrument_bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=instrument_by_spec_id[spec.subscription_spec_id],
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                        selector_by_spec_id[spec.subscription_spec_id],
                    ),
                    subscription_spec=spec,
                    adapter_profile=adapter_binding.adapter_code,
                )
                for spec in specs
            ),
            key=lambda item: item.canonical_components(),
        )
    )
    normalization_bindings = tuple(
        sorted(
            (
                NormalizationBinding(
                    subscription_spec_id=spec.subscription_spec_id,
                    adapter_profile=adapter_binding.adapter_code,
                    event_family="trade",
                    event_family_schema_version=2,
                    payload_type="trade",
                )
                for spec in specs
            ),
            key=lambda item: item.subscription_spec_id.value,
        )
    )
    plan = SubscriptionPlanIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        adapter_feed_binding_id=adapter_binding.adapter_feed_binding_id,
        subscription_specs=specs,
        instrument_bindings=instrument_bindings,
        normalization_bindings=normalization_bindings,
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.BINANCE_PRODUCTION_SPOT,
            ),
        ),
    )
    run_id = CollectorRunId("multi-spec-collector-run-fixture")
    session = ConnectionSessionIdentity(run_id, 0)
    snapshots = tuple(
        sorted(
            (
                SubscriptionAttemptSnapshot(
                    SubscriptionAttemptIdentity(session, spec, 0),
                    SubscriptionAttemptStatus.ACKNOWLEDGED,
                )
                for spec in specs
            ),
            key=lambda item: (
                item.subscription_attempt.subscription_spec.subscription_spec_id.value,
                item.subscription_attempt.subscription_attempt_id.value,
            ),
        )
    )
    raw_record = RawMarketDataRecord(
        feed_product=BINANCE_MAINNET_SPOT_JSON_STREAMS,
        collector_run_id=run_id,
        connection_session=session,
        subscription_plan=plan,
        subscription_attempt_snapshots=snapshots,
        ingress_ordinal=0,
        frame_kind=FrameKind.TEXT,
        application_message_bytes=b'{"fixture":"multi-spec-trade"}',
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987654321,
        collector_version="collector-v1",
        collector_commit="collector-commit-fixture",
    )
    return raw_record, btc_spec, eth_spec, btc_instrument, eth_instrument


def _resolved_metadata(
    *,
    instrument: Instrument | None = None,
    raw_received_time: datetime = _RECEIVED_TIME,
    event_time: datetime = _EVENT_TIME,
) -> ResolvedInstrumentMetadata:
    selected_instrument = instrument or _instrument()
    specification = InstrumentSpecification(
        instrument=selected_instrument,
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    observation = InstrumentMetadataObservation(
        canonical_instrument_id=selected_instrument.canonical_instrument_id,
        instrument_specification_id=specification.instrument_specification_id,
        metadata_authority_id=MetadataAuthorityId("binance-public-metadata"),
        effective_from=datetime(2026, 8, 1, tzinfo=UTC),
        effective_basis=MetadataEffectiveBasis.SOURCE_DECLARED,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    return select_instrument_metadata(
        canonical_instrument_id=selected_instrument.canonical_instrument_id,
        metadata_authority_id=observation.metadata_authority_id,
        event_time=event_time,
        raw_received_time=raw_received_time,
        specifications=(specification,),
        observations=(observation,),
    )


def _coverage(
    *,
    spec: SubscriptionSpecIdentity,
    instrument: Instrument | None = None,
    collector_run_id: CollectorRunId | None = None,
) -> EventCoverage:
    selected_instrument = instrument or _instrument()
    selected_collector_run_id = collector_run_id or CollectorRunId("collector-run-fixture")
    adapter = _adapter_binding()
    session = ConnectionSessionIdentity(selected_collector_run_id, 0)
    plan = SubscriptionPlanIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        adapter_feed_binding_id=adapter.adapter_feed_binding_id,
        subscription_specs=(spec,),
        instrument_bindings=(
            InstrumentSubscriptionBinding(
                instrument=selected_instrument,
                source_selector=PublicSourceSelector(
                    PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                    f"{selected_instrument.native_symbol.lower()}@trade",
                ),
                subscription_spec=spec,
                adapter_profile=adapter.adapter_code,
            ),
        ),
        normalization_bindings=(
            NormalizationBinding(
                subscription_spec_id=spec.subscription_spec_id,
                adapter_profile=adapter.adapter_code,
                event_family="trade",
                event_family_schema_version=2,
                payload_type="trade",
            ),
        ),
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.BINANCE_PRODUCTION_SPOT,
            ),
        ),
    )
    snapshot = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(session, spec, 0),
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)

    def committed_state(domain: CoverageDomain) -> CommittedCoverageState:
        fanout = CoverageFanoutProof.acknowledged_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(snapshot,),
            selected_attempt_ids=(snapshot.subscription_attempt.subscription_attempt_id,),
            domain=domain,
        )
        assert len(fanout.target_scopes) == 1
        scope = fanout.target_scopes[0]
        activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
        epoch = CoverageEpochIdentity(
            scope=scope,
            collector_run_id=selected_collector_run_id,
            epoch_ordinal=0,
            activation_time=activation_time,
            activation_monotonic_ns=100,
        )
        evidence = CoverageEvidence(
            kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
            source=InitialActivationEvidenceSource(session, (snapshot,)),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time,
            observed_monotonic_ns=100,
        )
        batch = prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=(
                RequestedCoverageMutation(
                    scope,
                    epoch,
                    CoverageStatus.COMPLETE,
                    InitialCoverageReason.INITIAL_ACTIVATION,
                    CoverageReason.INITIAL_SCOPE,
                    evidence,
                ),
            ),
        )
        committed_references = batch.verify_compare_and_swap(())
        acceptance = CoverageCommitAcceptance.after_compare_and_swap(
            batch=batch,
            committed_state_references=committed_references,
        )
        return CommittedCoverageState.from_commit(
            state_reference=committed_references[0],
            commit_acceptance=acceptance,
        )

    return EventCoverage(
        bronze_ingress=committed_state(CoverageDomain.BRONZE_INGRESS),
        silver_normalization=committed_state(CoverageDomain.SILVER_NORMALIZATION),
    )


def _source_provenance() -> SourceProvenance:
    return SourceProvenance(
        source_event_id=SourceEventId('["binance-spot-trade-v1","BTCUSDT",42]'),
        source_transaction_id=None,
        source_time_facts=(
            SourceTimeFact(
                role=SourceTimeRole.TRADE_EXECUTION_TIME,
                raw_value=1787745599654321,
                unit=SourceTimeUnit.EPOCH_MICROSECONDS,
            ),
        ),
        source_sequence_ranges=(),
    )


def _context(
    *,
    attempt_status: SubscriptionAttemptStatus = SubscriptionAttemptStatus.ACKNOWLEDGED,
    source_provenance: SourceProvenance | None = None,
    resolved_instrument_metadata: ResolvedInstrumentMetadata | None = None,
    event_coverage: EventCoverage | None = None,
) -> NormalizationContext:
    raw_record, spec, attempt = _raw_record(attempt_status=attempt_status)
    decoded = DecodedWirePayloadContext(
        raw_record_id=raw_record.raw_record_id,
        decoded_event_count=1,
        event_bindings=(
            DecodedEventSubscriptionBinding(
                raw_event_index=0,
                subscription_spec=spec,
                subscription_attempt=attempt,
            ),
        ),
    )
    return NormalizationContext.from_raw_record(
        raw_record=raw_record,
        adapter_feed_binding=_adapter_binding(),
        decoded_wire_payload=decoded,
        raw_event_index=0,
        source_provenance=source_provenance or _source_provenance(),
        normalization_run_id=NormalizationRunId("normalization-run-fixture"),
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        resolved_instrument_metadata=resolved_instrument_metadata or _resolved_metadata(),
        event_coverage=event_coverage or _coverage(spec=spec),
    )


def _hyperliquid_context(attempt_status: SubscriptionAttemptStatus) -> NormalizationContext:
    instrument = Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDC",
        venue_market_id="BTC",
        native_symbol="BTC",
    )
    adapter = AdapterFeedBinding(
        adapter_code="hyperliquid-trades-v1",
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        venue=Venue.HYPERLIQUID,
        event_activation_requirement=EventActivationRequirement.ACKNOWLEDGED,
    )
    spec = SubscriptionSpecIdentity(
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        wire_method="subscribe",
        wire_subscription_type="trades",
        wire_parameters=(
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.HYPERLIQUID_COIN,
                "BTC",
            ),
        ),
    )
    run_id = CollectorRunId("hyperliquid-collector-run-fixture")
    session = ConnectionSessionIdentity(run_id, 0)
    plan = SubscriptionPlanIdentity(
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        adapter_feed_binding_id=adapter.adapter_feed_binding_id,
        subscription_specs=(spec,),
        instrument_bindings=(
            InstrumentSubscriptionBinding(
                instrument=instrument,
                source_selector=PublicSourceSelector(
                    PublicSourceSelectorKind.HYPERLIQUID_COIN,
                    "BTC",
                ),
                subscription_spec=spec,
                adapter_profile=adapter.adapter_code,
            ),
        ),
        normalization_bindings=(
            NormalizationBinding(
                spec.subscription_spec_id,
                "hyperliquid-trades-v1",
                "trade",
                2,
                "trade",
            ),
        ),
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.HYPERLIQUID_PRODUCTION_MAINNET,
            ),
        ),
    )
    attempt = SubscriptionAttemptIdentity(session, spec, 0)
    raw_record = RawMarketDataRecord(
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        collector_run_id=run_id,
        connection_session=session,
        subscription_plan=plan,
        subscription_attempt_snapshots=(SubscriptionAttemptSnapshot(attempt, attempt_status),),
        ingress_ordinal=0,
        frame_kind=FrameKind.TEXT,
        application_message_bytes=b'{"channel":"trades"}',
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987654321,
        collector_version="collector-v1",
        collector_commit="collector-commit-fixture",
    )
    specification = InstrumentSpecification(
        instrument=instrument,
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.LINEAR,
        settlement_asset="USDC",
    )
    observation = InstrumentMetadataObservation(
        canonical_instrument_id=instrument.canonical_instrument_id,
        instrument_specification_id=specification.instrument_specification_id,
        metadata_authority_id=MetadataAuthorityId("hyperliquid-public-metadata"),
        effective_from=datetime(2026, 8, 1, tzinfo=UTC),
        effective_basis=MetadataEffectiveBasis.SOURCE_DECLARED,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    resolved = select_instrument_metadata(
        canonical_instrument_id=instrument.canonical_instrument_id,
        metadata_authority_id=observation.metadata_authority_id,
        event_time=_EVENT_TIME,
        raw_received_time=_RECEIVED_TIME,
        specifications=(specification,),
        observations=(observation,),
    )

    snapshot = SubscriptionAttemptSnapshot(
        attempt,
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)

    def committed_state(domain: CoverageDomain) -> CommittedCoverageState:
        fanout = CoverageFanoutProof.acknowledged_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(snapshot,),
            selected_attempt_ids=(snapshot.subscription_attempt.subscription_attempt_id,),
            domain=domain,
        )
        assert len(fanout.target_scopes) == 1
        scope = fanout.target_scopes[0]
        activation = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
        epoch = CoverageEpochIdentity(scope, run_id, 0, activation, 100)
        evidence = CoverageEvidence(
            kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
            source=InitialActivationEvidenceSource(session, (snapshot,)),
            scope=scope,
            epoch=epoch,
            observed_at=activation,
            observed_monotonic_ns=100,
        )
        batch = prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=(
                RequestedCoverageMutation(
                    scope,
                    epoch,
                    CoverageStatus.COMPLETE,
                    InitialCoverageReason.INITIAL_ACTIVATION,
                    CoverageReason.INITIAL_SCOPE,
                    evidence,
                ),
            ),
        )
        committed_references = batch.verify_compare_and_swap(())
        acceptance = CoverageCommitAcceptance.after_compare_and_swap(
            batch=batch,
            committed_state_references=committed_references,
        )
        return CommittedCoverageState.from_commit(
            state_reference=committed_references[0],
            commit_acceptance=acceptance,
        )

    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, attempt),),
    )
    return NormalizationContext.from_raw_record(
        raw_record=raw_record,
        adapter_feed_binding=adapter,
        decoded_wire_payload=decoded,
        raw_event_index=0,
        source_provenance=SourceProvenance(
            SourceEventId('["hyperliquid-trade-v1",1787745599654,"BTC",42]'),
            SourceTransactionId("public-transaction-fixture"),
            (
                SourceTimeFact(
                    SourceTimeRole.TRADE_EXECUTION_TIME,
                    1_787_745_599_654_321,
                    SourceTimeUnit.EPOCH_MICROSECONDS,
                ),
            ),
            (),
        ),
        normalization_run_id=NormalizationRunId("normalization-run-fixture"),
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        resolved_instrument_metadata=resolved,
        event_coverage=EventCoverage(
            committed_state(CoverageDomain.BRONZE_INGRESS),
            committed_state(CoverageDomain.SILVER_NORMALIZATION),
        ),
    )


def _trade() -> TradeEvent:
    return TradeEvent(
        price=Decimal("65000.125"),
        quantity=Decimal("0.00025"),
        aggressor_side=AggressorSide.BUY,
    )


def _outcome_keys(index: int = 0) -> tuple[LogicalSourceKey, ObservationKey, MaterializationKey]:
    raw_record_id = _raw_record()[0].raw_record_id
    logical = LogicalSourceKey(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        source_event_id=SourceEventId("source-fixture"),
    )
    observation = ObservationKey(
        raw_record_id=raw_record_id,
        raw_event_index=index,
    )
    materialization = MaterializationKey(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        observation_key=observation,
        event_family=EventFamily.TRADE,
        event_family_schema_version=2,
        payload_type=PayloadType.TRADE,
    )
    return logical, observation, materialization


def _normalization_scope(
    raw_record: RawMarketDataRecord,
    spec: SubscriptionSpecIdentity,
    *,
    canonical_instrument_id: str | None = None,
    event_family: str = "trade",
    event_family_schema_version: int = 2,
    payload_type: str = "trade",
) -> CoverageScope:
    return CoverageScope(
        domain=CoverageDomain.SILVER_NORMALIZATION,
        feed_product_id=raw_record.feed_product.feed_product_id,
        subscription_spec_ids=(spec.subscription_spec_id,),
        canonical_instrument_ids=(
            canonical_instrument_id or _instrument().canonical_instrument_id,
        ),
        event_family=event_family,
        event_family_schema_version=event_family_schema_version,
        payload_type=payload_type,
    )


def _outcome_scope_binding(
    raw_event_index: int,
    *,
    raw_record: RawMarketDataRecord | None = None,
    spec: SubscriptionSpecIdentity | None = None,
    attempt: SubscriptionAttemptIdentity | None = None,
    scope: CoverageScope | None = None,
) -> RawEventNormalizationScopeBinding:
    if raw_record is None:
        raw_record, default_spec, default_attempt = _raw_record()
        spec = spec or default_spec
        attempt = attempt or default_attempt
    else:
        spec = spec or raw_record.subscription_plan.subscription_specs[0]
        attempt = attempt or raw_record.subscription_attempt_snapshots[0].subscription_attempt
    assert spec is not None
    assert attempt is not None
    event_bindings = tuple(
        DecodedEventSubscriptionBinding(index, spec, attempt)
        for index in range(raw_event_index + 1)
    )
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        raw_event_index + 1,
        event_bindings,
    )
    plan_binding = next(
        binding
        for binding in raw_record.subscription_plan.instrument_bindings
        if binding.subscription_spec_id == spec.subscription_spec_id
    )
    return RawEventNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        decoded_wire_payload=decoded,
        raw_event_index=raw_event_index,
        source_selector=plan_binding.source_selector,
        coverage_scope=scope or _normalization_scope(raw_record, spec),
    )


def _raw_record_id() -> RawRecordId:
    return _raw_record()[0].raw_record_id


def _preindex_scope_binding(
    raw_record: RawMarketDataRecord | None = None,
    *,
    scope: CoverageScope | None = None,
) -> RawFrameNormalizationScopeBinding:
    if raw_record is None:
        raw_record, spec, _attempt = _raw_record()
    else:
        spec = raw_record.subscription_plan.subscription_specs[0]
    return RawFrameNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        coverage_scope=scope or _normalization_scope(raw_record, spec),
    )


def _normalization_failure_transition(
    *,
    normalization_run_id: NormalizationRunId,
    raw_event_index: int | None,
    category: NormalizationFailureCategory,
    source_event_id: SourceEventId | None = None,
    source_identity_established: bool = True,
    canonical_instrument_id: str | None = None,
) -> tuple[RawMarketDataRecord, CoverageScope, CoverageTransition]:
    raw_record, spec, attempt = _raw_record()
    scope = _normalization_scope(
        raw_record,
        spec,
        canonical_instrument_id=canonical_instrument_id,
    )
    if raw_event_index is not None and source_event_id is None and source_identity_established:
        source_event_id = SourceEventId("source-fixture")
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        raw_record.collector_run_id,
        0,
        activation_time,
        100,
    )
    initial = CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=CoverageStatus.COMPLETE,
        initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
        initial_evidence=CoverageEvidence(
            kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
            source=InitialActivationEvidenceSource(
                raw_record.connection_session,
                (
                    SubscriptionAttemptSnapshot(
                        attempt,
                        SubscriptionAttemptStatus.ACKNOWLEDGED,
                    ),
                ),
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time,
            observed_monotonic_ns=100,
        ),
    )
    transition, _ = reduce_coverage(
        initial,
        RequestedCoverageTransition(
            scope,
            epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
        ),
        CoverageEvidence(
            kind=CoverageEvidenceKind.NORMALIZATION_FAILURE,
            source=NormalizationFailureEvidenceSource(
                raw_record.raw_record_id,
                normalization_run_id,
                raw_event_index,
                source_event_id,
                category,
                scope.coverage_scope_id,
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time + timedelta(seconds=1),
            observed_monotonic_ns=101,
        ),
    )
    return raw_record, scope, transition


def _source_conflict_transition(
    *,
    source_event_id: SourceEventId,
    raw_event_index: int,
    scope_spec: SubscriptionSpecIdentity | None = None,
    canonical_instrument_id: str | None = None,
) -> tuple[RawMarketDataRecord, CoverageScope, CoverageTransition]:
    raw_record, spec, attempt = _raw_record()
    selected_spec = scope_spec or spec
    selected_attempt = (
        attempt
        if selected_spec.subscription_spec_id == spec.subscription_spec_id
        else SubscriptionAttemptIdentity(
            raw_record.connection_session,
            selected_spec,
            0,
        )
    )
    scope = _normalization_scope(
        raw_record,
        selected_spec,
        canonical_instrument_id=canonical_instrument_id,
    )
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        raw_record.collector_run_id,
        0,
        activation_time,
        100,
    )
    initial = CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=CoverageStatus.COMPLETE,
        initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
        initial_evidence=CoverageEvidence(
            kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
            source=InitialActivationEvidenceSource(
                raw_record.connection_session,
                (
                    SubscriptionAttemptSnapshot(
                        selected_attempt,
                        SubscriptionAttemptStatus.ACKNOWLEDGED,
                    ),
                ),
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time,
            observed_monotonic_ns=100,
        ),
    )
    transition, _ = reduce_coverage(
        initial,
        RequestedCoverageTransition(
            scope,
            epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            CoverageReason.SOURCE_EVENT_CONFLICT,
        ),
        CoverageEvidence(
            kind=CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
            source=SourceEventConflictEvidenceSource(
                raw_record.feed_product.feed_product_id,
                source_event_id,
                raw_record.raw_record_id,
                raw_event_index,
                scope.coverage_scope_id,
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time + timedelta(seconds=1),
            observed_monotonic_ns=101,
        ),
    )
    return raw_record, scope, transition


def _unrelated_coverage_transition(
    kind: CoverageEvidenceKind,
) -> tuple[RawMarketDataRecord, CoverageTransition]:
    raw_record, spec, attempt = _raw_record()
    domain = (
        CoverageDomain.SILVER_NORMALIZATION
        if kind is CoverageEvidenceKind.SOURCE_SEQUENCE
        else CoverageDomain.BRONZE_INGRESS
    )
    scope = CoverageScope(
        domain=domain,
        feed_product_id=raw_record.feed_product.feed_product_id,
        subscription_spec_ids=(spec.subscription_spec_id,),
        canonical_instrument_ids=(_instrument().canonical_instrument_id,),
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(scope, raw_record.collector_run_id, 0, activation_time, 100)
    initial = CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=CoverageStatus.COMPLETE,
        initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
        initial_evidence=CoverageEvidence(
            CoverageEvidenceKind.INITIAL_ACTIVATION,
            InitialActivationEvidenceSource(
                raw_record.connection_session,
                (SubscriptionAttemptSnapshot(attempt, SubscriptionAttemptStatus.ACKNOWLEDGED),),
            ),
            scope,
            epoch,
            activation_time,
            100,
        ),
    )
    source: CoverageEvidenceSource
    if kind is CoverageEvidenceKind.TRANSPORT_FAILURE:
        reason = CoverageReason.TRANSPORT_AMBIGUITY
        status = CoverageStatus.UNCERTAIN
        source = TransportAmbiguityEvidenceSource(
            raw_record.feed_product.feed_product_id,
            raw_record.connection_session,
        )
    elif kind is CoverageEvidenceKind.RECONNECT:
        reason = CoverageReason.TRANSPORT_AMBIGUITY
        status = CoverageStatus.UNCERTAIN
        source = ReconnectEvidenceSource(
            raw_record.feed_product.feed_product_id,
            raw_record.connection_session,
        )
    elif kind is CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY:
        reason = CoverageReason.RAW_ACCEPTANCE_UNCERTAIN
        status = CoverageStatus.UNCERTAIN
        source = RawRecordEvidenceSource(raw_record.raw_record_id, scope.coverage_scope_id)
    elif kind is CoverageEvidenceKind.SOURCE_SEQUENCE:
        reason = CoverageReason.SOURCE_SEQUENCE_BREAK
        status = CoverageStatus.CONFIRMED_INCOMPLETE
        source = SourceSequenceBreakEvidenceSource(
            raw_record.feed_product.feed_product_id,
            SourceSequenceRange(SourceSequenceRole.EVENT_SEQUENCE, "public-trades", 1, 2),
            scope.coverage_scope_id,
        )
    else:  # pragma: no cover - helper is closed by parameterized tests
        raise AssertionError("unsupported unrelated transition fixture")
    transition, _ = reduce_coverage(
        initial,
        RequestedCoverageTransition(
            scope,
            epoch,
            CoverageStatus.COMPLETE,
            1,
            status,
            reason,
        ),
        CoverageEvidence(
            kind,
            source,
            scope,
            epoch,
            activation_time + timedelta(seconds=1),
            101,
        ),
    )
    return raw_record, transition


def _prepared_batch_for_transition(
    transition: CoverageTransition,
) -> CoverageMutationBatch:
    raw_record, _spec, attempt = _raw_record()
    snapshot = SubscriptionAttemptSnapshot(
        attempt,
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    initialization = CoverageInitialization(
        transition.scope,
        transition.epoch,
        CoverageStatus.COMPLETE,
        InitialCoverageReason.INITIAL_ACTIVATION,
        CoverageEvidence(
            CoverageEvidenceKind.INITIAL_ACTIVATION,
            InitialActivationEvidenceSource(raw_record.connection_session, (snapshot,)),
            transition.scope,
            transition.epoch,
            transition.epoch.activation_time,
            transition.epoch.activation_monotonic_ns,
        ),
    )
    current = CoverageStateReference.from_initialization(initialization)
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    source = transition.evidence.source
    if type(source) is NormalizationFailureEvidenceSource and source.raw_event_index is None:
        fanout = CoverageFanoutProof.all_possibly_active(
            plan=raw_record.subscription_plan,
            catalog=catalog,
            complete_snapshots=(snapshot,),
            domain=CoverageDomain.SILVER_NORMALIZATION,
            event_family=transition.scope.event_family,
            event_family_schema_version=transition.scope.event_family_schema_version,
            payload_type=transition.scope.payload_type,
        )
    else:
        fanout = CoverageFanoutProof.exact_routed_event(
            plan=raw_record.subscription_plan,
            catalog=catalog,
            acknowledged_snapshot=snapshot,
            canonical_instrument_id=transition.scope.canonical_instrument_ids[0],
            event_family=transition.scope.event_family,
            event_family_schema_version=transition.scope.event_family_schema_version,
            payload_type=transition.scope.payload_type,
        )
    initial_reason = (
        InitialCoverageReason.SOURCE_EVENT_CONFLICT
        if transition.evidence.kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT
        else InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(current,),
        requests=(
            RequestedCoverageMutation(
                transition.scope,
                transition.epoch,
                transition.new_status,
                initial_reason,
                transition.reason,
                transition.evidence,
            ),
        ),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=fanout,
        ),
    )
    assert batch.transitions == (transition,)
    return batch


def _typed_coverage_lineage(
    transition: CoverageTransition,
    aborts: tuple[FrameAtomicAbortEvidenceSource, ...] = (),
) -> NormalizationCoverageLineage:
    ordered_aborts = tuple(
        sorted(aborts, key=lambda item: item.frame_atomic_abort_evidence_id.value)
    )
    conflict_bindings = (
        (
            NormalizationSourceConflictBinding(
                transition.evidence,
                NormalizationRunId("normalization-fixture"),
            ),
        )
        if transition.evidence.kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT
        else ()
    )
    batch = _prepared_batch_for_transition(transition)
    raw_fanout_binding = batch.raw_fanout_binding
    assert type(raw_fanout_binding) is RawCoverageFanoutBinding
    return NormalizationCoverageLineage(
        coverage_mutation_batch=batch,
        frame_atomic_abort_evidence=ordered_aborts,
        raw_fanout_binding=raw_fanout_binding,
        source_conflict_bindings=conflict_bindings,
    )


def _typed_rejected_frame_with_abort() -> tuple[
    NormalizationOutcome,
    CoverageTransition,
    FrameAtomicAbortEvidenceSource,
]:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        source_event_id=SourceEventId("primary-rejected-source"),
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    aborted_source_id = SourceEventId("frame-aborted-source")
    aborted = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        LogicalSourceKey(raw_record.feed_product.feed_product_id, aborted_source_id),
        evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )
    rejected = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 1),
        _outcome_scope_binding(1, raw_record=raw_record, scope=scope),
        RawEventDisposition.REJECTED,
        LogicalSourceKey(
            raw_record.feed_product.feed_product_id,
            SourceEventId("primary-rejected-source"),
        ),
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )
    abort_evidence = FrameAtomicAbortEvidenceSource(
        raw_record_id=raw_record.raw_record_id,
        normalization_run_id=run_id,
        raw_event_index=0,
        source_event_id=aborted_source_id,
        identified_coverage_scope_id=scope.coverage_scope_id,
        primary_cause_coverage_evidence_ids=(transition.evidence.coverage_evidence_id,),
    )
    lineage = _typed_coverage_lineage(transition, aborts=(abort_evidence,))
    outcome = NormalizationOutcome(
        normalization_run_id=run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        decoded_event_count=2,
        raw_event_outcomes=(aborted, rejected),
        committed_materialization_keys=(),
        evidence=(
            NormalizationEvidence.DECODER_REJECTION,
            NormalizationEvidence.FRAME_ATOMIC_ABORT,
        ),
        coverage_lineage=lineage,
    )
    return outcome, transition, abort_evidence


def _materialized_outcome_for_delivery(*, item_count: int = 2) -> NormalizationOutcome:
    raw_event_outcomes: list[RawEventNormalizationOutcome] = []
    materializations: list[MaterializationKey] = []
    for index in range(item_count):
        logical, observation, materialization = _outcome_keys(index)
        raw_event_outcomes.append(
            RawEventNormalizationOutcome(
                observation,
                _outcome_scope_binding(index),
                RawEventDisposition.MATERIALIZED_NEW,
                logical,
                materialization,
            )
        )
        materializations.append(materialization)
    return NormalizationOutcome.legacy_transition_empty(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_record_id=_raw_record_id(),
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.MATERIALIZED,
        decoded_event_count=item_count,
        raw_event_outcomes=tuple(raw_event_outcomes),
        committed_materialization_keys=tuple(materializations),
    )


def test_source_and_observation_provenance_have_exact_separate_fields() -> None:
    assert tuple(field_.name for field_ in fields(SourceProvenance)) == (
        "source_event_id",
        "source_transaction_id",
        "source_time_facts",
        "source_sequence_ranges",
    )
    assert tuple(field_.name for field_ in fields(ObservationProvenance)) == (
        "feed_product_id",
        "collector_run_id",
        "connection_session_id",
        "subscription_plan_id",
        "subscription_spec_id",
        "subscription_attempt_id",
        "raw_record_id",
        "raw_event_index",
        "received_time",
        "received_monotonic_ns",
        "collector_version",
        "collector_commit",
        "normalization_run_id",
        "normalizer_version",
        "normalizer_commit",
    )
    assert "source_event_id" not in {field_.name for field_ in fields(ObservationProvenance)}


def test_source_fact_owners_accept_literal_bounds_and_reject_plus_one() -> None:
    time_roles = tuple(SourceTimeRole)
    sequence_roles = tuple(SourceSequenceRole)
    time_facts = tuple(
        SourceTimeFact(
            time_roles[index % len(time_roles)],
            index,
            SourceTimeUnit.EPOCH_MICROSECONDS,
        )
        for index in range(MAX_SOURCE_TIME_FACTS)
    )
    sequence_ranges = tuple(
        SourceSequenceRange(
            sequence_roles[index % len(sequence_roles)],
            f"sequence-domain-{index:02d}",
            index,
            index,
        )
        for index in range(MAX_SOURCE_SEQUENCE_RANGES)
    )
    source = SourceProvenance(
        SourceEventId("source-event-fixture"),
        None,
        time_facts,
        sequence_ranges,
    )
    assert len(source.source_time_facts) == MAX_SOURCE_TIME_FACTS
    assert len(source.source_sequence_ranges) == MAX_SOURCE_SEQUENCE_RANGES

    with pytest.raises(ValueError, match="item count"):
        replace(source, source_time_facts=(*time_facts, time_facts[0]))
    with pytest.raises(ValueError, match="item count"):
        replace(source, source_sequence_ranges=(*sequence_ranges, sequence_ranges[0]))


def test_owning_v3_contracts_apply_their_configured_collection_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_record, spec, attempt = _raw_record()
    binding = DecodedEventSubscriptionBinding(0, spec, attempt)
    monkeypatch.setattr(market_event_v3_module, "MAX_DECODED_EVENTS_PER_RAW_RECORD", 1)
    decoded = DecodedWirePayloadContext(raw_record.raw_record_id, 1, (binding,))
    assert len(decoded.event_bindings) == 1
    with pytest.raises(ValueError, match="per-record bound"):
        DecodedWirePayloadContext(
            raw_record.raw_record_id,
            2,
            (binding, replace(binding, raw_event_index=1)),
        )

    logical = LogicalSourceKey(
        BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        SourceEventId("source-fixture"),
    )
    observation = ObservationKey(raw_record.raw_record_id, 0)
    materialization = MaterializationKey(
        NormalizationRunId("normalization-fixture"),
        observation,
        EventFamily.TRADE,
        2,
        PayloadType.TRADE,
    )
    item = RawEventNormalizationOutcome(
        observation,
        _outcome_scope_binding(0, raw_record=raw_record),
        RawEventDisposition.MATERIALIZED_NEW,
        logical,
        materialization,
    )
    monkeypatch.setattr(market_event_v3_module, "MAX_NORMALIZATION_OUTCOME_ITEMS", 1)
    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.MATERIALIZED,
        1,
        (item,),
        (materialization,),
    )
    assert len(outcome.raw_event_outcomes) == len(outcome.committed_materialization_keys) == 1
    with pytest.raises(ValueError, match="item count"):
        replace(outcome, raw_event_outcomes=(item, item))
    with pytest.raises(ValueError, match="item count"):
        replace(outcome, committed_materialization_keys=(materialization, materialization))

    monkeypatch.setattr(market_event_v3_module, "MAX_NORMALIZATION_EVIDENCE_ITEMS", 1)
    rejected = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        None,
        (),
        (),
        preindex_scope_binding=_preindex_scope_binding(raw_record),
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
    )
    assert len(rejected.evidence) == 1
    with pytest.raises(ValueError, match="item count"):
        replace(
            rejected,
            evidence=(
                NormalizationEvidence.DECODER_REJECTION,
                NormalizationEvidence.PROTOCOL_REJECTION,
            ),
        )

    transition_raw, transition_scope, transition = _normalization_failure_transition(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_event_index=None,
        category=NormalizationFailureCategory.PROTOCOL_REJECTION,
    )
    monkeypatch.setattr(market_event_v3_module, "MAX_COVERAGE_TRANSITION_REFERENCES", 1)
    transitioned = replace(
        rejected,
        preindex_scope_binding=RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=transition_raw,
            coverage_scope=transition_scope,
        ),
        coverage_lineage=_typed_coverage_lineage(transition),
    )
    assert transitioned.coverage_transition_ids == (transition.coverage_transition_id,)
    with pytest.raises(ValueError, match="item count"):
        replace(
            transitioned,
            coverage_transition_ids=(
                transition.coverage_transition_id,
                transition.coverage_transition_id,
            ),
        )

    assert MAX_DECODED_EVENTS_PER_RAW_RECORD > 1
    assert MAX_NORMALIZATION_OUTCOME_ITEMS > 1
    assert MAX_NORMALIZATION_EVIDENCE_ITEMS > 1
    assert MAX_COVERAGE_TRANSITION_REFERENCES > 1


def test_normalization_lineage_primary_and_conflict_tuples_enforce_n_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure_transitions = tuple(
        _normalization_failure_transition(
            normalization_run_id=NormalizationRunId("normalization-fixture"),
            raw_event_index=0,
            source_event_id=SourceEventId("bounded-primary-source"),
            category=category,
        )[2]
        for category in (
            NormalizationFailureCategory.DECODER_REJECTION,
            NormalizationFailureCategory.UNKNOWN_INSTRUMENT,
            NormalizationFailureCategory.METADATA_UNAVAILABLE,
        )
    )
    selected_failure, *additional_failures = sorted(
        failure_transitions,
        key=lambda item: item.evidence.coverage_evidence_id.value,
    )
    failure_batch = _prepared_batch_for_transition(selected_failure)
    failure_binding = failure_batch.raw_fanout_binding
    assert type(failure_binding) is RawCoverageFanoutBinding

    monkeypatch.setattr(market_event_v3_module, "MAX_NORMALIZATION_OUTCOME_ITEMS", 1)
    accepted_failure_lineage = NormalizationCoverageLineage(
        coverage_mutation_batch=failure_batch,
        frame_atomic_abort_evidence=(),
        raw_fanout_binding=failure_binding,
        additional_primary_evidence=(additional_failures[0].evidence,),
    )
    assert len(accepted_failure_lineage.additional_primary_evidence) == 1
    with pytest.raises(ValueError, match="item count"):
        NormalizationCoverageLineage(
            coverage_mutation_batch=failure_batch,
            frame_atomic_abort_evidence=(),
            raw_fanout_binding=failure_binding,
            additional_primary_evidence=tuple(
                sorted(
                    (item.evidence for item in additional_failures),
                    key=lambda item: item.coverage_evidence_id.value,
                )
            ),
        )

    conflict_transitions = tuple(
        _source_conflict_transition(
            source_event_id=SourceEventId(f"bounded-conflict-source-{index}"),
            raw_event_index=index,
        )[2]
        for index in range(2)
    )
    selected_conflict, additional_conflict = sorted(
        conflict_transitions,
        key=lambda item: item.evidence.coverage_evidence_id.value,
    )
    conflict_batch = _prepared_batch_for_transition(selected_conflict)
    conflict_binding = conflict_batch.raw_fanout_binding
    assert type(conflict_binding) is RawCoverageFanoutBinding
    selected_run_binding = NormalizationSourceConflictBinding(
        selected_conflict.evidence,
        NormalizationRunId("normalization-fixture"),
    )
    accepted_conflict_lineage = NormalizationCoverageLineage(
        coverage_mutation_batch=conflict_batch,
        frame_atomic_abort_evidence=(),
        raw_fanout_binding=conflict_binding,
        source_conflict_bindings=(selected_run_binding,),
    )
    assert len(accepted_conflict_lineage.source_conflict_bindings) == 1
    all_run_bindings = tuple(
        sorted(
            (
                selected_run_binding,
                NormalizationSourceConflictBinding(
                    additional_conflict.evidence,
                    NormalizationRunId("normalization-fixture"),
                ),
            ),
            key=lambda item: item.normalization_source_conflict_binding_id.value,
        )
    )
    with pytest.raises(ValueError, match="item count"):
        NormalizationCoverageLineage(
            coverage_mutation_batch=conflict_batch,
            frame_atomic_abort_evidence=(),
            raw_fanout_binding=conflict_binding,
            additional_primary_evidence=(additional_conflict.evidence,),
            source_conflict_bindings=all_run_bindings,
        )

    assert MAX_NORMALIZATION_OUTCOME_ITEMS > 1


def test_adapter_and_key_ids_have_byte_exact_versioned_preimages() -> None:
    binding = _adapter_binding()
    logical, observation, materialization = _outcome_keys(index=3)

    assert binding.adapter_feed_binding_id.value == (
        '["adapter-feed-binding-v1","binance-spot-json-trade-v1",'
        '"[\\"feed-product-v1\\",\\"binance\\",\\"production\\",'
        '\\"mainnet\\",\\"spot-json-market-streams\\",'
        '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
        '\\"json-text\\"]","binance","acknowledged"]'
    )
    assert logical.logical_source_key_id.value == _expected_identifier(
        "logical-source-key-v1",
        BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id.value,
        "source-fixture",
    )
    assert observation.observation_key_id.value == _expected_identifier(
        "observation-key-v1",
        _raw_record_id().value,
        3,
    )
    assert materialization.materialization_key_id.value == _expected_identifier(
        "materialization-key-v1",
        "normalization-fixture",
        observation.observation_key_id.value,
        "trade",
        2,
        "trade",
    )


def test_computed_identifiers_revalidate_exact_persisted_values() -> None:
    binding = _adapter_binding()
    logical, observation, materialization = _outcome_keys()
    item = RawEventNormalizationOutcome(
        observation,
        _outcome_scope_binding(0),
        RawEventDisposition.MATERIALIZED_NEW,
        logical,
        materialization,
    )
    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        _raw_record_id(),
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.MATERIALIZED,
        1,
        (item,),
        (materialization,),
    )

    assert AdapterFeedBindingId(binding.adapter_feed_binding_id.value) == (
        binding.adapter_feed_binding_id
    )
    assert LogicalSourceKeyId(logical.logical_source_key_id.value) == (
        logical.logical_source_key_id
    )
    assert ObservationKeyId(observation.observation_key_id.value) == (
        observation.observation_key_id
    )
    assert MaterializationKeyId(materialization.materialization_key_id.value) == (
        materialization.materialization_key_id
    )
    assert (
        RawEventNormalizationOutcomeId(item.raw_event_normalization_outcome_id.value)
        == item.raw_event_normalization_outcome_id
    )
    assert NormalizationOutcomeId(outcome.normalization_outcome_id.value) == (
        outcome.normalization_outcome_id
    )


@pytest.mark.parametrize(
    ("identifier_type", "value"),
    [
        pytest.param(LogicalSourceKeyId, "not-json", id="logical-not-json"),
        pytest.param(
            LogicalSourceKeyId,
            '[ "logical-source-key-v1", "feed", "source" ]',
            id="logical-noncompact",
        ),
        pytest.param(
            LogicalSourceKeyId,
            _expected_identifier("wrong-tag", "feed", "source"),
            id="logical-wrong-tag",
        ),
        pytest.param(
            ObservationKeyId,
            _expected_identifier("observation-key-v1", _raw_record_id().value, True),
            id="observation-boolean-index",
        ),
        pytest.param(
            MaterializationKeyId,
            _expected_identifier(
                "materialization-key-v1",
                "normalization-fixture",
                _outcome_keys()[1].observation_key_id.value,
                "trade",
                "2",
                "trade",
            ),
            id="materialization-string-version",
        ),
        pytest.param(
            RawEventNormalizationOutcomeId,
            _expected_identifier(
                "raw-event-normalization-outcome-v1",
                _outcome_keys()[1].observation_key_id.value,
                "unknown-disposition",
                None,
                None,
                None,
            ),
            id="item-unknown-disposition",
        ),
        pytest.param(
            NormalizationOutcomeId,
            _expected_identifier(
                "normalization-outcome-v1",
                "normalization-fixture",
                _raw_record_id().value,
                "normalizer-v1",
                "normalizer-commit-fixture",
                "materialized",
                1,
                "not-a-sha256",
            ),
            id="outcome-invalid-content-digest",
        ),
    ],
)
def test_computed_identifier_wrappers_reject_malformed_or_noncanonical_values(
    identifier_type: Callable[[str], object],
    value: str,
) -> None:
    with pytest.raises(ValueError):
        identifier_type(value)


def test_malformed_v3_identifier_is_absent_from_bounded_exception_surface() -> None:
    marker = "v3-identifier-private-sentinel"
    malformed = f'["{marker}"'

    with pytest.raises(ValueError, match="canonical compact JSON array") as captured:
        LogicalSourceKeyId(malformed)

    _assert_bounded_exception_surface_excludes(captured.value, marker, malformed)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_keys_never_use_native_symbol_as_canonical_instrument_identity() -> None:
    first = _instrument(native_symbol="BTCUSDT")
    alias = _instrument(native_symbol="btc-usdt-adapter-alias")

    assert first == alias
    assert first.canonical_instrument_id == alias.canonical_instrument_id
    assert _outcome_keys() == _outcome_keys()


def test_source_provenance_is_frozen_slotted_hashable_and_exact() -> None:
    source = SourceProvenance(
        source_event_id=SourceEventId("source-event-fixture"),
        source_transaction_id=SourceTransactionId("source-transaction-fixture"),
        source_time_facts=(),
        source_sequence_ranges=(),
    )

    assert hash(source) == hash(source)
    assert not hasattr(source, "__dict__")
    with pytest.raises(FrozenInstanceError):
        source.source_event_id = SourceEventId("replacement")  # type: ignore[misc]


@pytest.mark.parametrize(
    "source_event_id",
    [
        '["hyperliquid-trade-v1",1720000000123,"BTC",42]',
        '["binance-spot-trade-v1","BTCUSDT",42]',
    ],
)
def test_existing_adapter_source_event_id_text_is_retained_byte_for_byte(
    source_event_id: str,
) -> None:
    provenance = SourceProvenance(
        source_event_id=SourceEventId(source_event_id),
        source_transaction_id=None,
        source_time_facts=(),
        source_sequence_ranges=(),
    )

    assert provenance.source_event_id.value == source_event_id


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        pytest.param("source_event_id", "source", id="source-event-string"),
        pytest.param("source_transaction_id", "transaction", id="transaction-string"),
        pytest.param("source_time_facts", [], id="time-facts-list"),
        pytest.param("source_sequence_ranges", [], id="sequence-ranges-list"),
    ],
)
def test_source_provenance_rejects_wrong_exact_runtime_types(
    field_name: str,
    invalid_value: object,
) -> None:
    values: dict[str, object] = {
        "source_event_id": SourceEventId("source"),
        "source_transaction_id": None,
        "source_time_facts": (),
        "source_sequence_ranges": (),
    }
    values[field_name] = invalid_value

    with pytest.raises(TypeError):
        SourceProvenance(**values)  # type: ignore[arg-type]


def test_normalization_context_derives_all_capture_side_observation_fields() -> None:
    context = _context()
    observation = context.observation_provenance

    assert observation.feed_product_id == BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id
    assert observation.collector_run_id == CollectorRunId("collector-run-fixture")
    assert observation.raw_event_index == 0
    assert observation.received_time == _RECEIVED_TIME
    assert observation.received_monotonic_ns == 987654321
    assert observation.collector_version == "collector-v1"
    assert observation.collector_commit == "collector-commit-fixture"
    assert observation.normalization_run_id == NormalizationRunId("normalization-run-fixture")
    assert observation.normalizer_version == "normalizer-v1"
    assert observation.normalizer_commit == "normalizer-commit-fixture"
    assert context.source_provenance == _source_provenance()


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        pytest.param({"raw_event_index": True}, TypeError, id="boolean-index"),
        pytest.param({"raw_event_index": -1}, ValueError, id="negative-index"),
        pytest.param({"received_time": datetime(2026, 8, 26)}, ValueError, id="naive-time"),
        pytest.param({"received_monotonic_ns": True}, TypeError, id="boolean-monotonic"),
        pytest.param({"collector_version": ""}, ValueError, id="empty-collector-version"),
        pytest.param({"normalizer_commit": 1}, TypeError, id="integer-normalizer-commit"),
    ],
)
def test_observation_provenance_revalidates_direct_construction(
    changes: dict[str, object],
    expected_error: type[Exception],
) -> None:
    provenance = _context().observation_provenance

    with pytest.raises(expected_error):
        replace(provenance, **changes)  # type: ignore[arg-type]


def test_observation_provenance_rejects_contradictory_parent_identities() -> None:
    provenance = _context().observation_provenance
    other_spec = _wire_spec("ETHUSDT")

    for changes in (
        {"feed_product_id": HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id},
        {"collector_run_id": CollectorRunId("other-collector-run")},
        {"subscription_spec_id": other_spec.subscription_spec_id},
    ):
        with pytest.raises(ValueError):
            replace(provenance, **changes)


def test_normalization_context_has_no_direct_public_constructor() -> None:
    with pytest.raises(TypeError, match="from_raw_record"):
        NormalizationContext()


@pytest.mark.parametrize(
    "attempt_status",
    [
        SubscriptionAttemptStatus.PENDING,
        SubscriptionAttemptStatus.SEND_STARTED,
        SubscriptionAttemptStatus.SENT,
    ],
)
def test_market_event_materialization_requires_acknowledged_attempt(
    attempt_status: SubscriptionAttemptStatus,
) -> None:
    with pytest.raises(ValueError, match="acknowledged subscription attempt"):
        _hyperliquid_context(attempt_status)


def test_acknowledged_attempt_allows_event_materialization() -> None:
    context = _hyperliquid_context(SubscriptionAttemptStatus.ACKNOWLEDGED)

    assert (
        context.adapter_feed_binding.event_activation_requirement
        is EventActivationRequirement.ACKNOWLEDGED
    )


def test_trade_binding_requires_exactly_one_matching_execution_time_fact() -> None:
    execution = _source_provenance().source_time_facts[0]
    missing = replace(_source_provenance(), source_time_facts=())
    duplicate_role = replace(
        _source_provenance(),
        source_time_facts=(
            execution,
            SourceTimeFact(
                SourceTimeRole.TRADE_EXECUTION_TIME,
                execution.raw_value + 1,
                execution.unit,
            ),
        ),
    )
    mismatch = replace(
        _source_provenance(),
        source_time_facts=(
            SourceTimeFact(
                SourceTimeRole.TRADE_EXECUTION_TIME,
                execution.raw_value + 1,
                execution.unit,
            ),
        ),
    )

    for source in (missing, duplicate_role):
        with pytest.raises(ValueError, match="exactly one"):
            _context(source_provenance=source)
    with pytest.raises(ValueError, match="must equal"):
        _context(source_provenance=mismatch)


def test_exchange_event_time_may_coexist_without_replacing_trade_execution_time() -> None:
    source = replace(
        _source_provenance(),
        source_time_facts=(
            SourceTimeFact(
                SourceTimeRole.EXCHANGE_EVENT_TIME,
                1_787_745_599_700,
                SourceTimeUnit.EPOCH_MILLISECONDS,
            ),
            _source_provenance().source_time_facts[0],
        ),
    )
    context = _context(source_provenance=source)

    assert context.resolved_instrument_metadata.event_time == source.source_time_facts[1].utc_value


def test_context_accepts_zero_offset_alias_and_canonicalizes_received_time() -> None:
    zero_offset = timezone(timedelta(0), name="zero-offset-alias")
    raw_record, spec, attempt = _raw_record()
    aliased_raw = replace(
        raw_record,
        received_time=_RECEIVED_TIME.replace(tzinfo=zero_offset),
    )
    decoded = DecodedWirePayloadContext(
        raw_record_id=aliased_raw.raw_record_id,
        decoded_event_count=1,
        event_bindings=(DecodedEventSubscriptionBinding(0, spec, attempt),),
    )
    resolved = _resolved_metadata(
        raw_received_time=_RECEIVED_TIME.replace(tzinfo=zero_offset),
    )

    context = NormalizationContext.from_raw_record(
        raw_record=aliased_raw,
        adapter_feed_binding=_adapter_binding(),
        decoded_wire_payload=decoded,
        raw_event_index=0,
        source_provenance=_source_provenance(),
        normalization_run_id=NormalizationRunId("normalization-run-fixture"),
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        resolved_instrument_metadata=resolved,
        event_coverage=_coverage(spec=spec),
    )

    assert context.observation_provenance.received_time.tzinfo is UTC


def test_trade_execution_fact_matches_an_equivalent_zero_offset_event_time() -> None:
    zero_offset = timezone(timedelta(0), name="zero-offset-alias")
    resolved = _resolved_metadata(event_time=_EVENT_TIME.replace(tzinfo=zero_offset))

    context = _context(resolved_instrument_metadata=resolved)
    envelope = MarketEventEnvelopeV3(context, _trade())

    assert context.resolved_instrument_metadata.event_time == _EVENT_TIME
    assert envelope.event_time == _EVENT_TIME
    assert envelope.event_time.tzinfo is UTC


def test_decoded_context_requires_complete_unique_wire_order() -> None:
    raw_record, spec, attempt = _raw_record()
    binding = DecodedEventSubscriptionBinding(1, spec, attempt)

    with pytest.raises(ValueError, match="every decoded index"):
        DecodedWirePayloadContext(raw_record.raw_record_id, 1, (binding,))
    with pytest.raises(TypeError):
        DecodedWirePayloadContext(
            raw_record.raw_record_id,
            1,
            cast(tuple[DecodedEventSubscriptionBinding, ...], [binding]),
        )
    with pytest.raises(TypeError):
        DecodedWirePayloadContext(raw_record.raw_record_id, cast(int, True), ())


def test_context_rejects_index_outside_decoded_payload() -> None:
    raw_record, spec, attempt = _raw_record()
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, attempt),),
    )

    with pytest.raises(ValueError, match="outside"):
        NormalizationContext.from_raw_record(
            raw_record=raw_record,
            adapter_feed_binding=_adapter_binding(),
            decoded_wire_payload=decoded,
            raw_event_index=1,
            source_provenance=_source_provenance(),
            normalization_run_id=NormalizationRunId("normalization-run-fixture"),
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            resolved_instrument_metadata=_resolved_metadata(),
            event_coverage=_coverage(spec=spec),
        )


def test_context_rejects_attempt_missing_from_raw_lineage() -> None:
    raw_record, spec, _ = _raw_record()
    foreign_attempt = SubscriptionAttemptIdentity(
        connection_session=raw_record.connection_session,
        subscription_spec=spec,
        attempt_ordinal=1,
    )
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, foreign_attempt),),
    )

    with pytest.raises(ValueError, match="lineage"):
        NormalizationContext.from_raw_record(
            raw_record=raw_record,
            adapter_feed_binding=_adapter_binding(),
            decoded_wire_payload=decoded,
            raw_event_index=0,
            source_provenance=_source_provenance(),
            normalization_run_id=NormalizationRunId("normalization-run-fixture"),
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            resolved_instrument_metadata=_resolved_metadata(),
            event_coverage=_coverage(spec=spec),
        )


def test_context_rejects_adapter_plan_and_raw_record_mismatch() -> None:
    raw_binding = AdapterFeedBinding(
        adapter_code="binance-raw-adapter-v1",
        feed_product=BINANCE_MAINNET_SPOT_JSON_STREAMS,
        venue=Venue.BINANCE,
        event_activation_requirement=EventActivationRequirement.ACKNOWLEDGED,
    )
    raw_record, spec, attempt = _raw_record()
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, attempt),),
    )

    with pytest.raises(ValueError, match="subscription plan"):
        NormalizationContext.from_raw_record(
            raw_record=raw_record,
            adapter_feed_binding=raw_binding,
            decoded_wire_payload=decoded,
            raw_event_index=0,
            source_provenance=_source_provenance(),
            normalization_run_id=NormalizationRunId("normalization-run-fixture"),
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            resolved_instrument_metadata=_resolved_metadata(),
            event_coverage=_coverage(spec=spec),
        )


def test_context_rejects_metadata_selected_for_different_received_time() -> None:
    raw_record, spec, attempt = _raw_record()
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, attempt),),
    )

    with pytest.raises(ValueError, match="raw-record received time"):
        NormalizationContext.from_raw_record(
            raw_record=raw_record,
            adapter_feed_binding=_adapter_binding(),
            decoded_wire_payload=decoded,
            raw_event_index=0,
            source_provenance=_source_provenance(),
            normalization_run_id=NormalizationRunId("normalization-run-fixture"),
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            resolved_instrument_metadata=_resolved_metadata(
                raw_received_time=_RECEIVED_TIME + timedelta(seconds=1)
            ),
            event_coverage=_coverage(spec=spec),
        )


def test_context_rejects_coverage_that_omits_the_subscription_spec() -> None:
    raw_record, spec, attempt = _raw_record()
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, attempt),),
    )
    other_spec = _wire_spec("ETHUSDT")
    other_instrument = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="ETH",
        quote_asset="USDT",
        venue_market_id="ETHUSDT",
        native_symbol="ETHUSDT",
    )

    with pytest.raises(ValueError, match="subscription spec"):
        NormalizationContext.from_raw_record(
            raw_record=raw_record,
            adapter_feed_binding=_adapter_binding(),
            decoded_wire_payload=decoded,
            raw_event_index=0,
            source_provenance=_source_provenance(),
            normalization_run_id=NormalizationRunId("normalization-run-fixture"),
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            resolved_instrument_metadata=_resolved_metadata(),
            event_coverage=_coverage(spec=other_spec, instrument=other_instrument),
        )


def test_event_coverage_requires_committed_states_and_preserves_exact_state_ids() -> None:
    raw_record, spec, _attempt = _raw_record()
    coverage = _coverage(spec=spec)
    context = _context(event_coverage=coverage)
    envelope = MarketEventEnvelopeV3(context, _trade())

    assert envelope.event_coverage is coverage
    assert (
        envelope.event_coverage.bronze_ingress.committed_coverage_state_id
        == coverage.bronze_ingress.committed_coverage_state_id
    )
    assert (
        envelope.event_coverage.silver_normalization.committed_coverage_state_id
        == coverage.silver_normalization.committed_coverage_state_id
    )
    assert coverage.silver_normalization.state_reference.reference.scope.subscription_spec_ids == (
        spec.subscription_spec_id,
    )
    assert context.observation_provenance.raw_record_id == raw_record.raw_record_id


def test_event_coverage_rejects_legacy_and_uncommitted_candidate_references() -> None:
    _raw_record_value, spec, _attempt = _raw_record()
    coverage = _coverage(spec=spec)
    candidate = coverage.bronze_ingress.state_reference
    legacy = candidate.reference

    with pytest.raises(TypeError, match="CommittedCoverageState"):
        EventCoverage(
            cast(CommittedCoverageState, candidate),
            coverage.silver_normalization,
        )
    with pytest.raises(TypeError, match="CommittedCoverageState"):
        EventCoverage(
            cast(CommittedCoverageState, legacy),
            coverage.silver_normalization,
        )


def test_context_rejects_coverage_epoch_from_another_collector_run() -> None:
    raw_record, spec, attempt = _raw_record()
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, spec, attempt),),
    )

    local = _coverage(spec=spec)
    foreign = _coverage(
        spec=spec,
        collector_run_id=CollectorRunId("foreign-collector-run"),
    )
    with pytest.raises(ValueError, match="one collector run"):
        EventCoverage(foreign.bronze_ingress, local.silver_normalization)
    with pytest.raises(ValueError, match="one collector run"):
        EventCoverage(local.bronze_ingress, foreign.silver_normalization)
    for event_coverage in (foreign,):
        with pytest.raises(ValueError, match="raw-record collector run"):
            NormalizationContext.from_raw_record(
                raw_record=raw_record,
                adapter_feed_binding=_adapter_binding(),
                decoded_wire_payload=decoded,
                raw_event_index=0,
                source_provenance=_source_provenance(),
                normalization_run_id=NormalizationRunId("normalization-run-fixture"),
                normalizer_version="normalizer-v1",
                normalizer_commit="normalizer-commit-fixture",
                resolved_instrument_metadata=_resolved_metadata(),
                event_coverage=event_coverage,
            )


def test_market_event_v3_binds_outer_v3_to_trade_family_v2() -> None:
    envelope = MarketEventEnvelopeV3(
        normalization_context=_context(),
        event=_trade(),
        correlation_id=CorrelationId("research-correlation-fixture"),
    )

    assert MARKET_EVENT_SCHEMA_VERSION == 2
    assert MARKET_EVENT_ENVELOPE_SCHEMA_VERSION == 3
    assert envelope.envelope_schema_version == 3
    assert envelope.event_family is EventFamily.TRADE
    assert envelope.event_family_schema_version == TRADE_EVENT_FAMILY_SCHEMA_VERSION == 2
    assert envelope.payload_type is PayloadType.TRADE
    assert envelope.instrument == _instrument()
    assert envelope.event_time == _EVENT_TIME
    assert envelope.event == _trade()
    assert envelope.source_provenance == _source_provenance()
    assert envelope.observation_provenance == _context().observation_provenance
    assert envelope.event_coverage == _context().event_coverage
    assert envelope.logical_source_key.source_event_id.value == (
        '["binance-spot-trade-v1","BTCUSDT",42]'
    )


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        pytest.param({"envelope_schema_version": 2}, ValueError, id="outer-v2"),
        pytest.param({"envelope_schema_version": True}, TypeError, id="outer-bool"),
        pytest.param({"event_family_schema_version": 1}, ValueError, id="trade-v1"),
        pytest.param({"event_family_schema_version": True}, TypeError, id="family-bool"),
        pytest.param({"event_family": "trade"}, TypeError, id="family-string"),
        pytest.param({"payload_type": "trade"}, TypeError, id="payload-string"),
        pytest.param({"event": "trade"}, TypeError, id="payload-object"),
        pytest.param({"correlation_id": "correlation"}, TypeError, id="correlation-string"),
    ],
)
def test_market_event_v3_rejects_invalid_binding_runtime_values(
    changes: dict[str, object],
    expected_error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "normalization_context": _context(),
        "event": _trade(),
    }
    values.update(changes)

    with pytest.raises(expected_error):
        MarketEventEnvelopeV3(**values)  # type: ignore[arg-type]


def test_market_event_v3_is_frozen_slotted_and_hashable() -> None:
    envelope = MarketEventEnvelopeV3(_context(), _trade())

    assert hash(envelope) == hash(envelope)
    assert not hasattr(envelope, "__dict__")
    with pytest.raises(FrozenInstanceError):
        envelope.event = _trade()  # type: ignore[misc]


def test_raw_event_outcome_ids_are_byte_exact() -> None:
    logical, observation, materialization = _outcome_keys()
    scope_binding = _outcome_scope_binding(0)
    item = RawEventNormalizationOutcome(
        observation_key=observation,
        normalization_scope_binding=scope_binding,
        disposition=RawEventDisposition.MATERIALIZED_NEW,
        logical_source_key=logical,
        materialization_key=materialization,
    )

    assert item.raw_event_normalization_outcome_id.value == _expected_identifier(
        "raw-event-normalization-outcome-v1",
        observation.observation_key_id.value,
        scope_binding.canonical_components(),
        "materialized_new",
        logical.logical_source_key_id.value,
        materialization.materialization_key_id.value,
        None,
    )


def test_normalization_outcome_id_binds_normalizer_build_and_closed_results() -> None:
    logical, observation, materialization = _outcome_keys()
    item = RawEventNormalizationOutcome(
        observation,
        _outcome_scope_binding(0),
        RawEventDisposition.MATERIALIZED_NEW,
        logical,
        materialization,
    )
    outcome = NormalizationOutcome(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_record_id=_raw_record_id(),
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.MATERIALIZED,
        decoded_event_count=1,
        raw_event_outcomes=(item,),
        committed_materialization_keys=(materialization,),
    )

    expected_content = _expected_identifier(
        "normalization-outcome-content-v1",
        (item.raw_event_normalization_outcome_id.value,),
        (materialization.materialization_key_id.value,),
        (),
        (),
        None,
    )
    expected_content_sha256 = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
    assert outcome.normalization_outcome_content_sha256 == expected_content_sha256
    assert outcome.normalization_outcome_id.value == _expected_identifier(
        "normalization-outcome-v1",
        "normalization-fixture",
        _raw_record_id().value,
        "normalizer-v1",
        "normalizer-commit-fixture",
        "materialized",
        1,
        expected_content_sha256,
    )
    assert replace(outcome, normalizer_commit="another-commit").normalization_outcome_id != (
        outcome.normalization_outcome_id
    )


def test_normalization_outcome_accepts_control_empty_and_success_matrices() -> None:
    control = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        _raw_record_id(),
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.CONTROL_NO_EVENT,
        None,
        (),
        (),
    )
    empty = replace(
        control,
        frame_status=FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME,
        decoded_event_count=0,
    )
    rejected_before = replace(
        control,
        frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        preindex_scope_binding=_preindex_scope_binding(),
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
    )

    assert control.raw_event_outcomes == ()
    assert empty.decoded_event_count == 0
    assert rejected_before.evidence == (NormalizationEvidence.PROTOCOL_REJECTION,)


def test_preindex_scope_is_mandatory_plan_bound_and_transition_free() -> None:
    raw_record, _spec, _attempt = _raw_record()
    binding = _preindex_scope_binding(raw_record)
    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        None,
        (),
        (),
        preindex_scope_binding=binding,
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
    )

    assert outcome.preindex_scope_binding is binding
    assert outcome.coverage_transition_ids == ()
    with pytest.raises(ValueError, match="complete raw-frame scope"):
        replace(outcome, preindex_scope_binding=None)

    other_raw = replace(raw_record, ingress_ordinal=raw_record.ingress_ordinal + 1)
    with pytest.raises(ValueError, match="outcome raw record"):
        replace(outcome, preindex_scope_binding=_preindex_scope_binding(other_raw))
    with pytest.raises(TypeError, match="RawFrameNormalizationScopeBinding"):
        replace(outcome, preindex_scope_binding=object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "status",
    tuple(
        status
        for status in FrameNormalizationStatus
        if status is not FrameNormalizationStatus.REJECTED_BEFORE_INDEXING
    ),
)
def test_preindex_scope_binding_is_forbidden_for_every_other_frame_status(
    status: FrameNormalizationStatus,
) -> None:
    with pytest.raises(ValueError, match="only for pre-index rejection"):
        NormalizationOutcome(
            NormalizationRunId("normalization-fixture"),
            _raw_record_id(),
            "normalizer-v1",
            "normalizer-commit-fixture",
            status,
            None,
            (),
            (),
            preindex_scope_binding=_preindex_scope_binding(),
        )


def test_preindex_scope_binding_is_byte_exact_in_outcome_content_identity() -> None:
    raw_record, _spec, _attempt = _raw_record()
    binding = _preindex_scope_binding(raw_record)
    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        None,
        (),
        (),
        preindex_scope_binding=binding,
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
    )
    expected_content = _expected_identifier(
        "normalization-outcome-content-v1",
        (),
        (),
        (NormalizationEvidence.PROTOCOL_REJECTION.value,),
        (),
        binding.canonical_components(),
    )
    expected_digest = hashlib.sha256(expected_content.encode()).hexdigest()

    assert outcome.normalization_outcome_content_sha256 == expected_digest
    assert outcome.normalization_outcome_id.value == _expected_identifier(
        "normalization-outcome-v1",
        "normalization-fixture",
        raw_record.raw_record_id.value,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_BEFORE_INDEXING.value,
        None,
        expected_digest,
    )
    assert replace(outcome) == outcome


@pytest.mark.parametrize(
    "evidence",
    tuple(NormalizationEvidence),
    ids=lambda item: item.value,
)
def test_preindex_rejection_evidence_uses_an_exhaustive_positive_allowlist(
    evidence: NormalizationEvidence,
) -> None:
    allowed = frozenset(
        {
            NormalizationEvidence.PROTOCOL_REJECTION,
            NormalizationEvidence.DECODER_REJECTION,
            NormalizationEvidence.UNKNOWN_INSTRUMENT,
            NormalizationEvidence.METADATA_UNAVAILABLE,
            NormalizationEvidence.PROVENANCE_MISMATCH,
            NormalizationEvidence.LOCAL_CONTRACT_FAILURE,
        }
    )

    def construct() -> NormalizationOutcome:
        return NormalizationOutcome(
            NormalizationRunId("normalization-fixture"),
            _raw_record_id(),
            "normalizer-v1",
            "normalizer-commit-fixture",
            FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            None,
            (),
            (),
            preindex_scope_binding=_preindex_scope_binding(),
            evidence=(evidence,),
        )

    if evidence in allowed:
        assert construct().evidence == (evidence,)
    else:
        with pytest.raises(ValueError, match="explicit closed allowlist"):
            construct()


@pytest.mark.parametrize(
    "evidence",
    (
        NormalizationEvidence.SOURCE_EVENT_CONFLICT,
        NormalizationEvidence.FRAME_ATOMIC_ABORT,
    ),
)
def test_index_only_evidence_is_explicitly_rejected_before_indexing(
    evidence: NormalizationEvidence,
) -> None:
    with pytest.raises(ValueError, match="explicit closed allowlist"):
        NormalizationOutcome(
            NormalizationRunId("normalization-fixture"),
            _raw_record_id(),
            "normalizer-v1",
            "normalizer-commit-fixture",
            FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            None,
            (),
            (),
            preindex_scope_binding=_preindex_scope_binding(),
            evidence=(evidence,),
        )


def test_conflict_and_frame_abort_evidence_remain_valid_in_indexed_matrices() -> None:
    logical0, observation0, _ = _outcome_keys(0)
    logical1, observation1, _ = _outcome_keys(1)
    rejected = RawEventNormalizationOutcome(
        observation0,
        _outcome_scope_binding(0),
        RawEventDisposition.REJECTED,
        logical0,
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )
    aborted = RawEventNormalizationOutcome(
        observation1,
        _outcome_scope_binding(1),
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        logical1,
        evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )
    rejected_frame = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        _raw_record_id(),
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        2,
        (rejected, aborted),
        (),
        evidence=(
            NormalizationEvidence.DECODER_REJECTION,
            NormalizationEvidence.FRAME_ATOMIC_ABORT,
        ),
    )
    conflict = RawEventNormalizationOutcome(
        observation0,
        _outcome_scope_binding(0),
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
        logical0,
        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
    )
    conflict_frame = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        _raw_record_id(),
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
        1,
        (conflict,),
        (),
        evidence=(NormalizationEvidence.SOURCE_EVENT_CONFLICT,),
    )

    assert NormalizationEvidence.FRAME_ATOMIC_ABORT in rejected_frame.evidence
    assert conflict_frame.evidence == (NormalizationEvidence.SOURCE_EVENT_CONFLICT,)


def test_valid_preindex_failure_evidence_and_transition_remain_accepted() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=None,
        category=NormalizationFailureCategory.PROTOCOL_REJECTION,
    )
    outcome = NormalizationOutcome(
        normalization_run_id=run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        decoded_event_count=None,
        raw_event_outcomes=(),
        committed_materialization_keys=(),
        preindex_scope_binding=RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=scope,
        ),
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
        coverage_lineage=_typed_coverage_lineage(transition),
    )

    assert outcome.coverage_transition_ids == (transition.coverage_transition_id,)


def test_normalization_outcome_accepts_mixed_new_and_duplicate_indexes() -> None:
    logical0, observation0, materialization0 = _outcome_keys(0)
    logical1, observation1, _ = _outcome_keys(1)
    new = RawEventNormalizationOutcome(
        observation0,
        _outcome_scope_binding(0),
        RawEventDisposition.MATERIALIZED_NEW,
        logical0,
        materialization0,
    )
    duplicate = RawEventNormalizationOutcome(
        observation1,
        _outcome_scope_binding(1),
        RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
        logical1,
    )

    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        _raw_record_id(),
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.MIXED_SUCCESS,
        2,
        (new, duplicate),
        (materialization0,),
    )

    assert outcome.frame_status is FrameNormalizationStatus.MIXED_SUCCESS
    assert outcome.committed_materialization_keys == (materialization0,)


def test_conflict_outcome_aborts_candidates_and_commits_nothing() -> None:
    logical0, observation0, _ = _outcome_keys(0)
    logical1, observation1, _ = _outcome_keys(1)
    aborted = RawEventNormalizationOutcome(
        observation0,
        _outcome_scope_binding(0),
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        logical0,
        evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )
    conflict = RawEventNormalizationOutcome(
        observation1,
        _outcome_scope_binding(1),
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
        logical1,
        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
    )

    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        _raw_record_id(),
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
        2,
        (aborted, conflict),
        (),
        evidence=(
            NormalizationEvidence.FRAME_ATOMIC_ABORT,
            NormalizationEvidence.SOURCE_EVENT_CONFLICT,
        ),
    )

    assert outcome.committed_materialization_keys == ()
    assert all(item.materialization_key is None for item in outcome.raw_event_outcomes)


def test_indexed_frame_evidence_is_the_exact_canonical_index_union() -> None:
    logical0, observation0, _ = _outcome_keys(0)
    logical1, observation1, _ = _outcome_keys(1)
    rejected = RawEventNormalizationOutcome(
        observation0,
        _outcome_scope_binding(0),
        RawEventDisposition.REJECTED,
        logical0,
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )
    aborted = RawEventNormalizationOutcome(
        observation1,
        _outcome_scope_binding(1),
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        logical1,
        evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )
    common: dict[str, object] = {
        "normalization_run_id": NormalizationRunId("normalization-fixture"),
        "raw_record_id": _raw_record_id(),
        "normalizer_version": "normalizer-v1",
        "normalizer_commit": "normalizer-commit-fixture",
        "frame_status": FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        "decoded_event_count": 2,
        "raw_event_outcomes": (rejected, aborted),
        "committed_materialization_keys": (),
    }
    canonical_union = (
        NormalizationEvidence.DECODER_REJECTION,
        NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )

    assert NormalizationOutcome(**common, evidence=canonical_union).evidence == canonical_union  # type: ignore[arg-type]
    for invalid in (
        (),
        (NormalizationEvidence.DECODER_REJECTION,),
        (
            NormalizationEvidence.DECODER_REJECTION,
            NormalizationEvidence.LOCAL_CONTRACT_FAILURE,
        ),
        tuple(reversed(canonical_union)),
        (*canonical_union, NormalizationEvidence.PROVENANCE_MISMATCH),
    ):
        with pytest.raises(ValueError):
            NormalizationOutcome(**common, evidence=invalid)  # type: ignore[arg-type]


def test_source_conflict_requires_its_logical_source_key() -> None:
    with pytest.raises(ValueError, match="conflicting logical source key"):
        RawEventNormalizationOutcome(
            _outcome_keys()[1],
            _outcome_scope_binding(0),
            RawEventDisposition.SOURCE_EVENT_CONFLICT,
            evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
        )


def test_generic_rejected_index_cannot_carry_source_conflict_evidence() -> None:
    logical, observation, _ = _outcome_keys()

    with pytest.raises(ValueError, match="source-conflict disposition"):
        RawEventNormalizationOutcome(
            observation,
            _outcome_scope_binding(0),
            RawEventDisposition.REJECTED,
            logical,
            evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
        )


def test_source_conflict_disposition_requires_matching_evidence() -> None:
    logical, observation, _ = _outcome_keys()

    with pytest.raises(ValueError, match="source-conflict evidence"):
        RawEventNormalizationOutcome(
            observation,
            _outcome_scope_binding(0),
            RawEventDisposition.SOURCE_EVENT_CONFLICT,
            logical,
            evidence=NormalizationEvidence.DECODER_REJECTION,
        )


def test_conflict_index_requires_dedicated_frame_status() -> None:
    logical, observation, _ = _outcome_keys()
    conflict = RawEventNormalizationOutcome(
        observation,
        _outcome_scope_binding(0),
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
        logical,
        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
    )

    with pytest.raises(ValueError, match="indexed rejection"):
        NormalizationOutcome(
            NormalizationRunId("normalization-fixture"),
            _raw_record_id(),
            "normalizer-v1",
            "normalizer-commit-fixture",
            FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
            1,
            (conflict,),
            (),
            evidence=(NormalizationEvidence.SOURCE_EVENT_CONFLICT,),
        )


def test_frame_aborted_candidate_requires_its_known_logical_source_key() -> None:
    with pytest.raises(ValueError, match="known logical source key"):
        RawEventNormalizationOutcome(
            _outcome_keys()[1],
            _outcome_scope_binding(0),
            RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
            evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
        )


def test_raw_event_outcome_rejects_logical_source_from_another_feed() -> None:
    _, observation, materialization = _outcome_keys()
    foreign_logical = LogicalSourceKey(
        HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        SourceEventId('["hyperliquid-trade-v1",1,"BTC",2]'),
    )

    with pytest.raises(ValueError, match="raw-record feed product"):
        RawEventNormalizationOutcome(
            observation,
            _outcome_scope_binding(0),
            RawEventDisposition.MATERIALIZED_NEW,
            foreign_logical,
            materialization,
        )


def test_delivery_coverage_transition_id_is_rejected_before_normalization_outcome() -> None:
    spec = _wire_spec()
    scope = CoverageScope(
        domain=CoverageDomain.SILVER_DELIVERY,
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        subscription_spec_ids=(spec.subscription_spec_id,),
        canonical_instrument_ids=(_instrument().canonical_instrument_id,),
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture"),
        0,
        datetime(2026, 8, 26, 11, 0, tzinfo=UTC),
        100,
    )
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageTransitionId(
            _expected_identifier(
                "coverage-transition-v1",
                scope.coverage_scope_id.value,
                epoch.coverage_epoch_id.value,
                1,
                "complete",
                "uncertain",
                "transport-ambiguity",
                "transport-failure",
                "delivery-transition-fixture",
                "2026-08-26T11:00:01.000000Z",
                101,
            )
        )


def test_normalization_outcome_rejects_coverage_transition_from_another_collector_run() -> None:
    _raw_record_value, spec, _ = _raw_record()
    foreign_run = CollectorRunId("foreign-collector-run")
    foreign_session = ConnectionSessionIdentity(foreign_run, 0)
    foreign_attempt = SubscriptionAttemptIdentity(foreign_session, spec, 0)
    scope = CoverageScope(
        domain=CoverageDomain.BRONZE_INGRESS,
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        subscription_spec_ids=(spec.subscription_spec_id,),
        canonical_instrument_ids=(_instrument().canonical_instrument_id,),
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(scope, foreign_run, 0, activation_time, 100)
    initial = CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=CoverageStatus.COMPLETE,
        initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
        initial_evidence=CoverageEvidence(
            kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
            source=InitialActivationEvidenceSource(
                foreign_session,
                (
                    SubscriptionAttemptSnapshot(
                        foreign_attempt,
                        SubscriptionAttemptStatus.ACKNOWLEDGED,
                    ),
                ),
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time,
            observed_monotonic_ns=100,
        ),
    )
    transition, _ = reduce_coverage(
        initial,
        RequestedCoverageTransition(
            scope,
            epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.UNCERTAIN,
            CoverageReason.TRANSPORT_AMBIGUITY,
        ),
        CoverageEvidence(
            kind=CoverageEvidenceKind.TRANSPORT_FAILURE,
            source=TransportAmbiguityEvidenceSource(
                BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
                foreign_session,
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time + timedelta(seconds=1),
            observed_monotonic_ns=101,
        ),
    )

    with pytest.raises(ValueError):
        _typed_coverage_lineage(transition)


@pytest.mark.parametrize(
    "evidence_kind",
    [
        CoverageEvidenceKind.TRANSPORT_FAILURE,
        CoverageEvidenceKind.RECONNECT,
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.SOURCE_SEQUENCE,
    ],
)
def test_normalization_outcome_rejects_unrelated_coverage_transition_kinds(
    evidence_kind: CoverageEvidenceKind,
) -> None:
    _raw_record_value, transition = _unrelated_coverage_transition(evidence_kind)

    with pytest.raises(ValueError):
        _typed_coverage_lineage(transition)


def test_normalization_outcome_rejects_failure_transition_from_another_normalization_run() -> None:
    raw_record, spec, attempt = _raw_record()
    scope = CoverageScope(
        domain=CoverageDomain.SILVER_NORMALIZATION,
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        subscription_spec_ids=(spec.subscription_spec_id,),
        canonical_instrument_ids=(_instrument().canonical_instrument_id,),
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        raw_record.collector_run_id,
        0,
        activation_time,
        100,
    )
    initial = CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=CoverageStatus.COMPLETE,
        initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
        initial_evidence=CoverageEvidence(
            kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
            source=InitialActivationEvidenceSource(
                raw_record.connection_session,
                (
                    SubscriptionAttemptSnapshot(
                        attempt,
                        SubscriptionAttemptStatus.ACKNOWLEDGED,
                    ),
                ),
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time,
            observed_monotonic_ns=100,
        ),
    )
    transition, _ = reduce_coverage(
        initial,
        RequestedCoverageTransition(
            scope,
            epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
        ),
        CoverageEvidence(
            kind=CoverageEvidenceKind.NORMALIZATION_FAILURE,
            source=NormalizationFailureEvidenceSource(
                raw_record.raw_record_id,
                NormalizationRunId("foreign-normalization-run"),
                0,
                SourceEventId("source-fixture"),
                NormalizationFailureCategory.DECODER_REJECTION,
                scope.coverage_scope_id,
            ),
            scope=scope,
            epoch=epoch,
            observed_at=activation_time + timedelta(seconds=1),
            observed_monotonic_ns=101,
        ),
    )

    with pytest.raises(ValueError, match="normalization run"):
        NormalizationOutcome(
            normalization_run_id=NormalizationRunId("expected-normalization-run"),
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            committed_materialization_keys=(),
            preindex_scope_binding=_preindex_scope_binding(raw_record, scope=scope),
            evidence=(NormalizationEvidence.DECODER_REJECTION,),
            coverage_lineage=_typed_coverage_lineage(transition),
        )


def test_normalization_outcome_rejects_failure_transition_for_another_raw_record() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, _, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=None,
        category=NormalizationFailureCategory.PROTOCOL_REJECTION,
    )
    other_raw_record = replace(raw_record, ingress_ordinal=raw_record.ingress_ordinal + 1)

    with pytest.raises(ValueError, match="outcome raw record"):
        NormalizationOutcome(
            normalization_run_id=run_id,
            raw_record_id=other_raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            committed_materialization_keys=(),
            preindex_scope_binding=_preindex_scope_binding(other_raw_record),
            evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
            coverage_lineage=_typed_coverage_lineage(transition),
        )


def test_normalization_outcome_rejects_contradictory_transition_failure_category() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, _, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=None,
        category=NormalizationFailureCategory.PROTOCOL_REJECTION,
    )

    with pytest.raises(ValueError, match="frame failure evidence"):
        NormalizationOutcome(
            normalization_run_id=run_id,
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            committed_materialization_keys=(),
            preindex_scope_binding=_preindex_scope_binding(raw_record),
            evidence=(NormalizationEvidence.DECODER_REJECTION,),
            coverage_lineage=_typed_coverage_lineage(transition),
        )


def test_normalization_outcome_binds_failure_transition_to_exact_index() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, valid_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    logical = LogicalSourceKey(
        BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        SourceEventId("source-fixture"),
    )
    rejected = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.REJECTED,
        logical,
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )
    outcome = NormalizationOutcome(
        normalization_run_id=run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        decoded_event_count=1,
        raw_event_outcomes=(rejected,),
        committed_materialization_keys=(),
        evidence=(NormalizationEvidence.DECODER_REJECTION,),
        coverage_lineage=_typed_coverage_lineage(valid_transition),
    )
    assert outcome.coverage_transition_ids == (valid_transition.coverage_transition_id,)

    _, _, wrong_index_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        category=NormalizationFailureCategory.DECODER_REJECTION,
        source_event_id=SourceEventId("source-fixture"),
    )
    with pytest.raises(ValueError, match="match one rejected frame index"):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=_typed_coverage_lineage(wrong_index_transition),
        )

    _, _, wrong_source_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_event_id=SourceEventId("another-source-event"),
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    with pytest.raises(ValueError, match="source identity must exactly match"):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=_typed_coverage_lineage(wrong_source_transition),
        )


def test_indexed_normalization_failure_without_source_identity_binds_exact_lineage() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_identity_established=False,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    rejected = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.REJECTED,
        logical_source_key=None,
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )

    outcome = NormalizationOutcome(
        normalization_run_id=run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        decoded_event_count=1,
        raw_event_outcomes=(rejected,),
        committed_materialization_keys=(),
        evidence=(NormalizationEvidence.DECODER_REJECTION,),
        coverage_lineage=_typed_coverage_lineage(transition),
    )

    assert json.loads(transition.coverage_transition_id.value)[5:7] == [
        CoverageStatus.CONFIRMED_INCOMPLETE.value,
        CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE.value,
    ]
    assert outcome.raw_event_outcomes[0].logical_source_key is None


def test_indexed_normalization_failure_source_identity_is_nullable_but_exact() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, without_source_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_identity_established=False,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    no_logical_source = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.REJECTED,
        logical_source_key=None,
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )

    def outcome_for(
        item: RawEventNormalizationOutcome,
        transition: CoverageTransition,
    ) -> NormalizationOutcome:
        return NormalizationOutcome(
            normalization_run_id=run_id,
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
            decoded_event_count=1,
            raw_event_outcomes=(item,),
            committed_materialization_keys=(),
            evidence=(NormalizationEvidence.DECODER_REJECTION,),
            coverage_lineage=_typed_coverage_lineage(transition),
        )

    known_logical_source = LogicalSourceKey(
        raw_record.feed_product.feed_product_id,
        SourceEventId("source-fixture"),
    )

    with pytest.raises(ValueError, match="source identity must exactly match"):
        outcome_for(
            replace(no_logical_source, logical_source_key=known_logical_source),
            without_source_transition,
        )

    _, _, with_source_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_event_id=SourceEventId("source-fixture"),
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    with pytest.raises(ValueError, match="source identity must exactly match"):
        outcome_for(no_logical_source, with_source_transition)

    unequal_logical_source = LogicalSourceKey(
        raw_record.feed_product.feed_product_id,
        SourceEventId("different-source-fixture"),
    )
    with pytest.raises(ValueError, match="source identity must exactly match"):
        outcome_for(
            replace(no_logical_source, logical_source_key=unequal_logical_source),
            with_source_transition,
        )

    _, _, wrong_index_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        source_identity_established=False,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    with pytest.raises(ValueError, match="match one rejected frame index"):
        outcome_for(no_logical_source, wrong_index_transition)

    eth_instrument = Instrument(
        Venue.BINANCE,
        InstrumentType.SPOT,
        "ETH",
        "USDT",
        "ETHUSDT",
        "ETHUSDT",
    )
    _, _, wrong_scope_transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_identity_established=False,
        category=NormalizationFailureCategory.DECODER_REJECTION,
        canonical_instrument_id=eth_instrument.canonical_instrument_id,
    )
    with pytest.raises(ValueError):
        outcome_for(no_logical_source, wrong_scope_transition)


def test_normalization_outcome_binds_source_conflict_transition_to_exact_lineage() -> None:
    source_event_id = SourceEventId("source-conflict-fixture")
    raw_record, scope, valid_transition = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=0,
    )
    conflict = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
        LogicalSourceKey(raw_record.feed_product.feed_product_id, source_event_id),
        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
    )
    outcome = NormalizationOutcome(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
        decoded_event_count=1,
        raw_event_outcomes=(conflict,),
        committed_materialization_keys=(),
        evidence=(NormalizationEvidence.SOURCE_EVENT_CONFLICT,),
        coverage_lineage=_typed_coverage_lineage(valid_transition),
    )
    assert outcome.coverage_transition_ids == (valid_transition.coverage_transition_id,)

    other_raw_record = replace(raw_record, ingress_ordinal=raw_record.ingress_ordinal + 1)
    other_conflict = replace(
        conflict,
        observation_key=ObservationKey(other_raw_record.raw_record_id, 0),
        normalization_scope_binding=_outcome_scope_binding(
            0,
            raw_record=other_raw_record,
            scope=scope,
        ),
    )
    with pytest.raises(ValueError, match="outcome raw record"):
        replace(
            outcome,
            raw_record_id=other_raw_record.raw_record_id,
            raw_event_outcomes=(other_conflict,),
            coverage_transition_ids=(),
        )

    _, _, wrong_source_transition = _source_conflict_transition(
        source_event_id=SourceEventId("another-source-event"),
        raw_event_index=0,
    )
    with pytest.raises(ValueError, match="match one conflicting frame index"):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=_typed_coverage_lineage(wrong_source_transition),
        )

    _, _, wrong_index_transition = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=1,
    )
    with pytest.raises(ValueError, match="match one conflicting frame index"):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=_typed_coverage_lineage(wrong_index_transition),
        )


@pytest.mark.parametrize("foreign_scope_kind", ["instrument", "subscription-spec"])
def test_source_conflict_transition_cannot_cross_event_scope(
    foreign_scope_kind: str,
) -> None:
    source_event_id = SourceEventId("scope-bound-source-conflict")
    raw_record, scope, transition = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=0,
    )
    conflict = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
        LogicalSourceKey(raw_record.feed_product.feed_product_id, source_event_id),
        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
    )
    outcome = NormalizationOutcome(
        NormalizationRunId("normalization-fixture"),
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
        1,
        (conflict,),
        (),
        evidence=(NormalizationEvidence.SOURCE_EVENT_CONFLICT,),
        coverage_lineage=_typed_coverage_lineage(transition),
    )
    eth_instrument = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="ETH",
        quote_asset="USDT",
        venue_market_id="ETHUSDT",
        native_symbol="ETHUSDT",
    )
    foreign_spec = _wire_spec("ETHUSDT") if foreign_scope_kind == "subscription-spec" else None
    _, _, foreign_transition = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=0,
        scope_spec=foreign_spec,
        canonical_instrument_id=eth_instrument.canonical_instrument_id,
    )

    with pytest.raises(ValueError):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=_typed_coverage_lineage(foreign_transition),
        )


def test_normalization_scope_bindings_are_factory_only_and_plan_bound() -> None:
    raw_record, spec, _ = _raw_record()
    valid_scope = _normalization_scope(raw_record, spec)
    event_binding = _outcome_scope_binding(0, raw_record=raw_record, scope=valid_scope)
    frame_binding = RawFrameNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        coverage_scope=valid_scope,
    )

    assert event_binding.coverage_scope_id == valid_scope.coverage_scope_id
    assert frame_binding.coverage_scope_id == valid_scope.coverage_scope_id
    plan_binding = raw_record.subscription_plan.instrument_bindings[0]
    assert event_binding.canonical_components() == (
        "raw-event-normalization-scope-binding-v1",
        raw_record.raw_record_id.value,
        raw_record.full_record_integrity_sha256,
        raw_record.subscription_plan.subscription_plan_id.value,
        0,
        spec.subscription_spec_id.value,
        raw_record.subscription_attempt_snapshots[
            0
        ].subscription_attempt.subscription_attempt_id.value,
        "acknowledged",
        hashlib.sha256(plan_binding.canonical_instrument_id.encode("utf-8")).hexdigest(),
        (
            "public-source-selector-v1",
            "binance-spot-trade-stream",
            "btcusdt@trade",
        ),
        valid_scope.coverage_scope_id.value,
    )
    assert frame_binding.canonical_components() == (
        "raw-frame-normalization-scope-binding-v1",
        raw_record.raw_record_id.value,
        raw_record.full_record_integrity_sha256,
        raw_record.subscription_plan.subscription_plan_id.value,
        valid_scope.coverage_scope_id.value,
    )
    with pytest.raises(TypeError, match="from_raw_record"):
        RawEventNormalizationScopeBinding()
    with pytest.raises(TypeError, match="from_raw_record"):
        RawFrameNormalizationScopeBinding()
    with pytest.raises(TypeError):
        replace(event_binding, raw_event_index=1)

    foreign_instrument = Instrument(
        Venue.BINANCE,
        InstrumentType.SPOT,
        "ETH",
        "USDT",
        "ETHUSDT",
        "ETHUSDT",
    )
    foreign_scope = _normalization_scope(
        raw_record,
        spec,
        canonical_instrument_id=foreign_instrument.canonical_instrument_id,
    )
    with pytest.raises(ValueError, match="instrument outside"):
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=foreign_scope,
        )


def test_preindex_scope_requires_complete_relevant_multi_spec_plan_scope() -> None:
    raw_record, btc_spec, eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()

    def scope_for(
        specs: tuple[SubscriptionSpecIdentity, ...],
        instruments: tuple[Instrument, ...],
        *,
        event_family: str = "trade",
        event_family_schema_version: int = 2,
        payload_type: str = "trade",
    ) -> CoverageScope:
        return CoverageScope(
            domain=CoverageDomain.SILVER_NORMALIZATION,
            feed_product_id=raw_record.feed_product.feed_product_id,
            subscription_spec_ids=tuple(
                sorted(
                    (spec.subscription_spec_id for spec in specs),
                    key=lambda item: item.value,
                )
            ),
            canonical_instrument_ids=tuple(
                sorted(instrument.canonical_instrument_id for instrument in instruments)
            ),
            event_family=event_family,
            event_family_schema_version=event_family_schema_version,
            payload_type=payload_type,
        )

    complete_scope = scope_for(
        (btc_spec, eth_spec),
        (btc_instrument, eth_instrument),
    )
    reordered_scope = scope_for(
        (eth_spec, btc_spec),
        (eth_instrument, btc_instrument),
    )
    complete_binding = RawFrameNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        coverage_scope=complete_scope,
    )
    reordered_binding = RawFrameNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        coverage_scope=reordered_scope,
    )

    assert complete_scope.coverage_scope_id == reordered_scope.coverage_scope_id
    assert complete_binding.canonical_components() == reordered_binding.canonical_components()

    one_spec_subset = scope_for((btc_spec,), (btc_instrument,))
    with pytest.raises(ValueError, match="complete relevant subscription-plan scope"):
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=one_spec_subset,
        )

    missing_instrument = scope_for(
        (btc_spec, eth_spec),
        (btc_instrument,),
    )
    with pytest.raises(ValueError, match="instrument outside or missing"):
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=missing_instrument,
        )

    sol_spec = _wire_spec("SOLUSDT", request_id=3)
    sol_instrument = Instrument(
        Venue.BINANCE,
        InstrumentType.SPOT,
        "SOL",
        "USDT",
        "SOLUSDT",
        "SOLUSDT",
    )
    with pytest.raises(ValueError, match="complete relevant subscription-plan scope"):
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=scope_for(
                (btc_spec, eth_spec, sol_spec),
                (btc_instrument, eth_instrument, sol_instrument),
            ),
        )
    with pytest.raises(ValueError, match="instrument outside or missing"):
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=scope_for(
                (btc_spec, eth_spec),
                (btc_instrument, sol_instrument),
            ),
        )


@pytest.mark.parametrize(
    ("event_family", "family_version", "payload_type"),
    [
        ("book-delta", 2, "trade"),
        ("trade", 3, "trade"),
        ("trade", 2, "book-delta"),
    ],
)
def test_preindex_scope_rejects_mismatched_relevant_binding(
    event_family: str,
    family_version: int,
    payload_type: str,
) -> None:
    raw_record, btc_spec, eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    mismatched_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        tuple(
            sorted(
                (btc_spec.subscription_spec_id, eth_spec.subscription_spec_id),
                key=lambda item: item.value,
            )
        ),
        tuple(
            sorted((btc_instrument.canonical_instrument_id, eth_instrument.canonical_instrument_id))
        ),
        event_family,
        family_version,
        payload_type,
    )

    with pytest.raises(ValueError, match="complete relevant subscription-plan scope"):
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=mismatched_scope,
        )


def test_indexed_scope_remains_exactly_narrow_with_a_multi_spec_plan() -> None:
    raw_record, btc_spec, _, btc_instrument, _ = _multi_spec_raw_record()
    btc_attempt = next(
        snapshot.subscription_attempt
        for snapshot in raw_record.subscription_attempt_snapshots
        if snapshot.subscription_spec.subscription_spec_id == btc_spec.subscription_spec_id
    )
    decoded = DecodedWirePayloadContext(
        raw_record.raw_record_id,
        1,
        (DecodedEventSubscriptionBinding(0, btc_spec, btc_attempt),),
    )
    narrow_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        (btc_spec.subscription_spec_id,),
        (btc_instrument.canonical_instrument_id,),
        "trade",
        2,
        "trade",
    )
    plan_binding = next(
        binding
        for binding in raw_record.subscription_plan.instrument_bindings
        if binding.subscription_spec_id == btc_spec.subscription_spec_id
    )

    binding = RawEventNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        decoded_wire_payload=decoded,
        raw_event_index=0,
        source_selector=plan_binding.source_selector,
        coverage_scope=narrow_scope,
    )

    assert binding.subscription_spec_id == btc_spec.subscription_spec_id
    assert binding.coverage_scope_id == narrow_scope.coverage_scope_id


@pytest.mark.parametrize(
    "attempt_status",
    [
        SubscriptionAttemptStatus.PENDING,
        SubscriptionAttemptStatus.SEND_STARTED,
        SubscriptionAttemptStatus.SENT,
    ],
)
def test_non_rejected_event_outcomes_require_acknowledged_attempt(
    attempt_status: SubscriptionAttemptStatus,
) -> None:
    raw_record, spec, attempt = _raw_record(attempt_status=attempt_status)
    scope = _normalization_scope(raw_record, spec)
    binding = _outcome_scope_binding(
        0,
        raw_record=raw_record,
        spec=spec,
        attempt=attempt,
        scope=scope,
    )
    observation = ObservationKey(raw_record.raw_record_id, 0)
    logical = LogicalSourceKey(
        raw_record.feed_product.feed_product_id,
        SourceEventId("pre-ack-source-event"),
    )
    materialization = MaterializationKey(
        NormalizationRunId("normalization-fixture"),
        observation,
        EventFamily.TRADE,
        2,
        PayloadType.TRADE,
    )

    with pytest.raises(ValueError, match="acknowledged"):
        RawEventNormalizationOutcome(
            observation,
            binding,
            RawEventDisposition.MATERIALIZED_NEW,
            logical,
            materialization,
        )
    rejected = RawEventNormalizationOutcome(
        observation,
        binding,
        RawEventDisposition.REJECTED,
        logical,
        evidence=NormalizationEvidence.PROTOCOL_REJECTION,
    )
    assert rejected.disposition is RawEventDisposition.REJECTED


def test_normalization_failure_transition_cannot_point_to_an_aborted_candidate() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    rejected = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.REJECTED,
        LogicalSourceKey(
            BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            SourceEventId("rejected-source-fixture"),
        ),
        evidence=NormalizationEvidence.PROTOCOL_REJECTION,
    )
    aborted = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 1),
        _outcome_scope_binding(1, raw_record=raw_record, scope=scope),
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        LogicalSourceKey(
            BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            SourceEventId("aborted-source-fixture"),
        ),
        evidence=NormalizationEvidence.FRAME_ATOMIC_ABORT,
    )

    with pytest.raises(ValueError, match="match one rejected frame index"):
        NormalizationOutcome(
            normalization_run_id=run_id,
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
            decoded_event_count=2,
            raw_event_outcomes=(rejected, aborted),
            committed_materialization_keys=(),
            evidence=(
                NormalizationEvidence.DECODER_REJECTION,
                NormalizationEvidence.PROTOCOL_REJECTION,
            ),
            coverage_lineage=_typed_coverage_lineage(transition),
        )


@pytest.mark.parametrize(
    "invalid_outcome",
    [
        pytest.param(
            lambda: NormalizationOutcome(
                NormalizationRunId("normalization-fixture"),
                _raw_record_id(),
                "normalizer-v1",
                "normalizer-commit-fixture",
                FrameNormalizationStatus.CONTROL_NO_EVENT,
                0,
                (),
                (),
            ),
            id="control-with-count",
        ),
        pytest.param(
            lambda: NormalizationOutcome(
                NormalizationRunId("normalization-fixture"),
                _raw_record_id(),
                "normalizer-v1",
                "normalizer-commit-fixture",
                FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
                None,
                (),
                (),
                preindex_scope_binding=_preindex_scope_binding(),
            ),
            id="rejection-without-evidence",
        ),
        pytest.param(
            lambda: NormalizationOutcome(
                NormalizationRunId("normalization-fixture"),
                _raw_record_id(),
                "normalizer-v1",
                "normalizer-commit-fixture",
                FrameNormalizationStatus.MIXED_SUCCESS,
                1,
                (
                    RawEventNormalizationOutcome(
                        _outcome_keys()[1],
                        _outcome_scope_binding(0),
                        RawEventDisposition.MATERIALIZED_NEW,
                        _outcome_keys()[0],
                        _outcome_keys()[2],
                    ),
                ),
                (_outcome_keys()[2],),
            ),
            id="mixed-without-duplicate",
        ),
        pytest.param(
            lambda: NormalizationOutcome(
                NormalizationRunId("normalization-fixture"),
                _raw_record_id(),
                "normalizer-v1",
                "normalizer-commit-fixture",
                FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
                1,
                (
                    RawEventNormalizationOutcome(
                        _outcome_keys()[1],
                        _outcome_scope_binding(0),
                        RawEventDisposition.SOURCE_EVENT_CONFLICT,
                        _outcome_keys()[0],
                        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
                    ),
                ),
                (),
                evidence=(),
            ),
            id="conflict-without-frame-evidence",
        ),
    ],
)
def test_normalization_outcome_rejects_contradictory_frame_matrices(
    invalid_outcome: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        invalid_outcome()


def test_frame_atomic_abort_evidence_has_byte_exact_outcome_only_identity() -> None:
    outcome, transition, abort = _typed_rejected_frame_with_abort()
    cause_content = _expected_identifier(
        "frame-atomic-abort-primary-cause-content-v1",
        (transition.evidence.coverage_evidence_id.value,),
    )
    cause_sha256 = hashlib.sha256(cause_content.encode()).hexdigest()

    assert abort.primary_cause_content_sha256 == cause_sha256
    assert abort.frame_atomic_abort_evidence_id.value == _expected_identifier(
        "frame-atomic-abort-evidence-v1",
        outcome.raw_record_id.value,
        outcome.normalization_run_id.value,
        0,
        "frame-aborted-source",
        abort.identified_coverage_scope_id.value,
        1,
        cause_sha256,
    )
    assert FrameAtomicAbortEvidenceId(abort.frame_atomic_abort_evidence_id.value) == (
        abort.frame_atomic_abort_evidence_id
    )
    assert not isinstance(abort, (CoverageEvidence, CoverageTransition))
    assert hash(abort) == hash(abort)
    with pytest.raises(FrozenInstanceError):
        abort.raw_event_index = 9  # type: ignore[misc]
    assert not hasattr(abort, "__dict__")


def test_frame_atomic_abort_rejects_invalid_primary_cause_lineage() -> None:
    _outcome, transition, abort = _typed_rejected_frame_with_abort()
    _raw, unrelated = _unrelated_coverage_transition(CoverageEvidenceKind.SOURCE_SEQUENCE)
    other_raw_record = replace(_raw_record()[0], ingress_ordinal=11)
    _preindex_raw, _preindex_scope, preindex_transition = _normalization_failure_transition(
        normalization_run_id=abort.normalization_run_id,
        raw_event_index=None,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )

    with pytest.raises(ValueError, match="at least one primary cause"):
        replace(abort, primary_cause_coverage_evidence_ids=())
    with pytest.raises(ValueError, match="unique"):
        replace(
            abort,
            primary_cause_coverage_evidence_ids=(
                transition.evidence.coverage_evidence_id,
                transition.evidence.coverage_evidence_id,
            ),
        )
    with pytest.raises(ValueError, match="normalization failures or source conflicts"):
        replace(
            abort,
            primary_cause_coverage_evidence_ids=(unrelated.evidence.coverage_evidence_id,),
        )
    with pytest.raises(ValueError, match="another indexed item"):
        replace(abort, raw_event_index=1)
    with pytest.raises(ValueError, match="another indexed item"):
        replace(
            abort,
            primary_cause_coverage_evidence_ids=(
                preindex_transition.evidence.coverage_evidence_id,
            ),
        )
    with pytest.raises(ValueError, match="match the frame"):
        replace(abort, raw_record_id=other_raw_record.raw_record_id)
    with pytest.raises(ValueError, match="match the frame"):
        replace(abort, normalization_run_id=NormalizationRunId("another-run"))


def test_frame_atomic_abort_cause_set_is_sorted_unique_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, first = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        source_event_id=SourceEventId("first-rejected-source"),
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    _same_raw, _same_scope, second = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=2,
        source_event_id=SourceEventId("second-rejected-source"),
        category=NormalizationFailureCategory.UNKNOWN_INSTRUMENT,
    )
    causes = tuple(
        sorted(
            (
                first.evidence.coverage_evidence_id,
                second.evidence.coverage_evidence_id,
            ),
            key=lambda item: item.value,
        )
    )
    valid = FrameAtomicAbortEvidenceSource(
        raw_record.raw_record_id,
        run_id,
        0,
        SourceEventId("aborted-source"),
        scope.coverage_scope_id,
        causes,
    )
    with pytest.raises(ValueError, match="sorted"):
        replace(valid, primary_cause_coverage_evidence_ids=tuple(reversed(causes)))

    monkeypatch.setattr(market_event_v3_module, "MAX_NORMALIZATION_OUTCOME_ITEMS", 1)
    with pytest.raises(ValueError, match="item count"):
        replace(valid)


def test_typed_coverage_lineage_and_outcome_have_byte_exact_content_ids() -> None:
    outcome, transition, abort = _typed_rejected_frame_with_abort()
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)
    batch = lineage.coverage_mutation_batch
    expected_content = _expected_identifier(
        "normalization-coverage-lineage-content-v2",
        batch.coverage_mutation_batch_id.value,
        lineage.raw_fanout_binding.raw_coverage_fanout_binding_id.value,
        (transition.evidence.coverage_evidence_id.value,),
        (abort.frame_atomic_abort_evidence_id.value,),
        (),
        (transition.coverage_transition_id.value,),
        (),
        tuple(
            state.coverage_state_reference_id.value for state in batch.resulting_state_references
        ),
    )
    expected_content_sha256 = hashlib.sha256(expected_content.encode()).hexdigest()
    expected_lineage_id = _expected_identifier(
        "normalization-coverage-lineage-v2",
        batch.coverage_mutation_batch_id.value,
        1,
        1,
        0,
        1,
        0,
        1,
        expected_content_sha256,
    )
    outcome_content = _expected_identifier(
        "normalization-outcome-content-v2",
        tuple(item.raw_event_normalization_outcome_id.value for item in outcome.raw_event_outcomes),
        (),
        ("decoder_rejection", "frame_atomic_abort"),
        expected_lineage_id,
        None,
    )

    assert lineage.canonical_content == expected_content
    assert lineage.content_sha256 == expected_content_sha256
    assert lineage.normalization_coverage_lineage_id.value == expected_lineage_id
    assert NormalizationCoverageLineageId(expected_lineage_id) == (
        lineage.normalization_coverage_lineage_id
    )
    assert (
        outcome.normalization_outcome_content_sha256
        == hashlib.sha256(outcome_content.encode()).hexdigest()
    )
    assert outcome.coverage_transition_ids == (transition.coverage_transition_id,)
    assert lineage.primary_evidence == (transition.evidence,)
    assert lineage.coverage_transitions == batch.transitions
    assert lineage.coverage_no_ops == ()
    assert lineage.resulting_state_references == batch.resulting_state_references


def test_typed_lineage_and_abort_stored_verification_recompute_complete_content() -> None:
    outcome, _transition, abort = _typed_rejected_frame_with_abort()
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)

    assert (
        FrameAtomicAbortEvidenceSource.from_stored(
            raw_record_id=abort.raw_record_id,
            normalization_run_id=abort.normalization_run_id,
            raw_event_index=abort.raw_event_index,
            source_event_id=abort.source_event_id,
            identified_coverage_scope_id=abort.identified_coverage_scope_id,
            primary_cause_coverage_evidence_ids=abort.primary_cause_coverage_evidence_ids,
            expected_primary_cause_content_sha256=abort.primary_cause_content_sha256,
            expected_evidence_id=abort.frame_atomic_abort_evidence_id,
        )
        == abort
    )
    assert (
        NormalizationCoverageLineage.from_stored(
            coverage_mutation_batch=lineage.coverage_mutation_batch,
            frame_atomic_abort_evidence=lineage.frame_atomic_abort_evidence,
            raw_fanout_binding=lineage.raw_fanout_binding,
            additional_primary_evidence=lineage.additional_primary_evidence,
            source_conflict_bindings=lineage.source_conflict_bindings,
            expected_canonical_content=lineage.canonical_content,
            expected_lineage_id=lineage.normalization_coverage_lineage_id,
        )
        == lineage
    )
    with pytest.raises(ValueError, match="does not match"):
        FrameAtomicAbortEvidenceSource.from_stored(
            raw_record_id=abort.raw_record_id,
            normalization_run_id=abort.normalization_run_id,
            raw_event_index=abort.raw_event_index,
            source_event_id=abort.source_event_id,
            identified_coverage_scope_id=abort.identified_coverage_scope_id,
            primary_cause_coverage_evidence_ids=abort.primary_cause_coverage_evidence_ids,
            expected_primary_cause_content_sha256="0" * 64,
            expected_evidence_id=abort.frame_atomic_abort_evidence_id,
        )


def test_raw_coverage_fanout_binding_has_byte_exact_snapshot_and_stored_identity() -> None:
    raw_record, spec, attempt = _raw_record()
    _raw, _scope, transition = _normalization_failure_transition(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_event_index=0,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    batch = _prepared_batch_for_transition(transition)
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=batch.fanout_proof,
    )
    snapshot_content = _expected_identifier(
        "raw-coverage-fanout-snapshot-content-v1",
        (
            (
                spec.subscription_spec_id.value,
                attempt.subscription_attempt_id.value,
                SubscriptionAttemptStatus.ACKNOWLEDGED.value,
            ),
        ),
    )
    snapshot_sha256 = hashlib.sha256(snapshot_content.encode()).hexdigest()
    content = _expected_identifier(
        "raw-coverage-fanout-binding-content-v1",
        raw_record.raw_record_id.value,
        raw_record.full_record_integrity_sha256,
        raw_record.subscription_plan.subscription_plan_id.value,
        raw_record.connection_session.connection_session_id.value,
        batch.fanout_proof.coverage_fanout_proof_id.value,
        snapshot_sha256,
    )
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    expected_id = _expected_identifier(
        "raw-coverage-fanout-binding-v1",
        raw_record.raw_record_id.value,
        batch.fanout_proof.coverage_fanout_proof_id.value,
        snapshot_sha256,
        content_sha256,
    )

    assert binding.subscription_snapshot_content_sha256 == snapshot_sha256
    assert binding.canonical_content == content
    assert binding.content_sha256 == content_sha256
    assert binding.raw_coverage_fanout_binding_id.value == expected_id
    assert RawCoverageFanoutBindingId(expected_id) == binding.raw_coverage_fanout_binding_id
    assert (
        RawCoverageFanoutBinding.from_stored(
            raw_record=raw_record,
            coverage_fanout_proof=batch.fanout_proof,
            expected_subscription_snapshot_content_sha256=snapshot_sha256,
            expected_canonical_content=content,
            expected_content_sha256=content_sha256,
            expected_binding_id=binding.raw_coverage_fanout_binding_id,
        )
        == binding
    )
    assert not hasattr(binding, "application_message_bytes")
    assert not hasattr(binding, "subscription_attempt_snapshots")
    assert not hasattr(binding, "raw_record")
    with pytest.raises(TypeError, match="from_raw_record"):
        RawCoverageFanoutBinding()


def test_raw_coverage_fanout_binding_rejects_foreign_or_incomplete_snapshot_lineage() -> None:
    raw_record, spec, _attempt = _raw_record()
    _raw, _scope, transition = _normalization_failure_transition(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_event_index=0,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    proof = _prepared_batch_for_transition(transition).fanout_proof
    sent_raw = _raw_record(attempt_status=SubscriptionAttemptStatus.SENT)[0]
    later_attempt_raw = _raw_record(attempt_ordinal=1)[0]
    multi_raw = _multi_spec_raw_record()[0]
    foreign_session = ConnectionSessionIdentity(raw_record.collector_run_id, 1)
    foreign_attempt = SubscriptionAttemptIdentity(foreign_session, spec, 0)
    foreign_session_raw = RawMarketDataRecord(
        feed_product=raw_record.feed_product,
        collector_run_id=raw_record.collector_run_id,
        connection_session=foreign_session,
        subscription_plan=raw_record.subscription_plan,
        subscription_attempt_snapshots=(
            SubscriptionAttemptSnapshot(
                foreign_attempt,
                SubscriptionAttemptStatus.ACKNOWLEDGED,
            ),
        ),
        ingress_ordinal=raw_record.ingress_ordinal,
        frame_kind=raw_record.frame_kind,
        application_message_bytes=raw_record.application_message_bytes,
        received_time=raw_record.received_time,
        received_monotonic_ns=raw_record.received_monotonic_ns,
        collector_version=raw_record.collector_version,
        collector_commit=raw_record.collector_commit,
    )

    for invalid_raw in (sent_raw, later_attempt_raw, multi_raw, foreign_session_raw):
        with pytest.raises(ValueError):
            RawCoverageFanoutBinding.from_raw_record(
                raw_record=invalid_raw,
                coverage_fanout_proof=proof,
            )


def test_raw_coverage_fanout_binding_stored_verification_rejects_every_digest_tamper() -> None:
    raw_record = _raw_record()[0]
    _raw, _scope, transition = _normalization_failure_transition(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_event_index=0,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    proof = _prepared_batch_for_transition(transition).fanout_proof
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=proof,
    )

    def verify(
        *,
        snapshot_sha256: str = binding.subscription_snapshot_content_sha256,
        canonical_content: str = binding.canonical_content,
        content_sha256: str = binding.content_sha256,
        binding_id: RawCoverageFanoutBindingId = binding.raw_coverage_fanout_binding_id,
    ) -> RawCoverageFanoutBinding:
        return RawCoverageFanoutBinding.from_stored(
            raw_record=raw_record,
            coverage_fanout_proof=proof,
            expected_subscription_snapshot_content_sha256=snapshot_sha256,
            expected_canonical_content=canonical_content,
            expected_content_sha256=content_sha256,
            expected_binding_id=binding_id,
        )

    with pytest.raises(ValueError, match="doesn't match"):
        verify(snapshot_sha256="f" * 64)
    with pytest.raises(ValueError, match="doesn't match"):
        verify(content_sha256="f" * 64)
    with pytest.raises(ValueError, match="doesn't match"):
        verify(canonical_content=f"{binding.canonical_content} ")
    foreign_id = RawCoverageFanoutBindingId(
        _expected_identifier(
            "raw-coverage-fanout-binding-v1",
            binding.raw_record_id.value,
            binding.coverage_fanout_proof_id.value,
            binding.subscription_snapshot_content_sha256,
            "f" * 64,
        )
    )
    with pytest.raises(ValueError, match="doesn't match"):
        verify(binding_id=foreign_id)


def test_raw_coverage_preindex_fanout_rederives_complete_possibly_active_snapshot() -> None:
    _raw, _scope, transition = _normalization_failure_transition(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_event_index=None,
        category=NormalizationFailureCategory.PROTOCOL_REJECTION,
    )
    batch = _prepared_batch_for_transition(transition)
    raw_record = _raw_record()[0]
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=batch.fanout_proof,
    )

    assert batch.fanout_proof.kind.value == "all-possibly-active"
    assert binding.raw_record_id == raw_record.raw_record_id
    with pytest.raises(ValueError):
        RawCoverageFanoutBinding.from_raw_record(
            raw_record=_raw_record(attempt_status=SubscriptionAttemptStatus.PENDING)[0],
            coverage_fanout_proof=batch.fanout_proof,
        )


def test_legacy_transition_empty_factory_preserves_exact_existing_outcome_identity() -> None:
    direct = _materialized_outcome_for_delivery(item_count=2)
    legacy = NormalizationOutcome.legacy_transition_empty(
        normalization_run_id=direct.normalization_run_id,
        raw_record_id=direct.raw_record_id,
        normalizer_version=direct.normalizer_version,
        normalizer_commit=direct.normalizer_commit,
        frame_status=direct.frame_status,
        decoded_event_count=direct.decoded_event_count,
        raw_event_outcomes=direct.raw_event_outcomes,
        committed_materialization_keys=direct.committed_materialization_keys,
    )
    reconstructed_direct = NormalizationOutcome(
        normalization_run_id=direct.normalization_run_id,
        raw_record_id=direct.raw_record_id,
        normalizer_version=direct.normalizer_version,
        normalizer_commit=direct.normalizer_commit,
        frame_status=direct.frame_status,
        decoded_event_count=direct.decoded_event_count,
        raw_event_outcomes=direct.raw_event_outcomes,
        committed_materialization_keys=direct.committed_materialization_keys,
    )

    assert legacy == reconstructed_direct
    legacy_id = legacy.normalization_outcome_id.value
    reconstructed_id = reconstructed_direct.normalization_outcome_id.value
    assert legacy_id == reconstructed_id
    assert legacy.normalization_outcome_content_sha256 == (
        reconstructed_direct.normalization_outcome_content_sha256
    )
    assert legacy.coverage_transition_ids == ()
    assert legacy.coverage_lineage is None


def test_typed_normalization_outcome_stored_verification_is_byte_exact() -> None:
    outcome, _transition, _abort = _typed_rejected_frame_with_abort()
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)

    def verify(
        *,
        content_sha256: str = outcome.normalization_outcome_content_sha256,
        outcome_id: NormalizationOutcomeId = outcome.normalization_outcome_id,
    ) -> NormalizationOutcome:
        return NormalizationOutcome.from_stored_typed(
            normalization_run_id=outcome.normalization_run_id,
            raw_record_id=outcome.raw_record_id,
            normalizer_version=outcome.normalizer_version,
            normalizer_commit=outcome.normalizer_commit,
            frame_status=outcome.frame_status,
            decoded_event_count=outcome.decoded_event_count,
            raw_event_outcomes=outcome.raw_event_outcomes,
            committed_materialization_keys=outcome.committed_materialization_keys,
            coverage_lineage=lineage,
            preindex_scope_binding=outcome.preindex_scope_binding,
            evidence=outcome.evidence,
            expected_content_sha256=content_sha256,
            expected_outcome_id=outcome_id,
        )

    assert verify() == outcome
    with pytest.raises(ValueError, match="doesn't match"):
        verify(content_sha256="f" * 64)
    foreign_id = NormalizationOutcome(
        normalization_run_id=outcome.normalization_run_id,
        raw_record_id=outcome.raw_record_id,
        normalizer_version="foreign-normalizer",
        normalizer_commit=outcome.normalizer_commit,
        frame_status=outcome.frame_status,
        decoded_event_count=outcome.decoded_event_count,
        raw_event_outcomes=outcome.raw_event_outcomes,
        committed_materialization_keys=outcome.committed_materialization_keys,
        evidence=outcome.evidence,
        coverage_lineage=lineage,
    ).normalization_outcome_id
    with pytest.raises(ValueError, match="doesn't match"):
        verify(outcome_id=foreign_id)


def test_legacy_outcome_rejects_transition_ids_without_typed_lineage() -> None:
    legacy = _materialized_outcome_for_delivery(item_count=1)
    _raw, transition = _unrelated_coverage_transition(CoverageEvidenceKind.SOURCE_SEQUENCE)

    with pytest.raises(ValueError, match="transition-empty"):
        replace(legacy, coverage_transition_ids=(transition.coverage_transition_id,))


def test_typed_lineage_requires_prepared_batch_not_commit_acceptance() -> None:
    _outcome, _transition, _abort = _typed_rejected_frame_with_abort()
    batch = _prepared_batch_for_transition(_transition)
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=batch.resulting_state_references,
    )

    with pytest.raises(TypeError, match="prepared CoverageMutationBatch"):
        NormalizationCoverageLineage(
            cast(CoverageMutationBatch, acceptance),
            (),
            cast(RawCoverageFanoutBinding, object()),
        )


def test_typed_lineage_derives_transition_ids_and_rejects_even_exact_supplied_ids() -> None:
    outcome, transition, _abort = _typed_rejected_frame_with_abort()

    with pytest.raises(ValueError, match="derived and cannot be supplied"):
        replace(
            outcome,
            coverage_transition_ids=(transition.coverage_transition_id,),
        )


def test_additional_primary_evidence_requires_exact_selected_epoch_and_run() -> None:
    _outcome, transition, _abort = _typed_rejected_frame_with_abort()
    batch = _prepared_batch_for_transition(transition)
    raw_record = _raw_record()[0]
    foreign_epoch = replace(transition.epoch, epoch_ordinal=1)
    foreign_epoch_evidence = replace(transition.evidence, epoch=foreign_epoch)

    with pytest.raises(ValueError, match="selected target epoch and run"):
        NormalizationCoverageLineage(
            coverage_mutation_batch=batch,
            frame_atomic_abort_evidence=(),
            raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=batch.fanout_proof,
            ),
            additional_primary_evidence=(foreign_epoch_evidence,),
        )


def test_batch_selects_canonical_lowest_primary_evidence_per_target() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    transitions = tuple(
        _normalization_failure_transition(
            normalization_run_id=run_id,
            raw_event_index=0,
            source_event_id=SourceEventId("canonical-cause-source"),
            category=category,
        )[2]
        for category in (
            NormalizationFailureCategory.DECODER_REJECTION,
            NormalizationFailureCategory.UNKNOWN_INSTRUMENT,
        )
    )
    low, high = sorted(
        transitions,
        key=lambda item: item.evidence.coverage_evidence_id.value,
    )
    low_batch = _prepared_batch_for_transition(low)
    high_batch = _prepared_batch_for_transition(high)
    raw_record = _raw_record()[0]
    lineage = NormalizationCoverageLineage(
        coverage_mutation_batch=low_batch,
        frame_atomic_abort_evidence=(),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=low_batch.fanout_proof,
        ),
        additional_primary_evidence=(high.evidence,),
    )

    assert lineage.primary_evidence == (low.evidence, high.evidence)
    with pytest.raises(ValueError, match="canonical-lowest"):
        NormalizationCoverageLineage(
            coverage_mutation_batch=high_batch,
            frame_atomic_abort_evidence=(),
            raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=high_batch.fanout_proof,
            ),
            additional_primary_evidence=(low.evidence,),
        )


def test_typed_lineage_requires_exact_abort_and_primary_evidence_sets() -> None:
    outcome, _transition, abort = _typed_rejected_frame_with_abort()
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)

    with pytest.raises(ValueError, match="frame-aborted index"):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=replace(lineage, frame_atomic_abort_evidence=()),
        )
    _raw, _scope, second_transition = _normalization_failure_transition(
        normalization_run_id=outcome.normalization_run_id,
        raw_event_index=2,
        source_event_id=SourceEventId("second-primary-source"),
        category=NormalizationFailureCategory.UNKNOWN_INSTRUMENT,
    )
    with pytest.raises(ValueError, match="complete primary cause set"):
        NormalizationCoverageLineage(
            coverage_mutation_batch=lineage.coverage_mutation_batch,
            frame_atomic_abort_evidence=(abort,),
            raw_fanout_binding=lineage.raw_fanout_binding,
            additional_primary_evidence=(second_transition.evidence,),
        )

    foreign_abort = replace(abort, source_event_id=SourceEventId("another-aborted-source"))
    with pytest.raises(ValueError, match="frame-aborted index"):
        replace(
            outcome,
            coverage_transition_ids=(),
            coverage_lineage=replace(
                lineage,
                frame_atomic_abort_evidence=(foreign_abort,),
            ),
        )


def test_typed_lineage_supports_noop_primary_but_never_abort_transitions() -> None:
    outcome, transition, abort = _typed_rejected_frame_with_abort()
    first_batch = _prepared_batch_for_transition(transition)
    repeated = prepare_coverage_mutation_batch(
        fanout_proof=first_batch.fanout_proof,
        current_state_references=first_batch.resulting_state_references,
        requests=(
            RequestedCoverageMutation(
                transition.scope,
                transition.epoch,
                transition.new_status,
                InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                transition.reason,
                transition.evidence,
            ),
        ),
        raw_fanout_binding=first_batch.raw_fanout_binding,
    )
    no_op_lineage = NormalizationCoverageLineage(
        coverage_mutation_batch=repeated,
        frame_atomic_abort_evidence=(abort,),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=_raw_record()[0],
            coverage_fanout_proof=repeated.fanout_proof,
        ),
    )
    no_op_outcome = replace(
        outcome,
        coverage_transition_ids=(),
        coverage_lineage=no_op_lineage,
    )

    assert no_op_outcome.coverage_transition_ids == ()
    assert no_op_lineage.coverage_transitions == ()
    assert len(no_op_lineage.coverage_no_ops) == 1
    assert no_op_lineage.primary_evidence == (transition.evidence,)
    assert not isinstance(abort, CoverageTransition)


def test_typed_indexed_lineage_supports_exact_multi_scope_frame_fanout() -> None:
    raw_record, _btc_spec, _eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    snapshots_by_spec = {
        snapshot.subscription_spec.subscription_spec_id: snapshot
        for snapshot in raw_record.subscription_attempt_snapshots
    }
    instruments_by_id = {
        btc_instrument.canonical_instrument_id: (0, NormalizationFailureCategory.DECODER_REJECTION),
        eth_instrument.canonical_instrument_id: (
            1,
            NormalizationFailureCategory.UNKNOWN_INSTRUMENT,
        ),
    }
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    routed_targets = tuple(
        RoutedCoverageTarget(
            acknowledged_snapshot=snapshots_by_spec[binding.subscription_spec_id],
            canonical_instrument_id=binding.canonical_instrument_id,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
        for binding in raw_record.subscription_plan.instrument_bindings
    )
    fanout = CoverageFanoutProof.exact_routed_events(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        routed_targets=routed_targets,
    )
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    normalization_run_id = NormalizationRunId("normalization-fixture")
    current_states: list[CoverageStateReference] = []
    requests: list[RequestedCoverageMutation] = []
    indexed_outcomes: list[RawEventNormalizationOutcome] = []
    for scope in fanout.target_scopes:
        index, category = instruments_by_id[scope.canonical_instrument_ids[0]]
        snapshot = snapshots_by_spec[scope.subscription_spec_ids[0]]
        source_event_id = SourceEventId(f"multi-scope-source-{index}")
        epoch = CoverageEpochIdentity(
            scope,
            raw_record.collector_run_id,
            0,
            activation_time,
            100,
        )
        initial_evidence = CoverageEvidence(
            CoverageEvidenceKind.INITIAL_ACTIVATION,
            InitialActivationEvidenceSource(raw_record.connection_session, (snapshot,)),
            scope,
            epoch,
            activation_time,
            100,
        )
        current_states.append(
            CoverageStateReference.from_initialization(
                CoverageInitialization(
                    scope,
                    epoch,
                    CoverageStatus.COMPLETE,
                    InitialCoverageReason.INITIAL_ACTIVATION,
                    initial_evidence,
                )
            )
        )
        failure_evidence = CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
            NormalizationFailureEvidenceSource(
                raw_record.raw_record_id,
                normalization_run_id,
                index,
                source_event_id,
                category,
                scope.coverage_scope_id,
            ),
            scope,
            epoch,
            activation_time + timedelta(seconds=1),
            101,
        )
        requests.append(
            RequestedCoverageMutation(
                scope,
                epoch,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                failure_evidence,
            )
        )
        spec = snapshot.subscription_spec
        indexed_outcomes.append(
            RawEventNormalizationOutcome(
                ObservationKey(raw_record.raw_record_id, index),
                _outcome_scope_binding(
                    index,
                    raw_record=raw_record,
                    spec=spec,
                    attempt=snapshot.subscription_attempt,
                    scope=scope,
                ),
                RawEventDisposition.REJECTED,
                LogicalSourceKey(raw_record.feed_product.feed_product_id, source_event_id),
                evidence={
                    NormalizationFailureCategory.DECODER_REJECTION: (
                        NormalizationEvidence.DECODER_REJECTION
                    ),
                    NormalizationFailureCategory.UNKNOWN_INSTRUMENT: (
                        NormalizationEvidence.UNKNOWN_INSTRUMENT
                    ),
                }[category],
            )
        )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=tuple(current_states),
        requests=tuple(requests),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=fanout,
        ),
    )
    ordered_outcomes = tuple(
        sorted(indexed_outcomes, key=lambda item: item.observation_key.raw_event_index)
    )
    frame_evidence = tuple(
        sorted(
            {cast(NormalizationEvidence, item.evidence) for item in ordered_outcomes},
            key=lambda item: item.value,
        )
    )
    outcome = NormalizationOutcome(
        normalization_run_id=normalization_run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
        decoded_event_count=2,
        raw_event_outcomes=ordered_outcomes,
        committed_materialization_keys=(),
        evidence=frame_evidence,
        coverage_lineage=NormalizationCoverageLineage(
            batch,
            (),
            RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=batch.fanout_proof,
            ),
        ),
    )

    assert fanout.kind.value == "exact-routed-events"
    assert len(fanout.target_scopes) == 2
    assert set(outcome.coverage_transition_ids) == {
        transition.coverage_transition_id for transition in batch.transitions
    }
    first_snapshot, *remaining_snapshots = raw_record.subscription_attempt_snapshots
    tampered_raw = replace(
        raw_record,
        subscription_attempt_snapshots=(
            replace(first_snapshot, attempt_status=SubscriptionAttemptStatus.SENT),
            *remaining_snapshots,
        ),
    )
    with pytest.raises(ValueError):
        RawCoverageFanoutBinding.from_raw_record(
            raw_record=tampered_raw,
            coverage_fanout_proof=fanout,
        )


def test_typed_lineage_rejects_lifecycle_fanout_for_frame_outcome() -> None:
    raw_record, _spec, attempt = _raw_record()
    snapshot = SubscriptionAttemptSnapshot(attempt, SubscriptionAttemptStatus.ACKNOWLEDGED)
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    fanout = CoverageFanoutProof.acknowledged_active(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        complete_snapshots=(snapshot,),
        selected_attempt_ids=(snapshot.subscription_attempt.subscription_attempt_id,),
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    requests: list[RequestedCoverageMutation] = []
    for scope in fanout.target_scopes:
        epoch = CoverageEpochIdentity(
            scope,
            raw_record.collector_run_id,
            0,
            datetime(2026, 8, 26, 11, 0, tzinfo=UTC),
            100,
        )
        evidence = CoverageEvidence(
            CoverageEvidenceKind.INITIAL_ACTIVATION,
            InitialActivationEvidenceSource(raw_record.connection_session, (snapshot,)),
            scope,
            epoch,
            epoch.activation_time,
            epoch.activation_monotonic_ns,
        )
        requests.append(
            RequestedCoverageMutation(
                scope,
                epoch,
                CoverageStatus.COMPLETE,
                InitialCoverageReason.INITIAL_ACTIVATION,
                CoverageReason.INITIAL_SCOPE,
                evidence,
            )
        )
    lifecycle_batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=tuple(requests),
    )

    with pytest.raises(ValueError, match="lifecycle-only"):
        RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=lifecycle_batch.fanout_proof,
        )


def test_post_outcome_sink_failure_evidence_cannot_enter_that_outcome_lineage() -> None:
    raw_record, spec, attempt = _raw_record()
    scope = _normalization_scope(raw_record, spec)
    snapshot = SubscriptionAttemptSnapshot(attempt, SubscriptionAttemptStatus.ACKNOWLEDGED)
    fanout = CoverageFanoutProof.exact_routed_event(
        plan=raw_record.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan),
        acknowledged_snapshot=snapshot,
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    epoch = CoverageEpochIdentity(
        scope,
        raw_record.collector_run_id,
        0,
        datetime(2026, 8, 26, 11, 0, tzinfo=UTC),
        100,
    )
    outcome_id = ProvenanceNormalizationOutcomeId(
        _expected_identifier(
            "normalization-outcome-v1",
            "normalization-fixture",
            raw_record.raw_record_id.value,
            "normalizer-v1",
            "normalizer-commit-fixture",
            "materialized",
            1,
            "0" * 64,
        )
    )
    evidence = CoverageEvidence(
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        NormalizationOutcomeEvidenceSource(outcome_id, scope.coverage_scope_id),
        scope,
        epoch,
        epoch.activation_time,
        epoch.activation_monotonic_ns,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=(
            RequestedCoverageMutation(
                scope,
                epoch,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                InitialCoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
                CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
                evidence,
            ),
        ),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=fanout,
        ),
    )
    raw_binding = batch.raw_fanout_binding
    assert type(raw_binding) is RawCoverageFanoutBinding
    with pytest.raises(ValueError, match="only primary failure or conflict"):
        NormalizationCoverageLineage(batch, (), raw_binding)


def _outcome_sink_failure_batch(
    *,
    raw_record: RawMarketDataRecord,
    outcome: NormalizationOutcome,
    fanout: CoverageFanoutProof,
    kind: CoverageEvidenceKind = CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
    current_state_references: tuple[CoverageStateReference, ...] | None = None,
) -> CoverageMutationBatch:
    if kind is CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION:
        status = CoverageStatus.CONFIRMED_INCOMPLETE
        initial_reason = InitialCoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION
        transition_reason = CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION
    else:
        assert kind is CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY
        status = CoverageStatus.UNCERTAIN
        initial_reason = InitialCoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN
        transition_reason = CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN
    boundary = raw_record.received_time - timedelta(seconds=1)
    boundary_monotonic_ns = raw_record.received_monotonic_ns - 1
    requests: list[RequestedCoverageMutation] = []
    current_states: list[CoverageStateReference] = []
    supplied_states = {
        state.reference.scope.coverage_scope_id: state for state in current_state_references or ()
    }
    if current_state_references:
        assert len(supplied_states) == len(current_state_references)
        assert set(supplied_states) == {scope.coverage_scope_id for scope in fanout.target_scopes}
    for scope in fanout.target_scopes:
        matching_snapshots = tuple(
            snapshot
            for snapshot in raw_record.subscription_attempt_snapshots
            if snapshot.subscription_spec.subscription_spec_id in scope.subscription_spec_ids
        )
        assert len(matching_snapshots) == 1
        supplied_state = supplied_states.get(scope.coverage_scope_id)
        if supplied_state is None:
            epoch_time = raw_record.received_time if current_state_references == () else boundary
            epoch_monotonic_ns = (
                raw_record.received_monotonic_ns
                if current_state_references == ()
                else boundary_monotonic_ns
            )
            epoch = CoverageEpochIdentity(
                scope,
                raw_record.collector_run_id,
                0,
                epoch_time,
                epoch_monotonic_ns,
            )
            if current_state_references is None:
                activation_evidence = CoverageEvidence(
                    CoverageEvidenceKind.INITIAL_ACTIVATION,
                    InitialActivationEvidenceSource(
                        raw_record.connection_session,
                        matching_snapshots,
                    ),
                    scope,
                    epoch,
                    boundary,
                    boundary_monotonic_ns,
                )
                current_states.append(
                    CoverageStateReference.from_initialization(
                        CoverageInitialization(
                            scope,
                            epoch,
                            CoverageStatus.COMPLETE,
                            InitialCoverageReason.INITIAL_ACTIVATION,
                            activation_evidence,
                        )
                    )
                )
        else:
            epoch = supplied_state.reference.epoch
            current_states.append(supplied_state)
        evidence = CoverageEvidence(
            kind,
            NormalizationOutcomeEvidenceSource(
                outcome.normalization_outcome_id,
                scope.coverage_scope_id,
            ),
            scope,
            epoch,
            raw_record.received_time,
            raw_record.received_monotonic_ns,
        )
        requests.append(
            RequestedCoverageMutation(
                scope,
                epoch,
                status,
                initial_reason,
                transition_reason,
                evidence,
            )
        )
    raw_binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=fanout,
    )
    return prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=tuple(current_states),
        requests=tuple(requests),
        raw_fanout_binding=raw_binding,
    )


def test_outcome_sink_failure_binding_proves_exact_indexed_scope_and_attempt_union() -> None:
    raw_record, btc_spec, eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    snapshots = {
        snapshot.subscription_spec.subscription_spec_id: snapshot
        for snapshot in raw_record.subscription_attempt_snapshots
    }
    routes = (
        (0, btc_spec, btc_instrument),
        (1, eth_spec, eth_instrument),
    )
    fanout = CoverageFanoutProof.exact_routed_events(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        routed_targets=tuple(
            RoutedCoverageTarget(
                snapshots[spec.subscription_spec_id],
                instrument.canonical_instrument_id,
                "trade",
                2,
                "trade",
            )
            for _index, spec, instrument in routes
        ),
    )
    scopes = {scope.canonical_instrument_ids[0]: scope for scope in fanout.target_scopes}
    normalization_run_id = NormalizationRunId("sink-failure-binding-run")
    indexed_outcomes: list[RawEventNormalizationOutcome] = []
    for index, spec, instrument in routes:
        snapshot = snapshots[spec.subscription_spec_id]
        observation = ObservationKey(raw_record.raw_record_id, index)
        source = LogicalSourceKey(
            raw_record.feed_product.feed_product_id,
            SourceEventId(f"sink-failure-source-{index}"),
        )
        materialization = MaterializationKey(
            normalization_run_id,
            observation,
            EventFamily.TRADE,
            2,
            PayloadType.TRADE,
        )
        indexed_outcomes.append(
            RawEventNormalizationOutcome(
                observation,
                _outcome_scope_binding(
                    index,
                    raw_record=raw_record,
                    spec=spec,
                    attempt=snapshot.subscription_attempt,
                    scope=scopes[instrument.canonical_instrument_id],
                ),
                RawEventDisposition.MATERIALIZED_NEW,
                source,
                materialization,
            )
        )
    ordered_outcomes = tuple(indexed_outcomes)
    outcome = NormalizationOutcome(
        normalization_run_id,
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.MATERIALIZED,
        2,
        ordered_outcomes,
        tuple(cast(MaterializationKey, item.materialization_key) for item in ordered_outcomes),
    )
    batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=outcome,
        fanout=fanout,
    )
    binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=outcome,
        coverage_mutation_batch=batch,
    )

    expected_attempt_ids = tuple(
        sorted(
            {item.normalization_scope_binding.subscription_attempt_id for item in ordered_outcomes},
            key=lambda item: item.value,
        )
    )
    assert fanout.selected_attempt_ids == expected_attempt_ids
    expected_id = _expected_identifier(
        "normalization-outcome-sink-failure-coverage-binding-v1",
        outcome.normalization_outcome_id.value,
        batch.coverage_mutation_batch_id.value,
        cast(
            RawCoverageFanoutBinding,
            batch.raw_fanout_binding,
        ).raw_coverage_fanout_binding_id.value,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION.value,
    )
    assert binding.normalization_outcome_sink_failure_coverage_binding_id.value == expected_id
    assert (
        NormalizationOutcomeSinkFailureCoverageBinding.from_stored(
            normalization_outcome=outcome,
            coverage_mutation_batch=batch,
            expected_binding_id=binding.normalization_outcome_sink_failure_coverage_binding_id,
        )
        == binding
    )

    btc_snapshot = snapshots[btc_spec.subscription_spec_id]
    btc_fanout = CoverageFanoutProof.exact_routed_event(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        acknowledged_snapshot=btc_snapshot,
        canonical_instrument_id=btc_instrument.canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    subset_batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=outcome,
        fanout=btc_fanout,
    )
    with pytest.raises(ValueError, match="decoded scope union"):
        NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
            normalization_outcome=outcome,
            coverage_mutation_batch=subset_batch,
        )
    with pytest.raises(ValueError, match="concrete outcome"):
        NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
            normalization_outcome=replace(outcome, normalizer_commit="different-commit"),
            coverage_mutation_batch=batch,
        )
    tampered_id = NormalizationOutcomeSinkFailureCoverageBindingId(
        _expected_identifier(
            "normalization-outcome-sink-failure-coverage-binding-v1",
            outcome.normalization_outcome_id.value,
            batch.coverage_mutation_batch_id.value,
            cast(
                RawCoverageFanoutBinding,
                batch.raw_fanout_binding,
            ).raw_coverage_fanout_binding_id.value,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY.value,
        )
    )
    with pytest.raises(ValueError, match="stored outcome sink-failure"):
        NormalizationOutcomeSinkFailureCoverageBinding.from_stored(
            normalization_outcome=outcome,
            coverage_mutation_batch=batch,
            expected_binding_id=tampered_id,
        )


def test_outcome_sink_failure_binding_accepts_transition_and_no_op_in_one_batch() -> None:
    raw_record, _btc_spec, _eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=raw_record.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan),
        complete_snapshots=raw_record.subscription_attempt_snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    aggregate_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        tuple(
            spec.subscription_spec_id for spec in raw_record.subscription_plan.subscription_specs
        ),
        tuple(
            sorted(
                (
                    btc_instrument.canonical_instrument_id,
                    eth_instrument.canonical_instrument_id,
                )
            )
        ),
        "trade",
        2,
        "trade",
    )

    def outcome(commit: str) -> NormalizationOutcome:
        return NormalizationOutcome(
            NormalizationRunId("mixed-state-sink-failure-run"),
            raw_record.raw_record_id,
            "normalizer-v1",
            commit,
            FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            None,
            (),
            (),
            RawFrameNormalizationScopeBinding.from_raw_record(
                raw_record=raw_record,
                coverage_scope=aggregate_scope,
            ),
            (NormalizationEvidence.DECODER_REJECTION,),
        )

    uncertain_batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=outcome("uncertain-commit"),
        fanout=fanout,
        kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
    )
    incomplete_batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=outcome("incomplete-commit"),
        fanout=fanout,
        current_state_references=uncertain_batch.resulting_state_references,
    )
    uncertain_by_scope = {
        state.reference.scope.coverage_scope_id: state
        for state in uncertain_batch.resulting_state_references
    }
    incomplete_by_scope = {
        state.reference.scope.coverage_scope_id: state
        for state in incomplete_batch.resulting_state_references
    }
    mixed_states = tuple(
        (
            uncertain_by_scope[scope.coverage_scope_id]
            if index == 0
            else incomplete_by_scope[scope.coverage_scope_id]
        )
        for index, scope in enumerate(fanout.target_scopes)
    )
    concrete_outcome = outcome("mixed-transition-no-op-commit")
    batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=concrete_outcome,
        fanout=fanout,
        current_state_references=mixed_states,
    )
    binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=concrete_outcome,
        coverage_mutation_batch=batch,
    )

    assert len(batch.transitions) == 1
    assert len(batch.no_ops) == 1
    assert batch.initializations == ()
    evidence = tuple(transition.evidence for transition in batch.transitions) + tuple(
        no_op.request.evidence for no_op in batch.no_ops
    )
    assert {
        cast(NormalizationOutcomeEvidenceSource, item.source).normalization_outcome_id
        for item in evidence
    } == {concrete_outcome.normalization_outcome_id}
    assert {item.kind for item in evidence} == {
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION
    }
    assert binding.coverage_mutation_batch == batch


def test_outcome_sink_failure_binding_accepts_initialization_and_full_no_op() -> None:
    raw_record, _btc_spec, _eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=raw_record.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan),
        complete_snapshots=raw_record.subscription_attempt_snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    aggregate_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        tuple(
            spec.subscription_spec_id for spec in raw_record.subscription_plan.subscription_specs
        ),
        tuple(
            sorted(
                (
                    btc_instrument.canonical_instrument_id,
                    eth_instrument.canonical_instrument_id,
                )
            )
        ),
        "trade",
        2,
        "trade",
    )

    def outcome(commit: str) -> NormalizationOutcome:
        return NormalizationOutcome(
            NormalizationRunId("initial-noop-sink-failure-run"),
            raw_record.raw_record_id,
            "normalizer-v1",
            commit,
            FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            None,
            (),
            (),
            RawFrameNormalizationScopeBinding.from_raw_record(
                raw_record=raw_record,
                coverage_scope=aggregate_scope,
            ),
            (NormalizationEvidence.DECODER_REJECTION,),
        )

    initial_outcome = outcome("initial-ambiguity-commit")
    initial_batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=initial_outcome,
        fanout=fanout,
        kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        current_state_references=(),
    )
    initial_binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=initial_outcome,
        coverage_mutation_batch=initial_batch,
    )

    assert len(initial_batch.initializations) == 2
    assert initial_batch.transitions == ()
    assert initial_batch.no_ops == ()
    assert initial_binding.evidence_kind is (
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY
    )

    repeated_outcome = outcome("repeated-ambiguity-commit")
    repeated_batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=repeated_outcome,
        fanout=fanout,
        kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        current_state_references=initial_batch.resulting_state_references,
    )
    repeated_binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=repeated_outcome,
        coverage_mutation_batch=repeated_batch,
    )

    assert repeated_batch.initializations == ()
    assert repeated_batch.transitions == ()
    assert len(repeated_batch.no_ops) == 2
    assert repeated_binding.evidence_kind is (
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY
    )


def test_outcome_sink_failure_binding_proves_complete_preindex_plan_slice() -> None:
    raw_record, _btc_spec, _eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    plan = raw_record.subscription_plan
    aggregate_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        tuple(spec.subscription_spec_id for spec in plan.subscription_specs),
        tuple(
            sorted(
                (
                    btc_instrument.canonical_instrument_id,
                    eth_instrument.canonical_instrument_id,
                )
            )
        ),
        "trade",
        2,
        "trade",
    )
    outcome = NormalizationOutcome(
        NormalizationRunId("preindex-sink-failure-run"),
        raw_record.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        None,
        (),
        (),
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=aggregate_scope,
        ),
        (NormalizationEvidence.DECODER_REJECTION,),
    )
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(plan),
        complete_snapshots=raw_record.subscription_attempt_snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    batch = _outcome_sink_failure_batch(
        raw_record=raw_record,
        outcome=outcome,
        fanout=fanout,
        kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
    )
    binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=outcome,
        coverage_mutation_batch=batch,
    )

    assert binding.evidence_kind is (
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY
    )
    assert len(fanout.target_scopes) == 2


def test_preindex_sink_failure_filters_scope_but_retains_complete_possible_attempts() -> None:
    raw_record, btc_spec, eth_spec, btc_instrument, _eth_instrument = _multi_spec_raw_record()
    adapter_profile = raw_record.subscription_plan.normalization_bindings[0].adapter_profile
    mixed_plan = replace(
        raw_record.subscription_plan,
        normalization_bindings=tuple(
            sorted(
                (
                    NormalizationBinding(
                        btc_spec.subscription_spec_id,
                        adapter_profile,
                        "trade",
                        2,
                        "trade",
                    ),
                    NormalizationBinding(
                        eth_spec.subscription_spec_id,
                        adapter_profile,
                        "bbo",
                        1,
                        "bbo",
                    ),
                ),
                key=lambda item: item.subscription_spec_id.value,
            )
        ),
    )
    mixed_raw = RawMarketDataRecord(
        feed_product=raw_record.feed_product,
        collector_run_id=raw_record.collector_run_id,
        connection_session=raw_record.connection_session,
        subscription_plan=mixed_plan,
        subscription_attempt_snapshots=raw_record.subscription_attempt_snapshots,
        ingress_ordinal=raw_record.ingress_ordinal,
        frame_kind=raw_record.frame_kind,
        application_message_bytes=raw_record.application_message_bytes,
        received_time=raw_record.received_time,
        received_monotonic_ns=raw_record.received_monotonic_ns,
        collector_version=raw_record.collector_version,
        collector_commit=raw_record.collector_commit,
    )
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=mixed_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(mixed_plan),
        complete_snapshots=mixed_raw.subscription_attempt_snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    aggregate_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        mixed_raw.feed_product.feed_product_id,
        (btc_spec.subscription_spec_id,),
        (btc_instrument.canonical_instrument_id,),
        "trade",
        2,
        "trade",
    )
    outcome = NormalizationOutcome(
        NormalizationRunId("mixed-family-preindex-run"),
        mixed_raw.raw_record_id,
        "normalizer-v1",
        "normalizer-commit-fixture",
        FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        None,
        (),
        (),
        RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=mixed_raw,
            coverage_scope=aggregate_scope,
        ),
        (NormalizationEvidence.DECODER_REJECTION,),
    )
    batch = _outcome_sink_failure_batch(
        raw_record=mixed_raw,
        outcome=outcome,
        fanout=fanout,
    )
    binding = NormalizationOutcomeSinkFailureCoverageBinding.from_outcome_and_batch(
        normalization_outcome=outcome,
        coverage_mutation_batch=batch,
    )

    assert len(fanout.selected_attempt_ids) == 2
    assert len(fanout.target_scopes) == 1
    assert fanout.target_scopes[0].subscription_spec_ids == (btc_spec.subscription_spec_id,)
    assert binding.coverage_mutation_batch == batch


def test_typed_preindex_provenance_mismatch_has_exact_failure_category() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=None,
        category=NormalizationFailureCategory.PROVENANCE_MISMATCH,
    )
    outcome = NormalizationOutcome(
        normalization_run_id=run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        decoded_event_count=None,
        raw_event_outcomes=(),
        committed_materialization_keys=(),
        preindex_scope_binding=RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=scope,
        ),
        evidence=(NormalizationEvidence.PROVENANCE_MISMATCH,),
        coverage_lineage=_typed_coverage_lineage(transition),
    )

    assert outcome.coverage_transition_ids == (transition.coverage_transition_id,)
    assert cast(NormalizationFailureEvidenceSource, transition.evidence.source).category is (
        NormalizationFailureCategory.PROVENANCE_MISMATCH
    )


def test_typed_preindex_lineage_rejects_same_raw_locator_with_different_full_record() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=None,
        category=NormalizationFailureCategory.PROTOCOL_REJECTION,
    )
    altered_raw = replace(
        raw_record,
        received_monotonic_ns=raw_record.received_monotonic_ns + 1,
    )
    assert altered_raw.raw_record_id == raw_record.raw_record_id
    assert altered_raw.full_record_integrity_sha256 != raw_record.full_record_integrity_sha256

    with pytest.raises(ValueError, match="exact raw full-record content"):
        NormalizationOutcome(
            normalization_run_id=run_id,
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            committed_materialization_keys=(),
            preindex_scope_binding=RawFrameNormalizationScopeBinding.from_raw_record(
                raw_record=altered_raw,
                coverage_scope=scope,
            ),
            evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
            coverage_lineage=_typed_coverage_lineage(transition),
        )


def test_typed_indexed_lineage_rejects_same_raw_locator_with_different_full_record() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    source_event_id = SourceEventId("full-record-binding-source")
    raw_record, scope, transition = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_event_id=source_event_id,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    altered_raw = replace(
        raw_record,
        received_monotonic_ns=raw_record.received_monotonic_ns + 1,
    )
    assert altered_raw.raw_record_id == raw_record.raw_record_id
    assert altered_raw.full_record_integrity_sha256 != raw_record.full_record_integrity_sha256
    rejected = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=altered_raw, scope=scope),
        RawEventDisposition.REJECTED,
        LogicalSourceKey(raw_record.feed_product.feed_product_id, source_event_id),
        evidence=NormalizationEvidence.DECODER_REJECTION,
    )

    with pytest.raises(ValueError, match="exact raw full-record content"):
        NormalizationOutcome(
            normalization_run_id=run_id,
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
            decoded_event_count=1,
            raw_event_outcomes=(rejected,),
            committed_materialization_keys=(),
            evidence=(NormalizationEvidence.DECODER_REJECTION,),
            coverage_lineage=_typed_coverage_lineage(transition),
        )


def test_typed_preindex_lineage_accepts_complete_multi_spec_leaf_fanout() -> None:
    raw_record, btc_spec, eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        complete_snapshots=raw_record.subscription_attempt_snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    normalization_run_id = NormalizationRunId("normalization-fixture")
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    snapshots_by_spec = {
        snapshot.subscription_spec.subscription_spec_id: snapshot
        for snapshot in raw_record.subscription_attempt_snapshots
    }
    current_states: list[CoverageStateReference] = []
    requests: list[RequestedCoverageMutation] = []
    for scope in fanout.target_scopes:
        snapshot = snapshots_by_spec[scope.subscription_spec_ids[0]]
        epoch = CoverageEpochIdentity(
            scope,
            raw_record.collector_run_id,
            0,
            activation_time,
            100,
        )
        initial_evidence = CoverageEvidence(
            CoverageEvidenceKind.INITIAL_ACTIVATION,
            InitialActivationEvidenceSource(raw_record.connection_session, (snapshot,)),
            scope,
            epoch,
            activation_time,
            100,
        )
        current_states.append(
            CoverageStateReference.from_initialization(
                CoverageInitialization(
                    scope,
                    epoch,
                    CoverageStatus.COMPLETE,
                    InitialCoverageReason.INITIAL_ACTIVATION,
                    initial_evidence,
                )
            )
        )
        failure_evidence = CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
            NormalizationFailureEvidenceSource(
                raw_record.raw_record_id,
                normalization_run_id,
                None,
                None,
                NormalizationFailureCategory.PROTOCOL_REJECTION,
                scope.coverage_scope_id,
            ),
            scope,
            epoch,
            activation_time + timedelta(seconds=1),
            101,
        )
        requests.append(
            RequestedCoverageMutation(
                scope,
                epoch,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                failure_evidence,
            )
        )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=tuple(current_states),
        requests=tuple(requests),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=fanout,
        ),
    )
    aggregate_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        tuple(
            sorted(
                (btc_spec.subscription_spec_id, eth_spec.subscription_spec_id),
                key=lambda item: item.value,
            )
        ),
        tuple(
            sorted((btc_instrument.canonical_instrument_id, eth_instrument.canonical_instrument_id))
        ),
        "trade",
        2,
        "trade",
    )
    outcome = NormalizationOutcome(
        normalization_run_id=normalization_run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        decoded_event_count=None,
        raw_event_outcomes=(),
        committed_materialization_keys=(),
        preindex_scope_binding=RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=aggregate_scope,
        ),
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
        coverage_lineage=NormalizationCoverageLineage(
            coverage_mutation_batch=batch,
            frame_atomic_abort_evidence=(),
            raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=fanout,
            ),
        ),
    )

    assert len(fanout.target_scopes) == len(batch.transitions) == 2
    assert aggregate_scope.coverage_scope_id not in {
        transition.scope.coverage_scope_id for transition in batch.transitions
    }
    assert set(outcome.coverage_transition_ids) == {
        transition.coverage_transition_id for transition in batch.transitions
    }


def test_typed_preindex_lineage_targets_only_possibly_active_multi_spec_slice() -> None:
    base_raw, btc_spec, eth_spec, btc_instrument, eth_instrument = _multi_spec_raw_record()
    snapshots = tuple(
        replace(snapshot, attempt_status=SubscriptionAttemptStatus.PENDING)
        if snapshot.subscription_spec.subscription_spec_id == eth_spec.subscription_spec_id
        else snapshot
        for snapshot in base_raw.subscription_attempt_snapshots
    )
    raw_record = replace(base_raw, subscription_attempt_snapshots=snapshots)
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=raw_record.subscription_plan,
        catalog=catalog,
        complete_snapshots=raw_record.subscription_attempt_snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    assert len(fanout.target_scopes) == 1
    target_scope = fanout.target_scopes[0]
    assert target_scope.subscription_spec_ids == (btc_spec.subscription_spec_id,)

    acknowledged = next(
        snapshot
        for snapshot in raw_record.subscription_attempt_snapshots
        if snapshot.attempt_status is SubscriptionAttemptStatus.ACKNOWLEDGED
    )
    normalization_run_id = NormalizationRunId("normalization-fixture")
    activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        target_scope,
        raw_record.collector_run_id,
        0,
        activation_time,
        100,
    )
    initial_evidence = CoverageEvidence(
        CoverageEvidenceKind.INITIAL_ACTIVATION,
        InitialActivationEvidenceSource(raw_record.connection_session, (acknowledged,)),
        target_scope,
        epoch,
        activation_time,
        100,
    )
    current_state = CoverageStateReference.from_initialization(
        CoverageInitialization(
            target_scope,
            epoch,
            CoverageStatus.COMPLETE,
            InitialCoverageReason.INITIAL_ACTIVATION,
            initial_evidence,
        )
    )
    failure_evidence = CoverageEvidence(
        CoverageEvidenceKind.NORMALIZATION_FAILURE,
        NormalizationFailureEvidenceSource(
            raw_record.raw_record_id,
            normalization_run_id,
            None,
            None,
            NormalizationFailureCategory.PROTOCOL_REJECTION,
            target_scope.coverage_scope_id,
        ),
        target_scope,
        epoch,
        activation_time + timedelta(seconds=1),
        101,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(current_state,),
        requests=(
            RequestedCoverageMutation(
                target_scope,
                epoch,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                failure_evidence,
            ),
        ),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=fanout,
        ),
    )
    aggregate_scope = CoverageScope(
        CoverageDomain.SILVER_NORMALIZATION,
        raw_record.feed_product.feed_product_id,
        tuple(
            sorted(
                (btc_spec.subscription_spec_id, eth_spec.subscription_spec_id),
                key=lambda item: item.value,
            )
        ),
        tuple(
            sorted((btc_instrument.canonical_instrument_id, eth_instrument.canonical_instrument_id))
        ),
        "trade",
        2,
        "trade",
    )
    outcome = NormalizationOutcome(
        normalization_run_id=normalization_run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        decoded_event_count=None,
        raw_event_outcomes=(),
        committed_materialization_keys=(),
        preindex_scope_binding=RawFrameNormalizationScopeBinding.from_raw_record(
            raw_record=raw_record,
            coverage_scope=aggregate_scope,
        ),
        evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
        coverage_lineage=NormalizationCoverageLineage(
            coverage_mutation_batch=batch,
            frame_atomic_abort_evidence=(),
            raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=fanout,
            ),
        ),
    )

    assert outcome.coverage_transition_ids == (batch.transitions[0].coverage_transition_id,)
    assert eth_spec.subscription_spec_id not in {
        spec_id for scope in fanout.target_scopes for spec_id in scope.subscription_spec_ids
    }


def test_source_conflict_requires_exact_upper_normalization_run_binding() -> None:
    source_event_id = SourceEventId("strict-conflict-run")
    raw_record, scope, transition = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=0,
    )
    conflict = RawEventNormalizationOutcome(
        ObservationKey(raw_record.raw_record_id, 0),
        _outcome_scope_binding(0, raw_record=raw_record, scope=scope),
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
        LogicalSourceKey(raw_record.feed_product.feed_product_id, source_event_id),
        evidence=NormalizationEvidence.SOURCE_EVENT_CONFLICT,
    )
    lineage = _typed_coverage_lineage(transition)
    binding = lineage.source_conflict_bindings[0]
    assert (
        NormalizationSourceConflictBinding.from_stored(
            coverage_evidence=binding.coverage_evidence,
            normalization_run_id=binding.normalization_run_id,
            expected_binding_id=binding.normalization_source_conflict_binding_id,
        )
        == binding
    )
    assert (
        NormalizationSourceConflictBindingId(binding.normalization_source_conflict_binding_id.value)
        == binding.normalization_source_conflict_binding_id
    )

    with pytest.raises(ValueError, match="normalization run"):
        NormalizationOutcome(
            normalization_run_id=NormalizationRunId("foreign-normalization-run"),
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
            decoded_event_count=1,
            raw_event_outcomes=(conflict,),
            committed_materialization_keys=(),
            evidence=(NormalizationEvidence.SOURCE_EVENT_CONFLICT,),
            coverage_lineage=lineage,
        )


def _delivery_commitments(outcome: NormalizationOutcome) -> tuple[DeliveryItemCommitment, ...]:
    return tuple(
        DeliveryItemCommitment(
            key.materialization_key_id,
            hashlib.sha256(f"event-content-{index}".encode()).hexdigest(),
        )
        for index, key in enumerate(outcome.committed_materialization_keys)
    )


def test_outcome_delivery_attempt_binding_composes_byte_stable_legacy_locator() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=2)
    items = _delivery_commitments(outcome)
    batch = DeliveryBatchCommitment(outcome, items)
    destination = DeliveryDestinationId("schema-v2-output-queue")
    attempt = OutcomeDeliveryAttemptBinding(destination, batch, 0)
    legacy = DeliveryAttemptIdentity(
        destination_id=destination.value,
        materialization_key_canonical_texts=tuple(
            item.materialization_key_id.value for item in items
        ),
        attempt_ordinal=0,
    )
    expected = _expected_identifier(
        "normalization-delivery-attempt-binding-v1",
        legacy.delivery_attempt_id.value,
        batch.delivery_batch_commitment_id.value,
    )

    assert attempt.legacy_delivery_attempt == legacy
    assert batch.legacy_delivery_batch_id == legacy.delivery_batch_id
    assert DeliveryBatchId(legacy.delivery_batch_id.value) == legacy.delivery_batch_id
    assert DeliveryAttemptId(legacy.delivery_attempt_id.value) == legacy.delivery_attempt_id
    assert attempt.outcome_delivery_attempt_binding_id.value == expected
    assert OutcomeDeliveryAttemptBindingId(expected) == attempt.outcome_delivery_attempt_binding_id
    assert hash(attempt) == hash(attempt)
    assert not hasattr(attempt, "__dict__")
    assert (
        OutcomeDeliveryAttemptBinding.from_stored(
            destination_id=destination,
            delivery_batch_commitment=batch,
            attempt_ordinal=0,
            expected_legacy_delivery_attempt_id=legacy.delivery_attempt_id,
            expected_binding_id=attempt.outcome_delivery_attempt_binding_id,
        )
        == attempt
    )
    with pytest.raises(ValueError, match="only attempt ordinal 0"):
        OutcomeDeliveryAttemptBinding(destination, batch, 1)
    with pytest.raises(TypeError, match="built-in integer"):
        OutcomeDeliveryAttemptBinding(destination, batch, cast(int, True))


def test_delivery_identifier_wrappers_reject_inconsistent_nested_legacy_locators() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=2)
    items = _delivery_commitments(outcome)
    batch = DeliveryBatchCommitment(outcome, items)
    destination = DeliveryDestinationId("schema-v2-output-queue")

    with pytest.raises(ValueError, match="locator counts disagree"):
        DeliveryBatchCommitmentId(
            _expected_identifier(
                "normalization-delivery-batch-commitment-v1",
                batch.legacy_delivery_batch_id.value,
                outcome.normalization_outcome_id.value,
                1,
                batch.batch_content_sha256,
            )
        )

    foreign_legacy_attempt = DeliveryAttemptIdentity(
        destination_id=destination.value,
        materialization_key_canonical_texts=(_outcome_keys(2)[2].materialization_key_id.value,),
        attempt_ordinal=0,
    )
    with pytest.raises(ValueError, match="legacy locators disagree"):
        OutcomeDeliveryAttemptBindingId(
            _expected_identifier(
                "normalization-delivery-attempt-binding-v1",
                foreign_legacy_attempt.delivery_attempt_id.value,
                batch.delivery_batch_commitment_id.value,
            )
        )

    nonzero_legacy_attempt = DeliveryAttemptIdentity(
        destination_id=destination.value,
        materialization_key_canonical_texts=tuple(
            item.materialization_key_id.value for item in items
        ),
        attempt_ordinal=1,
    )
    with pytest.raises(ValueError, match="only attempt ordinal 0"):
        OutcomeDeliveryAttemptBindingId(
            _expected_identifier(
                "normalization-delivery-attempt-binding-v1",
                nonzero_legacy_attempt.delivery_attempt_id.value,
                batch.delivery_batch_commitment_id.value,
            )
        )


def test_delivery_batch_has_byte_exact_composite_identity_and_stored_verification() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=2)
    items = _delivery_commitments(outcome)
    batch = DeliveryBatchCommitment(outcome, items)
    item_rows = tuple(
        (item.materialization_key_id.value, item.event_content_sha256) for item in items
    )
    ordered_item_content = _expected_identifier("delivery-item-commitments-v1", item_rows)
    aggregate_digest = hashlib.sha256(ordered_item_content.encode()).hexdigest()
    content = _expected_identifier(
        "normalization-delivery-batch-content-v1",
        outcome.normalization_outcome_id.value,
        item_rows,
        2,
        aggregate_digest,
    )
    content_digest = hashlib.sha256(content.encode()).hexdigest()
    legacy = DeliveryAttemptIdentity(
        destination_id="normalization-delivery-commitment",
        materialization_key_canonical_texts=tuple(row[0] for row in item_rows),
        attempt_ordinal=0,
    )

    assert batch.canonical_content == content
    assert batch.aggregate_content_sha256 == aggregate_digest
    assert batch.batch_content_sha256 == content_digest
    assert batch.legacy_delivery_batch_id == legacy.delivery_batch_id
    assert batch.delivery_batch_commitment_id.value == _expected_identifier(
        "normalization-delivery-batch-commitment-v1",
        legacy.delivery_batch_id.value,
        outcome.normalization_outcome_id.value,
        2,
        content_digest,
    )
    assert (
        DeliveryBatchCommitment.from_stored(
            normalization_outcome=outcome,
            item_commitments=items,
            expected_legacy_delivery_batch_id=legacy.delivery_batch_id,
            expected_canonical_content=content,
            expected_aggregate_content_sha256=aggregate_digest,
            expected_batch_content_sha256=content_digest,
            expected_commitment_id=batch.delivery_batch_commitment_id,
        )
        == batch
    )
    assert (
        DeliveryItemCommitment.from_stored(
            materialization_key_id=items[0].materialization_key_id,
            event_content_sha256=items[0].event_content_sha256,
            expected_commitment_id=items[0].delivery_item_commitment_id,
        )
        == items[0]
    )


def test_delivery_batch_content_changes_with_outcome_even_for_identical_items() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    items = _delivery_commitments(outcome)
    first = DeliveryBatchCommitment(outcome, items)
    other_outcome = replace(outcome, normalizer_version="other-normalizer-v1")
    second = DeliveryBatchCommitment(other_outcome, items)

    assert first.legacy_delivery_batch_id == second.legacy_delivery_batch_id
    assert first.aggregate_content_sha256 == second.aggregate_content_sha256
    assert first.canonical_content != second.canonical_content
    assert first.batch_content_sha256 != second.batch_content_sha256
    assert first.delivery_batch_commitment_id != second.delivery_batch_commitment_id


def test_delivery_stored_verification_rejects_content_and_locator_tampering() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    items = _delivery_commitments(outcome)
    batch = DeliveryBatchCommitment(outcome, items)
    destination = DeliveryDestinationId("schema-v2-output-queue")
    attempt = OutcomeDeliveryAttemptBinding(destination, batch)

    with pytest.raises(ValueError, match="does not match"):
        DeliveryItemCommitment.from_stored(
            materialization_key_id=items[0].materialization_key_id,
            event_content_sha256="f" * 64,
            expected_commitment_id=items[0].delivery_item_commitment_id,
        )
    with pytest.raises(ValueError, match="does not match"):
        DeliveryBatchCommitment.from_stored(
            normalization_outcome=outcome,
            item_commitments=items,
            expected_legacy_delivery_batch_id=batch.legacy_delivery_batch_id,
            expected_canonical_content=f"{batch.canonical_content} ",
            expected_aggregate_content_sha256=batch.aggregate_content_sha256,
            expected_batch_content_sha256=batch.batch_content_sha256,
            expected_commitment_id=batch.delivery_batch_commitment_id,
        )
    with pytest.raises(ValueError, match="does not match"):
        DeliveryBatchCommitment.from_stored(
            normalization_outcome=outcome,
            item_commitments=items,
            expected_legacy_delivery_batch_id=batch.legacy_delivery_batch_id,
            expected_canonical_content=batch.canonical_content,
            expected_aggregate_content_sha256="f" * 64,
            expected_batch_content_sha256=batch.batch_content_sha256,
            expected_commitment_id=batch.delivery_batch_commitment_id,
        )
    with pytest.raises(ValueError, match="does not match"):
        DeliveryBatchCommitment.from_stored(
            normalization_outcome=outcome,
            item_commitments=items,
            expected_legacy_delivery_batch_id=batch.legacy_delivery_batch_id,
            expected_canonical_content=batch.canonical_content,
            expected_aggregate_content_sha256=batch.aggregate_content_sha256,
            expected_batch_content_sha256="f" * 64,
            expected_commitment_id=batch.delivery_batch_commitment_id,
        )
    foreign_legacy = DeliveryAttemptIdentity(
        destination_id="normalization-delivery-commitment",
        materialization_key_canonical_texts=(_outcome_keys(1)[2].materialization_key_id.value,),
        attempt_ordinal=0,
    )
    with pytest.raises(ValueError, match="does not match"):
        DeliveryBatchCommitment.from_stored(
            normalization_outcome=outcome,
            item_commitments=items,
            expected_legacy_delivery_batch_id=foreign_legacy.delivery_batch_id,
            expected_canonical_content=batch.canonical_content,
            expected_aggregate_content_sha256=batch.aggregate_content_sha256,
            expected_batch_content_sha256=batch.batch_content_sha256,
            expected_commitment_id=batch.delivery_batch_commitment_id,
        )
    foreign_commitment_id = DeliveryBatchCommitmentId(
        _expected_identifier(
            "normalization-delivery-batch-commitment-v1",
            batch.legacy_delivery_batch_id.value,
            outcome.normalization_outcome_id.value,
            batch.item_count,
            "f" * 64,
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        DeliveryBatchCommitment.from_stored(
            normalization_outcome=outcome,
            item_commitments=items,
            expected_legacy_delivery_batch_id=batch.legacy_delivery_batch_id,
            expected_canonical_content=batch.canonical_content,
            expected_aggregate_content_sha256=batch.aggregate_content_sha256,
            expected_batch_content_sha256=batch.batch_content_sha256,
            expected_commitment_id=foreign_commitment_id,
        )
    with pytest.raises(ValueError, match="does not match"):
        OutcomeDeliveryAttemptBinding.from_stored(
            destination_id=DeliveryDestinationId("foreign-output-queue"),
            delivery_batch_commitment=batch,
            attempt_ordinal=0,
            expected_legacy_delivery_attempt_id=(
                attempt.legacy_delivery_attempt.delivery_attempt_id
            ),
            expected_binding_id=attempt.outcome_delivery_attempt_binding_id,
        )


@pytest.mark.parametrize(
    "mutation",
    ["n-minus-one", "n-plus-one", "reordered", "duplicate", "foreign-substitution"],
)
def test_delivery_batch_rejects_incomplete_reordered_or_duplicate_commitments(
    mutation: str,
) -> None:
    outcome = _materialized_outcome_for_delivery(item_count=2)
    items = _delivery_commitments(outcome)
    foreign_item = DeliveryItemCommitment(
        _outcome_keys(2)[2].materialization_key_id,
        "f" * 64,
    )
    foreign_run_item = DeliveryItemCommitment(
        MaterializationKey(
            NormalizationRunId("foreign-normalization-run"),
            _outcome_keys(1)[1],
            EventFamily.TRADE,
            2,
            PayloadType.TRADE,
        ).materialization_key_id,
        items[1].event_content_sha256,
    )
    invalid = {
        "n-minus-one": items[:-1],
        "n-plus-one": (*items, foreign_item),
        "reordered": tuple(reversed(items)),
        "duplicate": (items[0], items[0]),
        "foreign-substitution": (items[0], foreign_run_item),
    }[mutation]

    with pytest.raises(ValueError, match="exactly match outcome order"):
        DeliveryBatchCommitment(outcome, invalid)


def test_delivery_batch_rejects_zero_materialization_and_non_success_outcomes() -> None:
    empty = NormalizationOutcome.legacy_transition_empty(
        normalization_run_id=NormalizationRunId("normalization-fixture"),
        raw_record_id=_raw_record_id(),
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit-fixture",
        frame_status=FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME,
        decoded_event_count=0,
        raw_event_outcomes=(),
        committed_materialization_keys=(),
    )
    with pytest.raises(ValueError, match="successful materializing outcome"):
        DeliveryBatchCommitment(empty, ())


def test_delivery_specific_collection_destination_and_monotonic_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = _materialized_outcome_for_delivery(item_count=2)
    items = _delivery_commitments(outcome)
    extra_item = DeliveryItemCommitment(
        _outcome_keys(2)[2].materialization_key_id,
        "f" * 64,
    )
    monkeypatch.setattr(market_event_v3_module, "MAX_NORMALIZATION_OUTCOME_ITEMS", 2)

    batch = DeliveryBatchCommitment(outcome, items)
    assert batch.item_count == 2
    with pytest.raises(ValueError, match="item count"):
        DeliveryBatchCommitment(outcome, (*items, extra_item))
    assert MAX_NORMALIZATION_OUTCOME_ITEMS > 2

    destination = DeliveryDestinationId("d" * 128)
    with pytest.raises(ValueError, match="maximum length"):
        DeliveryDestinationId("d" * 129)
    attempt = OutcomeDeliveryAttemptBinding(destination, batch)
    accepted = DeliveryCommitAcceptance.from_linearized(
        attempt=attempt,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=MAX_UNSIGNED_64,
    )
    assert accepted.delivery_outcome.observed_monotonic_ns == MAX_UNSIGNED_64
    with pytest.raises(ValueError, match="unsigned-64"):
        DeliveryCommitFailure.from_attempt(
            attempt=attempt,
            knowledge_status=DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            reason=DeliveryOutcomeReason.EXPLICIT_REJECTION,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=MAX_UNSIGNED_64 + 1,
        )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (
            DeliveryKnowledgeStatus.ACCEPTED,
            DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE,
        ),
        (
            DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            DeliveryOutcomeReason.EXPLICIT_REJECTION,
        ),
        (
            DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            DeliveryOutcomeReason.CANCELLATION_BEFORE_LINEARIZATION,
        ),
        (
            DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            DeliveryOutcomeReason.QUEUE_CAPACITY_TIMEOUT,
        ),
        (
            DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            DeliveryOutcomeReason.LOCAL_CONTRACT_FAILURE,
        ),
        (
            DeliveryKnowledgeStatus.ACCEPTANCE_UNCERTAIN,
            DeliveryOutcomeReason.AMBIGUOUS_COMPLETION,
        ),
    ],
)
def test_outcome_delivery_result_has_truthful_byte_exact_commit_semantics(
    status: DeliveryKnowledgeStatus,
    reason: DeliveryOutcomeReason,
) -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )
    if status is DeliveryKnowledgeStatus.ACCEPTED:
        proof: DeliveryCommitAcceptance | DeliveryCommitFailure = (
            DeliveryCommitAcceptance.from_linearized(
                attempt=attempt,
                observed_at=_RECEIVED_TIME,
                observed_monotonic_ns=123,
            )
        )
    else:
        proof = DeliveryCommitFailure.from_attempt(
            attempt=attempt,
            knowledge_status=status,
            reason=reason,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
        )
    result = proof.delivery_outcome
    expected = _expected_identifier(
        "normalization-delivery-outcome-v1",
        attempt.outcome_delivery_attempt_binding_id.value,
        status.value,
        reason.value,
        "2026-08-26T12:00:00.123456Z",
        123,
    )

    assert result.delivery_outcome_id.value == expected
    assert DeliveryOutcomeId(expected) == result.delivery_outcome_id
    assert hash(result) == hash(result)
    assert not hasattr(result, "__dict__")
    with pytest.raises(TypeError, match="commit result factories"):
        OutcomeDeliveryResult()


@pytest.mark.parametrize("status", tuple(DeliveryKnowledgeStatus))
@pytest.mark.parametrize("reason", tuple(DeliveryOutcomeReason))
def test_delivery_outcome_status_reason_matrix_is_exhaustive(
    status: DeliveryKnowledgeStatus,
    reason: DeliveryOutcomeReason,
) -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )
    canonical = _expected_identifier(
        "normalization-delivery-outcome-v1",
        attempt.outcome_delivery_attempt_binding_id.value,
        status.value,
        reason.value,
        "2026-08-26T12:00:00.123456Z",
        123,
    )
    valid = {
        (
            DeliveryKnowledgeStatus.ACCEPTED,
            DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE,
        ),
        *(
            (DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED, allowed_reason)
            for allowed_reason in (
                DeliveryOutcomeReason.QUEUE_CAPACITY_TIMEOUT,
                DeliveryOutcomeReason.CANCELLATION_BEFORE_LINEARIZATION,
                DeliveryOutcomeReason.EXPLICIT_REJECTION,
                DeliveryOutcomeReason.LOCAL_CONTRACT_FAILURE,
            )
        ),
        (
            DeliveryKnowledgeStatus.ACCEPTANCE_UNCERTAIN,
            DeliveryOutcomeReason.AMBIGUOUS_COMPLETION,
        ),
    }

    if (status, reason) in valid:
        assert DeliveryOutcomeId(canonical).value == canonical
    else:
        with pytest.raises(ValueError, match="inconsistent"):
            DeliveryOutcomeId(canonical)


@pytest.mark.parametrize(
    ("observed_at", "monotonic", "expected_error"),
    [
        (datetime(2026, 8, 26, 12, 0), 1, ValueError),
        (datetime(2026, 8, 26, 12, 0, tzinfo=timezone(timedelta(hours=1))), 1, ValueError),
        (cast(datetime, "2026-08-26T12:00:00Z"), 1, TypeError),
        (_RECEIVED_TIME, True, TypeError),
        (_RECEIVED_TIME, -1, ValueError),
    ],
)
def test_delivery_factories_reject_noncanonical_time_and_monotonic_values(
    observed_at: datetime,
    monotonic: int,
    expected_error: type[Exception],
) -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )

    with pytest.raises(expected_error):
        DeliveryCommitFailure.from_attempt(
            attempt=attempt,
            knowledge_status=DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            reason=DeliveryOutcomeReason.EXPLICIT_REJECTION,
            observed_at=observed_at,
            observed_monotonic_ns=monotonic,
        )


def test_delivery_outcome_id_rejects_boolean_monotonic_value() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )

    with pytest.raises(ValueError, match="invalid components"):
        DeliveryOutcomeId(
            _expected_identifier(
                "normalization-delivery-outcome-v1",
                attempt.outcome_delivery_attempt_binding_id.value,
                DeliveryKnowledgeStatus.ACCEPTED.value,
                DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE.value,
                "2026-08-26T12:00:00.123456Z",
                True,
            )
        )


def test_delivery_commit_acceptance_is_only_positive_proof_and_binds_exact_batch() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )
    acceptance = DeliveryCommitAcceptance.from_linearized(
        attempt=attempt,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=123,
    )
    accepted = acceptance.delivery_outcome

    assert (
        DeliveryCommitAcceptance.from_stored(
            attempt=attempt,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
            expected_outcome_id=accepted.delivery_outcome_id,
            expected_acceptance_id=acceptance.delivery_commit_acceptance_id,
        )
        == acceptance
    )
    assert (
        DeliveryCommitAcceptanceId(acceptance.delivery_commit_acceptance_id.value)
        == acceptance.delivery_commit_acceptance_id
    )

    foreign_outcome = replace(outcome, normalizer_version="foreign-normalizer")
    foreign_batch = DeliveryBatchCommitment(
        foreign_outcome,
        _delivery_commitments(foreign_outcome),
    )
    foreign_attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        foreign_batch,
    )
    with pytest.raises(ValueError, match="does not match"):
        DeliveryCommitAcceptance.from_stored(
            attempt=foreign_attempt,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
            expected_outcome_id=accepted.delivery_outcome_id,
            expected_acceptance_id=acceptance.delivery_commit_acceptance_id,
        )
    with pytest.raises(TypeError, match="from_linearized"):
        DeliveryCommitAcceptance()
    with pytest.raises(ValueError, match="requires commit acceptance"):
        DeliveryCommitFailure.from_attempt(
            attempt=attempt,
            knowledge_status=DeliveryKnowledgeStatus.ACCEPTED,
            reason=DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
        )
    with pytest.raises(TypeError):
        replace(accepted, reason=DeliveryOutcomeReason.EXPLICIT_REJECTION)


def test_nonaccepted_delivery_helper_rejects_accepted_knowledge() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )
    factory = market_event_v3_module._construct_nonaccepted_delivery_outcome

    with pytest.raises(ValueError, match="requires commit acceptance"):
        factory(
            attempt=attempt,
            knowledge_status=DeliveryKnowledgeStatus.ACCEPTED,
            reason=DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
        )


def test_delivery_failure_rejects_inconsistent_knowledge_and_reason() -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )

    with pytest.raises(ValueError, match="inconsistent"):
        DeliveryCommitFailure.from_attempt(
            attempt=attempt,
            knowledge_status=DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            reason=DeliveryOutcomeReason.AMBIGUOUS_COMPLETION,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
        )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (
            DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
            DeliveryOutcomeReason.EXPLICIT_REJECTION,
        ),
        (
            DeliveryKnowledgeStatus.ACCEPTANCE_UNCERTAIN,
            DeliveryOutcomeReason.AMBIGUOUS_COMPLETION,
        ),
    ],
)
def test_delivery_commit_failure_has_no_event_batch(
    status: DeliveryKnowledgeStatus,
    reason: DeliveryOutcomeReason,
) -> None:
    outcome = _materialized_outcome_for_delivery(item_count=1)
    batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        batch,
    )
    failure = DeliveryCommitFailure.from_attempt(
        attempt=attempt,
        knowledge_status=status,
        reason=reason,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=123,
    )
    result = failure.delivery_outcome

    assert {
        "delivery_batch",
        "delivery_batch_commitment",
        "item_commitments",
    }.isdisjoint({item.name for item in fields(DeliveryCommitFailure)})
    assert (
        DeliveryCommitFailure.from_stored(
            attempt=attempt,
            knowledge_status=status,
            reason=reason,
            observed_at=_RECEIVED_TIME,
            observed_monotonic_ns=123,
            expected_outcome_id=result.delivery_outcome_id,
            expected_failure_id=failure.delivery_commit_failure_id,
        )
        == failure
    )
    assert (
        DeliveryCommitFailureId(failure.delivery_commit_failure_id.value)
        == failure.delivery_commit_failure_id
    )
    with pytest.raises(TypeError, match="from_attempt"):
        DeliveryCommitFailure()


def test_new_outcome_lineage_and_delivery_identifiers_reject_malformed_values() -> None:
    outcome, _transition, abort = _typed_rejected_frame_with_abort()
    lineage = cast(NormalizationCoverageLineage, outcome.coverage_lineage)

    malformed_values: tuple[tuple[Callable[[str], object], str], ...] = (
        (
            FrameAtomicAbortEvidenceId,
            _expected_identifier(
                "frame-atomic-abort-evidence-v1",
                outcome.raw_record_id.value,
                outcome.normalization_run_id.value,
                True,
                "source",
                abort.identified_coverage_scope_id.value,
                1,
                "0" * 64,
            ),
        ),
        (
            NormalizationCoverageLineageId,
            _expected_identifier(
                "normalization-coverage-lineage-v2",
                lineage.coverage_mutation_batch.coverage_mutation_batch_id.value,
                1,
                1,
                0,
                True,
                0,
                1,
                "0" * 64,
            ),
        ),
        (
            RawCoverageFanoutBindingId,
            _expected_identifier(
                "raw-coverage-fanout-binding-v1",
                "not-a-raw-record",
                "not-a-fanout-proof",
                "0" * 64,
                "0" * 64,
            ),
        ),
        (
            OutcomeDeliveryAttemptBindingId,
            _expected_identifier(
                "normalization-delivery-attempt-binding-v1",
                "not-a-delivery-attempt",
                "not-a-delivery-batch-commitment",
            ),
        ),
        (
            DeliveryItemCommitmentId,
            _expected_identifier(
                "delivery-item-commitment-v1",
                "not-a-materialization-key",
                "0" * 64,
            ),
        ),
        (
            DeliveryBatchCommitmentId,
            _expected_identifier(
                "normalization-delivery-batch-commitment-v1",
                "not-a-delivery-batch",
                "not-a-normalization-outcome",
                1,
                "0" * 64,
            ),
        ),
        (
            DeliveryOutcomeId,
            _expected_identifier(
                "normalization-delivery-outcome-v1",
                "not-an-attempt",
                DeliveryKnowledgeStatus.ACCEPTED.value,
                DeliveryOutcomeReason.OUTPUT_QUEUE_ACCEPTANCE.value,
                "2026-08-26T12:00:00.123456Z",
                1,
            ),
        ),
    )
    for identifier_type, malformed in malformed_values:
        with pytest.raises(ValueError):
            identifier_type(malformed)


def test_outcome_only_abort_and_delivery_repr_hide_private_source_marker() -> None:
    marker = "private-source-marker"
    _outcome, transition, abort = _typed_rejected_frame_with_abort()
    marked_abort = replace(abort, source_event_id=SourceEventId(marker))
    batch = _prepared_batch_for_transition(transition)
    lineage = NormalizationCoverageLineage(
        coverage_mutation_batch=batch,
        frame_atomic_abort_evidence=(marked_abort,),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=_raw_record()[0],
            coverage_fanout_proof=batch.fanout_proof,
        ),
    )

    assert marker not in repr(marked_abort)
    assert marker not in repr(lineage)
    assert marker not in repr(lineage.normalization_coverage_lineage_id)

    materialized = _materialized_outcome_for_delivery(item_count=1)
    marked_logical_source = LogicalSourceKey(
        BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        SourceEventId(marker),
    )
    marked_index = replace(
        materialized.raw_event_outcomes[0],
        logical_source_key=marked_logical_source,
    )
    marked_outcome = replace(materialized, raw_event_outcomes=(marked_index,))
    delivery_batch = DeliveryBatchCommitment(
        marked_outcome,
        (
            DeliveryItemCommitment(
                marked_outcome.committed_materialization_keys[0].materialization_key_id,
                hashlib.sha256(marker.encode()).hexdigest(),
            ),
        ),
    )
    delivery_attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        delivery_batch,
    )
    acceptance = DeliveryCommitAcceptance.from_linearized(
        attempt=delivery_attempt,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=123,
    )
    failure = DeliveryCommitFailure.from_attempt(
        attempt=delivery_attempt,
        knowledge_status=DeliveryKnowledgeStatus.DEFINITELY_NOT_ACCEPTED,
        reason=DeliveryOutcomeReason.EXPLICIT_REJECTION,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=123,
    )
    for private_delivery_value in (
        delivery_batch,
        delivery_attempt,
        acceptance,
        acceptance.delivery_outcome,
        failure,
        failure.delivery_outcome,
    ):
        assert marker not in repr(private_delivery_value)


def test_index_outcome_rejects_committed_materialization_for_other_observation() -> None:
    logical, observation, _ = _outcome_keys(0)
    other_materialization = _outcome_keys(1)[2]

    with pytest.raises(ValueError, match="same observation"):
        RawEventNormalizationOutcome(
            observation,
            _outcome_scope_binding(0),
            RawEventDisposition.MATERIALIZED_NEW,
            logical,
            other_materialization,
        )


def test_outcomes_never_contain_delivery_status_or_raw_payload_fields() -> None:
    outcome_fields = {field_.name for field_ in fields(NormalizationOutcome)}
    item_fields = {field_.name for field_ in fields(RawEventNormalizationOutcome)}

    assert "delivery_status" not in outcome_fields
    assert "delivery_outcome" not in outcome_fields
    assert "application_message_bytes" not in outcome_fields | item_fields
    assert "exception" not in outcome_fields | item_fields
    assert "users" not in outcome_fields | item_fields


@pytest.mark.parametrize("invalid_index", [True, 1.0, -1])
def test_observation_key_rejects_invalid_index_types(invalid_index: object) -> None:
    expected_error = ValueError if invalid_index == -1 else TypeError
    with pytest.raises(expected_error):
        ObservationKey(_raw_record_id(), cast(int, invalid_index))


def test_new_contracts_perform_no_runtime_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("runtime I/O is forbidden")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("os.getenv", forbidden)
    monkeypatch.setattr("time.time", forbidden)

    envelope = MarketEventEnvelopeV3(_context(), _trade())
    outcome = _materialized_outcome_for_delivery(item_count=1)
    delivery_batch = DeliveryBatchCommitment(outcome, _delivery_commitments(outcome))
    delivery_attempt = OutcomeDeliveryAttemptBinding(
        DeliveryDestinationId("schema-v2-output-queue"),
        delivery_batch,
    )
    delivery_acceptance = DeliveryCommitAcceptance.from_linearized(
        attempt=delivery_attempt,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=123,
    )
    delivery_failure = DeliveryCommitFailure.from_attempt(
        attempt=delivery_attempt,
        knowledge_status=DeliveryKnowledgeStatus.ACCEPTANCE_UNCERTAIN,
        reason=DeliveryOutcomeReason.AMBIGUOUS_COMPLETION,
        observed_at=_RECEIVED_TIME,
        observed_monotonic_ns=123,
    )

    assert envelope.envelope_schema_version == 3
    assert delivery_acceptance.delivery_outcome.knowledge_status is (
        DeliveryKnowledgeStatus.ACCEPTED
    )
    assert delivery_failure.delivery_outcome.knowledge_status is (
        DeliveryKnowledgeStatus.ACCEPTANCE_UNCERTAIN
    )
