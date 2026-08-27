"""Pure tests for market-data provenance and dormant coverage contracts."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from types import FrameType, TracebackType
from typing import cast

import pytest

import hyperliquid_bot.data_provenance as data_provenance_module
from hyperliquid_bot.contracts import Instrument, InstrumentType, Venue
from hyperliquid_bot.data_provenance import (
    BINANCE_MAINNET_SPOT_JSON_STREAMS,
    HYPERLIQUID_MAINNET_PUBLIC_TRADES,
    MAX_BINANCE_STREAMS,
    MAX_CANONICAL_IDENTIFIER_LENGTH,
    MAX_CANONICAL_INSTRUMENT_ID_LENGTH,
    MAX_CANONICAL_NESTING_DEPTH,
    MAX_CANONICAL_SCALAR_TEXT_LENGTH,
    MAX_CANONICAL_VALUE_NODES,
    MAX_CAPABILITIES,
    MAX_COLLECTION_BOUND,
    MAX_CONNECTION_WIRE_OPTIONS,
    MAX_COVERAGE_MUTATION_TARGETS,
    MAX_COVERAGE_SCOPE_MEMBERS,
    MAX_COVERAGE_SCOPE_MERKLE_SIBLINGS,
    MAX_COVERAGE_TRANSITION_REFERENCES,
    MAX_DECODED_EVENTS_PER_RAW_RECORD,
    MAX_DELIVERY_BATCH_ITEMS,
    MAX_INSTRUMENT_BINDINGS,
    MAX_INSTRUMENT_PRICE_REFERENCES,
    MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH,
    MAX_METADATA_CATALOGUE_ITEMS,
    MAX_NORMALIZATION_BINDINGS,
    MAX_NORMALIZATION_EVIDENCE_ITEMS,
    MAX_NORMALIZATION_OUTCOME_ITEMS,
    MAX_OPAQUE_IDENTIFIER_LENGTH,
    MAX_RAW_APPLICATION_MESSAGE_BYTES,
    MAX_RAW_RECORD_SEQUENCE,
    MAX_SOURCE_SEQUENCE_RANGES,
    MAX_SOURCE_TIME_FACTS,
    MAX_SUBSCRIPTION_ATTEMPT_SNAPSHOTS,
    MAX_SUBSCRIPTION_ATTEMPTS,
    MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
    MAX_SUBSCRIPTION_SPECS,
    MAX_UNSIGNED_64,
    MAX_WIRE_PARAMETERS,
    AcknowledgementEvidenceSource,
    AdapterFeedBindingId,
    AuthoritativeStateSnapshotEvidenceSource,
    CollectorRunId,
    CommittedCoverageState,
    CommittedCoverageStateId,
    ConnectionSessionId,
    ConnectionSessionIdentity,
    CorrelationId,
    CoverageCommitAcceptance,
    CoverageCommitAcceptanceId,
    CoverageDomain,
    CoverageEpochId,
    CoverageEpochIdentity,
    CoverageEvidence,
    CoverageEvidenceId,
    CoverageEvidenceKind,
    CoverageEvidenceSource,
    CoverageFanoutKind,
    CoverageFanoutProof,
    CoverageFanoutProofId,
    CoverageInitialization,
    CoverageInitializationId,
    CoverageMutationBatch,
    CoverageMutationBatchId,
    CoverageMutationDisposition,
    CoverageMutationNoOp,
    CoverageReason,
    CoverageReference,
    CoverageScope,
    CoverageScopeId,
    CoverageStateReference,
    CoverageStateReferenceId,
    CoverageStatus,
    CoverageTargetCatalog,
    CoverageTargetCatalogId,
    CoverageTransitionId,
    DeliveryAttemptId,
    DeliveryAttemptIdentity,
    DeliveryBatchId,
    EffectiveBoundaryBasis,
    EventCoverage,
    ExactIdentifiedRejectionTarget,
    FeedAccessRequirement,
    FeedCapabilitiesObservation,
    FeedCapabilitiesObservationId,
    FeedCapabilityCode,
    FeedCapabilitySet,
    FeedCapabilitySetId,
    FeedEntitlementClass,
    FeedProductId,
    FeedProductIdentity,
    FeedTransport,
    FrameKind,
    InitialActivationEvidenceSource,
    InitialCoverageReason,
    InstrumentMetadataObservationId,
    InstrumentSpecificationId,
    InstrumentSubscriptionBinding,
    NormalizationBinding,
    NormalizationFailureCategory,
    NormalizationFailureEvidenceId,
    NormalizationFailureEvidenceSource,
    NormalizationOutcomeEvidenceSource,
    NormalizationOutcomeId,
    NormalizationRunId,
    PublicConnectionOption,
    PublicConnectionOptionKind,
    PublicEndpointProfile,
    PublicSourceSelector,
    PublicSourceSelectorKind,
    PublicSubscriptionParameter,
    PublicSubscriptionParameterKind,
    RawCoverageFanoutBinding,
    RawMarketDataRecord,
    RawRecordEvidenceSource,
    RawRecordId,
    ReconnectEvidenceSource,
    RequestedCoverageMutation,
    RequestedCoverageTransition,
    RoutedCoverageTarget,
    SanitizedValidationFailure,
    SourceEventConflictEvidenceSource,
    SourceEventId,
    SourceSequenceBreakEvidenceSource,
    SourceSequenceRange,
    SourceSequenceRole,
    SourceTimeFact,
    SourceTimeRole,
    SourceTimeUnit,
    SubscriptionAttemptId,
    SubscriptionAttemptIdentity,
    SubscriptionAttemptSnapshot,
    SubscriptionAttemptStatus,
    SubscriptionAttemptTransition,
    SubscriptionPlanId,
    SubscriptionPlanIdentity,
    SubscriptionSpecId,
    SubscriptionSpecIdentity,
    SubscriptionSpecMembershipProof,
    TransportAmbiguityEvidenceSource,
    UpstreamCoverageStateEvidenceSource,
    UpstreamCoverageTransitionEvidenceSource,
    ValidationFailureCategory,
    WireEncoding,
    canonical_json_array,
    canonical_utc_datetime,
    coverage_scope_id_from_canonical_content,
    parse_canonical_json_array,
    prepare_coverage_mutation_batch,
    reduce_coverage,
    reduce_subscription_attempt_status,
    require_collection_size,
    require_text,
    subscription_plan_id_from_canonical_content,
    subscription_spec_id_from_canonical_content,
    subscription_spec_membership_proof,
    validate_canonical_instrument_id,
    validate_coverage_scope_canonical_content,
    validate_raw_record_sequence,
    validate_subscription_attempt_sequence,
    validate_subscription_plan_canonical_content,
    validate_subscription_spec_canonical_content,
)


def _instrument(coin: str = "BTC") -> Instrument:
    base_asset = coin if coin.isalnum() and coin.isupper() else "BTC"
    return Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset=base_asset,
        quote_asset="USDC",
        venue_market_id=coin,
        native_symbol=coin,
    )


def _spec(coin: str = "BTC") -> SubscriptionSpecIdentity:
    return SubscriptionSpecIdentity(
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        wire_method="subscribe",
        wire_subscription_type="trades",
        wire_parameters=(
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.HYPERLIQUID_COIN,
                coin,
            ),
        ),
    )


def _binance_instrument(symbol: str) -> Instrument:
    base_asset = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}[symbol]
    return Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset=base_asset,
        quote_asset="USDT",
        venue_market_id=symbol,
        native_symbol=symbol,
    )


def _binance_spec(*streams: str, request_id: int = 1) -> SubscriptionSpecIdentity:
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
                tuple(sorted(streams)),
            ),
        ),
    )


def _binance_plan(*symbols: str) -> SubscriptionPlanIdentity:
    spec = _binance_spec(*(f"{symbol.lower()}@trade" for symbol in symbols))
    adapter = AdapterFeedBindingId(
        canonical_json_array(
            (
                "adapter-feed-binding-v1",
                "binance-spot-json-trade-v1",
                BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
                Venue.BINANCE.value,
                "acknowledged",
            )
        )
    )
    bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=_binance_instrument(symbol),
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                        f"{symbol.lower()}@trade",
                    ),
                    subscription_spec=spec,
                    adapter_profile="binance-spot-json-trade-v1",
                )
                for symbol in symbols
            ),
            key=lambda item: item.canonical_components(),
        )
    )
    return SubscriptionPlanIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        adapter_feed_binding_id=adapter,
        subscription_specs=(spec,),
        instrument_bindings=bindings,
        normalization_bindings=(
            NormalizationBinding(
                spec.subscription_spec_id,
                "binance-spot-json-trade-v1",
                "trade",
                2,
                "trade",
            ),
        ),
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.BINANCE_PRODUCTION_SPOT,
            ),
        ),
    )


def _binance_individual_spec_plan(
    *,
    second_request_id: int,
) -> SubscriptionPlanIdentity:
    first = _binance_spec("btcusdt@trade", request_id=1)
    second = _binance_spec("ethusdt@trade", request_id=second_request_id)
    specs = tuple(sorted((first, second), key=lambda item: item.subscription_spec_id.value))
    spec_by_stream = {
        cast(
            tuple[str, ...],
            dict(parameter.canonical_wire_row() for parameter in spec.wire_parameters)["params"],
        )[0]: spec
        for spec in specs
    }
    bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=_binance_instrument(symbol),
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                        stream,
                    ),
                    subscription_spec=spec_by_stream[stream],
                    adapter_profile="binance-spot-json-trade-v1",
                )
                for symbol, stream in (
                    ("BTCUSDT", "btcusdt@trade"),
                    ("ETHUSDT", "ethusdt@trade"),
                )
            ),
            key=lambda item: item.canonical_components(),
        )
    )
    normalizations = tuple(
        sorted(
            (
                NormalizationBinding(
                    spec.subscription_spec_id,
                    "binance-spot-json-trade-v1",
                    "trade",
                    2,
                    "trade",
                )
                for spec in specs
            ),
            key=lambda item: item.subscription_spec_id.value,
        )
    )
    return replace(
        _binance_plan("BTCUSDT", "ETHUSDT"),
        subscription_specs=specs,
        instrument_bindings=bindings,
        normalization_bindings=normalizations,
    )


def _plan(spec: SubscriptionSpecIdentity | None = None) -> SubscriptionPlanIdentity:
    selected = spec or _spec()
    coin = cast(str, selected.wire_parameters[0].value)
    instrument = _instrument(coin)
    return SubscriptionPlanIdentity(
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        adapter_feed_binding_id=AdapterFeedBindingId(
            '["adapter-feed-binding-v1","hyperliquid-trades-v1",'
            '"[\\"feed-product-v1\\",\\"hyperliquid\\",\\"production\\",'
            '\\"mainnet\\",\\"public-websocket-market-data\\",'
            '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
            '\\"json-text\\"]","hyperliquid","acknowledged"]'
        ),
        subscription_specs=(selected,),
        instrument_bindings=(
            InstrumentSubscriptionBinding(
                instrument=instrument,
                source_selector=PublicSourceSelector(
                    PublicSourceSelectorKind.HYPERLIQUID_COIN,
                    coin,
                ),
                subscription_spec=selected,
                adapter_profile="hyperliquid-trades-v1",
            ),
        ),
        normalization_bindings=(
            NormalizationBinding(
                subscription_spec_id=selected.subscription_spec_id,
                adapter_profile="hyperliquid-trades-v1",
                event_family="trade",
                event_family_schema_version=2,
                payload_type="trade",
            ),
        ),
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.HYPERLIQUID_PRODUCTION_MAINNET,
            ),
        ),
    )


def _multi_hyperliquid_plan(size: int) -> SubscriptionPlanIdentity:
    coins = tuple(f"C{index:02d}" for index in range(size))
    specs = tuple(
        sorted((_spec(coin) for coin in coins), key=lambda item: item.subscription_spec_id.value)
    )
    spec_by_coin = {cast(str, spec.wire_parameters[0].value): spec for spec in specs}
    instrument_bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=_instrument(coin),
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.HYPERLIQUID_COIN,
                        coin,
                    ),
                    subscription_spec=spec_by_coin[coin],
                    adapter_profile="hyperliquid-trades-v1",
                )
                for coin in coins
            ),
            key=lambda item: item.canonical_components(),
        )
    )
    normalizations = tuple(
        NormalizationBinding(
            spec.subscription_spec_id,
            "hyperliquid-trades-v1",
            "trade",
            2,
            "trade",
        )
        for spec in specs
    )
    template = _plan()
    return SubscriptionPlanIdentity(
        feed_product_id=template.feed_product_id,
        adapter_feed_binding_id=template.adapter_feed_binding_id,
        subscription_specs=specs,
        instrument_bindings=instrument_bindings,
        normalization_bindings=normalizations,
        connection_wire_options=template.connection_wire_options,
    )


def _session(*, connection_ordinal: int = 0) -> ConnectionSessionIdentity:
    return ConnectionSessionIdentity(
        collector_run_id=CollectorRunId("collector-run-fixture-1"),
        connection_ordinal=connection_ordinal,
    )


def _attempt_snapshot(
    *,
    status: SubscriptionAttemptStatus = SubscriptionAttemptStatus.SENT,
    connection_ordinal: int = 0,
    attempt_ordinal: int = 0,
    spec: SubscriptionSpecIdentity | None = None,
) -> SubscriptionAttemptSnapshot:
    selected = spec or _spec()
    return SubscriptionAttemptSnapshot(
        subscription_attempt=SubscriptionAttemptIdentity(
            connection_session=_session(connection_ordinal=connection_ordinal),
            subscription_spec=selected,
            attempt_ordinal=attempt_ordinal,
        ),
        attempt_status=status,
    )


def _selected_attempt_ids(
    *snapshots: SubscriptionAttemptSnapshot,
) -> tuple[SubscriptionAttemptId, ...]:
    return tuple(
        sorted(
            (item.subscription_attempt.subscription_attempt_id for item in snapshots),
            key=lambda item: item.value,
        )
    )


def _raw_record(
    *,
    ingress_ordinal: int = 0,
    connection_ordinal: int = 0,
    status: SubscriptionAttemptStatus = SubscriptionAttemptStatus.SENT,
    application_message_bytes: bytes = b'{"channel":"trades"}',
    frame_kind: FrameKind = FrameKind.TEXT,
    received_time: datetime = datetime(2026, 8, 26, 12, 0, 0, 123456, tzinfo=UTC),
) -> RawMarketDataRecord:
    spec = _spec()
    session = _session(connection_ordinal=connection_ordinal)
    snapshot = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(session, spec, 0),
        status,
    )
    return RawMarketDataRecord(
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        collector_run_id=session.collector_run_id,
        connection_session=session,
        subscription_plan=_plan(spec),
        subscription_attempt_snapshots=(snapshot,),
        ingress_ordinal=ingress_ordinal,
        frame_kind=frame_kind,
        application_message_bytes=application_message_bytes,
        received_time=received_time,
        received_monotonic_ns=123_456_789,
        collector_version="collector-v2",
        collector_commit="88d1591",
    )


def _normalization_outcome_id(
    raw_record_id: RawRecordId,
    *,
    normalization_run_id: NormalizationRunId | None = None,
    content_sha256: str = "0" * 64,
    frame_status: str = "materialized",
) -> NormalizationOutcomeId:
    decoded_event_count = {
        "control_no_event": None,
        "valid_empty_market_frame": 0,
        "rejected_before_indexing": None,
    }.get(frame_status, 1)
    return NormalizationOutcomeId(
        canonical_json_array(
            (
                "normalization-outcome-v1",
                normalization_run_id or NormalizationRunId("normalization-run-fixture"),
                raw_record_id,
                "normalizer-v1",
                "normalizer-commit-fixture",
                frame_status,
                decoded_event_count,
                content_sha256,
            )
        )
    )


def _scope(domain: CoverageDomain, coin: str = "BTC") -> CoverageScope:
    spec = _spec(coin)
    return CoverageScope(
        domain=domain,
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        subscription_spec_ids=(spec.subscription_spec_id,),
        canonical_instrument_ids=(_instrument(coin).canonical_instrument_id,),
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )


def _coverage_reference(
    domain: CoverageDomain,
    status: CoverageStatus = CoverageStatus.COMPLETE,
) -> CoverageReference:
    scope = _scope(domain)
    activation_time = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        activation_time,
        100,
    )
    initial_source: CoverageEvidenceSource
    if status is CoverageStatus.COMPLETE:
        initial_reason = InitialCoverageReason.INITIAL_ACTIVATION
        initial_kind = CoverageEvidenceKind.INITIAL_ACTIVATION
        initial_source = InitialActivationEvidenceSource(
            connection_session=_session(),
            acknowledged_attempts=(
                _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED),
            ),
        )
    elif status is CoverageStatus.UNCERTAIN and domain is CoverageDomain.BRONZE_INGRESS:
        initial_reason = InitialCoverageReason.TRANSPORT_AMBIGUITY
        initial_kind = CoverageEvidenceKind.TRANSPORT_FAILURE
        sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
        initial_source = TransportAmbiguityEvidenceSource(
            scope.feed_product_id,
            _session(),
            sent.subscription_attempt,
            subscription_spec_membership_proof(
                scope,
                sent.subscription_spec.subscription_spec_id,
            ),
        )
    elif status is CoverageStatus.CONFIRMED_INCOMPLETE and domain is CoverageDomain.BRONZE_INGRESS:
        initial_reason = InitialCoverageReason.RAW_DEFINITE_REJECTION
        initial_kind = CoverageEvidenceKind.RAW_RECORD_REJECTION
        initial_source = RawRecordEvidenceSource(
            _raw_record().raw_record_id,
            scope.coverage_scope_id,
        )
    elif (
        status is CoverageStatus.CONFIRMED_INCOMPLETE
        and domain is CoverageDomain.SILVER_NORMALIZATION
    ):
        initial_reason = InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE
        initial_kind = CoverageEvidenceKind.NORMALIZATION_FAILURE
        initial_source = NormalizationFailureEvidenceSource(
            _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED).raw_record_id,
            NormalizationRunId("normalization-run-fixture"),
            0,
            SourceEventId("source-event-fixture"),
            NormalizationFailureCategory.DECODER_REJECTION,
            scope.coverage_scope_id,
        )
    else:
        raise ValueError("test helper requires an explicitly supported initial coverage tuple")
    return CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=status,
        initial_reason=initial_reason,
        initial_evidence=CoverageEvidence(
            kind=initial_kind,
            source=initial_source,
            scope=scope,
            epoch=epoch,
            observed_at=activation_time,
            observed_monotonic_ns=100,
        ),
    )


def _initial_coverage_reference(
    domain: CoverageDomain,
    status: CoverageStatus,
    reason: InitialCoverageReason,
    kind: CoverageEvidenceKind,
) -> CoverageReference:
    scope = _scope(domain)
    activation_time = datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        activation_time,
        200,
    )
    raw_record_id = _raw_record().raw_record_id
    source: CoverageEvidenceSource
    if kind is CoverageEvidenceKind.INITIAL_ACTIVATION:
        source = InitialActivationEvidenceSource(
            _session(),
            (_attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED),),
        )
    elif kind is CoverageEvidenceKind.TRANSPORT_FAILURE:
        sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
        source = TransportAmbiguityEvidenceSource(
            scope.feed_product_id,
            _session(),
            sent.subscription_attempt,
            subscription_spec_membership_proof(
                scope,
                sent.subscription_spec.subscription_spec_id,
            ),
        )
    elif kind in {
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.RAW_RECORD_REJECTION,
    }:
        source = RawRecordEvidenceSource(raw_record_id, scope.coverage_scope_id)
    elif kind is CoverageEvidenceKind.NORMALIZATION_FAILURE:
        source = NormalizationFailureEvidenceSource(
            raw_record_id,
            NormalizationRunId("normalization-run-fixture"),
            0,
            SourceEventId("source-event-fixture"),
            NormalizationFailureCategory.DECODER_REJECTION,
            scope.coverage_scope_id,
        )
    elif kind in {
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
    }:
        source = NormalizationOutcomeEvidenceSource(
            _normalization_outcome_id(raw_record_id),
            scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.SOURCE_SEQUENCE:
        source = SourceSequenceBreakEvidenceSource(
            scope.feed_product_id,
            SourceSequenceRange(SourceSequenceRole.EVENT_SEQUENCE, "public-trades", 1, 2),
            scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT:
        source = SourceEventConflictEvidenceSource(
            scope.feed_product_id,
            SourceEventId("source-event-fixture"),
            raw_record_id,
            0,
            scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE:
        upstream = _coverage_reference(CoverageDomain.BRONZE_INGRESS, status)
        committed = _committed_coverage_state(upstream)
        source = UpstreamCoverageStateEvidenceSource(committed)
        activation_time = upstream.epoch.activation_time
        epoch = CoverageEpochIdentity(
            scope,
            upstream.epoch.collector_run_id,
            0,
            activation_time,
            upstream.epoch.activation_monotonic_ns,
        )
    else:  # pragma: no cover - helper is closed by the parameterized matrix
        raise AssertionError("unsupported initial evidence fixture")
    evidence = CoverageEvidence(
        kind=kind,
        source=source,
        scope=scope,
        epoch=epoch,
        observed_at=activation_time,
        observed_monotonic_ns=epoch.activation_monotonic_ns,
    )
    return CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=status,
        initial_reason=reason,
        initial_evidence=evidence,
    )


def _evidence(
    scope: CoverageScope,
    kind: CoverageEvidenceKind,
    evidence_id: str = "coverage-evidence-1",
    *,
    observed_monotonic_ns: int = 101,
) -> CoverageEvidence:
    del evidence_id
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    raw_record_id = _raw_record().raw_record_id
    source: CoverageEvidenceSource
    if kind is CoverageEvidenceKind.INITIAL_ACTIVATION:
        source = InitialActivationEvidenceSource(
            _session(),
            (_attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED),),
        )
    elif kind is CoverageEvidenceKind.TRANSPORT_FAILURE:
        source = TransportAmbiguityEvidenceSource(scope.feed_product_id, _session())
    elif kind in {
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.RAW_RECORD_REJECTION,
    }:
        source = RawRecordEvidenceSource(raw_record_id, scope.coverage_scope_id)
    elif kind is CoverageEvidenceKind.NORMALIZATION_FAILURE:
        source = NormalizationFailureEvidenceSource(
            raw_record_id,
            NormalizationRunId("normalization-run-fixture"),
            0,
            SourceEventId("source-event-fixture"),
            NormalizationFailureCategory.DECODER_REJECTION,
            scope.coverage_scope_id,
        )
    elif kind in {
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
    }:
        source = NormalizationOutcomeEvidenceSource(
            _normalization_outcome_id(raw_record_id),
            scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.SOURCE_SEQUENCE:
        source = SourceSequenceBreakEvidenceSource(
            scope.feed_product_id,
            SourceSequenceRange(SourceSequenceRole.EVENT_SEQUENCE, "public-trades", 1, 2),
            scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT:
        source = SourceEventConflictEvidenceSource(
            scope.feed_product_id,
            SourceEventId("source-event-fixture"),
            raw_record_id,
            0,
            scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.RECONNECT:
        source = ReconnectEvidenceSource(scope.feed_product_id, _session())
    elif kind is CoverageEvidenceKind.ACKNOWLEDGEMENT:
        acknowledged_attempt = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
        source = AcknowledgementEvidenceSource(
            acknowledged_attempt,
            subscription_spec_membership_proof(
                scope,
                acknowledged_attempt.subscription_attempt.subscription_spec.subscription_spec_id,
            ),
        )
    elif kind is CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT:
        source = AuthoritativeStateSnapshotEvidenceSource(raw_record_id)
    elif kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION:
        source = UpstreamCoverageTransitionEvidenceSource(
            _committed_upstream_transition(CoverageStatus.UNCERTAIN)
        )
    elif kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE:
        upstream = _coverage_reference(
            CoverageDomain.BRONZE_INGRESS,
            CoverageStatus.UNCERTAIN,
        )
        source = UpstreamCoverageStateEvidenceSource(_committed_coverage_state(upstream))
    else:
        source = ReconnectEvidenceSource(scope.feed_product_id, _session())
    return CoverageEvidence(
        kind=kind,
        source=source,
        scope=scope,
        epoch=epoch,
        observed_at=datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
        observed_monotonic_ns=observed_monotonic_ns,
    )


def _activation_mutation_request(
    scope: CoverageScope,
    acknowledged_snapshot: SubscriptionAttemptSnapshot,
) -> RequestedCoverageMutation:
    boundary = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        acknowledged_snapshot.subscription_attempt.connection_session.collector_run_id,
        0,
        boundary,
        100,
    )
    evidence = CoverageEvidence(
        CoverageEvidenceKind.INITIAL_ACTIVATION,
        InitialActivationEvidenceSource(
            acknowledged_snapshot.subscription_attempt.connection_session,
            (acknowledged_snapshot,),
        ),
        scope,
        epoch,
        boundary,
        100,
    )
    return RequestedCoverageMutation(
        scope,
        epoch,
        CoverageStatus.COMPLETE,
        InitialCoverageReason.INITIAL_ACTIVATION,
        CoverageReason.INITIAL_SCOPE,
        evidence,
    )


def _outcome_sink_failure_mutation_request(
    *,
    scope: CoverageScope,
    raw_record_id: RawRecordId,
    kind: CoverageEvidenceKind,
    normalization_outcome_id: NormalizationOutcomeId | None = None,
    epoch: CoverageEpochIdentity | None = None,
) -> RequestedCoverageMutation:
    boundary = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    selected_epoch = epoch or CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        boundary,
        100,
    )
    if kind is CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION:
        status = CoverageStatus.CONFIRMED_INCOMPLETE
        initial_reason = InitialCoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION
        transition_reason = CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION
    elif kind is CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY:
        status = CoverageStatus.UNCERTAIN
        initial_reason = InitialCoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN
        transition_reason = CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN
    else:  # pragma: no cover - helper has a closed test-only API
        raise AssertionError("unsupported outcome-sink failure kind")
    evidence = CoverageEvidence(
        kind,
        NormalizationOutcomeEvidenceSource(
            normalization_outcome_id or _normalization_outcome_id(raw_record_id),
            scope.coverage_scope_id,
        ),
        scope,
        selected_epoch,
        selected_epoch.activation_time,
        selected_epoch.activation_monotonic_ns,
    )
    return RequestedCoverageMutation(
        scope,
        selected_epoch,
        status,
        initial_reason,
        transition_reason,
        evidence,
    )


def _source_conflict_mutation_request(
    state: CoverageStateReference,
) -> RequestedCoverageMutation:
    scope = state.reference.scope
    evidence = CoverageEvidence(
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
        SourceEventConflictEvidenceSource(
            scope.feed_product_id,
            SourceEventId("source-event-fixture"),
            _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED).raw_record_id,
            0,
            scope.coverage_scope_id,
        ),
        scope,
        state.reference.epoch,
        datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
        101,
    )
    return RequestedCoverageMutation(
        scope,
        state.reference.epoch,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        InitialCoverageReason.SOURCE_EVENT_CONFLICT,
        CoverageReason.SOURCE_EVENT_CONFLICT,
        evidence,
    )


def _committed_coverage_state(
    reference: CoverageReference,
) -> CommittedCoverageState:
    """Commit one helper state through the same complete prepared-CAS boundary."""

    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
    if reference.status is CoverageStatus.COMPLETE:
        fanout = CoverageFanoutProof.acknowledged_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(acknowledged,),
            selected_attempt_ids=_selected_attempt_ids(acknowledged),
            domain=reference.scope.domain,
        )
    elif reference.scope.domain is CoverageDomain.BRONZE_INGRESS:
        fanout = (
            CoverageFanoutProof.one_possibly_delivered_spec(
                plan=plan,
                catalog=catalog,
                snapshot=sent,
            )
            if reference.status is CoverageStatus.UNCERTAIN
            else CoverageFanoutProof.all_possibly_active(
                plan=plan,
                catalog=catalog,
                complete_snapshots=(sent,),
                domain=CoverageDomain.BRONZE_INGRESS,
            )
        )
    elif reference.initial_evidence.kind in {
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
    }:
        fanout = CoverageFanoutProof.all_possibly_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(acknowledged,),
            domain=CoverageDomain.SILVER_NORMALIZATION,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
    else:
        fanout = CoverageFanoutProof.exact_routed_event(
            plan=plan,
            catalog=catalog,
            acknowledged_snapshot=acknowledged,
            canonical_instrument_id=_instrument().canonical_instrument_id,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
    transition_reason = {
        InitialCoverageReason.INITIAL_ACTIVATION: CoverageReason.INITIAL_SCOPE,
        InitialCoverageReason.TRANSPORT_AMBIGUITY: CoverageReason.TRANSPORT_AMBIGUITY,
        InitialCoverageReason.RAW_ACCEPTANCE_UNCERTAIN: (CoverageReason.RAW_ACCEPTANCE_UNCERTAIN),
        InitialCoverageReason.RAW_DEFINITE_REJECTION: CoverageReason.RAW_DEFINITE_REJECTION,
        InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED: (
            CoverageReason.UPSTREAM_COVERAGE_DEGRADED
        ),
        InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE: (
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE
        ),
        InitialCoverageReason.SOURCE_SEQUENCE_BREAK: CoverageReason.SOURCE_SEQUENCE_BREAK,
        InitialCoverageReason.SOURCE_EVENT_CONFLICT: CoverageReason.SOURCE_EVENT_CONFLICT,
    }[reference.initial_reason]
    request = RequestedCoverageMutation(
        reference.scope,
        reference.epoch,
        reference.status,
        reference.initial_reason,
        transition_reason,
        reference.initial_evidence,
    )
    raw_record: RawMarketDataRecord | None = None
    if reference.initial_evidence.kind in {
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.RAW_RECORD_REJECTION,
    }:
        raw_record = _raw_record(status=SubscriptionAttemptStatus.SENT)
    elif reference.initial_evidence.kind in {
        CoverageEvidenceKind.NORMALIZATION_FAILURE,
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
    }:
        raw_record = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=(request,),
        raw_fanout_binding=(
            RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=fanout,
            )
            if raw_record is not None
            else None
        ),
    )
    committed = batch.verify_compare_and_swap(())
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=committed,
    )
    return CommittedCoverageState.from_commit(
        state_reference=committed[0],
        commit_acceptance=acceptance,
    )


def _committed_upstream_transition(
    status: CoverageStatus,
) -> CommittedCoverageState:
    active = _committed_coverage_state(
        _coverage_reference(CoverageDomain.BRONZE_INGRESS, CoverageStatus.COMPLETE)
    )
    scope = active.state_reference.reference.scope
    epoch = active.state_reference.reference.epoch
    sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
    if status is CoverageStatus.UNCERTAIN:
        evidence = CoverageEvidence(
            CoverageEvidenceKind.TRANSPORT_FAILURE,
            TransportAmbiguityEvidenceSource(
                scope.feed_product_id,
                _session(),
                sent.subscription_attempt,
                subscription_spec_membership_proof(
                    scope,
                    sent.subscription_spec.subscription_spec_id,
                ),
            ),
            scope,
            epoch,
            datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            101,
        )
        fanout = CoverageFanoutProof.one_possibly_delivered_spec(
            plan=_plan(),
            catalog=CoverageTargetCatalog.from_subscription_plan(_plan()),
            snapshot=sent,
        )
        initial_reason = InitialCoverageReason.TRANSPORT_AMBIGUITY
        transition_reason = CoverageReason.TRANSPORT_AMBIGUITY
    elif status is CoverageStatus.CONFIRMED_INCOMPLETE:
        raw_record = _raw_record(status=SubscriptionAttemptStatus.SENT)
        evidence = CoverageEvidence(
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            RawRecordEvidenceSource(raw_record.raw_record_id, scope.coverage_scope_id),
            scope,
            epoch,
            datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            101,
        )
        fanout = CoverageFanoutProof.all_possibly_active(
            plan=_plan(),
            catalog=CoverageTargetCatalog.from_subscription_plan(_plan()),
            complete_snapshots=(sent,),
            domain=CoverageDomain.BRONZE_INGRESS,
        )
        initial_reason = InitialCoverageReason.RAW_DEFINITE_REJECTION
        transition_reason = CoverageReason.RAW_DEFINITE_REJECTION
    else:
        raise ValueError("upstream transition helper requires a degraded status")
    request = RequestedCoverageMutation(
        scope,
        epoch,
        status,
        initial_reason,
        transition_reason,
        evidence,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(active.state_reference,),
        requests=(request,),
        raw_fanout_binding=(
            RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw_record,
                coverage_fanout_proof=fanout,
            )
            if status is CoverageStatus.CONFIRMED_INCOMPLETE
            else None
        ),
    )
    committed = batch.verify_compare_and_swap((active.state_reference,))
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=committed,
    )
    return CommittedCoverageState.from_commit(
        state_reference=committed[0],
        commit_acceptance=acceptance,
    )


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _assert_bounded_exception_surface_excludes(
    error: BaseException,
    *markers: str | bytes,
) -> None:
    """Check bounded attributes only; traceback frames are intentionally private."""

    stack: list[object] = [error]
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        representation = repr(current)
        for marker in markers:
            marker_text = marker.decode("latin-1") if isinstance(marker, bytes) else marker
            assert marker_text not in representation
            if isinstance(marker, bytes):
                assert marker not in representation.encode("utf-8", errors="replace")
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


def _materialization_key_text() -> str:
    observation = canonical_json_array(("observation-key-v1", _raw_record().raw_record_id, 0))
    return canonical_json_array(
        (
            "materialization-key-v1",
            "normalization-run-1",
            observation,
            "trade",
            2,
            "trade",
        )
    )


def _restore_raw_record(
    record: RawMarketDataRecord,
    *,
    expected_payload_length: int | None = None,
    expected_payload_sha256: str | None = None,
    expected_raw_record_id: RawRecordId | None = None,
    expected_full_record_integrity_sha256: str | None = None,
) -> RawMarketDataRecord:
    return RawMarketDataRecord.from_stored(
        feed_product=record.feed_product,
        collector_run_id=record.collector_run_id,
        connection_session=record.connection_session,
        subscription_plan=record.subscription_plan,
        subscription_attempt_snapshots=record.subscription_attempt_snapshots,
        ingress_ordinal=record.ingress_ordinal,
        frame_kind=record.frame_kind,
        application_message_bytes=record.application_message_bytes,
        received_time=record.received_time,
        received_monotonic_ns=record.received_monotonic_ns,
        collector_version=record.collector_version,
        collector_commit=record.collector_commit,
        raw_record_schema_version=record.raw_record_schema_version,
        expected_payload_length=(
            record.payload_length if expected_payload_length is None else expected_payload_length
        ),
        expected_payload_sha256=(
            record.payload_sha256 if expected_payload_sha256 is None else expected_payload_sha256
        ),
        expected_raw_record_id=(
            record.raw_record_id if expected_raw_record_id is None else expected_raw_record_id
        ),
        expected_full_record_integrity_sha256=(
            record.full_record_integrity_sha256
            if expected_full_record_integrity_sha256 is None
            else expected_full_record_integrity_sha256
        ),
    )


def test_feed_product_ids_have_exact_versioned_preimages() -> None:
    assert HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id.value == (
        '["feed-product-v1","hyperliquid","production","mainnet",'
        '"public-websocket-market-data","public-unauthenticated","public",'
        '"websocket","json-text"]'
    )
    assert BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id.value == (
        '["feed-product-v1","binance","production","mainnet",'
        '"spot-json-market-streams","public-unauthenticated","public",'
        '"websocket","json-text"]'
    )


def test_feed_products_distinguish_product_and_access_requirement() -> None:
    binance_usds_m = replace(
        BINANCE_MAINNET_SPOT_JSON_STREAMS,
        product_code="usds-m-json-market-streams",
    )
    bitvavo_standard = FeedProductIdentity(
        venue=Venue.BITVAVO,
        source_environment="production",
        source_network="mainnet",
        product_code="standard-market-data",
        access_requirement=FeedAccessRequirement.PUBLIC_UNAUTHENTICATED,
        entitlement_class=FeedEntitlementClass.STANDARD,
        transport=FeedTransport.WEBSOCKET,
        wire_encoding=WireEncoding.JSON_TEXT,
    )
    bitvavo_pro = replace(
        bitvavo_standard,
        product_code="market-data-pro",
        access_requirement=FeedAccessRequirement.AUTHENTICATED_READ_ONLY,
        entitlement_class=FeedEntitlementClass.PRO,
    )

    assert binance_usds_m.feed_product_id != BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id
    assert bitvavo_standard.feed_product_id != bitvavo_pro.feed_product_id
    assert bitvavo_pro.access_requirement is FeedAccessRequirement.AUTHENTICATED_READ_ONLY


def test_capability_ids_have_exact_versioned_preimages_and_do_not_grant_access() -> None:
    capability_set = FeedCapabilitySet(
        HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        (FeedCapabilityCode.BBO, FeedCapabilityCode.TRADES),
    )
    observation = FeedCapabilitiesObservation(
        capability_set=capability_set,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_basis=EffectiveBoundaryBasis.SOURCE_DECLARED,
        source_declared_until=None,
        observed_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert capability_set.capability_set_id.value == (
        '["feed-capability-set-v1",'
        '"[\\"feed-product-v1\\",\\"hyperliquid\\",\\"production\\",'
        '\\"mainnet\\",\\"public-websocket-market-data\\",'
        '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
        '\\"json-text\\"]",["bbo","trades"]]'
    )
    assert _text_sha256(observation.observation_id.value) == (
        "eb26bf8ed0c4dd57a5a3564c17155b04808957398a9e4c852c4608a982ba9ab7"
    )
    assert json.loads(observation.observation_id.value) == [
        "feed-capabilities-observation-v1",
        capability_set.capability_set_id.value,
        "2026-01-01T00:00:00.000000Z",
        "source-declared",
        None,
        "2026-02-01T00:00:00.000000Z",
    ]
    assert not hasattr(observation, "authorized")
    assert not hasattr(observation, "credential")


def test_capabilities_must_be_sorted_unique_and_exactly_typed() -> None:
    with pytest.raises(ValueError):
        FeedCapabilitySet(
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            (FeedCapabilityCode.TRADES, FeedCapabilityCode.BBO),
        )
    with pytest.raises(ValueError):
        FeedCapabilitySet(
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            (FeedCapabilityCode.TRADES, FeedCapabilityCode.TRADES),
        )
    with pytest.raises(TypeError):
        FeedCapabilitySet(
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            cast(tuple[FeedCapabilityCode, ...], ("trades",)),
        )


def test_connection_spec_plan_and_attempt_ids_have_exact_preimages() -> None:
    spec = _spec()
    session = _session()
    plan = _plan(spec)
    attempt = SubscriptionAttemptIdentity(session, spec, 0)

    assert session.connection_session_id.value == (
        '["connection-session-v1","collector-run-fixture-1",0]'
    )
    assert spec.subscription_spec_id.value == (
        '["subscription-spec-v1",'
        '"[\\"feed-product-v1\\",\\"hyperliquid\\",\\"production\\",'
        '\\"mainnet\\",\\"public-websocket-market-data\\",'
        '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
        '\\"json-text\\"]","subscribe","trades",1,'
        '"70fbd6a3064313a5576f0d3fa8fb4bbf578e0b351ebad704d317ffd5c8671ec4"]'
    )
    assert spec.subscription_spec_canonical_content == (
        '["subscription-spec-content-v1",'
        '"[\\"feed-product-v1\\",\\"hyperliquid\\",\\"production\\",'
        '\\"mainnet\\",\\"public-websocket-market-data\\",'
        '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
        '\\"json-text\\"]","subscribe","trades",[["coin","BTC"]]]'
    )
    assert json.loads(plan.subscription_plan_id.value) == [
        "subscription-plan-v1",
        HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id.value,
        plan.adapter_feed_binding_id.value,
        plan.subscription_plan_content_sha256,
    ]
    assert json.loads(plan.subscription_plan_canonical_content) == [
        "subscription-plan-content-v1",
        HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id.value,
        plan.adapter_feed_binding_id.value,
        [
            [
                spec.subscription_spec_id.value,
                spec.subscription_spec_canonical_content,
            ]
        ],
        [
            [
                plan.instrument_bindings[0].canonical_instrument_id,
                "BTC",
                ["public-source-selector-v1", "hyperliquid-coin", "BTC"],
                spec.subscription_spec_id.value,
            ]
        ],
        [
            [
                spec.subscription_spec_id.value,
                "hyperliquid-trades-v1",
                "trade",
                2,
                "trade",
            ]
        ],
        [["endpoint-profile", "hyperliquid-production-mainnet-public"]],
    ]
    assert (
        validate_subscription_plan_canonical_content(plan.subscription_plan_canonical_content)
        == plan.subscription_plan_canonical_content
    )
    assert (
        subscription_plan_id_from_canonical_content(plan.subscription_plan_canonical_content)
        == plan.subscription_plan_id
    )
    assert plan.subscription_plan_content_sha256 == (
        "19fd26fb9e00482c2c728e4a9d2fc8fa09cff7a5c51971b1f8f87b87c49f17aa"
    )
    assert plan.subscription_plan_id.value == json.dumps(
        [
            "subscription-plan-v1",
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id.value,
            plan.adapter_feed_binding_id.value,
            "19fd26fb9e00482c2c728e4a9d2fc8fa09cff7a5c51971b1f8f87b87c49f17aa",
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert _text_sha256(attempt.subscription_attempt_id.value) == (
        "da3f09b61b48aa84afd5f24a429bf646d87e10ad6bb757facdd102dadb899ac5"
    )
    assert json.loads(attempt.subscription_attempt_id.value) == [
        "subscription-attempt-v1",
        session.connection_session_id.value,
        spec.subscription_spec_id.value,
        0,
    ]


@pytest.mark.parametrize("size", [1, 4, 50])
def test_subscription_plan_id_is_bounded_content_addressed_and_deterministic(size: int) -> None:
    coins = tuple(f"C{index:02d}" for index in range(size))
    specs = tuple(
        sorted((_spec(coin) for coin in coins), key=lambda item: item.subscription_spec_id.value)
    )
    instruments = tuple(
        Instrument(
            venue=Venue.HYPERLIQUID,
            instrument_type=InstrumentType.PERPETUAL,
            base_asset=coin,
            quote_asset="USDC",
            venue_market_id=coin,
            native_symbol=coin,
        )
        for coin in coins
    )
    binding_by_coin = {spec.wire_parameters[0].value: spec for spec in specs}
    instrument_bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=instrument,
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.HYPERLIQUID_COIN,
                        instrument.native_symbol,
                    ),
                    subscription_spec=binding_by_coin[instrument.native_symbol],
                    adapter_profile="hyperliquid-trades-v1",
                )
                for instrument in instruments
            ),
            key=lambda item: (item.canonical_instrument_id, item.subscription_spec_id.value),
        )
    )
    normalization_bindings = tuple(
        NormalizationBinding(
            spec.subscription_spec_id,
            "hyperliquid-trades-v1",
            "trade",
            2,
            "trade",
        )
        for spec in specs
    )
    template = _plan()
    plan = SubscriptionPlanIdentity(
        feed_product_id=template.feed_product_id,
        adapter_feed_binding_id=template.adapter_feed_binding_id,
        subscription_specs=specs,
        instrument_bindings=instrument_bindings,
        normalization_bindings=normalization_bindings,
        connection_wire_options=template.connection_wire_options,
    )
    repeated = replace(plan)

    assert repeated.subscription_plan_id == plan.subscription_plan_id
    assert repeated.subscription_plan_canonical_content == plan.subscription_plan_canonical_content
    assert len(plan.subscription_plan_id.value) < 1024
    assert (
        plan.subscription_plan_content_sha256
        == hashlib.sha256(plan.subscription_plan_canonical_content.encode("utf-8")).hexdigest()
    )
    assert (
        subscription_plan_id_from_canonical_content(plan.subscription_plan_canonical_content)
        == plan.subscription_plan_id
    )


def test_every_semantic_plan_change_changes_content_digest_identity() -> None:
    original = _plan()
    changed = replace(
        original,
        normalization_bindings=(
            replace(original.normalization_bindings[0], event_family_schema_version=3),
        ),
    )

    assert changed.subscription_plan_content_sha256 != original.subscription_plan_content_sha256
    assert changed.subscription_plan_id != original.subscription_plan_id


def test_plan_rejects_normalizer_adapter_profile_that_contradicts_binding() -> None:
    original = _plan()

    with pytest.raises(ValueError, match="adapter profile"):
        replace(
            original,
            normalization_bindings=(
                replace(
                    original.normalization_bindings[0],
                    adapter_profile="other-public-adapter-v1",
                ),
            ),
        )


def test_subscription_spec_preserves_case_sensitive_wire_tokens_byte_exactly() -> None:
    binance = SubscriptionSpecIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        wire_method="SUBSCRIBE",
        wire_subscription_type="@trade",
        wire_parameters=(
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.BINANCE_REQUEST_ID,
                1,
            ),
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.BINANCE_STREAMS,
                ("btcusdt@trade",),
            ),
        ),
    )
    hyperliquid_hip3 = _spec("xyz:XYZ100")
    hyperliquid_spot = _spec("@107")

    assert binance.subscription_spec_id.value == (
        '["subscription-spec-v1",'
        '"[\\"feed-product-v1\\",\\"binance\\",\\"production\\",'
        '\\"mainnet\\",\\"spot-json-market-streams\\",'
        '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
        '\\"json-text\\"]","SUBSCRIBE","@trade",2,'
        '"abd06b9897578ab138286281f7f1769517245c938f6c701c1712f87b20ae13da"]'
    )
    assert json.loads(binance.subscription_spec_canonical_content)[4] == [
        ["id", 1],
        ["params", ["btcusdt@trade"]],
    ]
    assert json.loads(hyperliquid_hip3.subscription_spec_id.value)[2:] == [
        "subscribe",
        "trades",
        1,
        "f2e0ef11bf9e7691fa40a099b0ab12b777d83cbb10ade677b0b8a71cd3a4d344",
    ]
    assert json.loads(hyperliquid_spot.subscription_spec_id.value)[2:] == [
        "subscribe",
        "trades",
        1,
        "0e3d5d500ff515e77f4a222937ce2ee77ab1825b75a42c224458155aa9f52dd8",
    ]


@pytest.mark.parametrize(
    "stream",
    ["garbage", "BTCUSDT@trade", "@trade", "btcusdt@aggTrade"],
)
def test_binance_spec_rejects_non_trade_or_non_lowercase_streams_direct_and_persisted(
    stream: str,
) -> None:
    with pytest.raises(ValueError, match="lowercase <symbol>@trade"):
        PublicSubscriptionParameter(
            PublicSubscriptionParameterKind.BINANCE_STREAMS,
            (stream,),
        )

    persisted = canonical_json_array(
        (
            "subscription-spec-content-v1",
            BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            "SUBSCRIBE",
            "@trade",
            (("id", 1), ("params", (stream,))),
        )
    )
    with pytest.raises(ValueError, match="lowercase <symbol>@trade"):
        validate_subscription_spec_canonical_content(persisted)


@pytest.mark.parametrize(
    ("feed_product_id", "method", "subscription_type", "parameter_count"),
    [
        (
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            "garbage",
            "trades",
            1,
        ),
        (
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            "subscribe",
            "garbage",
            1,
        ),
        (
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            "subscribe",
            "trades",
            2,
        ),
        (
            BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            "SUBSCRIBE",
            "@trade",
            1,
        ),
    ],
)
def test_bounded_subscription_spec_id_revalidates_its_closed_public_summary(
    feed_product_id: FeedProductId,
    method: str,
    subscription_type: str,
    parameter_count: int,
) -> None:
    with pytest.raises(ValueError, match="invalid canonical components"):
        SubscriptionSpecId(
            canonical_json_array(
                (
                    "subscription-spec-v1",
                    feed_product_id,
                    method,
                    subscription_type,
                    parameter_count,
                    "0" * 64,
                )
            )
        )


def test_binance_stream_count_bound_accepts_n_and_rejects_n_plus_one() -> None:
    streams = tuple(f"s{index:04d}@trade" for index in range(MAX_BINANCE_STREAMS))
    spec = _binance_spec(*streams)

    assert len(spec.subscription_spec_canonical_content) <= (MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH)
    assert len(spec.subscription_spec_id.value) < 1024
    assert (
        subscription_spec_id_from_canonical_content(spec.subscription_spec_canonical_content)
        == spec.subscription_spec_id
    )
    with pytest.raises(ValueError, match="item count"):
        PublicSubscriptionParameter(
            PublicSubscriptionParameterKind.BINANCE_STREAMS,
            (*streams, "overflow@trade"),
        )

    oversized_content = canonical_json_array(
        (
            "subscription-spec-content-v1",
            BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            "SUBSCRIBE",
            "@trade",
            (("id", 1), ("params", (*streams, "overflow@trade"))),
        )
    )
    with pytest.raises(ValueError, match="item count"):
        validate_subscription_spec_canonical_content(oversized_content)


def test_subscription_spec_content_size_bound_has_direct_and_persisted_parity() -> None:
    streams = tuple(f"s{index:04d}{'é' * 1000}@trade" for index in range(MAX_BINANCE_STREAMS))
    parameters = (
        PublicSubscriptionParameter(
            PublicSubscriptionParameterKind.BINANCE_REQUEST_ID,
            1,
        ),
        PublicSubscriptionParameter(
            PublicSubscriptionParameterKind.BINANCE_STREAMS,
            streams,
        ),
    )
    with pytest.raises(ValueError, match="serialized-size bound"):
        SubscriptionSpecIdentity(
            feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            wire_method="SUBSCRIBE",
            wire_subscription_type="@trade",
            wire_parameters=parameters,
        )

    oversized_content = canonical_json_array(
        (
            "subscription-spec-content-v1",
            BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            "SUBSCRIBE",
            "@trade",
            tuple(parameter.canonical_wire_row() for parameter in parameters),
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    assert len(oversized_content) > MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH
    with pytest.raises(ValueError, match="maximum length"):
        validate_subscription_spec_canonical_content(oversized_content)


@pytest.mark.parametrize("coin", ["BTC", "xyz:XYZ100", "@107"])
def test_hyperliquid_instrument_binding_requires_exact_native_coin_selector(
    coin: str,
) -> None:
    spec = _spec(coin)
    instrument = _instrument(coin)
    binding = InstrumentSubscriptionBinding(
        instrument=instrument,
        source_selector=PublicSourceSelector(
            PublicSourceSelectorKind.HYPERLIQUID_COIN,
            coin,
        ),
        subscription_spec=spec,
        adapter_profile="hyperliquid-trades-v1",
    )

    assert binding.instrument == instrument
    assert binding.canonical_components() == (
        instrument.canonical_instrument_id,
        coin,
        ("public-source-selector-v1", "hyperliquid-coin", coin),
        spec.subscription_spec_id.value,
    )


def test_hyperliquid_eth_wire_spec_cannot_bind_canonical_btc_instrument() -> None:
    with pytest.raises(ValueError, match="must match"):
        InstrumentSubscriptionBinding(
            instrument=_instrument("BTC"),
            source_selector=PublicSourceSelector(
                PublicSourceSelectorKind.HYPERLIQUID_COIN,
                "ETH",
            ),
            subscription_spec=_spec("ETH"),
            adapter_profile="hyperliquid-trades-v1",
        )


def test_binance_single_and_combined_specs_bind_each_exact_instrument_stream() -> None:
    single = _binance_plan("BTCUSDT")
    combined = _binance_plan("BTCUSDT", "ETHUSDT")

    assert tuple(binding.source_selector.value for binding in single.instrument_bindings) == (
        "btcusdt@trade",
    )
    assert tuple(
        sorted(binding.source_selector.value for binding in combined.instrument_bindings)
    ) == ("btcusdt@trade", "ethusdt@trade")


@pytest.mark.parametrize(
    ("instrument_symbol", "selector", "streams"),
    [
        ("BTCUSDT", "ethusdt@trade", ("ethusdt@trade",)),
        ("BTCUSDT", "btcusdt@trade", ("ethusdt@trade",)),
        ("ETHUSDT", "btcusdt@trade", ("btcusdt@trade",)),
    ],
)
def test_binance_binding_rejects_wrong_or_absent_instrument_stream(
    instrument_symbol: str,
    selector: str,
    streams: tuple[str, ...],
) -> None:
    spec = _binance_spec(*streams)

    with pytest.raises(ValueError, match="must match exactly"):
        InstrumentSubscriptionBinding(
            instrument=_binance_instrument(instrument_symbol),
            source_selector=PublicSourceSelector(
                PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                selector,
            ),
            subscription_spec=spec,
            adapter_profile="binance-spot-json-trade-v1",
        )


@pytest.mark.parametrize("tampered_field", ["canonical_instrument_id", "base_asset"])
def test_instrument_binding_revalidates_exact_instrument_identity(
    tampered_field: str,
) -> None:
    instrument = _instrument("BTC")
    if tampered_field == "canonical_instrument_id":
        object.__setattr__(instrument, tampered_field, "invalid-canonical-instrument")
    else:
        object.__setattr__(instrument, tampered_field, "ETH")

    with pytest.raises(ValueError):
        InstrumentSubscriptionBinding(
            instrument=instrument,
            source_selector=PublicSourceSelector(
                PublicSourceSelectorKind.HYPERLIQUID_COIN,
                "BTC",
            ),
            subscription_spec=_spec("BTC"),
            adapter_profile="hyperliquid-trades-v1",
        )


def test_binance_plan_requires_unique_request_ids_across_wire_specs() -> None:
    with pytest.raises(ValueError, match="request IDs must be unique"):
        _binance_individual_spec_plan(second_request_id=1)


def test_persisted_binance_plan_revalidates_unique_request_ids() -> None:
    valid = _binance_individual_spec_plan(second_request_id=2)
    replacement = _binance_spec("ethusdt@trade", request_id=1)
    content = json.loads(valid.subscription_plan_canonical_content)
    old = next(
        spec
        for spec in valid.subscription_specs
        if "ethusdt@trade" in spec.subscription_spec_canonical_content
    )
    for row in content[3]:
        if row[0] == old.subscription_spec_id.value:
            row[:] = [
                replacement.subscription_spec_id.value,
                replacement.subscription_spec_canonical_content,
            ]
    for row in content[4]:
        if row[3] == old.subscription_spec_id.value:
            row[3] = replacement.subscription_spec_id.value
    for row in content[5]:
        if row[0] == old.subscription_spec_id.value:
            row[0] = replacement.subscription_spec_id.value
    content[3].sort(key=lambda row: row[0])
    content[4].sort()
    content[5].sort()

    with pytest.raises(ValueError, match="request IDs must be unique"):
        validate_subscription_plan_canonical_content(
            json.dumps(content, ensure_ascii=True, separators=(",", ":"))
        )


def test_persisted_subscription_plan_content_revalidates_instrument_selector_binding() -> None:
    plan = _plan()
    content = json.loads(plan.subscription_plan_canonical_content)
    content[4][0][2][2] = "ETH"
    persisted = json.dumps(content, ensure_ascii=True, separators=(",", ":"))

    with pytest.raises(ValueError, match="must match"):
        validate_subscription_plan_canonical_content(persisted)


def test_one_selector_cannot_be_bound_to_two_canonical_markets() -> None:
    plan = _plan()
    spec = plan.subscription_specs[0]
    alias_market = Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDC",
        venue_market_id="different-market",
        native_symbol="BTC",
    )
    second = InstrumentSubscriptionBinding(
        instrument=alias_market,
        source_selector=PublicSourceSelector(
            PublicSourceSelectorKind.HYPERLIQUID_COIN,
            "BTC",
        ),
        subscription_spec=spec,
        adapter_profile="hyperliquid-trades-v1",
    )

    with pytest.raises(ValueError, match="exactly one plan binding"):
        replace(
            plan,
            instrument_bindings=tuple(
                sorted(
                    (*plan.instrument_bindings, second),
                    key=lambda item: item.canonical_components(),
                )
            ),
        )


def test_one_binance_stream_cannot_be_bound_through_two_request_specs() -> None:
    first = _binance_spec("btcusdt@trade", request_id=1)
    second = _binance_spec("btcusdt@trade", request_id=2)
    specs = tuple(sorted((first, second), key=lambda item: item.subscription_spec_id.value))
    instrument = _binance_instrument("BTCUSDT")
    bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=instrument,
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM,
                        "btcusdt@trade",
                    ),
                    subscription_spec=spec,
                    adapter_profile="binance-spot-json-trade-v1",
                )
                for spec in specs
            ),
            key=lambda item: item.canonical_components(),
        )
    )
    template = _binance_plan("BTCUSDT")
    normalizations = tuple(
        NormalizationBinding(
            spec.subscription_spec_id,
            "binance-spot-json-trade-v1",
            "trade",
            2,
            "trade",
        )
        for spec in specs
    )

    with pytest.raises(ValueError, match="exactly one plan binding"):
        replace(
            template,
            subscription_specs=specs,
            instrument_bindings=bindings,
            normalization_bindings=normalizations,
        )


def test_persisted_plan_rejects_one_stream_bound_through_two_request_specs() -> None:
    plan = _binance_plan("BTCUSDT")
    second = _binance_spec("btcusdt@trade", request_id=2)
    content = json.loads(plan.subscription_plan_canonical_content)
    content[3].append(
        [
            second.subscription_spec_id.value,
            second.subscription_spec_canonical_content,
        ]
    )
    content[3].sort(key=lambda row: row[0])
    second_instrument_row = list(content[4][0])
    second_instrument_row[3] = second.subscription_spec_id.value
    content[4].append(second_instrument_row)
    content[4].sort()
    second_normalization_row = list(content[5][0])
    second_normalization_row[0] = second.subscription_spec_id.value
    content[5].append(second_normalization_row)
    content[5].sort()

    with pytest.raises(ValueError, match="exactly one plan binding"):
        validate_subscription_plan_canonical_content(
            json.dumps(content, ensure_ascii=True, separators=(",", ":"))
        )


@pytest.mark.parametrize(
    "invalid_parameters",
    [
        (("api-key", "synthetic-sensitive-marker"),),
        (("token", "synthetic-sensitive-marker"),),
        (("signature", "synthetic-sensitive-marker"),),
        (("account-id", "synthetic-sensitive-marker"),),
        (("credential-state", "synthetic-sensitive-marker"),),
        (("authorization-state", "synthetic-sensitive-marker"),),
        (("secret-endpoint", "synthetic-sensitive-marker"),),
        (("timeout", 5),),
        (("retry", 3),),
    ],
)
def test_subscription_spec_rejects_non_public_semantics_without_disclosure(
    invalid_parameters: object,
) -> None:
    marker = "synthetic-sensitive-marker"
    with pytest.raises(TypeError) as captured:
        SubscriptionSpecIdentity(
            feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            wire_method="subscribe",
            wire_subscription_type="trades",
            wire_parameters=cast(tuple[PublicSubscriptionParameter, ...], invalid_parameters),
        )

    assert marker not in str(captured.value)
    assert marker not in repr(captured.value)
    safe_plan = _plan()
    assert marker not in safe_plan.subscription_plan_id.value
    assert marker not in safe_plan.subscription_plan_canonical_content
    assert marker not in repr(safe_plan)


def test_public_parameter_values_are_hidden_from_repr() -> None:
    parameter = PublicSubscriptionParameter(
        PublicSubscriptionParameterKind.HYPERLIQUID_COIN,
        "BTC",
    )

    assert "BTC" not in repr(parameter)


def test_persisted_wire_identity_rejects_secret_bearing_fields_without_disclosure() -> None:
    marker = "synthetic-sensitive-marker"
    rejected = canonical_json_array(
        (
            "subscription-spec-content-v1",
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            "subscribe",
            "trades",
            (("api-key", marker),),
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )

    with pytest.raises(ValueError) as captured:
        validate_subscription_spec_canonical_content(rejected)

    _assert_bounded_exception_surface_excludes(captured.value, marker, rejected)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "forbidden_name",
    [
        "api-key",
        "token",
        "signature",
        "account-id",
        "credential-state",
        "authorization-state",
        "secret-endpoint",
        "timeout",
        "retry",
    ],
)
def test_subscription_plan_rejects_non_public_connection_options_without_disclosure(
    forbidden_name: str,
) -> None:
    marker = "synthetic-sensitive-marker"
    with pytest.raises(TypeError) as captured:
        replace(
            _plan(),
            connection_wire_options=cast(
                tuple[PublicConnectionOption, ...],
                ((forbidden_name, marker),),
            ),
        )

    assert marker not in str(captured.value)
    assert marker not in repr(captured.value)
    safe_plan = _plan()
    assert marker not in safe_plan.subscription_plan_id.value
    assert marker not in safe_plan.subscription_plan_canonical_content
    assert marker not in repr(safe_plan)


def test_wire_spec_identity_excludes_normalized_event_binding() -> None:
    spec = _spec()
    first = _plan(spec)
    different_family_binding = replace(
        first,
        normalization_bindings=(
            NormalizationBinding(
                spec.subscription_spec_id,
                "hyperliquid-trades-v1",
                "audit-trade",
                7,
                "audit-trade",
            ),
        ),
    )

    assert first.subscription_specs[0].subscription_spec_id == spec.subscription_spec_id
    assert different_family_binding.subscription_specs[0].subscription_spec_id == (
        spec.subscription_spec_id
    )
    assert first.subscription_plan_id != different_family_binding.subscription_plan_id


@pytest.mark.parametrize(
    ("previous", "requested"),
    [
        (SubscriptionAttemptStatus.PENDING, SubscriptionAttemptStatus.SEND_STARTED),
        (SubscriptionAttemptStatus.SEND_STARTED, SubscriptionAttemptStatus.SENT),
        (SubscriptionAttemptStatus.SEND_STARTED, SubscriptionAttemptStatus.ACKNOWLEDGED),
        (SubscriptionAttemptStatus.SENT, SubscriptionAttemptStatus.ACKNOWLEDGED),
        (SubscriptionAttemptStatus.ACKNOWLEDGED, SubscriptionAttemptStatus.ACKNOWLEDGED),
    ],
)
def test_ack_race_compatible_attempt_transitions(
    previous: SubscriptionAttemptStatus,
    requested: SubscriptionAttemptStatus,
) -> None:
    initial = _attempt_snapshot(status=previous)
    transition, current = reduce_subscription_attempt_status(initial, requested)

    assert transition.previous_status is previous
    assert transition.new_status is requested
    assert current.attempt_status is requested
    assert current.subscription_attempt == initial.subscription_attempt


@pytest.mark.parametrize(
    ("previous", "requested"),
    [
        (SubscriptionAttemptStatus.PENDING, SubscriptionAttemptStatus.SENT),
        (SubscriptionAttemptStatus.SENT, SubscriptionAttemptStatus.SEND_STARTED),
        (SubscriptionAttemptStatus.ACKNOWLEDGED, SubscriptionAttemptStatus.SENT),
        (SubscriptionAttemptStatus.ACKNOWLEDGED, SubscriptionAttemptStatus.PENDING),
    ],
)
def test_attempt_transition_rejects_skips_and_regressions(
    previous: SubscriptionAttemptStatus,
    requested: SubscriptionAttemptStatus,
) -> None:
    with pytest.raises(ValueError):
        reduce_subscription_attempt_status(_attempt_snapshot(status=previous), requested)


def test_explicit_attempt_sequence_validator_enforces_zero_based_per_group_ordinals() -> None:
    session = _session()
    btc = _spec("BTC")
    eth = _spec("ETH")
    attempts = (
        SubscriptionAttemptIdentity(session, btc, 0),
        SubscriptionAttemptIdentity(session, btc, 1),
        SubscriptionAttemptIdentity(session, eth, 0),
    )

    assert validate_subscription_attempt_sequence(attempts) == attempts
    with pytest.raises(ValueError, match="contiguous from zero"):
        validate_subscription_attempt_sequence((SubscriptionAttemptIdentity(session, btc, 1),))
    with pytest.raises(ValueError, match="duplicate"):
        validate_subscription_attempt_sequence((attempts[0], attempts[0]))


def test_direct_attempt_transition_construction_revalidates_matrix() -> None:
    attempt = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    with pytest.raises(ValueError, match="not allowed"):
        SubscriptionAttemptTransition(
            attempt.subscription_attempt,
            SubscriptionAttemptStatus.ACKNOWLEDGED,
            SubscriptionAttemptStatus.SENT,
        )


def test_raw_record_ids_and_digests_are_byte_exact_regressions() -> None:
    record = _raw_record()

    assert record.payload_length == 20
    assert (
        record.payload_sha256 == "38048516e1d41cd8fe2d1c286f0f54f9e5549c4cb06aa2e11fb7fd4c82f432a4"
    )
    assert record.raw_record_id.value == (
        '["raw-record-v1",'
        '"[\\"feed-product-v1\\",\\"hyperliquid\\",\\"production\\",'
        '\\"mainnet\\",\\"public-websocket-market-data\\",'
        '\\"public-unauthenticated\\",\\"public\\",\\"websocket\\",'
        '\\"json-text\\"]","collector-run-fixture-1",'
        '"[\\"connection-session-v1\\",\\"collector-run-fixture-1\\",0]",0,'
        '"text","38048516e1d41cd8fe2d1c286f0f54f9e5549c4cb06aa2e11fb7fd4c82f432a4"]'
    )
    assert record.full_record_integrity_sha256 == (
        "ee5ab4e01d96acf037535412ccd9aa84f44ad1c8c05e9fa216e0d2d287aa4f84"
    )


def test_raw_attempt_status_changes_full_digest_but_not_raw_identity() -> None:
    sent = _raw_record(status=SubscriptionAttemptStatus.SENT)
    acknowledged = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)

    assert sent.raw_record_id == acknowledged.raw_record_id
    assert sent.payload_sha256 == acknowledged.payload_sha256
    assert sent.full_record_integrity_sha256 != acknowledged.full_record_integrity_sha256


@pytest.mark.parametrize("status", list(SubscriptionAttemptStatus))
def test_raw_capture_snapshot_accepts_every_preparse_attempt_status(
    status: SubscriptionAttemptStatus,
) -> None:
    record = _raw_record(status=status)

    assert record.subscription_attempt_snapshots[0].attempt_status is status


def test_raw_payload_change_changes_payload_and_raw_identity() -> None:
    first = _raw_record(application_message_bytes=b"")
    second = _raw_record(application_message_bytes=b"{}")

    assert first.payload_length == 0
    assert first.payload_sha256 != second.payload_sha256
    assert first.raw_record_id != second.raw_record_id


def test_raw_record_repr_hides_application_message_bytes() -> None:
    record = _raw_record(application_message_bytes=b"synthetic-private-sentinel")

    assert "synthetic-private-sentinel" not in repr(record)
    assert (
        next(item for item in fields(record) if item.name == "application_message_bytes").repr
        is False
    )


def test_text_requires_strict_utf8_while_binary_accepts_arbitrary_bytes() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        _raw_record(application_message_bytes=b"\xff", frame_kind=FrameKind.TEXT)

    binary = _raw_record(application_message_bytes=b"\xff", frame_kind=FrameKind.BINARY)
    assert binary.application_message_bytes == b"\xff"


def test_invalid_text_bytes_are_absent_from_the_bounded_exception_surface() -> None:
    marker_text = "utf8-private-sentinel"
    marker_bytes = b"\xffutf8-private-sentinel"

    with pytest.raises(ValueError, match="UTF-8") as captured:
        _raw_record(
            application_message_bytes=marker_bytes,
            frame_kind=FrameKind.TEXT,
        )

    _assert_bounded_exception_surface_excludes(captured.value, marker_text, marker_bytes)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "constructor",
    [
        FeedProductId,
        CoverageEvidenceId,
        validate_subscription_plan_canonical_content,
    ],
)
def test_malformed_canonical_text_is_absent_from_bounded_exception_attributes(
    constructor: Callable[[str], object],
) -> None:
    marker = "canonical-private-sentinel"
    malformed = f'["{marker}"'

    with pytest.raises(ValueError) as captured:
        constructor(malformed)

    _assert_bounded_exception_surface_excludes(captured.value, marker, malformed)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_deep_json_parser_failure_has_a_bounded_unlinked_error_surface() -> None:
    marker = "deep-json-private-sentinel"
    malformed = ("[" * 20_000) + f'"{marker}"' + ("]" * 20_000)

    with pytest.raises(ValueError, match="canonical compact JSON array") as captured:
        parse_canonical_json_array(malformed, field_name="persisted_identifier")

    _assert_bounded_exception_surface_excludes(captured.value, marker, malformed)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_caller_supplied_diagnostic_label_is_never_echoed() -> None:
    marker = "PRIVATE_DIAGNOSTIC_SENTINEL"

    with pytest.raises(TypeError) as captured:
        require_text(1, field_name=marker)

    _assert_bounded_exception_surface_excludes(captured.value, marker)


def test_hostile_timezone_failure_is_absent_from_the_bounded_exception_surface() -> None:
    marker = "timezone-private-sentinel"

    class HostileTimezone(tzinfo):
        def utcoffset(self, value: datetime | None) -> timedelta | None:
            del value
            raise RuntimeError(marker)

        def dst(self, value: datetime | None) -> timedelta | None:
            del value
            return None

        def tzname(self, value: datetime | None) -> str | None:
            del value
            return None

    with pytest.raises(ValueError, match="zero UTC offset") as captured:
        canonical_utc_datetime(
            datetime(2026, 1, 1, tzinfo=HostileTimezone()),
            field_name="timestamp",
        )

    _assert_bounded_exception_surface_excludes(captured.value, marker)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_low_level_component_validation_does_not_chain_untrusted_enum_errors() -> None:
    marker = "unsupported-enum-private-sentinel"
    valid = HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id
    components = list(json.loads(valid.value))
    components[1] = marker

    with pytest.raises(ValueError, match="unsupported enum") as captured:
        valid._validate_components(tuple(components))

    _assert_bounded_exception_surface_excludes(captured.value, marker)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_component_count_rejects_hostile_length_hooks_without_invocation_or_disclosure() -> None:
    marker = "hostile-length-private-sentinel"

    class HostileLength:
        def __len__(self) -> int:
            raise RuntimeError(marker)

    valid = HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id
    with pytest.raises(TypeError, match="built-in tuple") as captured:
        valid._validate_components(
            cast(tuple[object, ...], HostileLength())  # type: ignore[arg-type]
        )

    _assert_bounded_exception_surface_excludes(captured.value, marker)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_internal_tracebacks_are_private_and_sanitized_failure_is_safe_to_export() -> None:
    marker_text = "synthetic-boundary-private-marker-7f4c"
    marker_bytes = b"\xffsynthetic-boundary-private-marker-7f4c"
    traceback_retained_marker = False

    try:
        _raw_record(
            application_message_bytes=marker_bytes,
            frame_kind=FrameKind.TEXT,
        )
    except ValueError as internal_error:
        assert internal_error.__traceback__ is not None
        current: TracebackType | None = internal_error.__traceback__
        while current is not None:
            for local_value in current.tb_frame.f_locals.values():
                if type(local_value) is bytes and marker_bytes in local_value:
                    traceback_retained_marker = True
                if (
                    type(local_value) is RawMarketDataRecord
                    and local_value.application_message_bytes == marker_bytes
                ):
                    traceback_retained_marker = True
            current = current.tb_next
        exported = SanitizedValidationFailure(ValidationFailureCategory.MALFORMED_CANONICAL_INPUT)
    else:  # pragma: no cover - fail-closed constructor is exercised above
        raise AssertionError("invalid TEXT bytes must fail")

    assert traceback_retained_marker
    assert tuple(field_.name for field_ in fields(exported)) == ("category",)
    assert not hasattr(exported, "__dict__")
    assert marker_text not in repr(exported)
    assert marker_text not in str(exported)

    stack: list[object] = [exported]
    visited: set[int] = set()
    while stack:
        current_value = stack.pop()
        if id(current_value) in visited:
            continue
        visited.add(id(current_value))
        assert not isinstance(current_value, (BaseException, TracebackType, FrameType))
        assert marker_text not in repr(current_value)
        if is_dataclass(current_value) and not isinstance(current_value, type):
            stack.extend(getattr(current_value, item.name) for item in fields(current_value))


def test_sanitized_validation_failure_is_frozen_slotted_hashable_and_closed() -> None:
    values = tuple(ValidationFailureCategory)
    assert values == (
        ValidationFailureCategory.INVALID_RUNTIME_TYPE,
        ValidationFailureCategory.INVALID_VALUE,
        ValidationFailureCategory.RESOURCE_BOUND_EXCEEDED,
        ValidationFailureCategory.MALFORMED_CANONICAL_INPUT,
        ValidationFailureCategory.INTEGRITY_MISMATCH,
        ValidationFailureCategory.INVARIANT_VIOLATION,
        ValidationFailureCategory.LOCAL_VALIDATION_FAILURE,
    )
    failure = SanitizedValidationFailure(ValidationFailureCategory.INVALID_VALUE)
    assert hash(failure) == hash(failure)
    with pytest.raises(FrozenInstanceError):
        failure.category = ValidationFailureCategory.INTEGRITY_MISMATCH  # type: ignore[misc]
    with pytest.raises(TypeError, match="ValidationFailureCategory"):
        SanitizedValidationFailure(cast(ValidationFailureCategory, "invalid-value"))

    with pytest.raises(TypeError, match="subclass"):

        class ExtendedFailure(SanitizedValidationFailure):
            pass

    with pytest.raises(TypeError, match="subclass"):

        class OverrideFailure(SanitizedValidationFailure):
            def __post_init__(self) -> None:
                pass


def test_hostile_canonical_identifier_subclass_metadata_is_not_disclosed() -> None:
    marker = "synthetic-private-version-marker-91c2"

    class HostileCanonicalIdentifier(data_provenance_module._CanonicalIdentifier):
        VERSION_TAG = marker

    with pytest.raises(ValueError) as captured:
        HostileCanonicalIdentifier("[]")

    _assert_bounded_exception_surface_excludes(captured.value, marker)


def test_stored_record_verification_recomputes_all_derived_values() -> None:
    record = _raw_record()
    restored = _restore_raw_record(record)

    assert restored == record


def test_stored_record_verification_fails_closed_on_every_derived_mismatch() -> None:
    record = _raw_record()
    with pytest.raises(ValueError):
        _restore_raw_record(record, expected_payload_length=record.payload_length + 1)
    with pytest.raises(ValueError):
        _restore_raw_record(record, expected_payload_sha256="0" * 64)
    with pytest.raises(ValueError):
        _restore_raw_record(
            record,
            expected_raw_record_id=replace(record, ingress_ordinal=1).raw_record_id,
        )
    with pytest.raises(ValueError):
        _restore_raw_record(record, expected_full_record_integrity_sha256="0" * 64)


def test_raw_record_validates_all_structured_parent_relationships() -> None:
    record = _raw_record()
    wrong_session = ConnectionSessionIdentity(CollectorRunId("another-run"), 0)
    wrong_snapshot = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(wrong_session, _spec(), 0),
        SubscriptionAttemptStatus.SENT,
    )

    with pytest.raises(ValueError, match="session"):
        replace(record, subscription_attempt_snapshots=(wrong_snapshot,))
    with pytest.raises(ValueError, match="collector_run_id"):
        replace(record, collector_run_id=CollectorRunId("another-run"))


def test_attempt_snapshots_must_be_canonically_sorted_and_unique() -> None:
    first = _attempt_snapshot(attempt_ordinal=0)
    second = _attempt_snapshot(attempt_ordinal=1)
    record = _raw_record()

    with pytest.raises(ValueError, match="sorted and unique"):
        replace(record, subscription_attempt_snapshots=(second, first))
    with pytest.raises(ValueError, match="unique by spec and attempt"):
        replace(record, subscription_attempt_snapshots=(first, first))
    duplicate_with_other_status = replace(
        first,
        attempt_status=SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    with pytest.raises(ValueError, match="unique by spec and attempt"):
        replace(
            record,
            subscription_attempt_snapshots=(first, duplicate_with_other_status),
        )


def test_individual_raw_record_only_requires_nonnegative_ordinal() -> None:
    assert _raw_record(ingress_ordinal=9).ingress_ordinal == 9
    with pytest.raises(ValueError, match="non-negative"):
        _raw_record(ingress_ordinal=-1)


def test_explicit_sequence_validator_proves_only_supplied_contiguous_prefix() -> None:
    records = (_raw_record(ingress_ordinal=0), _raw_record(ingress_ordinal=1))
    assert validate_raw_record_sequence(records) == records

    with pytest.raises(ValueError, match="starting at zero"):
        validate_raw_record_sequence((_raw_record(ingress_ordinal=1),))
    with pytest.raises(ValueError, match="contiguous"):
        validate_raw_record_sequence(
            (_raw_record(ingress_ordinal=0), _raw_record(ingress_ordinal=2))
        )


def test_source_time_facts_convert_milliseconds_and_microseconds_exactly() -> None:
    milliseconds = SourceTimeFact(
        SourceTimeRole.EXCHANGE_EVENT_TIME,
        1_725_192_000_123,
        SourceTimeUnit.EPOCH_MILLISECONDS,
    )
    microseconds = SourceTimeFact(
        SourceTimeRole.TRADE_EXECUTION_TIME,
        1_725_192_000_123_456,
        SourceTimeUnit.EPOCH_MICROSECONDS,
    )

    assert milliseconds.utc_value == datetime(2024, 9, 1, 12, 0, 0, 123000, tzinfo=UTC)
    assert microseconds.utc_value == datetime(2024, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)


@pytest.mark.parametrize("invalid", [True, 1.0, -1])
def test_source_time_facts_reject_wrong_or_negative_raw_values(invalid: object) -> None:
    error = TypeError if type(invalid) is not int else ValueError
    with pytest.raises(error):
        SourceTimeFact(
            SourceTimeRole.SOURCE_EVENT_TIME,
            cast(int, invalid),
            SourceTimeUnit.EPOCH_MILLISECONDS,
        )


def test_source_time_fact_fails_cleanly_on_datetime_overflow() -> None:
    with pytest.raises(ValueError, match="supported UTC datetime range"):
        SourceTimeFact(
            SourceTimeRole.SOURCE_EVENT_TIME,
            2**64 - 1,
            SourceTimeUnit.EPOCH_MICROSECONDS,
        )


def test_source_sequence_range_is_namespaced_nonnegative_and_inclusive() -> None:
    sequence = SourceSequenceRange(SourceSequenceRole.BOOK_UPDATE, "binance-depth", 5, 9)
    assert (sequence.first, sequence.last) == (5, 9)
    assert tuple[SourceSequenceRange, ...]() == ()

    with pytest.raises(ValueError):
        replace(sequence, last=4)
    with pytest.raises(TypeError):
        replace(sequence, first=cast(int, True))


def test_utc_canonicalization_is_exact_and_platform_independent() -> None:
    assert (
        canonical_utc_datetime(
            datetime(1, 2, 3, 4, 5, 6, 7, tzinfo=UTC),
            field_name="timestamp",
        )
        == "0001-02-03T04:05:06.000007Z"
    )
    assert (
        canonical_utc_datetime(
            datetime(2026, 1, 1, tzinfo=timezone(timedelta(0))),
            field_name="timestamp",
        )
        == "2026-01-01T00:00:00.000000Z"
    )
    with pytest.raises(ValueError):
        canonical_utc_datetime(datetime(2026, 1, 1), field_name="timestamp")


def test_canonical_json_rejects_maps_lists_and_unordered_collections() -> None:
    for invalid in ({"key": "value"}, ["value"], {"value"}):
        with pytest.raises(TypeError):
            canonical_json_array((invalid,))


def test_canonical_array_item_bounds_apply_to_top_level_and_nested_values() -> None:
    members = (None,) * MAX_COLLECTION_BOUND
    top_level = canonical_json_array(members)
    nested = canonical_json_array((members,))

    assert len(parse_canonical_json_array(top_level, field_name="canonical")) == (
        MAX_COLLECTION_BOUND
    )
    parsed_nested = parse_canonical_json_array(nested, field_name="canonical")[0]
    assert type(parsed_nested) is tuple
    assert len(parsed_nested) == MAX_COLLECTION_BOUND
    with pytest.raises(ValueError, match="top-level array-item bound"):
        canonical_json_array((*members, None))
    with pytest.raises(ValueError, match="array-item bound"):
        canonical_json_array(((*members, None),))

    oversized_top_level = "[" + ",".join(("null",) * (MAX_COLLECTION_BOUND + 1)) + "]"
    oversized_nested = f"[{oversized_top_level}]"
    with pytest.raises(ValueError, match="top-level array-item bound"):
        parse_canonical_json_array(oversized_top_level, field_name="canonical")
    with pytest.raises(ValueError, match="array-item bound"):
        parse_canonical_json_array(oversized_nested, field_name="canonical")


def test_canonical_scalar_integer_and_total_text_bounds_are_exact() -> None:
    scalar_at_bound = "x" * MAX_CANONICAL_SCALAR_TEXT_LENGTH
    assert canonical_json_array(
        (scalar_at_bound,),
        maximum_length=MAX_CANONICAL_SCALAR_TEXT_LENGTH + 4,
    ).startswith('["x')
    with pytest.raises(ValueError, match="scalar-text bound"):
        canonical_json_array(
            (scalar_at_bound + "x",),
            maximum_length=MAX_CANONICAL_SCALAR_TEXT_LENGTH + 5,
        )

    assert canonical_json_array((MAX_UNSIGNED_64,)) == f"[{MAX_UNSIGNED_64}]"
    with pytest.raises(ValueError, match="integer bound"):
        canonical_json_array((MAX_UNSIGNED_64 + 1,))

    assert canonical_json_array(("123456",), maximum_length=10) == '["123456"]'
    with pytest.raises(ValueError, match="serialized-size bound"):
        canonical_json_array(("123456",), maximum_length=9)


def test_generic_canonical_identifier_size_bound_accepts_n_and_rejects_n_plus_one() -> None:
    payload = "x" * (MAX_CANONICAL_IDENTIFIER_LENGTH - 4)
    canonical = canonical_json_array((payload,))

    assert len(canonical) == MAX_CANONICAL_IDENTIFIER_LENGTH
    assert parse_canonical_json_array(canonical, field_name="canonical") == (payload,)
    with pytest.raises(ValueError, match="serialized-size bound"):
        canonical_json_array((payload + "x",))
    with pytest.raises(ValueError, match="maximum length"):
        parse_canonical_json_array(canonical + " ", field_name="canonical")


def test_largest_canonical_content_bound_accepts_n_and_rejects_n_plus_one() -> None:
    first = "x" * MAX_CANONICAL_SCALAR_TEXT_LENGTH
    second = "y" * (MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH - MAX_CANONICAL_SCALAR_TEXT_LENGTH - 7)

    canonical = canonical_json_array(
        (first, second),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    assert len(canonical) == MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH
    with pytest.raises(ValueError, match="serialized-size bound"):
        canonical_json_array(
            (first, second + "y"),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )


def test_canonical_nesting_depth_bound_accepts_n_and_rejects_n_plus_one() -> None:
    accepted: object = None
    for _ in range(MAX_CANONICAL_NESTING_DEPTH):
        accepted = (accepted,)
    canonical = canonical_json_array((accepted,))
    assert parse_canonical_json_array(canonical, field_name="canonical")

    rejected = (accepted,)
    with pytest.raises(ValueError, match="nesting-depth bound"):
        canonical_json_array((rejected,))
    rejected_json = (
        "[" * (MAX_CANONICAL_NESTING_DEPTH + 2) + "null" + "]" * (MAX_CANONICAL_NESTING_DEPTH + 2)
    )
    with pytest.raises(ValueError, match="nesting-depth bound"):
        parse_canonical_json_array(rejected_json, field_name="canonical")


def test_canonical_value_node_bound_accepts_n_and_rejects_n_plus_one() -> None:
    left = (None,) * 50_000
    right = (None,) * (MAX_CANONICAL_VALUE_NODES - len(left) - 2)
    accepted = (left, right)
    canonical = canonical_json_array(accepted)

    assert len(parse_canonical_json_array(canonical, field_name="canonical")) == 2
    with pytest.raises(ValueError, match="value-node bound"):
        canonical_json_array((left, (*right, None)))
    rejected_json = "[" + ",".join((json.dumps(left), json.dumps((*right, None)))) + "]"
    with pytest.raises(ValueError, match="value-node bound"):
        parse_canonical_json_array(rejected_json, field_name="canonical")


def test_canonical_instrument_id_bound_accepts_n_and_rejects_n_plus_one() -> None:
    probe = Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDC",
        venue_market_id="M",
        native_symbol="M",
    )
    fixed_length = len(probe.canonical_instrument_id) - 1
    market_id = "M" * (MAX_CANONICAL_INSTRUMENT_ID_LENGTH - fixed_length)
    accepted = replace(probe, venue_market_id=market_id)
    rejected = replace(probe, venue_market_id=market_id + "M")

    assert len(accepted.canonical_instrument_id) == MAX_CANONICAL_INSTRUMENT_ID_LENGTH
    assert validate_canonical_instrument_id(accepted.canonical_instrument_id) == (
        accepted.canonical_instrument_id
    )
    with pytest.raises(ValueError, match="maximum length"):
        validate_canonical_instrument_id(rejected.canonical_instrument_id)


def test_instrument_specification_digest_id_binds_a_finite_content_length() -> None:
    valid = canonical_json_array(
        (
            "instrument-specification-v1",
            "0" * 64,
            MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH,
            "1" * 64,
        )
    )
    assert InstrumentSpecificationId(valid).value == valid

    with pytest.raises(ValueError, match="invalid canonical components"):
        InstrumentSpecificationId(
            canonical_json_array(
                (
                    "instrument-specification-v1",
                    "0" * 64,
                    MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH + 1,
                    "1" * 64,
                )
            )
        )


@pytest.mark.parametrize(
    ("collection_name", "maximum_items"),
    [
        ("feed capabilities", MAX_CAPABILITIES),
        ("Binance streams", MAX_BINANCE_STREAMS),
        ("wire parameters", MAX_WIRE_PARAMETERS),
        ("subscription specs", MAX_SUBSCRIPTION_SPECS),
        ("instrument bindings", MAX_INSTRUMENT_BINDINGS),
        ("normalization bindings", MAX_NORMALIZATION_BINDINGS),
        ("connection wire options", MAX_CONNECTION_WIRE_OPTIONS),
        ("subscription attempt snapshots", MAX_SUBSCRIPTION_ATTEMPT_SNAPSHOTS),
        ("subscription attempts", MAX_SUBSCRIPTION_ATTEMPTS),
        ("raw record sequence", MAX_RAW_RECORD_SEQUENCE),
        ("coverage scope members", MAX_COVERAGE_SCOPE_MEMBERS),
        ("coverage transition references", MAX_COVERAGE_TRANSITION_REFERENCES),
        ("delivery batch items", MAX_DELIVERY_BATCH_ITEMS),
        ("instrument price references", MAX_INSTRUMENT_PRICE_REFERENCES),
        ("metadata catalogue items", MAX_METADATA_CATALOGUE_ITEMS),
        ("source time facts", MAX_SOURCE_TIME_FACTS),
        ("source sequence ranges", MAX_SOURCE_SEQUENCE_RANGES),
        ("decoded events", MAX_DECODED_EVENTS_PER_RAW_RECORD),
        ("normalization outcome items", MAX_NORMALIZATION_OUTCOME_ITEMS),
        ("normalization evidence items", MAX_NORMALIZATION_EVIDENCE_ITEMS),
    ],
)
def test_each_repeated_collection_bound_accepts_n_and_rejects_n_plus_one(
    collection_name: str,
    maximum_items: int,
) -> None:
    at_bound = (None,) * maximum_items

    require_collection_size(
        at_bound,
        field_name=collection_name,
        maximum_items=maximum_items,
    )
    with pytest.raises(ValueError, match="item count"):
        require_collection_size(
            (*at_bound, None),
            field_name=collection_name,
            maximum_items=maximum_items,
        )


def test_capability_owner_accepts_its_literal_bound_and_rejects_plus_one() -> None:
    capabilities = tuple(sorted(FeedCapabilityCode, key=lambda item: item.value))
    assert len(capabilities) == MAX_CAPABILITIES

    FeedCapabilitySet(HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id, capabilities)
    with pytest.raises(ValueError, match="item count"):
        FeedCapabilitySet(
            HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            (*capabilities, capabilities[0]),
        )


def test_owning_provenance_contracts_apply_their_configured_collection_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    record = _raw_record()

    monkeypatch.setattr(data_provenance_module, "MAX_WIRE_PARAMETERS", 1)
    hyperliquid_spec = _spec()
    assert len(hyperliquid_spec.wire_parameters) == 1
    with pytest.raises(ValueError, match="item count"):
        replace(
            hyperliquid_spec,
            wire_parameters=(
                *hyperliquid_spec.wire_parameters,
                hyperliquid_spec.wire_parameters[0],
            ),
        )

    for constant_name, field_name in (
        ("MAX_SUBSCRIPTION_SPECS", "subscription_specs"),
        ("MAX_INSTRUMENT_BINDINGS", "instrument_bindings"),
        ("MAX_NORMALIZATION_BINDINGS", "normalization_bindings"),
        ("MAX_CONNECTION_WIRE_OPTIONS", "connection_wire_options"),
    ):
        monkeypatch.setattr(data_provenance_module, constant_name, 1)
        assert len(getattr(plan, field_name)) == 1
        with pytest.raises(ValueError, match="item count"):
            replace(plan, **{field_name: getattr(plan, field_name) * 2})

    monkeypatch.setattr(data_provenance_module, "MAX_SUBSCRIPTION_ATTEMPT_SNAPSHOTS", 1)
    assert len(record.subscription_attempt_snapshots) == 1
    with pytest.raises(ValueError, match="item count"):
        replace(
            record,
            subscription_attempt_snapshots=record.subscription_attempt_snapshots * 2,
        )

    attempt0 = record.subscription_attempt_snapshots[0].subscription_attempt
    attempt1 = SubscriptionAttemptIdentity(
        attempt0.connection_session,
        attempt0.subscription_spec,
        1,
    )
    monkeypatch.setattr(data_provenance_module, "MAX_SUBSCRIPTION_ATTEMPTS", 1)
    assert validate_subscription_attempt_sequence((attempt0,)) == (attempt0,)
    with pytest.raises(ValueError, match="item count"):
        validate_subscription_attempt_sequence((attempt0, attempt1))

    record0 = _raw_record(ingress_ordinal=0)
    record1 = _raw_record(ingress_ordinal=1)
    monkeypatch.setattr(data_provenance_module, "MAX_RAW_RECORD_SEQUENCE", 1)
    assert validate_raw_record_sequence((record0,)) == (record0,)
    with pytest.raises(ValueError, match="item count"):
        validate_raw_record_sequence((record0, record1))

    scope = _scope(CoverageDomain.BRONZE_INGRESS)
    monkeypatch.setattr(data_provenance_module, "MAX_COVERAGE_SCOPE_MEMBERS", 1)
    assert len(scope.subscription_spec_ids) == len(scope.canonical_instrument_ids) == 1
    with pytest.raises(ValueError, match="item count"):
        replace(scope, subscription_spec_ids=scope.subscription_spec_ids * 2)
    with pytest.raises(ValueError, match="item count"):
        replace(scope, canonical_instrument_ids=scope.canonical_instrument_ids * 2)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    InitialActivationEvidenceSource(_session(), (acknowledged,))
    with pytest.raises(ValueError, match="item count"):
        InitialActivationEvidenceSource(_session(), (acknowledged, acknowledged))

    delivery = DeliveryAttemptIdentity(
        "collector-output-queue",
        (_materialization_key_text(),),
        0,
    )
    monkeypatch.setattr(data_provenance_module, "MAX_DELIVERY_BATCH_ITEMS", 1)
    assert len(delivery.materialization_key_canonical_texts) == 1
    with pytest.raises(ValueError, match="item count"):
        replace(
            delivery,
            materialization_key_canonical_texts=delivery.materialization_key_canonical_texts * 2,
        )


def test_opaque_identifier_and_generic_collection_bounds_accept_n_and_reject_n_plus_one() -> None:
    assert CollectorRunId("r" * MAX_OPAQUE_IDENTIFIER_LENGTH).value.endswith("r")
    with pytest.raises(ValueError, match="maximum length"):
        CollectorRunId("r" * (MAX_OPAQUE_IDENTIFIER_LENGTH + 1))

    at_bound = (None,) * MAX_COLLECTION_BOUND
    require_collection_size(
        at_bound,
        field_name="items",
        maximum_items=MAX_COLLECTION_BOUND,
    )
    with pytest.raises(ValueError, match="item count"):
        require_collection_size(
            (*at_bound, None),
            field_name="items",
            maximum_items=MAX_COLLECTION_BOUND,
        )
    with pytest.raises(ValueError, match="collection bounds"):
        require_collection_size((), field_name="items", maximum_items=MAX_COLLECTION_BOUND + 1)


def test_raw_application_message_bound_accepts_n_and_rejects_n_plus_one() -> None:
    accepted = _raw_record(
        application_message_bytes=b"x" * MAX_RAW_APPLICATION_MESSAGE_BYTES,
        frame_kind=FrameKind.BINARY,
    )
    assert accepted.payload_length == MAX_RAW_APPLICATION_MESSAGE_BYTES
    with pytest.raises(ValueError, match="Bronze record size bound"):
        _raw_record(
            application_message_bytes=b"x" * (MAX_RAW_APPLICATION_MESSAGE_BYTES + 1),
            frame_kind=FrameKind.BINARY,
        )


def test_coverage_scope_content_is_independently_validated_and_digest_addressed() -> None:
    scope = _scope(CoverageDomain.BRONZE_INGRESS)

    assert (
        validate_coverage_scope_canonical_content(scope.coverage_scope_canonical_content)
        == scope.coverage_scope_canonical_content
    )
    assert (
        coverage_scope_id_from_canonical_content(
            domain=scope.domain,
            canonical_content=scope.coverage_scope_canonical_content,
        )
        == scope.coverage_scope_id
    )
    assert len(scope.coverage_scope_id.value) < 1024

    changed_content = json.loads(scope.coverage_scope_canonical_content)
    changed_content[6] = "bbo"
    changed = coverage_scope_id_from_canonical_content(
        domain=scope.domain,
        canonical_content=json.dumps(
            changed_content,
            ensure_ascii=True,
            separators=(",", ":"),
        ),
    )
    assert changed != scope.coverage_scope_id


def test_high_cardinality_coverage_scope_accepts_member_bound_and_rejects_plus_one() -> None:
    coins = tuple(f"M{index:04d}" for index in range(MAX_COVERAGE_SCOPE_MEMBERS))
    specs = tuple(
        sorted(
            (_spec(coin).subscription_spec_id for coin in coins),
            key=lambda item: item.value,
        )
    )
    instruments = tuple(sorted(_instrument(coin).canonical_instrument_id for coin in coins))
    scope = CoverageScope(
        domain=CoverageDomain.BRONZE_INGRESS,
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        subscription_spec_ids=specs,
        canonical_instrument_ids=instruments,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )

    assert json.loads(scope.coverage_scope_id.value)[6:8] == [
        MAX_COVERAGE_SCOPE_MEMBERS,
        MAX_COVERAGE_SCOPE_MEMBERS,
    ]
    assert len(scope.coverage_scope_id.value) < 1024
    proof = subscription_spec_membership_proof(scope, specs[-1])
    assert type(proof) is SubscriptionSpecMembershipProof
    assert len(proof.sibling_sha256s) == MAX_COVERAGE_SCOPE_MERKLE_SIBLINGS
    assert proof.subscription_spec_id == specs[-1]
    with pytest.raises(ValueError, match="item count"):
        replace(proof, sibling_sha256s=(*proof.sibling_sha256s, "0" * 64))
    with pytest.raises(ValueError, match="does not match coverage scope"):
        replace(
            proof,
            sibling_sha256s=("0" * 64, *proof.sibling_sha256s[1:]),
        )
    with pytest.raises(ValueError, match="item count"):
        replace(
            scope,
            subscription_spec_ids=(*specs, specs[-1]),
        )


def test_coverage_scope_and_epoch_ids_have_exact_versioned_preimages() -> None:
    scope = _scope(CoverageDomain.BRONZE_INGRESS)
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )

    assert json.loads(scope.coverage_scope_id.value) == [
        "coverage-scope-v1",
        "bronze-ingress",
        HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id.value,
        "trade",
        2,
        "trade",
        1,
        1,
        scope.subscription_spec_members_sha256,
        scope.instrument_members_sha256,
    ]
    assert json.loads(epoch.coverage_epoch_id.value) == [
        "coverage-epoch-v1",
        scope.coverage_scope_id.value,
        "collector-run-fixture-1",
        0,
        "2026-08-26T12:00:00.000000Z",
        100,
    ]
    other_run = replace(epoch, collector_run_id=CollectorRunId("collector-run-fixture-2"))
    assert other_run.coverage_epoch_id != epoch.coverage_epoch_id


@pytest.mark.parametrize(
    "invalid_instrument_id",
    [
        '["instrument-v1","binance","spot","BTCBTC","BTC","BTC",null]',
        '["instrument-v1","binance","spot","btcusdt","btc","USDT",null]',
    ],
)
def test_persisted_canonical_instrument_ids_revalidate_existing_contract_invariants(
    invalid_instrument_id: str,
) -> None:
    spec = _spec()

    with pytest.raises(ValueError, match="instrument-v1 contract"):
        CoverageScope(
            domain=CoverageDomain.BRONZE_INGRESS,
            feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            subscription_spec_ids=(spec.subscription_spec_id,),
            canonical_instrument_ids=(invalid_instrument_id,),
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )


def test_instrument_subscription_binding_rejects_noncanonical_identity_text_directly() -> None:
    plan = _plan()
    content = json.loads(plan.subscription_plan_canonical_content)
    content[4][0][0] = "not-an-instrument-id"

    with pytest.raises(ValueError, match="canonical compact JSON array"):
        validate_subscription_plan_canonical_content(
            json.dumps(content, ensure_ascii=True, separators=(",", ":"))
        )


def test_coverage_scope_and_plan_reject_instruments_from_another_feed_venue() -> None:
    binance_spec = SubscriptionSpecIdentity(
        feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        wire_method="SUBSCRIBE",
        wire_subscription_type="@trade",
        wire_parameters=(
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.BINANCE_REQUEST_ID,
                1,
            ),
            PublicSubscriptionParameter(
                PublicSubscriptionParameterKind.BINANCE_STREAMS,
                ("btcusdt@trade",),
            ),
        ),
    )
    hyperliquid_instrument_id = (
        '["instrument-v1","hyperliquid","perpetual","BTC","BTC","USDC",null]'
    )

    with pytest.raises(ValueError, match="coverage instruments"):
        CoverageScope(
            domain=CoverageDomain.BRONZE_INGRESS,
            feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            subscription_spec_ids=(binance_spec.subscription_spec_id,),
            canonical_instrument_ids=(hyperliquid_instrument_id,),
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )

    with pytest.raises(ValueError, match="Hyperliquid selector binding requires"):
        InstrumentSubscriptionBinding(
            instrument=Instrument(
                venue=Venue.BINANCE,
                instrument_type=InstrumentType.SPOT,
                base_asset="BTC",
                quote_asset="USDT",
                venue_market_id="BTCUSDT",
                native_symbol="BTCUSDT",
            ),
            source_selector=PublicSourceSelector(
                PublicSourceSelectorKind.HYPERLIQUID_COIN,
                "BTC",
            ),
            subscription_spec=_spec(),
            adapter_profile="hyperliquid-trades-v1",
        )


def test_coverage_reducer_degrades_with_exact_transition_id() -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    request = RequestedCoverageTransition(
        scope=current.scope,
        epoch=current.epoch,
        previous_status=CoverageStatus.COMPLETE,
        next_transition_ordinal=1,
        requested_status=CoverageStatus.UNCERTAIN,
        reason=CoverageReason.TRANSPORT_AMBIGUITY,
    )
    evidence = _evidence(
        current.scope,
        CoverageEvidenceKind.TRANSPORT_FAILURE,
        "transport-failure-7",
    )

    transition, updated = reduce_coverage(current, request, evidence)

    assert json.loads(transition.coverage_transition_id.value) == [
        "coverage-transition-v1",
        current.scope.coverage_scope_id.value,
        current.epoch.coverage_epoch_id.value,
        1,
        "complete",
        "uncertain",
        "transport-ambiguity",
        evidence.coverage_evidence_id.value,
    ]
    assert updated.status is CoverageStatus.UNCERTAIN
    assert updated.transition_id == transition.coverage_transition_id


def test_initial_complete_coverage_requires_exact_typed_activation_boundary() -> None:
    reference = _coverage_reference(CoverageDomain.BRONZE_INGRESS)

    with pytest.raises(ValueError, match="exact closed source type"):
        replace(
            reference,
            initial_evidence=replace(
                reference.initial_evidence,
                kind=CoverageEvidenceKind.TRANSPORT_FAILURE,
            ),
        )
    with pytest.raises(ValueError, match="activation boundary"):
        CoverageReference.initial(
            scope=reference.scope,
            epoch=reference.epoch,
            status=reference.status,
            initial_reason=reference.initial_reason,
            initial_evidence=replace(
                reference.initial_evidence,
                observed_monotonic_ns=reference.epoch.activation_monotonic_ns + 1,
            ),
        )


@pytest.mark.parametrize(
    ("domain", "status", "reason", "kind"),
    [
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageStatus.COMPLETE,
            InitialCoverageReason.INITIAL_ACTIVATION,
            CoverageEvidenceKind.INITIAL_ACTIVATION,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.COMPLETE,
            InitialCoverageReason.INITIAL_ACTIVATION,
            CoverageEvidenceKind.INITIAL_ACTIVATION,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageStatus.UNCERTAIN,
            InitialCoverageReason.TRANSPORT_AMBIGUITY,
            CoverageEvidenceKind.TRANSPORT_FAILURE,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageStatus.UNCERTAIN,
            InitialCoverageReason.RAW_ACCEPTANCE_UNCERTAIN,
            CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.UNCERTAIN,
            InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.SOURCE_EVENT_CONFLICT,
            CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.RAW_DEFINITE_REJECTION,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.UNCERTAIN,
            InitialCoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.SOURCE_SEQUENCE_BREAK,
            CoverageEvidenceKind.SOURCE_SEQUENCE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.SOURCE_SEQUENCE_BREAK,
            CoverageEvidenceKind.SOURCE_SEQUENCE,
        ),
    ],
)
def test_every_allowed_initial_coverage_tuple_has_exact_typed_evidence(
    domain: CoverageDomain,
    status: CoverageStatus,
    reason: InitialCoverageReason,
    kind: CoverageEvidenceKind,
) -> None:
    reference = _initial_coverage_reference(domain, status, reason, kind)

    assert reference.status is status
    assert reference.initial_reason is reason
    assert reference.initial_evidence.kind is kind
    assert reference.initial_evidence.coverage_scope_id == reference.scope.coverage_scope_id
    assert reference.initial_evidence.coverage_epoch_id == reference.epoch.coverage_epoch_id


def test_initial_confirmed_incomplete_cannot_use_transport_failure() -> None:
    reference = _initial_coverage_reference(
        CoverageDomain.BRONZE_INGRESS,
        CoverageStatus.UNCERTAIN,
        InitialCoverageReason.TRANSPORT_AMBIGUITY,
        CoverageEvidenceKind.TRANSPORT_FAILURE,
    )

    with pytest.raises(ValueError, match="incompatible"):
        CoverageReference.initial(
            scope=reference.scope,
            epoch=reference.epoch,
            status=CoverageStatus.CONFIRMED_INCOMPLETE,
            initial_reason=reference.initial_reason,
            initial_evidence=reference.initial_evidence,
        )


def test_coverage_evidence_from_another_run_or_epoch_is_rejected() -> None:
    reference = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    foreign_run_epoch = CoverageEpochIdentity(
        reference.scope,
        CollectorRunId("foreign-collector-run"),
        0,
        reference.epoch.activation_time,
        reference.epoch.activation_monotonic_ns,
    )
    with pytest.raises(ValueError, match="coverage feed and run"):
        CoverageEvidence(
            CoverageEvidenceKind.TRANSPORT_FAILURE,
            TransportAmbiguityEvidenceSource(
                reference.scope.feed_product_id,
                _session(),
            ),
            reference.scope,
            foreign_run_epoch,
            foreign_run_epoch.activation_time,
            foreign_run_epoch.activation_monotonic_ns,
        )

    foreign_epoch = replace(reference.epoch, epoch_ordinal=1)
    foreign_evidence = CoverageEvidence(
        CoverageEvidenceKind.TRANSPORT_FAILURE,
        TransportAmbiguityEvidenceSource(reference.scope.feed_product_id, _session()),
        reference.scope,
        foreign_epoch,
        foreign_epoch.activation_time,
        foreign_epoch.activation_monotonic_ns,
    )
    with pytest.raises(ValueError, match="reference epoch"):
        CoverageReference.initial(
            scope=reference.scope,
            epoch=reference.epoch,
            status=reference.status,
            initial_reason=reference.initial_reason,
            initial_evidence=foreign_evidence,
        )


def test_transport_and_reconnect_evidence_are_bound_to_the_exact_feed() -> None:
    spec = _binance_spec("btcusdt@trade")
    scope = CoverageScope(
        CoverageDomain.BRONZE_INGRESS,
        BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
        (spec.subscription_spec_id,),
        (_binance_instrument("BTCUSDT").canonical_instrument_id,),
        "trade",
        2,
        "trade",
    )
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )

    for kind, source in (
        (
            CoverageEvidenceKind.TRANSPORT_FAILURE,
            TransportAmbiguityEvidenceSource(
                HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
                _session(),
            ),
        ),
        (
            CoverageEvidenceKind.RECONNECT,
            ReconnectEvidenceSource(
                HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
                _session(),
            ),
        ),
    ):
        with pytest.raises(ValueError, match="coverage feed and run"):
            CoverageEvidence(
                kind,
                source,
                scope,
                epoch,
                epoch.activation_time,
                epoch.activation_monotonic_ns,
            )


def test_raw_rejection_evidence_cannot_cross_scope_or_domain() -> None:
    btc_scope = _scope(CoverageDomain.BRONZE_INGRESS, "BTC")
    eth_scope = _scope(CoverageDomain.BRONZE_INGRESS, "ETH")
    eth_epoch = CoverageEpochIdentity(
        eth_scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    source = RawRecordEvidenceSource(
        _raw_record().raw_record_id,
        btc_scope.coverage_scope_id,
    )
    with pytest.raises(ValueError, match="exact coverage scope"):
        CoverageEvidence(
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            source,
            eth_scope,
            eth_epoch,
            eth_epoch.activation_time,
            eth_epoch.activation_monotonic_ns,
        )

    silver_scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    silver_epoch = CoverageEpochIdentity(
        silver_scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    with pytest.raises(ValueError, match="incompatible with the coverage domain"):
        CoverageEvidence(
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            RawRecordEvidenceSource(
                _raw_record().raw_record_id,
                silver_scope.coverage_scope_id,
            ),
            silver_scope,
            silver_epoch,
            silver_epoch.activation_time,
            silver_epoch.activation_monotonic_ns,
        )


@pytest.mark.parametrize(
    "kind",
    [
        CoverageEvidenceKind.NORMALIZATION_FAILURE,
        CoverageEvidenceKind.SOURCE_SEQUENCE,
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
    ],
)
def test_positive_silver_evidence_cannot_be_rebound_to_another_instrument_scope(
    kind: CoverageEvidenceKind,
) -> None:
    btc_scope = _scope(CoverageDomain.SILVER_NORMALIZATION, "BTC")
    eth_scope = _scope(CoverageDomain.SILVER_NORMALIZATION, "ETH")
    eth_epoch = CoverageEpochIdentity(
        eth_scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    raw_record_id = _raw_record().raw_record_id
    source: CoverageEvidenceSource
    if kind is CoverageEvidenceKind.NORMALIZATION_FAILURE:
        source = NormalizationFailureEvidenceSource(
            raw_record_id,
            NormalizationRunId("normalization-run-fixture"),
            0,
            SourceEventId("source-event-fixture"),
            NormalizationFailureCategory.DECODER_REJECTION,
            btc_scope.coverage_scope_id,
        )
    elif kind is CoverageEvidenceKind.SOURCE_SEQUENCE:
        source = SourceSequenceBreakEvidenceSource(
            btc_scope.feed_product_id,
            SourceSequenceRange(
                SourceSequenceRole.EVENT_SEQUENCE,
                "public-trades",
                1,
                2,
            ),
            btc_scope.coverage_scope_id,
        )
    else:
        source = SourceEventConflictEvidenceSource(
            btc_scope.feed_product_id,
            SourceEventId("source-event-fixture"),
            raw_record_id,
            0,
            btc_scope.coverage_scope_id,
        )

    with pytest.raises(ValueError, match="exact"):
        CoverageEvidence(
            kind,
            source,
            eth_scope,
            eth_epoch,
            eth_epoch.activation_time,
            eth_epoch.activation_monotonic_ns,
        )


def test_persisted_coverage_evidence_rejects_foreign_run_and_scope_references() -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    acknowledgement = _evidence(current.scope, CoverageEvidenceKind.ACKNOWLEDGEMENT)
    components = json.loads(acknowledgement.coverage_evidence_id.value)
    foreign_attempt = SubscriptionAttemptIdentity(
        ConnectionSessionIdentity(CollectorRunId("foreign-collector-run"), 0),
        _spec(),
        0,
    )
    components[5][1] = foreign_attempt.subscription_attempt_id.value
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageEvidenceId(json.dumps(components, ensure_ascii=True, separators=(",", ":")))

    raw_rejection = _evidence(current.scope, CoverageEvidenceKind.RAW_RECORD_REJECTION)
    components = json.loads(raw_rejection.coverage_evidence_id.value)
    components[5][2] = _scope(
        CoverageDomain.BRONZE_INGRESS,
        "ETH",
    ).coverage_scope_id.value
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageEvidenceId(json.dumps(components, ensure_ascii=True, separators=(",", ":")))


def test_persisted_normalization_evidence_rejects_foreign_scope_and_free_category() -> None:
    btc_scope = _scope(CoverageDomain.SILVER_NORMALIZATION, "BTC")
    evidence = _evidence(btc_scope, CoverageEvidenceKind.NORMALIZATION_FAILURE)
    eth_scope = _scope(CoverageDomain.SILVER_NORMALIZATION, "ETH")
    eth_epoch = CoverageEpochIdentity(
        eth_scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    components = json.loads(evidence.coverage_evidence_id.value)
    components[1] = eth_scope.coverage_scope_id.value
    components[2] = eth_epoch.coverage_epoch_id.value
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageEvidenceId(json.dumps(components, ensure_ascii=True, separators=(",", ":")))

    failure_components = json.loads(
        cast(
            NormalizationFailureEvidenceSource,
            evidence.source,
        ).normalization_failure_evidence_id.value
    )
    failure_components[5] = "invented-label"
    with pytest.raises(ValueError, match="invalid canonical components"):
        NormalizationFailureEvidenceId(
            json.dumps(failure_components, ensure_ascii=True, separators=(",", ":"))
        )


def test_normalization_failure_evidence_binds_optional_indexed_source_identity_exactly() -> None:
    scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    source_event_id = SourceEventId("source-event-fixture")
    source = NormalizationFailureEvidenceSource(
        _raw_record().raw_record_id,
        NormalizationRunId("normalization-run-fixture"),
        0,
        source_event_id,
        NormalizationFailureCategory.DECODER_REJECTION,
        scope.coverage_scope_id,
    )

    assert json.loads(source.normalization_failure_evidence_id.value) == [
        "normalization-failure-evidence-v1",
        _raw_record().raw_record_id.value,
        "normalization-run-fixture",
        0,
        source_event_id.value,
        "decoder-rejection",
        scope.coverage_scope_id.value,
    ]
    without_source_identity = replace(source, source_event_id=None)
    assert json.loads(without_source_identity.normalization_failure_evidence_id.value) == [
        "normalization-failure-evidence-v1",
        _raw_record().raw_record_id.value,
        "normalization-run-fixture",
        0,
        None,
        "decoder-rejection",
        scope.coverage_scope_id.value,
    ]
    assert (
        NormalizationFailureEvidenceId(
            without_source_identity.normalization_failure_evidence_id.value
        )
        == without_source_identity.normalization_failure_evidence_id
    )
    preindex = replace(without_source_identity, raw_event_index=None)
    assert json.loads(preindex.normalization_failure_evidence_id.value)[3:5] == [None, None]
    with pytest.raises(ValueError, match="pre-index"):
        replace(source, raw_event_index=None)
    with pytest.raises(TypeError, match="SourceEventId or None"):
        replace(source, source_event_id="source-event-fixture")  # type: ignore[arg-type]


def test_source_conflict_evidence_still_requires_a_source_event_id() -> None:
    scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    with pytest.raises(TypeError, match="SourceEventId"):
        SourceEventConflictEvidenceSource(
            scope.feed_product_id,
            None,  # type: ignore[arg-type]
            _raw_record().raw_record_id,
            0,
            scope.coverage_scope_id,
        )


def test_persisted_attempt_evidence_requires_scope_membership_proof() -> None:
    scope = _scope(CoverageDomain.BRONZE_INGRESS, "BTC")
    for kind in (
        CoverageEvidenceKind.ACKNOWLEDGEMENT,
        CoverageEvidenceKind.TRANSPORT_FAILURE,
    ):
        if kind is CoverageEvidenceKind.ACKNOWLEDGEMENT:
            evidence = _evidence(scope, kind)
        else:
            epoch = CoverageEpochIdentity(
                scope,
                CollectorRunId("collector-run-fixture-1"),
                0,
                datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
                100,
            )
            attempted = SubscriptionAttemptIdentity(_session(), _spec("BTC"), 0)
            evidence = CoverageEvidence(
                kind,
                TransportAmbiguityEvidenceSource(
                    scope.feed_product_id,
                    _session(),
                    attempted,
                    subscription_spec_membership_proof(
                        scope,
                        attempted.subscription_spec.subscription_spec_id,
                    ),
                ),
                scope,
                epoch,
                epoch.activation_time,
                epoch.activation_monotonic_ns,
            )
        components = json.loads(evidence.coverage_evidence_id.value)
        foreign_attempt = SubscriptionAttemptIdentity(_session(), _spec("ETH"), 0)
        components[5][1 if kind is CoverageEvidenceKind.ACKNOWLEDGEMENT else 3] = (
            foreign_attempt.subscription_attempt_id.value
        )
        with pytest.raises(ValueError, match="invalid canonical components"):
            CoverageEvidenceId(json.dumps(components, ensure_ascii=True, separators=(",", ":")))


def test_bronze_rejection_and_sink_ambiguity_have_distinct_coverage_severity() -> None:
    rejected_current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    rejected_request = RequestedCoverageTransition(
        rejected_current.scope,
        rejected_current.epoch,
        rejected_current.status,
        1,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        CoverageReason.RAW_DEFINITE_REJECTION,
    )
    _, rejected = reduce_coverage(
        rejected_current,
        rejected_request,
        _evidence(
            rejected_current.scope,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
        ),
    )

    ambiguous_current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    ambiguous_request = RequestedCoverageTransition(
        ambiguous_current.scope,
        ambiguous_current.epoch,
        ambiguous_current.status,
        1,
        CoverageStatus.UNCERTAIN,
        CoverageReason.RAW_ACCEPTANCE_UNCERTAIN,
    )
    _, ambiguous = reduce_coverage(
        ambiguous_current,
        ambiguous_request,
        _evidence(
            ambiguous_current.scope,
            CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        ),
    )

    assert rejected.status is CoverageStatus.CONFIRMED_INCOMPLETE
    assert ambiguous.status is CoverageStatus.UNCERTAIN


@pytest.mark.parametrize(
    ("kind", "status", "initial_reason", "transition_reason"),
    [
        (
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
            CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
        ),
        (
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
            CoverageStatus.UNCERTAIN,
            InitialCoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
            CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
        ),
    ],
)
def test_outcome_sink_failure_evidence_has_exact_identity_and_closed_severity(
    kind: CoverageEvidenceKind,
    status: CoverageStatus,
    initial_reason: InitialCoverageReason,
    transition_reason: CoverageReason,
) -> None:
    raw_record = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    outcome_id = _normalization_outcome_id(raw_record.raw_record_id)
    source = NormalizationOutcomeEvidenceSource(outcome_id, scope.coverage_scope_id)
    epoch = CoverageEpochIdentity(
        scope,
        raw_record.collector_run_id,
        0,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    evidence = CoverageEvidence(
        kind,
        source,
        scope,
        epoch,
        epoch.activation_time,
        epoch.activation_monotonic_ns,
    )

    assert source.canonical_components() == (
        "normalization-outcome-evidence-v1",
        outcome_id.value,
        scope.coverage_scope_id.value,
    )
    assert source.raw_record_id == raw_record.raw_record_id
    assert CoverageEvidenceId(evidence.coverage_evidence_id.value) == (
        evidence.coverage_evidence_id
    )
    initialized = CoverageReference.initial(
        scope=scope,
        epoch=epoch,
        status=status,
        initial_reason=initial_reason,
        initial_evidence=evidence,
    )
    assert initialized.status is status

    current = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    transition_evidence = _evidence(current.scope, kind)
    transition, updated = reduce_coverage(
        current,
        RequestedCoverageTransition(
            current.scope,
            current.epoch,
            current.status,
            1,
            status,
            transition_reason,
        ),
        transition_evidence,
    )
    assert transition.evidence.kind is kind
    assert updated.status is status


@pytest.mark.parametrize(
    "kind",
    (
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
    ),
)
def test_outcome_sink_failure_requires_exact_raw_bound_frame_fanout(
    kind: CoverageEvidenceKind,
) -> None:
    raw_record = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    plan = raw_record.subscription_plan
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshot = raw_record.subscription_attempt_snapshots[0]
    fanout = CoverageFanoutProof.exact_routed_event(
        plan=plan,
        catalog=catalog,
        acknowledged_snapshot=snapshot,
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    request = _outcome_sink_failure_mutation_request(
        scope=fanout.target_scopes[0],
        raw_record_id=raw_record.raw_record_id,
        kind=kind,
    )

    with pytest.raises(ValueError, match="raw fanout binding"):
        prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=(request,),
        )
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=fanout,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=(request,),
        raw_fanout_binding=binding,
    )
    assert batch.raw_fanout_binding == binding
    assert batch.resulting_state_references[0].reference.status is request.requested_status


def test_outcome_sink_failure_all_possibly_active_fanout_binds_one_exact_outcome() -> None:
    plan = _multi_hyperliquid_plan(2)
    session = _session()
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(session, spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )
    raw_record = RawMarketDataRecord(
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        collector_run_id=session.collector_run_id,
        connection_session=session,
        subscription_plan=plan,
        subscription_attempt_snapshots=snapshots,
        ingress_ordinal=0,
        frame_kind=FrameKind.TEXT,
        application_message_bytes=b'{"channel":"trades"}',
        received_time=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        received_monotonic_ns=100,
        collector_version="collector-v2",
        collector_commit="88d1591",
    )
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    outcome_id = _normalization_outcome_id(
        raw_record.raw_record_id,
        frame_status="rejected_before_indexing",
    )
    requests = tuple(
        _outcome_sink_failure_mutation_request(
            scope=scope,
            raw_record_id=raw_record.raw_record_id,
            kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            normalization_outcome_id=outcome_id,
        )
        for scope in fanout.target_scopes
    )
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=fanout,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=requests,
        raw_fanout_binding=binding,
    )
    assert len(batch.initializations) == 2

    foreign_outcome = _normalization_outcome_id(
        raw_record.raw_record_id,
        content_sha256="1" * 64,
        frame_status="rejected_before_indexing",
    )
    mixed_outcomes = (
        requests[0],
        _outcome_sink_failure_mutation_request(
            scope=fanout.target_scopes[1],
            raw_record_id=raw_record.raw_record_id,
            kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            normalization_outcome_id=foreign_outcome,
        ),
    )
    with pytest.raises(ValueError, match="one exact outcome"):
        prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=mixed_outcomes,
            raw_fanout_binding=binding,
        )

    mixed_knowledge = (
        requests[0],
        _outcome_sink_failure_mutation_request(
            scope=fanout.target_scopes[1],
            raw_record_id=raw_record.raw_record_id,
            kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
            normalization_outcome_id=outcome_id,
        ),
    )
    with pytest.raises(ValueError, match="one exact outcome"):
        prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=mixed_knowledge,
            raw_fanout_binding=binding,
        )


@pytest.mark.parametrize(
    "kind",
    (
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
    ),
)
@pytest.mark.parametrize(
    ("frame_status", "fanout_mode", "accepted"),
    tuple(
        (frame_status, fanout_mode, accepted_mode == fanout_mode)
        for frame_status, accepted_mode in (
            ("materialized", "exact"),
            ("duplicates_only", "exact"),
            ("mixed_success", "exact"),
            ("rejected_after_indexing", "exact"),
            ("source_event_conflict", "exact"),
            ("rejected_before_indexing", "plan"),
            ("control_no_event", "none"),
            ("valid_empty_market_frame", "none"),
        )
        for fanout_mode in ("exact", "plan")
    ),
)
def test_outcome_sink_failure_frame_status_has_one_closed_fanout_mode(
    kind: CoverageEvidenceKind,
    frame_status: str,
    fanout_mode: str,
    accepted: bool,
) -> None:
    raw_record = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    catalog = CoverageTargetCatalog.from_subscription_plan(raw_record.subscription_plan)
    snapshot = raw_record.subscription_attempt_snapshots[0]
    if fanout_mode == "exact":
        fanout = CoverageFanoutProof.exact_routed_event(
            plan=raw_record.subscription_plan,
            catalog=catalog,
            acknowledged_snapshot=snapshot,
            canonical_instrument_id=_instrument().canonical_instrument_id,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
    else:
        fanout = CoverageFanoutProof.all_possibly_active(
            plan=raw_record.subscription_plan,
            catalog=catalog,
            complete_snapshots=raw_record.subscription_attempt_snapshots,
            domain=CoverageDomain.SILVER_NORMALIZATION,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
    request = _outcome_sink_failure_mutation_request(
        scope=fanout.target_scopes[0],
        raw_record_id=raw_record.raw_record_id,
        kind=kind,
        normalization_outcome_id=_normalization_outcome_id(
            raw_record.raw_record_id,
            frame_status=frame_status,
        ),
    )
    raw_binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=fanout,
    )

    def operation() -> CoverageMutationBatch:
        return prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=(request,),
            raw_fanout_binding=raw_binding,
        )

    if accepted:
        assert operation().resulting_state_references[0].reference.status is (
            request.requested_status
        )
    else:
        with pytest.raises(ValueError, match="outcome-sink failure"):
            operation()


def test_outcome_sink_failure_rejects_wrong_domain_scope_raw_and_failure_knowledge() -> None:
    raw_record = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    silver_scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    bronze_scope = _scope(CoverageDomain.BRONZE_INGRESS)
    source = NormalizationOutcomeEvidenceSource(
        _normalization_outcome_id(raw_record.raw_record_id),
        silver_scope.coverage_scope_id,
    )
    bronze_epoch = CoverageEpochIdentity(
        bronze_scope,
        raw_record.collector_run_id,
        0,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    with pytest.raises(ValueError, match="coverage domain"):
        CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            source,
            bronze_scope,
            bronze_epoch,
            bronze_epoch.activation_time,
            bronze_epoch.activation_monotonic_ns,
        )

    silver_epoch = CoverageEpochIdentity(
        silver_scope,
        raw_record.collector_run_id,
        0,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    with pytest.raises(ValueError, match="exact Silver scope"):
        CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            replace(
                source,
                identified_coverage_scope_id=_scope(
                    CoverageDomain.SILVER_NORMALIZATION,
                    "ETH",
                ).coverage_scope_id,
            ),
            silver_scope,
            silver_epoch,
            silver_epoch.activation_time,
            silver_epoch.activation_monotonic_ns,
        )

    definite = _outcome_sink_failure_mutation_request(
        scope=silver_scope,
        raw_record_id=raw_record.raw_record_id,
        kind=CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        epoch=silver_epoch,
    )
    with pytest.raises(ValueError, match="incompatible"):
        CoverageReference.initial(
            scope=silver_scope,
            epoch=silver_epoch,
            status=CoverageStatus.UNCERTAIN,
            initial_reason=InitialCoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
            initial_evidence=definite.evidence,
        )


@pytest.mark.parametrize(
    ("reason", "evidence_kind"),
    [
        (CoverageReason.TRANSPORT_AMBIGUITY, CoverageEvidenceKind.TRANSPORT_FAILURE),
        (CoverageReason.TRANSPORT_AMBIGUITY, CoverageEvidenceKind.ACKNOWLEDGEMENT),
        (
            CoverageReason.RAW_DEFINITE_REJECTION,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
        ),
    ],
)
def test_only_positive_in_scope_evidence_can_confirm_incomplete_coverage(
    reason: CoverageReason,
    evidence_kind: CoverageEvidenceKind,
) -> None:
    current = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        reason,
    )

    with pytest.raises(ValueError):
        reduce_coverage(current, request, _evidence(current.scope, evidence_kind))


@pytest.mark.parametrize(
    ("reason", "evidence_kind"),
    [
        (
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
        ),
        (CoverageReason.SOURCE_SEQUENCE_BREAK, CoverageEvidenceKind.SOURCE_SEQUENCE),
        (CoverageReason.SOURCE_EVENT_CONFLICT, CoverageEvidenceKind.SOURCE_EVENT_CONFLICT),
    ],
)
def test_positive_in_scope_evidence_can_confirm_incomplete_coverage(
    reason: CoverageReason,
    evidence_kind: CoverageEvidenceKind,
) -> None:
    current = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        reason,
    )

    _, updated = reduce_coverage(
        current,
        request,
        _evidence(current.scope, evidence_kind, "in-scope-evidence-1"),
    )

    assert updated.status is CoverageStatus.CONFIRMED_INCOMPLETE


def test_source_conflict_is_distinct_confirmed_incomplete_integrity_evidence() -> None:
    current = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        CoverageReason.SOURCE_EVENT_CONFLICT,
    )

    transition, updated = reduce_coverage(
        current,
        request,
        _evidence(
            current.scope,
            CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
            "conflict-evidence-1",
        ),
    )

    assert transition.reason is CoverageReason.SOURCE_EVENT_CONFLICT
    assert updated.status is CoverageStatus.CONFIRMED_INCOMPLETE

    with pytest.raises(ValueError, match="uncertain coverage"):
        reduce_coverage(
            current,
            replace(request, requested_status=CoverageStatus.UNCERTAIN),
            _evidence(
                current.scope,
                CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
                "conflict-evidence-2",
            ),
        )


@pytest.mark.parametrize(
    ("domain", "reason", "evidence_kind", "requested_status"),
    [
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
            CoverageStatus.CONFIRMED_INCOMPLETE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.RAW_ACCEPTANCE_UNCERTAIN,
            CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
            CoverageStatus.UNCERTAIN,
        ),
    ],
)
def test_coverage_evidence_cannot_cross_domain_boundaries(
    domain: CoverageDomain,
    reason: CoverageReason,
    evidence_kind: CoverageEvidenceKind,
    requested_status: CoverageStatus,
) -> None:
    current = _coverage_reference(domain)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        requested_status,
        reason,
    )

    with pytest.raises(ValueError):
        reduce_coverage(current, request, _evidence(current.scope, evidence_kind))


def test_normalization_coverage_degrades_via_typed_upstream_transition_evidence() -> None:
    current = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.UNCERTAIN,
        CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
    )

    transition, updated = reduce_coverage(
        current,
        request,
        _evidence(
            current.scope,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
            "upstream-transition-1",
        ),
    )

    assert transition.evidence.kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION
    assert updated.status is CoverageStatus.UNCERTAIN


@pytest.mark.parametrize(
    "evidence_kind",
    [CoverageEvidenceKind.ACKNOWLEDGEMENT, CoverageEvidenceKind.RECONNECT],
)
def test_ack_and_reconnect_cannot_improve_degraded_coverage(
    evidence_kind: CoverageEvidenceKind,
) -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS, CoverageStatus.UNCERTAIN)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.COMPLETE,
        CoverageReason.EXPLICIT_RECOVERY_PROOF,
    )

    with pytest.raises(ValueError, match="recovery is unsupported"):
        reduce_coverage(current, request, _evidence(current.scope, evidence_kind))


@pytest.mark.parametrize("evidence_kind", [CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT])
def test_placeholder_recovery_evidence_cannot_improve_trade_coverage(
    evidence_kind: CoverageEvidenceKind,
) -> None:
    current = _coverage_reference(
        CoverageDomain.SILVER_NORMALIZATION,
        CoverageStatus.CONFIRMED_INCOMPLETE,
    )
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.COMPLETE,
        CoverageReason.EXPLICIT_RECOVERY_PROOF,
    )
    with pytest.raises(ValueError, match="recovery is unsupported"):
        reduce_coverage(
            current,
            request,
            _evidence(current.scope, evidence_kind, "typed-recovery-placeholder"),
        )


def test_unimplemented_backfill_proof_has_no_typed_evidence_construction_path() -> None:
    current = _coverage_reference(
        CoverageDomain.SILVER_NORMALIZATION,
        CoverageStatus.CONFIRMED_INCOMPLETE,
    )

    with pytest.raises(ValueError, match="exact closed source type"):
        _evidence(current.scope, CoverageEvidenceKind.EXPLICIT_BACKFILL_PROOF)


def test_coverage_reducer_requires_scope_epoch_status_and_ordinal_continuity() -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    base = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.UNCERTAIN,
        CoverageReason.TRANSPORT_AMBIGUITY,
    )
    evidence = _evidence(current.scope, CoverageEvidenceKind.TRANSPORT_FAILURE, "transport-1")

    with pytest.raises(ValueError, match="requested scope"):
        replace(base, scope=_scope(CoverageDomain.SILVER_NORMALIZATION))
    for invalid in (
        replace(base, next_transition_ordinal=2),
        replace(base, previous_status=CoverageStatus.CONFIRMED_INCOMPLETE),
    ):
        with pytest.raises(ValueError):
            reduce_coverage(current, invalid, evidence)


def test_coverage_evidence_cannot_precede_activation_or_move_backwards() -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    first_request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.UNCERTAIN,
        CoverageReason.TRANSPORT_AMBIGUITY,
    )
    with pytest.raises(ValueError, match="precede epoch activation"):
        _evidence(
            current.scope,
            CoverageEvidenceKind.TRANSPORT_FAILURE,
            observed_monotonic_ns=current.epoch.activation_monotonic_ns - 1,
        )

    _, uncertain = reduce_coverage(
        current,
        first_request,
        _evidence(
            current.scope,
            CoverageEvidenceKind.TRANSPORT_FAILURE,
            observed_monotonic_ns=105,
        ),
    )
    second_request = RequestedCoverageTransition(
        uncertain.scope,
        uncertain.epoch,
        uncertain.status,
        2,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        CoverageReason.SOURCE_SEQUENCE_BREAK,
    )
    with pytest.raises(ValueError, match="monotonic time"):
        reduce_coverage(
            uncertain,
            second_request,
            _evidence(
                uncertain.scope,
                CoverageEvidenceKind.SOURCE_SEQUENCE,
                observed_monotonic_ns=104,
            ),
        )


def test_direct_coverage_transition_revalidates_content_and_recovery_rules() -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.UNCERTAIN,
        CoverageReason.TRANSPORT_AMBIGUITY,
    )
    transition, _ = reduce_coverage(
        current,
        request,
        _evidence(current.scope, CoverageEvidenceKind.TRANSPORT_FAILURE, "transport-1"),
    )

    with pytest.raises(ValueError, match="does not match"):
        replace(
            transition,
            evidence=_evidence(
                current.scope,
                CoverageEvidenceKind.TRANSPORT_FAILURE,
                observed_monotonic_ns=102,
            ),
        )
    with pytest.raises(ValueError, match="change status"):
        replace(transition, new_status=CoverageStatus.COMPLETE)


def test_coverage_reference_binds_transition_scope_epoch_ordinal_and_status() -> None:
    current = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    request = RequestedCoverageTransition(
        current.scope,
        current.epoch,
        current.status,
        1,
        CoverageStatus.UNCERTAIN,
        CoverageReason.TRANSPORT_AMBIGUITY,
    )
    _, updated = reduce_coverage(
        current,
        request,
        _evidence(current.scope, CoverageEvidenceKind.TRANSPORT_FAILURE, "transport-1"),
    )

    with pytest.raises(TypeError, match=r"CoverageReference\.initial|reduce_coverage"):
        replace(updated, transition_ordinal=2)
    with pytest.raises(TypeError, match=r"CoverageReference\.initial|reduce_coverage"):
        replace(updated, status=CoverageStatus.CONFIRMED_INCOMPLETE)


def test_coverage_reference_cannot_be_fabricated_or_skip_transition_ordinals() -> None:
    initial = _coverage_reference(CoverageDomain.BRONZE_INGRESS)

    with pytest.raises(TypeError, match=r"CoverageReference\.initial|reduce_coverage"):
        CoverageReference(
            scope=initial.scope,
            epoch=initial.epoch,
            transition_ordinal=2,
            status=CoverageStatus.UNCERTAIN,
            initial_reason=initial.initial_reason,
            initial_evidence=initial.initial_evidence,
            transition_id=None,
        )
    with pytest.raises(TypeError, match=r"CoverageReference\.initial|reduce_coverage"):
        replace(initial, transition_ordinal=2)


def test_coverage_initialization_and_state_ids_have_exact_preimages_and_stored_checks() -> None:
    legacy = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    initialization = CoverageInitialization(
        legacy.scope,
        legacy.epoch,
        legacy.status,
        legacy.initial_reason,
        legacy.initial_evidence,
    )
    initial_state = CoverageStateReference.from_initialization(initialization)

    assert json.loads(initialization.coverage_initialization_id.value) == [
        "coverage-initialization-v1",
        legacy.epoch.collector_run_id.value,
        legacy.scope.coverage_scope_id.value,
        legacy.epoch.coverage_epoch_id.value,
        "complete",
        "initial-activation",
        "2026-08-26T12:00:00.000000Z",
        100,
        legacy.initial_evidence.coverage_evidence_id.value,
        "2026-08-26T12:00:00.000000Z",
        100,
    ]
    assert json.loads(initial_state.coverage_state_reference_id.value) == [
        "coverage-state-reference-v1",
        "initialization",
        initialization.coverage_initialization_id.value,
    ]
    assert (
        CoverageInitialization.from_stored(
            scope=legacy.scope,
            epoch=legacy.epoch,
            status=legacy.status,
            initial_reason=legacy.initial_reason,
            initial_evidence=legacy.initial_evidence,
            expected_initialization_id=initialization.coverage_initialization_id,
        )
        == initialization
    )
    assert (
        CoverageStateReference.from_stored(
            initialization=initialization,
            reference=initialization.reference,
            latest_transition=None,
            expected_state_reference_id=initial_state.coverage_state_reference_id,
        )
        == initial_state
    )
    with pytest.raises(TypeError, match="from_initialization"):
        CoverageStateReference()

    components = json.loads(initialization.coverage_initialization_id.value)
    components[4] = "uncertain"
    with pytest.raises(ValueError, match="canonical components"):
        CoverageInitializationId(json.dumps(components, ensure_ascii=True, separators=(",", ":")))


def test_coverage_state_stored_transition_verification_requires_exact_previous_chain() -> None:
    legacy = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    initialization = CoverageInitialization(
        legacy.scope,
        legacy.epoch,
        legacy.status,
        legacy.initial_reason,
        legacy.initial_evidence,
    )
    initial_state = CoverageStateReference.from_initialization(initialization)
    first_transition, first_reference = reduce_coverage(
        initial_state.reference,
        RequestedCoverageTransition(
            legacy.scope,
            legacy.epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.UNCERTAIN,
            CoverageReason.TRANSPORT_AMBIGUITY,
        ),
        _evidence(legacy.scope, CoverageEvidenceKind.TRANSPORT_FAILURE),
    )
    first_state = CoverageStateReference.from_transition(
        previous=initial_state,
        transition=first_transition,
        resulting_reference=first_reference,
    )
    second_transition, second_reference = reduce_coverage(
        first_state.reference,
        RequestedCoverageTransition(
            legacy.scope,
            legacy.epoch,
            CoverageStatus.UNCERTAIN,
            2,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            CoverageReason.RAW_DEFINITE_REJECTION,
        ),
        _evidence(
            legacy.scope,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            observed_monotonic_ns=102,
        ),
    )
    second_state = CoverageStateReference.from_transition(
        previous=first_state,
        transition=second_transition,
        resulting_reference=second_reference,
    )

    assert (
        CoverageStateReference.from_stored(
            initialization=initialization,
            reference=first_reference,
            latest_transition=first_transition,
            previous_state=initial_state,
            expected_state_reference_id=first_state.coverage_state_reference_id,
        )
        == first_state
    )
    assert (
        CoverageStateReference.from_stored(
            initialization=initialization,
            reference=second_reference,
            latest_transition=second_transition,
            previous_state=first_state,
            expected_state_reference_id=second_state.coverage_state_reference_id,
        )
        == second_state
    )
    with pytest.raises(TypeError, match="previous_state"):
        CoverageStateReference.from_stored(
            initialization=initialization,
            reference=second_reference,
            latest_transition=second_transition,
            expected_state_reference_id=second_state.coverage_state_reference_id,
        )


@pytest.mark.parametrize(
    "degraded_status",
    (CoverageStatus.UNCERTAIN, CoverageStatus.CONFIRMED_INCOMPLETE),
)
def test_initial_silver_state_can_cite_exact_initial_degraded_bronze_state(
    degraded_status: CoverageStatus,
) -> None:
    bronze_reference = _coverage_reference(CoverageDomain.BRONZE_INGRESS, degraded_status)
    bronze_state = _committed_coverage_state(bronze_reference)
    silver_scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    silver_epoch = CoverageEpochIdentity(
        silver_scope,
        bronze_reference.epoch.collector_run_id,
        0,
        bronze_reference.epoch.activation_time,
        bronze_reference.epoch.activation_monotonic_ns,
    )
    source = UpstreamCoverageStateEvidenceSource(bronze_state)
    evidence = CoverageEvidence(
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
        source,
        silver_scope,
        silver_epoch,
        silver_epoch.activation_time,
        silver_epoch.activation_monotonic_ns,
    )
    silver_initialization = CoverageInitialization(
        silver_scope,
        silver_epoch,
        degraded_status,
        InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
        evidence,
    )

    assert silver_initialization.reference.status is degraded_status
    assert source.canonical_components() == (
        "upstream-coverage-state-evidence-v1",
        bronze_state.committed_coverage_state_id.value,
    )
    assert CoverageEvidenceId(evidence.coverage_evidence_id.value) == (
        evidence.coverage_evidence_id
    )
    other_status = (
        CoverageStatus.CONFIRMED_INCOMPLETE
        if degraded_status is CoverageStatus.UNCERTAIN
        else CoverageStatus.UNCERTAIN
    )
    with pytest.raises(ValueError, match="exactly match"):
        CoverageInitialization(
            silver_scope,
            silver_epoch,
            other_status,
            InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            evidence,
        )


def test_plan_derived_coverage_catalog_and_cause_fanout_are_exact_and_bounded() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)

    assert len(catalog.scopes) == 2
    assert {scope.domain for scope in catalog.scopes} == {
        CoverageDomain.BRONZE_INGRESS,
        CoverageDomain.SILVER_NORMALIZATION,
    }
    assert json.loads(catalog.coverage_target_catalog_id.value) == [
        "coverage-target-catalog-v1",
        plan.subscription_plan_id.value,
        2,
        catalog.content_sha256,
    ]
    assert (
        CoverageTargetCatalog.from_stored(
            plan=plan,
            expected_canonical_content=catalog.canonical_content,
            expected_catalog_id=catalog.coverage_target_catalog_id,
        )
        == catalog
    )

    handshake = CoverageFanoutProof.handshake_before_send(
        plan=plan,
        catalog=catalog,
        connection_session=_session(),
    )
    one = CoverageFanoutProof.one_possibly_delivered_spec(
        plan=plan,
        catalog=catalog,
        snapshot=sent,
    )
    active = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_attempt_ids=_selected_attempt_ids(acknowledged),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    routed = CoverageFanoutProof.exact_routed_event(
        plan=plan,
        catalog=catalog,
        acknowledged_snapshot=acknowledged,
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    all_possible = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(sent,),
        domain=CoverageDomain.BRONZE_INGRESS,
    )

    assert handshake.kind is CoverageFanoutKind.HANDSHAKE_BEFORE_SEND
    assert handshake.target_scopes == ()
    bronze_scopes = tuple(
        scope for scope in catalog.scopes if scope.domain is CoverageDomain.BRONZE_INGRESS
    )
    assert one.target_scopes == bronze_scopes
    assert active.target_scopes == bronze_scopes
    assert all_possible.target_scopes == bronze_scopes
    assert len(routed.target_scopes) == 1
    assert routed.target_scopes[0].domain is CoverageDomain.SILVER_NORMALIZATION
    assert json.loads(routed.coverage_fanout_proof_id.value) == [
        "coverage-fanout-proof-v1",
        "exact-routed-event",
        plan.subscription_plan_id.value,
        catalog.coverage_target_catalog_id.value,
        1,
        routed.content_sha256,
    ]
    routed.verify_stored(
        expected_canonical_content=routed.canonical_content,
        expected_proof_id=routed.coverage_fanout_proof_id,
    )
    with pytest.raises(ValueError, match="at least one possibly active"):
        CoverageFanoutProof.all_possibly_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(_attempt_snapshot(status=SubscriptionAttemptStatus.PENDING),),
            domain=CoverageDomain.BRONZE_INGRESS,
        )


@pytest.mark.parametrize("plan_size", (1, 4, 50, MAX_SUBSCRIPTION_SPECS))
def test_coverage_catalog_and_fanout_support_bounded_plan_sizes(
    plan_size: int,
) -> None:
    plan = _multi_hyperliquid_plan(plan_size)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )
    proof = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_attempt_ids=_selected_attempt_ids(*snapshots),
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    possible_snapshots = tuple(
        replace(
            snapshot,
            attempt_status=(
                SubscriptionAttemptStatus.SEND_STARTED
                if index % 2 == 0
                else SubscriptionAttemptStatus.SENT
            ),
        )
        for index, snapshot in enumerate(snapshots)
    )
    possible = CoverageFanoutProof.possibly_delivered_specs(
        plan=plan,
        catalog=catalog,
        complete_snapshots=possible_snapshots,
        selected_attempt_ids=tuple(
            sorted(
                (item.subscription_attempt.subscription_attempt_id for item in possible_snapshots),
                key=lambda item: item.value,
            )
        ),
    )

    assert len(catalog.scopes) == plan_size * 2
    assert len(proof.target_scopes) == plan_size
    assert len(possible.target_scopes) == plan_size
    assert len(catalog.coverage_target_catalog_id.value) < 1024
    assert len(proof.coverage_fanout_proof_id.value) < 4096


def test_coverage_fanout_rejects_incomplete_duplicate_reordered_and_foreign_membership() -> None:
    plan = _multi_hyperliquid_plan(4)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )

    proof = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_attempt_ids=_selected_attempt_ids(*snapshots),
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    assert len(proof.target_scopes) == 4
    first_selected_id = _selected_attempt_ids(snapshots[0])
    partial_activation = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_attempt_ids=first_selected_id,
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    assert len(partial_activation.target_scopes) == 1
    assert partial_activation.target_scopes[0].subscription_spec_ids == (
        snapshots[0].subscription_spec.subscription_spec_id,
    )
    assert partial_activation.source_attempt_snapshots == snapshots
    assert partial_activation.selected_attempt_ids == first_selected_id
    assert partial_activation.source_canonical_row[-1] == tuple(
        item.value for item in first_selected_id
    )
    first_only_batch = prepare_coverage_mutation_batch(
        fanout_proof=partial_activation,
        current_state_references=(),
        requests=tuple(
            _activation_mutation_request(scope, snapshots[0])
            for scope in partial_activation.target_scopes
        ),
    )
    assert len(first_only_batch.initializations) == 1
    assert first_only_batch.no_ops == ()
    assert all(
        state.reference.scope.subscription_spec_ids
        == (snapshots[0].subscription_spec.subscription_spec_id,)
        for state in first_only_batch.resulting_state_references
    )
    second_activation = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_attempt_ids=_selected_attempt_ids(snapshots[1]),
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    assert second_activation.coverage_fanout_proof_id != (
        partial_activation.coverage_fanout_proof_id
    )
    for invalid in (
        snapshots[:-1],
        (snapshots[0], *snapshots),
        tuple(reversed(snapshots)),
        (
            *snapshots[:-1],
            SubscriptionAttemptSnapshot(
                SubscriptionAttemptIdentity(_session(), _spec("FOREIGN"), 0),
                SubscriptionAttemptStatus.ACKNOWLEDGED,
            ),
        ),
    ):
        with pytest.raises(ValueError, match="complete sorted plan attempt set"):
            CoverageFanoutProof.acknowledged_active(
                plan=plan,
                catalog=catalog,
                complete_snapshots=invalid,
                selected_attempt_ids=_selected_attempt_ids(*snapshots),
                domain=CoverageDomain.SILVER_NORMALIZATION,
            )
    with pytest.raises(ValueError, match="ingress or normalization"):
        CoverageFanoutProof.acknowledged_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=snapshots,
            selected_attempt_ids=_selected_attempt_ids(*snapshots),
            domain=CoverageDomain.SILVER_DELIVERY,
        )


def test_acknowledged_fanout_rejects_every_invalid_explicit_selection() -> None:
    plan = _multi_hyperliquid_plan(4)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )
    attempt_ids = _selected_attempt_ids(*snapshots)
    foreign_id = SubscriptionAttemptIdentity(
        _session(),
        _spec("FOREIGN"),
        0,
    ).subscription_attempt_id

    for invalid_tuple in (
        (),
        (attempt_ids[0], attempt_ids[0]),
        tuple(reversed(attempt_ids[:2])),
        (foreign_id,),
    ):
        with pytest.raises(ValueError):
            CoverageFanoutProof.acknowledged_active(
                plan=plan,
                catalog=catalog,
                complete_snapshots=snapshots,
                selected_attempt_ids=invalid_tuple,
                domain=CoverageDomain.SILVER_NORMALIZATION,
            )

    invalid_runtime_values: tuple[object, ...] = (
        [attempt_ids[0]],
        ("not-an-attempt-id",),
    )
    for invalid_runtime_value in invalid_runtime_values:
        with pytest.raises(TypeError):
            CoverageFanoutProof.acknowledged_active(
                plan=plan,
                catalog=catalog,
                complete_snapshots=snapshots,
                selected_attempt_ids=invalid_runtime_value,  # type: ignore[arg-type]
                domain=CoverageDomain.SILVER_NORMALIZATION,
            )

    sent_first = (
        replace(snapshots[0], attempt_status=SubscriptionAttemptStatus.SENT),
        *snapshots[1:],
    )
    with pytest.raises(ValueError, match="ACKNOWLEDGED"):
        CoverageFanoutProof.acknowledged_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=sent_first,
            selected_attempt_ids=(attempt_ids[0],),
            domain=CoverageDomain.SILVER_NORMALIZATION,
        )


def test_acknowledged_fanout_selects_every_leaf_and_binds_unselected_snapshot_state() -> None:
    base = _multi_hyperliquid_plan(2)
    first_spec = base.subscription_specs[0]
    extra_binding = NormalizationBinding(
        first_spec.subscription_spec_id,
        "hyperliquid-trades-v1",
        "bbo",
        1,
        "bbo",
    )
    plan = replace(
        base,
        normalization_bindings=tuple(
            sorted(
                (*base.normalization_bindings, extra_binding),
                key=lambda item: (
                    item.subscription_spec_id.value,
                    item.adapter_profile,
                    item.event_family,
                    item.event_family_schema_version,
                    item.payload_type,
                ),
            )
        ),
    )
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )
    selected = _selected_attempt_ids(snapshots[0])
    proof = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_attempt_ids=selected,
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )

    assert len(proof.target_scopes) == 2
    assert {scope.event_family for scope in proof.target_scopes} == {"bbo", "trade"}
    assert all(
        scope.subscription_spec_ids == (first_spec.subscription_spec_id,)
        for scope in proof.target_scopes
    )

    changed_unselected = (
        snapshots[0],
        replace(snapshots[1], attempt_status=SubscriptionAttemptStatus.SENT),
    )
    changed_proof = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=changed_unselected,
        selected_attempt_ids=selected,
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    assert changed_proof.target_scopes == proof.target_scopes
    assert changed_proof.canonical_content != proof.canonical_content
    assert changed_proof.coverage_fanout_proof_id != proof.coverage_fanout_proof_id
    with pytest.raises(ValueError, match="stored coverage fanout proof"):
        proof.verify_stored(
            expected_canonical_content=changed_proof.canonical_content,
            expected_proof_id=changed_proof.coverage_fanout_proof_id,
        )


def test_indexed_frame_fanout_resolves_exact_multi_coin_scope_union() -> None:
    plan = _multi_hyperliquid_plan(2)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    routed = tuple(
        RoutedCoverageTarget(
            SubscriptionAttemptSnapshot(
                SubscriptionAttemptIdentity(_session(), spec, 0),
                SubscriptionAttemptStatus.ACKNOWLEDGED,
            ),
            next(
                binding.canonical_instrument_id
                for binding in plan.instrument_bindings
                if binding.subscription_spec_id == spec.subscription_spec_id
            ),
            "trade",
            2,
            "trade",
        )
        for spec in plan.subscription_specs
    )

    forward = CoverageFanoutProof.exact_routed_events(
        plan=plan,
        catalog=catalog,
        routed_targets=routed,
    )
    reversed_proof = CoverageFanoutProof.exact_routed_events(
        plan=plan,
        catalog=catalog,
        routed_targets=tuple(reversed(routed)),
    )

    assert len(forward.target_scopes) == 2
    assert all(
        scope.domain is CoverageDomain.SILVER_NORMALIZATION for scope in forward.target_scopes
    )
    assert reversed_proof.coverage_fanout_proof_id == forward.coverage_fanout_proof_id
    assert reversed_proof.canonical_content == forward.canonical_content
    with pytest.raises(ValueError, match="unique by exact scope"):
        CoverageFanoutProof.exact_routed_events(
            plan=plan,
            catalog=catalog,
            routed_targets=(routed[0], routed[0]),
        )
    with pytest.raises(ValueError, match="exactly one plan-derived"):
        CoverageFanoutProof.exact_routed_events(
            plan=plan,
            catalog=catalog,
            routed_targets=(
                replace(
                    routed[0],
                    canonical_instrument_id=_instrument("FOREIGN").canonical_instrument_id,
                ),
            ),
        )


def test_coverage_content_addressed_identifier_counts_are_explicitly_bounded() -> None:
    plan_id = _plan().subscription_plan_id
    digest = "0" * 64
    catalog_id = CoverageTargetCatalogId(
        canonical_json_array(
            ("coverage-target-catalog-v1", plan_id, MAX_COVERAGE_MUTATION_TARGETS, digest)
        )
    )
    proof_id = CoverageFanoutProofId(
        canonical_json_array(
            (
                "coverage-fanout-proof-v1",
                CoverageFanoutKind.ALL_POSSIBLY_ACTIVE.value,
                plan_id,
                catalog_id,
                MAX_COVERAGE_MUTATION_TARGETS,
                digest,
            )
        )
    )
    assert CoverageMutationBatchId(
        canonical_json_array(
            (
                "coverage-mutation-batch-v1",
                proof_id,
                MAX_COVERAGE_MUTATION_TARGETS,
                digest,
            )
        )
    )
    with pytest.raises(ValueError, match="canonical components"):
        CoverageTargetCatalogId(
            canonical_json_array(
                (
                    "coverage-target-catalog-v1",
                    plan_id,
                    MAX_COVERAGE_MUTATION_TARGETS + 1,
                    digest,
                )
            )
        )
    with pytest.raises(ValueError, match="canonical components"):
        CoverageFanoutProofId(
            canonical_json_array(
                (
                    "coverage-fanout-proof-v1",
                    CoverageFanoutKind.ALL_POSSIBLY_ACTIVE.value,
                    plan_id,
                    catalog_id,
                    MAX_COVERAGE_MUTATION_TARGETS + 1,
                    digest,
                )
            )
        )
    with pytest.raises(ValueError, match="canonical components"):
        CoverageFanoutProofId(
            canonical_json_array(
                (
                    "coverage-fanout-proof-v1",
                    CoverageFanoutKind.ACKNOWLEDGED_ACTIVE.value,
                    plan_id,
                    catalog_id,
                    0,
                    digest,
                )
            )
        )
    foreign_plan_id = _plan(_spec("ETH")).subscription_plan_id
    with pytest.raises(ValueError, match="canonical components"):
        CoverageFanoutProofId(
            canonical_json_array(
                (
                    "coverage-fanout-proof-v1",
                    CoverageFanoutKind.ALL_POSSIBLY_ACTIVE.value,
                    foreign_plan_id,
                    catalog_id,
                    1,
                    digest,
                )
            )
        )


def test_atomic_coverage_mutation_initializes_all_targets_and_echoes_exact_cas_results() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_attempt_ids=_selected_attempt_ids(acknowledged),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    requests = tuple(
        _activation_mutation_request(scope, acknowledged) for scope in fanout.target_scopes
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=requests,
    )

    assert len(batch.initializations) == 1
    assert batch.transitions == ()
    assert batch.no_ops == ()
    assert all(
        state.reference.status is CoverageStatus.COMPLETE
        and state.reference.transition_ordinal == 0
        for state in batch.resulting_state_references
    )
    assert batch.verify_compare_and_swap(()) == batch.resulting_state_references
    expected_decision_content = canonical_json_array(
        (
            "coverage-mutation-target-decision-content-v1",
            0,
            fanout.target_scopes[0].coverage_scope_id,
            None,
            CoverageMutationDisposition.INITIALIZATION,
            batch.initializations[0].coverage_initialization_id,
            None,
            None,
            batch.resulting_state_references[0].coverage_state_reference_id,
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    expected_decision_sha256 = hashlib.sha256(expected_decision_content.encode()).hexdigest()
    assert batch.target_decision_sha256s == (expected_decision_sha256,)
    assert json.loads(batch.coverage_mutation_batch_id.value) == [
        "coverage-mutation-batch-v2",
        fanout.coverage_fanout_proof_id.value,
        1,
        batch.content_sha256,
    ]
    assert batch.canonical_content == json.dumps(
        [
            "coverage-mutation-batch-content-v2",
            fanout.coverage_fanout_proof_id.value,
            None,
            1,
            (expected_decision_sha256,),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    batch.verify_stored(
        expected_canonical_content=batch.canonical_content,
        expected_batch_id=batch.coverage_mutation_batch_id,
    )
    committed = batch.verify_compare_and_swap(())
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=committed,
    )
    with pytest.raises(TypeError, match="after_compare_and_swap"):
        CoverageCommitAcceptance(
            acceptance.coverage_mutation_batch_id,
            acceptance.resulting_state_reference_ids,
        )
    assert (
        CoverageCommitAcceptance.from_stored(
            batch=batch,
            coverage_mutation_batch_id=acceptance.coverage_mutation_batch_id,
            resulting_state_reference_ids=acceptance.resulting_state_reference_ids,
            expected_canonical_content=acceptance.canonical_content,
            expected_acceptance_id=acceptance.coverage_commit_acceptance_id,
        )
        == acceptance
    )
    with pytest.raises(ValueError, match=r"canonical components|does not echo"):
        CoverageCommitAcceptance.from_stored(
            batch=batch,
            coverage_mutation_batch_id=acceptance.coverage_mutation_batch_id,
            resulting_state_reference_ids=acceptance.resulting_state_reference_ids[:-1],
            expected_canonical_content=acceptance.canonical_content,
            expected_acceptance_id=acceptance.coverage_commit_acceptance_id,
        )


def test_repeated_coverage_degradation_is_an_audited_no_op_without_new_ordinal() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    activation_fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_attempt_ids=_selected_attempt_ids(acknowledged),
        domain=CoverageDomain.SILVER_NORMALIZATION,
    )
    active = prepare_coverage_mutation_batch(
        fanout_proof=activation_fanout,
        current_state_references=(),
        requests=tuple(
            _activation_mutation_request(scope, acknowledged)
            for scope in activation_fanout.target_scopes
        ),
    )
    routed = CoverageFanoutProof.exact_routed_event(
        plan=plan,
        catalog=catalog,
        acknowledged_snapshot=acknowledged,
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    silver_state = next(
        state
        for state in active.resulting_state_references
        if state.reference.scope.domain is CoverageDomain.SILVER_NORMALIZATION
    )
    conflict_request = _source_conflict_mutation_request(silver_state)
    raw_record = _raw_record(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    raw_fanout_binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=routed,
    )
    degraded = prepare_coverage_mutation_batch(
        fanout_proof=routed,
        current_state_references=(silver_state,),
        requests=(conflict_request,),
        raw_fanout_binding=raw_fanout_binding,
    )
    degraded_state = degraded.resulting_state_references[0]
    repeated = prepare_coverage_mutation_batch(
        fanout_proof=routed,
        current_state_references=(degraded_state,),
        requests=(conflict_request,),
        raw_fanout_binding=raw_fanout_binding,
    )

    assert len(degraded.transitions) == 1
    assert degraded_state.reference.transition_ordinal == 1
    assert degraded_state.reference.status is CoverageStatus.CONFIRMED_INCOMPLETE
    assert repeated.transitions == ()
    assert len(repeated.no_ops) == 1
    assert type(repeated.no_ops[0]) is CoverageMutationNoOp
    assert repeated.resulting_state_references == (degraded_state,)
    assert repeated.resulting_state_references[0].reference.transition_ordinal == 1
    assert repeated.coverage_mutation_batch_id != degraded.coverage_mutation_batch_id

    stale_request = replace(
        conflict_request,
        evidence=replace(conflict_request.evidence, observed_monotonic_ns=100),
    )
    with pytest.raises(ValueError, match="cannot precede"):
        CoverageMutationNoOp(degraded_state, stale_request)
    with pytest.raises(ValueError, match="cannot precede"):
        prepare_coverage_mutation_batch(
            fanout_proof=routed,
            current_state_references=(degraded_state,),
            requests=(stale_request,),
            raw_fanout_binding=raw_fanout_binding,
        )

    foreign_epoch = replace(conflict_request.epoch, epoch_ordinal=1)
    foreign_epoch_request = replace(
        conflict_request,
        epoch=foreign_epoch,
        evidence=replace(conflict_request.evidence, epoch=foreign_epoch),
    )
    with pytest.raises(ValueError, match="current epoch"):
        CoverageMutationNoOp(degraded_state, foreign_epoch_request)

    wrong_reason_request = replace(
        conflict_request,
        transition_reason=CoverageReason.TRANSPORT_AMBIGUITY,
    )
    with pytest.raises(ValueError, match="reasons are incompatible"):
        CoverageMutationNoOp(degraded_state, wrong_reason_request)
    with pytest.raises(ValueError, match="reasons are incompatible"):
        prepare_coverage_mutation_batch(
            fanout_proof=routed,
            current_state_references=(degraded_state,),
            requests=(wrong_reason_request,),
            raw_fanout_binding=raw_fanout_binding,
        )


def test_coverage_mutation_rejects_partial_targets_stale_cas_and_cause_mismatch() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
    active_fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_attempt_ids=_selected_attempt_ids(acknowledged),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    requests = tuple(
        _activation_mutation_request(scope, acknowledged) for scope in active_fanout.target_scopes
    )
    with pytest.raises(ValueError, match="every fanout target"):
        prepare_coverage_mutation_batch(
            fanout_proof=active_fanout,
            current_state_references=(),
            requests=requests[:-1],
        )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=active_fanout,
        current_state_references=(),
        requests=requests,
    )
    with pytest.raises(ValueError, match="pre-state"):
        batch.verify_compare_and_swap((batch.resulting_state_references[0],))

    ambiguity_fanout = CoverageFanoutProof.one_possibly_delivered_spec(
        plan=plan,
        catalog=catalog,
        snapshot=sent,
    )
    with pytest.raises(ValueError, match="possibly-delivered"):
        prepare_coverage_mutation_batch(
            fanout_proof=ambiguity_fanout,
            current_state_references=(),
            requests=requests,
        )

    target_scope = active_fanout.target_scopes[0]
    foreign_session = ConnectionSessionIdentity(CollectorRunId("foreign-run"), 0)
    foreign_attempt = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(foreign_session, acknowledged.subscription_spec, 0),
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    foreign_epoch = CoverageEpochIdentity(
        target_scope,
        foreign_session.collector_run_id,
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    foreign_evidence = CoverageEvidence(
        CoverageEvidenceKind.INITIAL_ACTIVATION,
        InitialActivationEvidenceSource(foreign_session, (foreign_attempt,)),
        target_scope,
        foreign_epoch,
        foreign_epoch.activation_time,
        foreign_epoch.activation_monotonic_ns,
    )
    foreign_request = RequestedCoverageMutation(
        target_scope,
        foreign_epoch,
        CoverageStatus.COMPLETE,
        InitialCoverageReason.INITIAL_ACTIVATION,
        CoverageReason.INITIAL_SCOPE,
        foreign_evidence,
    )
    with pytest.raises(ValueError, match="fanout collector run"):
        prepare_coverage_mutation_batch(
            fanout_proof=active_fanout,
            current_state_references=(),
            requests=(foreign_request,),
        )


def test_coverage_mutation_rejects_same_run_foreign_session_evidence() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    session_zero_ack = _attempt_snapshot(
        status=SubscriptionAttemptStatus.ACKNOWLEDGED,
        connection_ordinal=0,
    )
    session_one_ack = _attempt_snapshot(
        status=SubscriptionAttemptStatus.ACKNOWLEDGED,
        connection_ordinal=1,
    )
    activation_fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(session_zero_ack,),
        selected_attempt_ids=_selected_attempt_ids(session_zero_ack),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    scope = activation_fanout.target_scopes[0]
    foreign_activation = _activation_mutation_request(scope, session_one_ack)
    with pytest.raises(ValueError, match="fanout session"):
        prepare_coverage_mutation_batch(
            fanout_proof=activation_fanout,
            current_state_references=(),
            requests=(foreign_activation,),
        )

    session_zero_sent = _attempt_snapshot(
        status=SubscriptionAttemptStatus.SENT,
        connection_ordinal=0,
    )
    session_one_sent = _attempt_snapshot(
        status=SubscriptionAttemptStatus.SENT,
        connection_ordinal=1,
    )
    ambiguity_fanout = CoverageFanoutProof.one_possibly_delivered_spec(
        plan=plan,
        catalog=catalog,
        snapshot=session_zero_sent,
    )
    ambiguity_scope = ambiguity_fanout.target_scopes[0]
    ambiguity_epoch = CoverageEpochIdentity(
        ambiguity_scope,
        _session().collector_run_id,
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    foreign_transport = RequestedCoverageMutation(
        ambiguity_scope,
        ambiguity_epoch,
        CoverageStatus.UNCERTAIN,
        InitialCoverageReason.TRANSPORT_AMBIGUITY,
        CoverageReason.TRANSPORT_AMBIGUITY,
        CoverageEvidence(
            CoverageEvidenceKind.TRANSPORT_FAILURE,
            TransportAmbiguityEvidenceSource(
                ambiguity_scope.feed_product_id,
                session_one_sent.subscription_attempt.connection_session,
                session_one_sent.subscription_attempt,
                subscription_spec_membership_proof(
                    ambiguity_scope,
                    session_one_sent.subscription_spec.subscription_spec_id,
                ),
            ),
            ambiguity_scope,
            ambiguity_epoch,
            ambiguity_epoch.activation_time,
            100,
        ),
    )
    with pytest.raises(ValueError, match="fanout session"):
        prepare_coverage_mutation_batch(
            fanout_proof=ambiguity_fanout,
            current_state_references=(),
            requests=(foreign_transport,),
        )

    active_fanout = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(session_zero_sent,),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    active_scope = active_fanout.target_scopes[0]
    active_epoch = CoverageEpochIdentity(
        active_scope,
        _session().collector_run_id,
        0,
        ambiguity_epoch.activation_time,
        100,
    )
    foreign_raw = RequestedCoverageMutation(
        active_scope,
        active_epoch,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        InitialCoverageReason.RAW_DEFINITE_REJECTION,
        CoverageReason.RAW_DEFINITE_REJECTION,
        CoverageEvidence(
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            RawRecordEvidenceSource(
                _raw_record(connection_ordinal=1).raw_record_id,
                active_scope.coverage_scope_id,
            ),
            active_scope,
            active_epoch,
            active_epoch.activation_time,
            100,
        ),
    )
    with pytest.raises(ValueError, match="fanout session"):
        prepare_coverage_mutation_batch(
            fanout_proof=active_fanout,
            current_state_references=(),
            requests=(foreign_raw,),
        )


def test_raw_backed_coverage_mutation_requires_exact_full_snapshot_binding() -> None:
    plan = _multi_hyperliquid_plan(2)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    session = _session()
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(session, spec, 0),
            (SubscriptionAttemptStatus.PENDING if index == 0 else SubscriptionAttemptStatus.SENT),
        )
        for index, spec in enumerate(plan.subscription_specs)
    )
    raw_record = RawMarketDataRecord(
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        collector_run_id=session.collector_run_id,
        connection_session=session,
        subscription_plan=plan,
        subscription_attempt_snapshots=snapshots,
        ingress_ordinal=0,
        frame_kind=FrameKind.TEXT,
        application_message_bytes=b'{"channel":"trades"}',
        received_time=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        received_monotonic_ns=100,
        collector_version="collector-v2",
        collector_commit="88d1591",
    )
    fanout = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    assert len(fanout.target_scopes) == 1
    scope = fanout.target_scopes[0]
    epoch = CoverageEpochIdentity(
        scope,
        session.collector_run_id,
        0,
        raw_record.received_time,
        raw_record.received_monotonic_ns,
    )
    request = RequestedCoverageMutation(
        scope,
        epoch,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        InitialCoverageReason.RAW_DEFINITE_REJECTION,
        CoverageReason.RAW_DEFINITE_REJECTION,
        CoverageEvidence(
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            RawRecordEvidenceSource(raw_record.raw_record_id, scope.coverage_scope_id),
            scope,
            epoch,
            raw_record.received_time,
            raw_record.received_monotonic_ns,
        ),
    )
    with pytest.raises(ValueError, match="requires an exact raw fanout binding"):
        prepare_coverage_mutation_batch(
            fanout_proof=fanout,
            current_state_references=(),
            requests=(request,),
        )
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw_record,
        coverage_fanout_proof=fanout,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=(request,),
        raw_fanout_binding=binding,
    )
    assert batch.raw_fanout_binding == binding
    assert json.loads(batch.canonical_content)[2] == binding.raw_coverage_fanout_binding_id.value

    swapped_snapshots = tuple(
        replace(
            snapshot,
            attempt_status=(
                SubscriptionAttemptStatus.SENT
                if snapshot.attempt_status is SubscriptionAttemptStatus.PENDING
                else SubscriptionAttemptStatus.PENDING
            ),
        )
        for snapshot in snapshots
    )
    swapped_fanout = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=swapped_snapshots,
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    with pytest.raises(ValueError, match="isn't exactly reproducible"):
        RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=swapped_fanout,
        )


def test_one_possibly_delivered_spec_validates_exact_parent_types_before_access() -> None:
    snapshot = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
    catalog = CoverageTargetCatalog.from_subscription_plan(_plan())
    with pytest.raises(TypeError, match="plan"):
        CoverageFanoutProof.one_possibly_delivered_spec(
            plan=cast(SubscriptionPlanIdentity, True),
            catalog=catalog,
            snapshot=snapshot,
        )
    with pytest.raises(TypeError, match="snapshot"):
        CoverageFanoutProof.one_possibly_delivered_spec(
            plan=_plan(),
            catalog=catalog,
            snapshot=cast(SubscriptionAttemptSnapshot, object()),
        )


def test_handshake_before_send_is_the_only_zero_target_coverage_batch() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    handshake = CoverageFanoutProof.handshake_before_send(
        plan=plan,
        catalog=catalog,
        connection_session=_session(),
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=handshake,
        current_state_references=(),
        requests=(),
    )

    assert batch.initializations == ()
    assert batch.transitions == ()
    assert batch.no_ops == ()
    assert batch.resulting_state_references == ()
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=batch.verify_compare_and_swap(()),
    )
    assert acceptance.resulting_state_reference_ids == ()


def test_transitioned_state_identity_binds_predecessor_and_complete_history() -> None:
    initial_reference = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    initialization = CoverageInitialization(
        initial_reference.scope,
        initial_reference.epoch,
        initial_reference.status,
        initial_reference.initial_reason,
        initial_reference.initial_evidence,
    )
    initial = CoverageStateReference.from_initialization(initialization)
    first_transition, first_reference = reduce_coverage(
        initial.reference,
        RequestedCoverageTransition(
            initial.reference.scope,
            initial.reference.epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.UNCERTAIN,
            CoverageReason.TRANSPORT_AMBIGUITY,
        ),
        _evidence(initial.reference.scope, CoverageEvidenceKind.TRANSPORT_FAILURE),
    )
    first = CoverageStateReference.from_transition(
        previous=initial,
        transition=first_transition,
        resulting_reference=first_reference,
    )
    second_transition, second_reference = reduce_coverage(
        first.reference,
        RequestedCoverageTransition(
            first.reference.scope,
            first.reference.epoch,
            CoverageStatus.UNCERTAIN,
            2,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            CoverageReason.RAW_DEFINITE_REJECTION,
        ),
        _evidence(
            first.reference.scope,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            observed_monotonic_ns=102,
        ),
    )
    second = CoverageStateReference.from_transition(
        previous=first,
        transition=second_transition,
        resulting_reference=second_reference,
    )

    assert json.loads(first.coverage_state_reference_id.value) == [
        "coverage-state-reference-v1",
        "transition",
        initialization.coverage_initialization_id.value,
        initial.coverage_state_reference_id.value,
        first_transition.coverage_transition_id.value,
    ]
    assert json.loads(second.coverage_state_reference_id.value)[3:] == [
        first.coverage_state_reference_id.value,
        second_transition.coverage_transition_id.value,
    ]

    alternate_first_transition, alternate_first_reference = reduce_coverage(
        initial.reference,
        RequestedCoverageTransition(
            initial.reference.scope,
            initial.reference.epoch,
            CoverageStatus.COMPLETE,
            1,
            CoverageStatus.UNCERTAIN,
            CoverageReason.TRANSPORT_AMBIGUITY,
        ),
        _evidence(initial.reference.scope, CoverageEvidenceKind.RECONNECT),
    )
    alternate_first = CoverageStateReference.from_transition(
        previous=initial,
        transition=alternate_first_transition,
        resulting_reference=alternate_first_reference,
    )
    alternate_second_transition, alternate_second_reference = reduce_coverage(
        alternate_first.reference,
        RequestedCoverageTransition(
            alternate_first.reference.scope,
            alternate_first.reference.epoch,
            CoverageStatus.UNCERTAIN,
            2,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            CoverageReason.RAW_DEFINITE_REJECTION,
        ),
        _evidence(
            alternate_first.reference.scope,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            observed_monotonic_ns=102,
        ),
    )
    alternate_second = CoverageStateReference.from_transition(
        previous=alternate_first,
        transition=alternate_second_transition,
        resulting_reference=alternate_second_reference,
    )
    assert alternate_second_transition.coverage_transition_id == (
        second_transition.coverage_transition_id
    )
    assert alternate_second.coverage_state_reference_id != second.coverage_state_reference_id

    tampered = json.loads(second.coverage_state_reference_id.value)
    tampered[3] = initial.coverage_state_reference_id.value
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageStateReferenceId(json.dumps(tampered, ensure_ascii=True, separators=(",", ":")))


def test_commit_acceptance_and_committed_state_have_exact_content_addressed_identity() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_attempt_ids=_selected_attempt_ids(acknowledged),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    batch = prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=tuple(
            _activation_mutation_request(scope, acknowledged) for scope in fanout.target_scopes
        ),
    )
    committed = batch.verify_compare_and_swap(())
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=committed,
    )
    state = CommittedCoverageState.from_commit(
        state_reference=committed[0],
        commit_acceptance=acceptance,
    )

    assert not hasattr(CoverageCommitAcceptance, "from_batch")
    expected_result_item = canonical_json_array(
        (
            "coverage-commit-resulting-state-content-v1",
            0,
            committed[0].coverage_state_reference_id,
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    expected_result_item_sha256 = hashlib.sha256(expected_result_item.encode()).hexdigest()
    expected_result_set = canonical_json_array(
        (
            "coverage-commit-resulting-states-content-v1",
            1,
            (expected_result_item_sha256,),
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    expected_result_set_sha256 = hashlib.sha256(expected_result_set.encode()).hexdigest()
    assert acceptance.resulting_state_item_sha256s == (expected_result_item_sha256,)
    assert acceptance.resulting_state_commitment_sha256 == expected_result_set_sha256
    assert json.loads(acceptance.canonical_content) == [
        "coverage-commit-acceptance-content-v2",
        batch.coverage_mutation_batch_id.value,
        1,
        expected_result_set_sha256,
    ]
    assert json.loads(acceptance.coverage_commit_acceptance_id.value) == [
        "coverage-commit-acceptance-v2",
        batch.coverage_mutation_batch_id.value,
        1,
        acceptance.content_sha256,
    ]
    assert json.loads(state.committed_coverage_state_id.value) == [
        "committed-coverage-state-v1",
        committed[0].coverage_state_reference_id.value,
        acceptance.coverage_commit_acceptance_id.value,
    ]
    assert (
        CommittedCoverageState.from_stored(
            state_reference=committed[0],
            commit_acceptance=acceptance,
            expected_committed_state_id=state.committed_coverage_state_id,
        )
        == state
    )
    tampered_committed_id = CommittedCoverageStateId(
        canonical_json_array(
            (
                "committed-coverage-state-v1",
                committed[0].coverage_state_reference_id,
                CoverageCommitAcceptance.after_compare_and_swap(
                    batch=prepare_coverage_mutation_batch(
                        fanout_proof=CoverageFanoutProof.handshake_before_send(
                            plan=plan,
                            catalog=catalog,
                            connection_session=_session(),
                        ),
                        current_state_references=(),
                        requests=(),
                    ),
                    committed_state_references=(),
                ).coverage_commit_acceptance_id,
            )
        )
    )
    with pytest.raises(ValueError, match="stored committed coverage state"):
        CommittedCoverageState.from_stored(
            state_reference=committed[0],
            commit_acceptance=acceptance,
            expected_committed_state_id=tampered_committed_id,
        )
    with pytest.raises(ValueError, match="not committed"):
        CommittedCoverageState.from_commit(
            state_reference=_committed_coverage_state(
                _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
            ).state_reference,
            commit_acceptance=acceptance,
        )
    with pytest.raises(ValueError, match="stored coverage commit acceptance"):
        CoverageCommitAcceptance.from_stored(
            batch=batch,
            coverage_mutation_batch_id=acceptance.coverage_mutation_batch_id,
            resulting_state_reference_ids=acceptance.resulting_state_reference_ids,
            expected_canonical_content=acceptance.canonical_content + " ",
            expected_acceptance_id=acceptance.coverage_commit_acceptance_id,
        )


def test_possibly_delivered_fanout_binds_complete_snapshot_and_exact_sent_subset() -> None:
    plan = _multi_hyperliquid_plan(4)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    statuses = (
        SubscriptionAttemptStatus.PENDING,
        SubscriptionAttemptStatus.SEND_STARTED,
        SubscriptionAttemptStatus.SENT,
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            status,
        )
        for spec, status in zip(plan.subscription_specs, statuses, strict=True)
    )
    selected = tuple(
        sorted(
            (
                snapshots[1].subscription_attempt.subscription_attempt_id,
                snapshots[2].subscription_attempt.subscription_attempt_id,
            ),
            key=lambda item: item.value,
        )
    )
    proof = CoverageFanoutProof.possibly_delivered_specs(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_attempt_ids=selected,
    )

    assert proof.kind is CoverageFanoutKind.POSSIBLY_DELIVERED_SPECS
    assert len(proof.target_scopes) == 2
    assert proof.source_canonical_row == (
        "possibly-delivered-specs-v1",
        tuple(
            (
                item.subscription_attempt.subscription_attempt_id.value,
                item.attempt_status.value,
            )
            for item in snapshots
        ),
        tuple(item.value for item in selected),
    )
    for invalid_selected in (
        (snapshots[0].subscription_attempt.subscription_attempt_id,),
        (snapshots[3].subscription_attempt.subscription_attempt_id,),
        (selected[0], selected[0]),
        tuple(reversed(selected)),
        (_attempt_snapshot(spec=_spec("FOREIGN")).subscription_attempt.subscription_attempt_id,),
    ):
        with pytest.raises(ValueError):
            CoverageFanoutProof.possibly_delivered_specs(
                plan=plan,
                catalog=catalog,
                complete_snapshots=snapshots,
                selected_attempt_ids=invalid_selected,
            )
    with pytest.raises(ValueError, match="complete sorted plan attempt set"):
        CoverageFanoutProof.possibly_delivered_specs(
            plan=plan,
            catalog=catalog,
            complete_snapshots=snapshots[:-1],
            selected_attempt_ids=selected,
        )
    with pytest.raises(ValueError, match="complete one-spec"):
        CoverageFanoutProof.one_possibly_delivered_spec(
            plan=plan,
            catalog=catalog,
            snapshot=snapshots[1],
        )


def test_exact_single_route_has_one_identity_and_all_active_silver_filters_binding() -> None:
    base = _plan()
    extra = NormalizationBinding(
        base.subscription_specs[0].subscription_spec_id,
        "hyperliquid-trades-v1",
        "bbo",
        1,
        "bbo",
    )
    plan = replace(
        base,
        normalization_bindings=tuple(
            sorted(
                (*base.normalization_bindings, extra),
                key=lambda item: (
                    item.subscription_spec_id.value,
                    item.adapter_profile,
                    item.event_family,
                    item.event_family_schema_version,
                    item.payload_type,
                ),
            )
        ),
    )
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    routed_target = RoutedCoverageTarget(
        acknowledged,
        _instrument().canonical_instrument_id,
        "trade",
        2,
        "trade",
    )
    singular = CoverageFanoutProof.exact_routed_event(
        plan=plan,
        catalog=catalog,
        acknowledged_snapshot=acknowledged,
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    plural = CoverageFanoutProof.exact_routed_events(
        plan=plan,
        catalog=catalog,
        routed_targets=(routed_target,),
    )
    active = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        domain=CoverageDomain.SILVER_NORMALIZATION,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )

    assert singular.kind is CoverageFanoutKind.EXACT_ROUTED_EVENT
    assert singular.coverage_fanout_proof_id == plural.coverage_fanout_proof_id
    assert singular.canonical_content == plural.canonical_content
    assert len(active.target_scopes) == 1
    assert active.target_scopes[0].event_family == "trade"
    with pytest.raises(TypeError, match="requires family"):
        CoverageFanoutProof.all_possibly_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(acknowledged,),
            domain=CoverageDomain.SILVER_NORMALIZATION,
        )
    with pytest.raises(ValueError, match="no normalized binding"):
        CoverageFanoutProof.all_possibly_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(acknowledged,),
            domain=CoverageDomain.BRONZE_INGRESS,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )


def test_no_attempt_transport_evidence_cannot_create_ordinal_zero_state_or_stored_id() -> None:
    scope = _scope(CoverageDomain.BRONZE_INGRESS)
    boundary = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        boundary,
        100,
    )
    evidence = CoverageEvidence(
        CoverageEvidenceKind.TRANSPORT_FAILURE,
        TransportAmbiguityEvidenceSource(scope.feed_product_id, _session()),
        scope,
        epoch,
        boundary,
        100,
    )
    with pytest.raises(ValueError, match="before any send"):
        CoverageReference.initial(
            scope=scope,
            epoch=epoch,
            status=CoverageStatus.UNCERTAIN,
            initial_reason=InitialCoverageReason.TRANSPORT_AMBIGUITY,
            initial_evidence=evidence,
        )
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageInitializationId(
            canonical_json_array(
                (
                    "coverage-initialization-v1",
                    epoch.collector_run_id,
                    scope.coverage_scope_id,
                    epoch.coverage_epoch_id,
                    CoverageStatus.UNCERTAIN.value,
                    InitialCoverageReason.TRANSPORT_AMBIGUITY.value,
                    canonical_utc_datetime(boundary, field_name="boundary"),
                    100,
                    evidence.coverage_evidence_id,
                    canonical_utc_datetime(boundary, field_name="boundary"),
                    100,
                )
            )
        )


@pytest.mark.parametrize(
    "upstream_status",
    (CoverageStatus.UNCERTAIN, CoverageStatus.CONFIRMED_INCOMPLETE),
)
def test_upstream_transition_requires_exact_status_and_monotonic_causality(
    upstream_status: CoverageStatus,
) -> None:
    upstream = _committed_upstream_transition(upstream_status)
    current = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    evidence = CoverageEvidence(
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
        UpstreamCoverageTransitionEvidenceSource(upstream),
        current.scope,
        current.epoch,
        datetime(2026, 8, 26, 11, 59, 59, tzinfo=UTC),
        101,
    )
    reason = CoverageReason.UPSTREAM_COVERAGE_DEGRADED
    transition, updated = reduce_coverage(
        current,
        RequestedCoverageTransition(
            current.scope,
            current.epoch,
            current.status,
            1,
            upstream_status,
            reason,
        ),
        evidence,
    )
    assert transition.new_status is upstream_status
    assert updated.status is upstream_status
    wrong_status = (
        CoverageStatus.CONFIRMED_INCOMPLETE
        if upstream_status is CoverageStatus.UNCERTAIN
        else CoverageStatus.UNCERTAIN
    )
    with pytest.raises(ValueError, match="exactly match"):
        reduce_coverage(
            current,
            RequestedCoverageTransition(
                current.scope,
                current.epoch,
                current.status,
                1,
                wrong_status,
                reason,
            ),
            evidence,
        )
    with pytest.raises(ValueError, match="cannot precede upstream"):
        CoverageEvidence(
            CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
            UpstreamCoverageTransitionEvidenceSource(upstream),
            current.scope,
            current.epoch,
            current.epoch.activation_time,
            100,
        )


def test_fanout_status_and_evidence_matrix_is_positive_and_fail_closed() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    possible = CoverageFanoutProof.one_possibly_delivered_spec(
        plan=plan,
        catalog=catalog,
        snapshot=sent,
    )
    possible_scope = possible.target_scopes[0]
    raw = _raw_record().raw_record_id
    epoch = CoverageEpochIdentity(
        possible_scope,
        _session().collector_run_id,
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )
    rejected = RequestedCoverageMutation(
        possible_scope,
        epoch,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        InitialCoverageReason.RAW_DEFINITE_REJECTION,
        CoverageReason.RAW_DEFINITE_REJECTION,
        CoverageEvidence(
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
            RawRecordEvidenceSource(raw, possible_scope.coverage_scope_id),
            possible_scope,
            epoch,
            epoch.activation_time,
            100,
        ),
    )
    with pytest.raises(ValueError, match="possibly-delivered"):
        prepare_coverage_mutation_batch(
            fanout_proof=possible,
            current_state_references=(),
            requests=(rejected,),
        )

    routed = CoverageFanoutProof.exact_routed_event(
        plan=plan,
        catalog=catalog,
        acknowledged_snapshot=acknowledged,
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )
    routed_scope = routed.target_scopes[0]
    routed_epoch = CoverageEpochIdentity(
        routed_scope,
        _session().collector_run_id,
        0,
        epoch.activation_time,
        100,
    )
    upstream = _committed_upstream_transition(CoverageStatus.UNCERTAIN)
    upstream_evidence = CoverageEvidence(
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
        UpstreamCoverageTransitionEvidenceSource(upstream),
        routed_scope,
        routed_epoch,
        datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
        101,
    )
    lifecycle = RequestedCoverageMutation(
        routed_scope,
        routed_epoch,
        CoverageStatus.UNCERTAIN,
        InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
        CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
        upstream_evidence,
    )
    with pytest.raises(ValueError, match="exact-routed"):
        prepare_coverage_mutation_batch(
            fanout_proof=routed,
            current_state_references=(),
            requests=(lifecycle,),
        )


def test_commit_acceptance_identifier_count_bound_accepts_n_and_rejects_n_plus_one() -> None:
    plan_id = _plan().subscription_plan_id
    digest = "0" * 64
    catalog_id = CoverageTargetCatalogId(
        canonical_json_array(
            ("coverage-target-catalog-v1", plan_id, MAX_COVERAGE_MUTATION_TARGETS, digest)
        )
    )
    fanout_id = CoverageFanoutProofId(
        canonical_json_array(
            (
                "coverage-fanout-proof-v1",
                CoverageFanoutKind.ALL_POSSIBLY_ACTIVE.value,
                plan_id,
                catalog_id,
                MAX_COVERAGE_MUTATION_TARGETS,
                digest,
            )
        )
    )
    batch_id = CoverageMutationBatchId(
        canonical_json_array(
            (
                "coverage-mutation-batch-v1",
                fanout_id,
                MAX_COVERAGE_MUTATION_TARGETS,
                digest,
            )
        )
    )
    assert CoverageCommitAcceptanceId(
        canonical_json_array(
            (
                "coverage-commit-acceptance-v1",
                batch_id,
                MAX_COVERAGE_MUTATION_TARGETS,
                digest,
            )
        )
    )
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageCommitAcceptanceId(
            canonical_json_array(
                (
                    "coverage-commit-acceptance-v1",
                    batch_id,
                    MAX_COVERAGE_MUTATION_TARGETS + 1,
                    digest,
                )
            )
        )


def test_provenance_mismatch_is_a_persistable_typed_normalization_failure_category() -> None:
    scope = _scope(CoverageDomain.SILVER_NORMALIZATION)
    source = NormalizationFailureEvidenceSource(
        _raw_record().raw_record_id,
        NormalizationRunId("normalization-run-fixture"),
        0,
        SourceEventId("source-event-fixture"),
        NormalizationFailureCategory.PROVENANCE_MISMATCH,
        scope.coverage_scope_id,
    )

    assert json.loads(source.normalization_failure_evidence_id.value)[5] == ("provenance-mismatch")
    assert (
        NormalizationFailureEvidenceId(source.normalization_failure_evidence_id.value)
        == source.normalization_failure_evidence_id
    )


def test_coverage_transition_id_revalidates_the_closed_domain_evidence_matrix() -> None:
    scope = _scope(CoverageDomain.BRONZE_INGRESS)
    epoch = CoverageEpochIdentity(
        scope,
        CollectorRunId("collector-run-fixture-1"),
        0,
        datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        100,
    )

    evidence = _evidence(scope, CoverageEvidenceKind.TRANSPORT_FAILURE)
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageTransitionId(
            canonical_json_array(
                (
                    "coverage-transition-v1",
                    scope.coverage_scope_id,
                    epoch.coverage_epoch_id,
                    1,
                    CoverageStatus.COMPLETE.value,
                    CoverageStatus.CONFIRMED_INCOMPLETE.value,
                    CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE.value,
                    evidence.coverage_evidence_id,
                )
            )
        )

    malformed_evidence = json.loads(evidence.coverage_evidence_id.value)
    malformed_evidence[-1] = 99
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageTransitionId(
            canonical_json_array(
                (
                    "coverage-transition-v1",
                    scope.coverage_scope_id,
                    epoch.coverage_epoch_id,
                    1,
                    CoverageStatus.COMPLETE.value,
                    CoverageStatus.UNCERTAIN.value,
                    CoverageReason.TRANSPORT_AMBIGUITY.value,
                    json.dumps(
                        malformed_evidence,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                )
            )
        )


def test_event_coverage_accepts_exactly_ingress_and_normalization() -> None:
    ingress = _committed_coverage_state(_coverage_reference(CoverageDomain.BRONZE_INGRESS))
    normalization = _committed_coverage_state(
        _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)
    )

    assert EventCoverage(ingress, normalization) == EventCoverage(ingress, normalization)
    with pytest.raises(ValueError):
        EventCoverage(normalization, normalization)
    with pytest.raises(ValueError):
        EventCoverage(ingress, ingress)


def _identified_rejection_target(
    plan: SubscriptionPlanIdentity,
    snapshot: SubscriptionAttemptSnapshot,
) -> ExactIdentifiedRejectionTarget:
    binding = next(
        item
        for item in plan.instrument_bindings
        if item.subscription_spec_id == snapshot.subscription_spec.subscription_spec_id
    )
    normalization = next(
        item
        for item in plan.normalization_bindings
        if item.subscription_spec_id == snapshot.subscription_spec.subscription_spec_id
        and item.event_family == "trade"
    )
    return ExactIdentifiedRejectionTarget(
        attempt_snapshot=snapshot,
        source_selector=binding.source_selector,
        canonical_instrument_id=binding.canonical_instrument_id,
        adapter_profile=binding.adapter_profile,
        event_family=normalization.event_family,
        event_family_schema_version=normalization.event_family_schema_version,
        payload_type=normalization.payload_type,
    )


@pytest.mark.parametrize(
    "status",
    (
        SubscriptionAttemptStatus.PENDING,
        SubscriptionAttemptStatus.SEND_STARTED,
        SubscriptionAttemptStatus.SENT,
    ),
)
@pytest.mark.parametrize("coin", ("BTC", "xyz:XYZ100", "@107"))
def test_identified_rejection_target_binds_exact_non_ack_route_and_raw_snapshot(
    status: SubscriptionAttemptStatus,
    coin: str,
) -> None:
    raw = _raw_record(status=status)
    if coin != "BTC":
        spec = _spec(coin)
        plan = _plan(spec)
        snapshot = _attempt_snapshot(status=status, spec=spec)
        raw = replace(
            raw,
            subscription_plan=plan,
            subscription_attempt_snapshots=(snapshot,),
        )
    plan = raw.subscription_plan
    snapshot = raw.subscription_attempt_snapshots[0]
    target = _identified_rejection_target(plan, snapshot)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    proof = CoverageFanoutProof.exact_identified_rejection(
        plan=plan,
        catalog=catalog,
        attempt_snapshot=target.attempt_snapshot,
        source_selector=target.source_selector,
        canonical_instrument_id=target.canonical_instrument_id,
        adapter_profile=target.adapter_profile,
        event_family=target.event_family,
        event_family_schema_version=target.event_family_schema_version,
        payload_type=target.payload_type,
    )
    binding = RawCoverageFanoutBinding.from_raw_record(
        raw_record=raw,
        coverage_fanout_proof=proof,
    )

    assert proof.kind is CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION
    assert json.loads(proof.coverage_fanout_proof_id.value)[0] == "coverage-fanout-proof-v2"
    assert json.loads(proof.canonical_content)[0] == "coverage-fanout-proof-content-v2"
    assert proof.source_canonical_row[0] == "exact-identified-rejections-v1"
    rejected_rows = cast(
        tuple[tuple[object, ...], ...],
        proof.source_canonical_row[1],
    )
    rejected_row = rejected_rows[0]
    assert rejected_row[0] == "exact-identified-rejection-target-v1"
    assert rejected_row[5] == status.value
    assert rejected_row[6] == target.source_selector.canonical_components()
    assert proof.target_scopes[0].domain is CoverageDomain.SILVER_NORMALIZATION
    assert binding.raw_record_id == raw.raw_record_id

    changed_raw = replace(
        raw,
        subscription_attempt_snapshots=(
            replace(
                snapshot,
                attempt_status=(
                    SubscriptionAttemptStatus.SENT
                    if status is not SubscriptionAttemptStatus.SENT
                    else SubscriptionAttemptStatus.SEND_STARTED
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match=r"reproducible|raw snapshot"):
        RawCoverageFanoutBinding.from_raw_record(
            raw_record=changed_raw,
            coverage_fanout_proof=proof,
        )


def test_identified_rejection_target_rejects_ack_and_foreign_route_components() -> None:
    plan = _plan()
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    with pytest.raises(ValueError, match="non-acknowledged"):
        _identified_rejection_target(plan, acknowledged)

    sent = _attempt_snapshot(status=SubscriptionAttemptStatus.SENT)
    target = _identified_rejection_target(plan, sent)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    foreign_cases = (
        replace(
            target,
            attempt_snapshot=_attempt_snapshot(
                status=SubscriptionAttemptStatus.SENT,
                spec=_spec("ETH"),
            ),
        ),
        replace(
            target,
            source_selector=PublicSourceSelector(
                PublicSourceSelectorKind.HYPERLIQUID_COIN,
                "ETH",
            ),
        ),
        replace(target, canonical_instrument_id=_instrument("ETH").canonical_instrument_id),
        replace(target, adapter_profile="foreign-adapter"),
        replace(target, event_family="bbo"),
    )
    for foreign in foreign_cases:
        with pytest.raises(ValueError, match="identified route"):
            CoverageFanoutProof.exact_identified_rejections(
                plan=plan,
                catalog=catalog,
                rejection_targets=(foreign,),
            )


@pytest.mark.parametrize(
    ("connection_ordinal", "attempt_ordinal"),
    ((1, 0), (0, 1)),
)
def test_identified_rejection_raw_binding_rejects_foreign_session_or_attempt(
    connection_ordinal: int,
    attempt_ordinal: int,
) -> None:
    raw = _raw_record(status=SubscriptionAttemptStatus.SENT)
    snapshot = _attempt_snapshot(
        status=SubscriptionAttemptStatus.SENT,
        connection_ordinal=connection_ordinal,
        attempt_ordinal=attempt_ordinal,
    )
    target = _identified_rejection_target(raw.subscription_plan, snapshot)
    proof = CoverageFanoutProof.exact_identified_rejections(
        plan=raw.subscription_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(raw.subscription_plan),
        rejection_targets=(target,),
    )

    with pytest.raises(ValueError, match=r"raw plan and session|raw snapshot"):
        RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw,
            coverage_fanout_proof=proof,
        )


@pytest.mark.parametrize("plan_size", (1, 4, 50))
def test_identified_rejection_fanout_is_bounded_deterministic_and_content_addressed(
    plan_size: int,
) -> None:
    plan = _multi_hyperliquid_plan(plan_size)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            (
                SubscriptionAttemptStatus.SEND_STARTED
                if index % 2 == 0
                else SubscriptionAttemptStatus.SENT
            ),
        )
        for index, spec in enumerate(plan.subscription_specs)
    )
    targets = tuple(_identified_rejection_target(plan, snapshot) for snapshot in snapshots)
    forward = CoverageFanoutProof.exact_identified_rejections(
        plan=plan,
        catalog=catalog,
        rejection_targets=targets,
    )
    reversed_proof = CoverageFanoutProof.exact_identified_rejections(
        plan=plan,
        catalog=catalog,
        rejection_targets=tuple(reversed(targets)),
    )

    assert forward.coverage_fanout_proof_id == reversed_proof.coverage_fanout_proof_id
    assert forward.canonical_content == reversed_proof.canonical_content
    assert len(forward.target_scopes) == plan_size
    assert forward.kind is (
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION
        if plan_size == 1
        else CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS
    )
    with pytest.raises(ValueError, match="unique by exact Silver scope"):
        CoverageFanoutProof.exact_identified_rejections(
            plan=plan,
            catalog=catalog,
            rejection_targets=(targets[0], targets[0]),
        )


def test_identified_rejection_mutation_matrix_is_provenance_only_and_silver_only() -> None:
    raw = _raw_record(status=SubscriptionAttemptStatus.SENT)
    plan = raw.subscription_plan
    target = _identified_rejection_target(plan, raw.subscription_attempt_snapshots[0])
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    proof = CoverageFanoutProof.exact_identified_rejections(
        plan=plan,
        catalog=catalog,
        rejection_targets=(target,),
    )
    scope = proof.target_scopes[0]
    epoch = CoverageEpochIdentity(
        scope,
        raw.collector_run_id,
        0,
        raw.received_time,
        raw.received_monotonic_ns,
    )

    def request(
        category: NormalizationFailureCategory,
        *,
        raw_event_index: int | None = 0,
    ) -> RequestedCoverageMutation:
        evidence = CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
            NormalizationFailureEvidenceSource(
                raw.raw_record_id,
                NormalizationRunId("preack-normalization-run"),
                raw_event_index,
                (SourceEventId("preack-source-event") if raw_event_index is not None else None),
                category,
                scope.coverage_scope_id,
            ),
            scope,
            epoch,
            raw.received_time,
            raw.received_monotonic_ns,
        )
        return RequestedCoverageMutation(
            scope,
            epoch,
            CoverageStatus.CONFIRMED_INCOMPLETE,
            InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
            evidence,
        )

    batch = prepare_coverage_mutation_batch(
        fanout_proof=proof,
        current_state_references=(),
        requests=(request(NormalizationFailureCategory.PROVENANCE_MISMATCH),),
        raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
            raw_record=raw,
            coverage_fanout_proof=proof,
        ),
    )
    assert batch.initializations[0].reference.status is CoverageStatus.CONFIRMED_INCOMPLETE
    with pytest.raises(ValueError, match="provenance-mismatch"):
        prepare_coverage_mutation_batch(
            fanout_proof=proof,
            current_state_references=(),
            requests=(request(NormalizationFailureCategory.DECODER_REJECTION),),
            raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw,
                coverage_fanout_proof=proof,
            ),
        )

    bronze_scope = next(
        item for item in catalog.scopes if item.domain is CoverageDomain.BRONZE_INGRESS
    )
    bronze_epoch = CoverageEpochIdentity(
        bronze_scope,
        raw.collector_run_id,
        0,
        raw.received_time,
        raw.received_monotonic_ns,
    )
    with pytest.raises(ValueError, match="incompatible with the coverage domain"):
        CoverageEvidence(
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
            NormalizationFailureEvidenceSource(
                raw.raw_record_id,
                NormalizationRunId("preack-normalization-run"),
                0,
                SourceEventId("preack-source-event"),
                NormalizationFailureCategory.PROVENANCE_MISMATCH,
                bronze_scope.coverage_scope_id,
            ),
            bronze_scope,
            bronze_epoch,
            raw.received_time,
            raw.received_monotonic_ns,
        )
    with pytest.raises(ValueError, match="provenance-mismatch"):
        prepare_coverage_mutation_batch(
            fanout_proof=proof,
            current_state_references=(),
            requests=(
                request(
                    NormalizationFailureCategory.PROVENANCE_MISMATCH,
                    raw_event_index=None,
                ),
            ),
            raw_fanout_binding=RawCoverageFanoutBinding.from_raw_record(
                raw_record=raw,
                coverage_fanout_proof=proof,
            ),
        )


def test_existing_acknowledged_exact_route_identity_remains_byte_exact() -> None:
    plan = _plan()
    proof = CoverageFanoutProof.exact_routed_event(
        plan=plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(plan),
        acknowledged_snapshot=_attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED),
        canonical_instrument_id=_instrument().canonical_instrument_id,
        event_family="trade",
        event_family_schema_version=2,
        payload_type="trade",
    )

    assert proof.content_sha256 == (
        "dc9e6d1a84b8d1baae997c12e9d6669aca3190e6d42a8593c066c576d92199e0"
    )
    assert json.loads(proof.coverage_fanout_proof_id.value)[:2] == [
        "coverage-fanout-proof-v1",
        "exact-routed-event",
    ]
    assert proof.source_canonical_row[0] == "exact-routed-events-v1"


@pytest.mark.parametrize(
    "status",
    (CoverageStatus.UNCERTAIN, CoverageStatus.CONFIRMED_INCOMPLETE),
)
def test_event_coverage_propagates_exact_initial_bronze_degradation(
    status: CoverageStatus,
) -> None:
    ingress = _committed_coverage_state(_coverage_reference(CoverageDomain.BRONZE_INGRESS, status))
    normalization = _committed_coverage_state(
        _initial_coverage_reference(
            CoverageDomain.SILVER_NORMALIZATION,
            status,
            InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
        )
    )

    assert EventCoverage(ingress, normalization).silver_normalization == normalization


def test_event_coverage_rejects_better_silver_state_or_foreign_upstream_commit() -> None:
    bronze_uncertain = _committed_coverage_state(
        _coverage_reference(CoverageDomain.BRONZE_INGRESS, CoverageStatus.UNCERTAIN)
    )
    silver_complete = _committed_coverage_state(
        _coverage_reference(CoverageDomain.SILVER_NORMALIZATION, CoverageStatus.COMPLETE)
    )
    with pytest.raises(ValueError, match="cannot be better"):
        EventCoverage(bronze_uncertain, silver_complete)

    silver_uncertain = _committed_coverage_state(
        _initial_coverage_reference(
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageStatus.UNCERTAIN,
            InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
        )
    )
    different_committed_bronze = _committed_upstream_transition(CoverageStatus.UNCERTAIN)
    with pytest.raises(ValueError, match="exact committed Bronze"):
        EventCoverage(different_committed_bronze, silver_uncertain)


def test_event_coverage_requires_exact_latest_upstream_transition_commit() -> None:
    bronze_transitioned = _committed_upstream_transition(CoverageStatus.UNCERTAIN)
    silver_active = _committed_coverage_state(
        _coverage_reference(CoverageDomain.SILVER_NORMALIZATION, CoverageStatus.COMPLETE)
    )
    silver_scope = silver_active.state_reference.reference.scope
    silver_epoch = silver_active.state_reference.reference.epoch
    evidence = CoverageEvidence(
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
        UpstreamCoverageTransitionEvidenceSource(bronze_transitioned),
        silver_scope,
        silver_epoch,
        datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
        102,
    )
    request = RequestedCoverageMutation(
        silver_scope,
        silver_epoch,
        CoverageStatus.UNCERTAIN,
        InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED,
        CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
        evidence,
    )
    plan = _plan()
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    batch = prepare_coverage_mutation_batch(
        fanout_proof=CoverageFanoutProof.all_possibly_active(
            plan=plan,
            catalog=CoverageTargetCatalog.from_subscription_plan(plan),
            complete_snapshots=(acknowledged,),
            domain=CoverageDomain.SILVER_NORMALIZATION,
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        ),
        current_state_references=(silver_active.state_reference,),
        requests=(request,),
    )
    resulting = batch.verify_compare_and_swap((silver_active.state_reference,))
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=batch,
        committed_state_references=resulting,
    )
    silver_degraded = CommittedCoverageState.from_commit(
        state_reference=resulting[0],
        commit_acceptance=acceptance,
    )

    assert EventCoverage(bronze_transitioned, silver_degraded).silver_normalization == (
        silver_degraded
    )
    foreign_same_status = _committed_coverage_state(
        _coverage_reference(CoverageDomain.BRONZE_INGRESS, CoverageStatus.UNCERTAIN)
    )
    with pytest.raises(ValueError, match="exact committed Bronze"):
        EventCoverage(foreign_same_status, silver_degraded)


def test_legacy_delivery_locators_remain_byte_exact_and_claim_no_commit_proof() -> None:
    attempt = DeliveryAttemptIdentity(
        destination_id="collector-output-queue",
        materialization_key_canonical_texts=(_materialization_key_text(),),
        attempt_ordinal=0,
    )
    assert _text_sha256(attempt.delivery_batch_id.value) == (
        "e4264bbd800bb21125b054267eb35ff7a12573c936a2b10c8f199dca92f0227d"
    )
    assert _text_sha256(attempt.delivery_attempt_id.value) == (
        "c38bb98fda4645bffdc460e082c86f5c722d702b05e94291d3937025f620306f"
    )
    assert not hasattr(attempt, "status")
    assert not hasattr(attempt, "accepted")
    assert not hasattr(data_provenance_module, "DeliveryOutcome")


def test_identifier_wrappers_are_distinct_frozen_slotted_and_hashable() -> None:
    source = CorrelationId("fixture-id")

    assert hash(source) == hash(CorrelationId("fixture-id"))
    assert not hasattr(source, "__dict__")
    with pytest.raises(FrozenInstanceError):
        source.value = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid",
    [
        "feed-product-v1",
        '["wrong-version"]',
        '[ "feed-product-v1"]',
        '{"version":"feed-product-v1"}',
        '["feed-product-v1",1.5]',
    ],
)
def test_computed_identifier_wrappers_reject_noncanonical_or_wrong_tag(invalid: str) -> None:
    with pytest.raises(ValueError):
        FeedProductId(invalid)


@pytest.mark.parametrize(
    ("constructor", "version_tag"),
    [
        (FeedProductId, "feed-product-v1"),
        (FeedCapabilitySetId, "feed-capability-set-v1"),
        (FeedCapabilitiesObservationId, "feed-capabilities-observation-v1"),
        (ConnectionSessionId, "connection-session-v1"),
        (AdapterFeedBindingId, "adapter-feed-binding-v1"),
        (SubscriptionPlanId, "subscription-plan-v1"),
        (SubscriptionSpecId, "subscription-spec-v1"),
        (SubscriptionAttemptId, "subscription-attempt-v1"),
        (RawRecordId, "raw-record-v1"),
        (InstrumentSpecificationId, "instrument-specification-v1"),
        (InstrumentMetadataObservationId, "instrument-metadata-observation-v1"),
        (CoverageScopeId, "coverage-scope-v1"),
        (CoverageEpochId, "coverage-epoch-v1"),
        (CoverageTransitionId, "coverage-transition-v1"),
        (DeliveryBatchId, "delivery-batch-v1"),
        (DeliveryAttemptId, "delivery-attempt-v1"),
    ],
)
def test_each_local_computed_id_rejects_wrong_component_count(
    constructor: Callable[[str], object],
    version_tag: str,
) -> None:
    with pytest.raises(ValueError, match="invalid canonical components"):
        constructor(f'["{version_tag}"]')


def test_computed_ids_reject_wrong_nested_shapes_and_component_types() -> None:
    with pytest.raises(ValueError):
        FeedCapabilitySetId('["feed-capability-set-v1","not-an-id",[]]')
    with pytest.raises(ValueError):
        SubscriptionSpecId(
            canonical_json_array(
                (
                    "subscription-spec-v1",
                    HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
                    "subscribe",
                    "trades",
                    (("coin", "BTC", "unexpected"),),
                )
            )
        )
    with pytest.raises(ValueError):
        DeliveryBatchId('["delivery-batch-v1","not-an-array"]')


def test_direct_construction_rejects_bool_for_all_representative_integer_fields() -> None:
    with pytest.raises(TypeError):
        ConnectionSessionIdentity(CollectorRunId("run"), cast(int, True))
    with pytest.raises(TypeError):
        SubscriptionAttemptIdentity(_session(), _spec(), cast(int, True))
    with pytest.raises(TypeError):
        _raw_record(ingress_ordinal=cast(int, True))
    with pytest.raises(TypeError):
        CoverageEpochIdentity(
            _scope(CoverageDomain.BRONZE_INGRESS),
            CollectorRunId("collector-run-fixture-1"),
            cast(int, True),
            datetime(2026, 8, 26, tzinfo=UTC),
            0,
        )


def test_raw_contract_has_no_sink_persistence_or_runtime_guarantee_fields() -> None:
    names = {item.name for item in fields(RawMarketDataRecord)}

    assert "sink" not in names
    assert "persisted" not in names
    assert "captured_before_parsing" not in names
    assert "complete_run" not in names
    assert "active_subscriptions" not in names
    assert "subscription_attempt_snapshots" in names


@pytest.fixture(scope="module")
def maximum_coverage_plan() -> tuple[
    SubscriptionPlanIdentity,
    CoverageTargetCatalog,
    tuple[SubscriptionAttemptSnapshot, ...],
]:
    plan = _multi_hyperliquid_plan(MAX_SUBSCRIPTION_SPECS)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )
    return plan, catalog, snapshots


def _activation_batch_for_selected_snapshots(
    *,
    plan: SubscriptionPlanIdentity,
    catalog: CoverageTargetCatalog,
    complete_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
    selected_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
) -> CoverageMutationBatch:
    fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=complete_snapshots,
        selected_attempt_ids=_selected_attempt_ids(*selected_snapshots),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    snapshot_by_spec = {
        item.subscription_attempt.subscription_spec.subscription_spec_id: item
        for item in selected_snapshots
    }
    requests = tuple(
        _activation_mutation_request(
            scope,
            snapshot_by_spec[scope.subscription_spec_ids[0]],
        )
        for scope in fanout.target_scopes
    )
    return prepare_coverage_mutation_batch(
        fanout_proof=fanout,
        current_state_references=(),
        requests=requests,
    )


@pytest.mark.parametrize("target_count", (1, 4, 50, 957, 958, 1_000))
def test_compact_coverage_mutation_v2_supports_previously_failing_target_counts(
    maximum_coverage_plan: tuple[
        SubscriptionPlanIdentity,
        CoverageTargetCatalog,
        tuple[SubscriptionAttemptSnapshot, ...],
    ],
    target_count: int,
) -> None:
    plan, catalog, snapshots = maximum_coverage_plan
    batch = _activation_batch_for_selected_snapshots(
        plan=plan,
        catalog=catalog,
        complete_snapshots=snapshots,
        selected_snapshots=snapshots[:target_count],
    )

    assert len(batch.resulting_state_references) == target_count
    assert len(batch.target_decision_sha256s) == target_count
    assert json.loads(batch.canonical_content)[:4] == [
        "coverage-mutation-batch-content-v2",
        batch.fanout_proof.coverage_fanout_proof_id.value,
        None,
        target_count,
    ]
    assert json.loads(batch.coverage_mutation_batch_id.value)[0] == ("coverage-mutation-batch-v2")
    assert len(batch.canonical_content) < MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH // 16


def _transport_ambiguity_request(
    state: CoverageStateReference,
    snapshot: SubscriptionAttemptSnapshot,
) -> RequestedCoverageMutation:
    scope = state.reference.scope
    evidence = CoverageEvidence(
        CoverageEvidenceKind.TRANSPORT_FAILURE,
        TransportAmbiguityEvidenceSource(
            scope.feed_product_id,
            snapshot.subscription_attempt.connection_session,
            snapshot.subscription_attempt,
            subscription_spec_membership_proof(
                scope,
                snapshot.subscription_attempt.subscription_spec.subscription_spec_id,
            ),
        ),
        scope,
        state.reference.epoch,
        datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
        101,
    )
    return RequestedCoverageMutation(
        scope,
        state.reference.epoch,
        CoverageStatus.UNCERTAIN,
        InitialCoverageReason.TRANSPORT_AMBIGUITY,
        CoverageReason.TRANSPORT_AMBIGUITY,
        evidence,
    )


def _sequence_break_request(
    state: CoverageStateReference,
) -> RequestedCoverageMutation:
    scope = state.reference.scope
    evidence = CoverageEvidence(
        CoverageEvidenceKind.SOURCE_SEQUENCE,
        SourceSequenceBreakEvidenceSource(
            scope.feed_product_id,
            SourceSequenceRange(
                SourceSequenceRole.EVENT_SEQUENCE,
                "scaling-public-trades",
                10,
                11,
            ),
            scope.coverage_scope_id,
        ),
        scope,
        state.reference.epoch,
        datetime(2026, 8, 26, 12, 0, 2, tzinfo=UTC),
        102,
    )
    return RequestedCoverageMutation(
        scope,
        state.reference.epoch,
        CoverageStatus.CONFIRMED_INCOMPLETE,
        InitialCoverageReason.SOURCE_SEQUENCE_BREAK,
        CoverageReason.SOURCE_SEQUENCE_BREAK,
        evidence,
    )


def test_maximum_plan_compact_batch_and_acceptance_cover_both_transitions_and_no_op(
    maximum_coverage_plan: tuple[
        SubscriptionPlanIdentity,
        CoverageTargetCatalog,
        tuple[SubscriptionAttemptSnapshot, ...],
    ],
) -> None:
    plan, catalog, acknowledged_snapshots = maximum_coverage_plan
    active = _activation_batch_for_selected_snapshots(
        plan=plan,
        catalog=catalog,
        complete_snapshots=acknowledged_snapshots,
        selected_snapshots=acknowledged_snapshots,
    )
    sent_snapshots = tuple(
        replace(item, attempt_status=SubscriptionAttemptStatus.SENT)
        for item in acknowledged_snapshots
    )
    ambiguity_fanout = CoverageFanoutProof.possibly_delivered_specs(
        plan=plan,
        catalog=catalog,
        complete_snapshots=sent_snapshots,
        selected_attempt_ids=_selected_attempt_ids(*sent_snapshots),
    )
    active_by_scope = {
        item.reference.scope.coverage_scope_id: item for item in active.resulting_state_references
    }
    sent_by_spec = {
        item.subscription_attempt.subscription_spec.subscription_spec_id: item
        for item in sent_snapshots
    }
    uncertain_requests = tuple(
        _transport_ambiguity_request(
            active_by_scope[scope.coverage_scope_id],
            sent_by_spec[scope.subscription_spec_ids[0]],
        )
        for scope in ambiguity_fanout.target_scopes
    )
    uncertain = prepare_coverage_mutation_batch(
        fanout_proof=ambiguity_fanout,
        current_state_references=active.resulting_state_references,
        requests=uncertain_requests,
    )
    incomplete_fanout = CoverageFanoutProof.all_possibly_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=sent_snapshots,
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    uncertain_by_scope = {
        item.reference.scope.coverage_scope_id: item
        for item in uncertain.resulting_state_references
    }
    incomplete_requests = tuple(
        _sequence_break_request(uncertain_by_scope[scope.coverage_scope_id])
        for scope in incomplete_fanout.target_scopes
    )
    incomplete = prepare_coverage_mutation_batch(
        fanout_proof=incomplete_fanout,
        current_state_references=uncertain.resulting_state_references,
        requests=incomplete_requests,
    )
    repeated = prepare_coverage_mutation_batch(
        fanout_proof=incomplete_fanout,
        current_state_references=incomplete.resulting_state_references,
        requests=incomplete_requests,
    )
    acceptance = CoverageCommitAcceptance.after_compare_and_swap(
        batch=repeated,
        committed_state_references=repeated.verify_compare_and_swap(
            incomplete.resulting_state_references
        ),
    )

    assert len(active.initializations) == MAX_SUBSCRIPTION_SPECS
    assert len(uncertain.transitions) == MAX_SUBSCRIPTION_SPECS
    assert len(incomplete.transitions) == MAX_SUBSCRIPTION_SPECS
    assert len(repeated.no_ops) == MAX_SUBSCRIPTION_SPECS
    assert all(
        item.reference.transition_ordinal == 2
        and item.reference.status is CoverageStatus.CONFIRMED_INCOMPLETE
        for item in incomplete.resulting_state_references
    )
    assert repeated.resulting_state_references == incomplete.resulting_state_references
    assert len(acceptance.resulting_state_reference_ids) == MAX_SUBSCRIPTION_SPECS
    assert len(acceptance.resulting_state_item_sha256s) == MAX_SUBSCRIPTION_SPECS
    assert len(active.canonical_content) < MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH // 16
    assert len(uncertain.canonical_content) < MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH // 16
    assert len(incomplete.canonical_content) < MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH // 16
    assert len(repeated.canonical_content) < MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH // 16
    assert len(acceptance.canonical_content) < MAX_CANONICAL_IDENTIFIER_LENGTH
    assert active.target_decision_sha256s[0] != uncertain.target_decision_sha256s[0]
    assert uncertain.target_decision_sha256s[0] != incomplete.target_decision_sha256s[0]
    assert incomplete.target_decision_sha256s[0] != repeated.target_decision_sha256s[0]

    before = repeated.resulting_state_references
    with pytest.raises(ValueError, match="pre-state"):
        repeated.verify_compare_and_swap(incomplete.resulting_state_references[:-1])
    assert repeated.resulting_state_references == before

    repeated.verify_stored(
        expected_canonical_content=repeated.canonical_content,
        expected_batch_id=repeated.coverage_mutation_batch_id,
    )
    with pytest.raises(ValueError, match="does not match"):
        repeated.verify_stored(
            expected_canonical_content=repeated.canonical_content + " ",
            expected_batch_id=repeated.coverage_mutation_batch_id,
        )
    tampered_batch_id = CoverageMutationBatchId(
        canonical_json_array(
            (
                "coverage-mutation-batch-v2",
                repeated.fanout_proof.coverage_fanout_proof_id,
                MAX_SUBSCRIPTION_SPECS,
                "f" * 64,
            )
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        repeated.verify_stored(
            expected_canonical_content=repeated.canonical_content,
            expected_batch_id=tampered_batch_id,
        )
    with pytest.raises(ValueError, match="does not match"):
        CoverageCommitAcceptance.from_stored(
            batch=repeated,
            coverage_mutation_batch_id=repeated.coverage_mutation_batch_id,
            resulting_state_reference_ids=acceptance.resulting_state_reference_ids,
            expected_canonical_content=acceptance.canonical_content + " ",
            expected_acceptance_id=acceptance.coverage_commit_acceptance_id,
        )
    tampered_acceptance_id = CoverageCommitAcceptanceId(
        canonical_json_array(
            (
                "coverage-commit-acceptance-v2",
                repeated.coverage_mutation_batch_id,
                MAX_SUBSCRIPTION_SPECS,
                "e" * 64,
            )
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        CoverageCommitAcceptance.from_stored(
            batch=repeated,
            coverage_mutation_batch_id=repeated.coverage_mutation_batch_id,
            resulting_state_reference_ids=acceptance.resulting_state_reference_ids,
            expected_canonical_content=acceptance.canonical_content,
            expected_acceptance_id=tampered_acceptance_id,
        )
    for batch, field_name in (
        (active, "initializations"),
        (uncertain, "transitions"),
        (repeated, "no_ops"),
    ):
        original = getattr(batch, field_name)
        object.__setattr__(batch, field_name, tuple(reversed(original)))
        with pytest.raises(ValueError, match="canonical order"):
            batch.verify_stored(
                expected_canonical_content=batch.canonical_content,
                expected_batch_id=batch.coverage_mutation_batch_id,
            )
        object.__setattr__(batch, field_name, original)


def test_subscription_plan_n_plus_one_remains_rejected_by_existing_bound(
    maximum_coverage_plan: tuple[
        SubscriptionPlanIdentity,
        CoverageTargetCatalog,
        tuple[SubscriptionAttemptSnapshot, ...],
    ],
) -> None:
    plan, _catalog, _snapshots = maximum_coverage_plan
    with pytest.raises(ValueError, match="item count"):
        SubscriptionPlanIdentity(
            feed_product_id=plan.feed_product_id,
            adapter_feed_binding_id=plan.adapter_feed_binding_id,
            subscription_specs=(*plan.subscription_specs, plan.subscription_specs[0]),
            instrument_bindings=plan.instrument_bindings,
            normalization_bindings=plan.normalization_bindings,
            connection_wire_options=plan.connection_wire_options,
        )


def test_legacy_coverage_commitment_identifiers_are_parser_only_and_version_paired() -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    fanout = CoverageFanoutProof.acknowledged_active(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_attempt_ids=_selected_attempt_ids(acknowledged),
        domain=CoverageDomain.BRONZE_INGRESS,
    )
    legacy_batch = CoverageMutationBatchId(
        canonical_json_array(
            (
                "coverage-mutation-batch-v1",
                fanout.coverage_fanout_proof_id,
                1,
                "0" * 64,
            )
        )
    )
    legacy_acceptance = CoverageCommitAcceptanceId(
        canonical_json_array(
            (
                "coverage-commit-acceptance-v1",
                legacy_batch,
                1,
                "1" * 64,
            )
        )
    )
    current_batch = _activation_batch_for_selected_snapshots(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_snapshots=(acknowledged,),
    )

    assert json.loads(legacy_batch.value)[0] == "coverage-mutation-batch-v1"
    assert json.loads(legacy_acceptance.value)[0] == "coverage-commit-acceptance-v1"
    assert json.loads(current_batch.coverage_mutation_batch_id.value)[0] == (
        "coverage-mutation-batch-v2"
    )
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageCommitAcceptanceId(
            canonical_json_array(
                (
                    "coverage-commit-acceptance-v2",
                    legacy_batch,
                    1,
                    "2" * 64,
                )
            )
        )
    with pytest.raises(ValueError, match="invalid canonical components"):
        CoverageCommitAcceptanceId(
            canonical_json_array(
                (
                    "coverage-commit-acceptance-v1",
                    current_batch.coverage_mutation_batch_id,
                    1,
                    "3" * 64,
                )
            )
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "expected_pre_state_rows",
        "initializations",
        "transitions",
        "no_ops",
        "resulting_state_references",
    ),
)
def test_stored_batch_reverification_rejects_non_tuple_typed_collections(
    field_name: str,
) -> None:
    plan = _plan()
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    acknowledged = _attempt_snapshot(status=SubscriptionAttemptStatus.ACKNOWLEDGED)
    batch = _activation_batch_for_selected_snapshots(
        plan=plan,
        catalog=catalog,
        complete_snapshots=(acknowledged,),
        selected_snapshots=(acknowledged,),
    )
    expected_content = batch.canonical_content
    expected_id = batch.coverage_mutation_batch_id
    object.__setattr__(batch, field_name, list(getattr(batch, field_name)))

    with pytest.raises(TypeError, match=field_name):
        batch.verify_stored(
            expected_canonical_content=expected_content,
            expected_batch_id=expected_id,
        )


def test_stored_batch_reverification_rejects_operation_role_and_membership_forgery() -> None:
    plan = _multi_hyperliquid_plan(2)
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = tuple(
        SubscriptionAttemptSnapshot(
            SubscriptionAttemptIdentity(_session(), spec, 0),
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
        for spec in plan.subscription_specs
    )

    def fresh_batch() -> CoverageMutationBatch:
        return _activation_batch_for_selected_snapshots(
            plan=plan,
            catalog=catalog,
            complete_snapshots=snapshots,
            selected_snapshots=snapshots,
        )

    def verify_forged(batch: CoverageMutationBatch) -> None:
        batch.verify_stored(
            expected_canonical_content=batch.canonical_content,
            expected_batch_id=batch.coverage_mutation_batch_id,
        )

    subset = fresh_batch()
    object.__setattr__(subset, "initializations", subset.initializations[:-1])
    with pytest.raises(ValueError, match="partition"):
        verify_forged(subset)

    duplicate = fresh_batch()
    object.__setattr__(
        duplicate,
        "initializations",
        (*duplicate.initializations, duplicate.initializations[0]),
    )
    with pytest.raises(ValueError, match=r"canonical order|unique|partition"):
        verify_forged(duplicate)

    foreign_plan = _plan(_spec("FOREIGN"))
    foreign_snapshot = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(_session(), foreign_plan.subscription_specs[0], 0),
        SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    foreign = _activation_batch_for_selected_snapshots(
        plan=foreign_plan,
        catalog=CoverageTargetCatalog.from_subscription_plan(foreign_plan),
        complete_snapshots=(foreign_snapshot,),
        selected_snapshots=(foreign_snapshot,),
    )
    superset = fresh_batch()
    object.__setattr__(
        superset,
        "initializations",
        (*superset.initializations, foreign.initializations[0]),
    )
    with pytest.raises(ValueError, match="partition"):
        verify_forged(superset)

    reordered = fresh_batch()
    object.__setattr__(
        reordered,
        "resulting_state_references",
        tuple(reversed(reordered.resulting_state_references)),
    )
    with pytest.raises(ValueError, match="fanout order"):
        verify_forged(reordered)

    role_substitution = fresh_batch()
    object.__setattr__(
        role_substitution,
        "no_ops",
        cast(tuple[CoverageMutationNoOp, ...], role_substitution.initializations[:1]),
    )
    with pytest.raises(TypeError, match="no_ops"):
        verify_forged(role_substitution)
