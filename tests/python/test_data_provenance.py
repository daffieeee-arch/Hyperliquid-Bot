"""Pure tests for the dormant market-data provenance contract spine."""

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
    ConnectionSessionId,
    ConnectionSessionIdentity,
    CorrelationId,
    CoverageDomain,
    CoverageEpochId,
    CoverageEpochIdentity,
    CoverageEvidence,
    CoverageEvidenceId,
    CoverageEvidenceKind,
    CoverageEvidenceSource,
    CoverageReason,
    CoverageReference,
    CoverageScope,
    CoverageScopeId,
    CoverageStatus,
    CoverageTransitionId,
    DeliveryAttemptId,
    DeliveryAttemptIdentity,
    DeliveryBatchId,
    DeliveryOutcome,
    DeliveryReason,
    DeliveryStatus,
    EffectiveBoundaryBasis,
    EventCoverage,
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
    NormalizationRunId,
    PublicConnectionOption,
    PublicConnectionOptionKind,
    PublicEndpointProfile,
    PublicSourceSelector,
    PublicSourceSelectorKind,
    PublicSubscriptionParameter,
    PublicSubscriptionParameterKind,
    RawMarketDataRecord,
    RawRecordEvidenceSource,
    RawRecordId,
    ReconnectEvidenceSource,
    RequestedCoverageTransition,
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
    UpstreamCoverageTransitionEvidenceSource,
    ValidationFailureCategory,
    WireEncoding,
    canonical_json_array,
    canonical_utc_datetime,
    coverage_scope_id_from_canonical_content,
    parse_canonical_json_array,
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


def _raw_record(
    *,
    ingress_ordinal: int = 0,
    status: SubscriptionAttemptStatus = SubscriptionAttemptStatus.SENT,
    application_message_bytes: bytes = b'{"channel":"trades"}',
    frame_kind: FrameKind = FrameKind.TEXT,
    received_time: datetime = datetime(2026, 8, 26, 12, 0, 0, 123456, tzinfo=UTC),
) -> RawMarketDataRecord:
    spec = _spec()
    session = _session()
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
        initial_source = TransportAmbiguityEvidenceSource(
            scope.feed_product_id,
            _session(),
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
            _raw_record().raw_record_id,
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
    elif kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION:
        upstream = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
        transition, _ = reduce_coverage(
            upstream,
            RequestedCoverageTransition(
                upstream.scope,
                upstream.epoch,
                upstream.status,
                1,
                CoverageStatus.UNCERTAIN,
                CoverageReason.TRANSPORT_AMBIGUITY,
            ),
            _evidence(upstream.scope, CoverageEvidenceKind.TRANSPORT_FAILURE),
        )
        source = UpstreamCoverageTransitionEvidenceSource(transition.coverage_transition_id)
    else:  # pragma: no cover - helper is closed by the parameterized matrix
        raise AssertionError("unsupported initial evidence fixture")
    evidence = CoverageEvidence(
        kind=kind,
        source=source,
        scope=scope,
        epoch=epoch,
        observed_at=activation_time,
        observed_monotonic_ns=200,
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
        upstream = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
        upstream_transition, _ = reduce_coverage(
            upstream,
            RequestedCoverageTransition(
                upstream.scope,
                upstream.epoch,
                upstream.status,
                1,
                CoverageStatus.UNCERTAIN,
                CoverageReason.TRANSPORT_AMBIGUITY,
            ),
            _evidence(upstream.scope, CoverageEvidenceKind.TRANSPORT_FAILURE),
        )
        source = UpstreamCoverageTransitionEvidenceSource(
            upstream_transition.coverage_transition_id
        )
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
            CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
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
    ingress = _coverage_reference(CoverageDomain.BRONZE_INGRESS)
    normalization = _coverage_reference(CoverageDomain.SILVER_NORMALIZATION)

    assert EventCoverage(ingress, normalization) == EventCoverage(ingress, normalization)
    with pytest.raises(ValueError):
        EventCoverage(normalization, normalization)
    with pytest.raises(ValueError):
        EventCoverage(ingress, ingress)


def test_delivery_ids_are_versioned_and_outcome_is_separate_from_event_coverage() -> None:
    attempt = DeliveryAttemptIdentity(
        destination_id="collector-output-queue",
        materialization_key_canonical_texts=(_materialization_key_text(),),
        attempt_ordinal=0,
    )
    outcome = DeliveryOutcome(
        attempt=attempt,
        status=DeliveryStatus.ACCEPTED,
        reason=DeliveryReason.OUTPUT_QUEUE_ACCEPTANCE,
        observed_at=datetime(2026, 8, 26, tzinfo=UTC),
        observed_monotonic_ns=1,
    )

    assert _text_sha256(attempt.delivery_batch_id.value) == (
        "e4264bbd800bb21125b054267eb35ff7a12573c936a2b10c8f199dca92f0227d"
    )
    assert _text_sha256(attempt.delivery_attempt_id.value) == (
        "c38bb98fda4645bffdc460e082c86f5c722d702b05e94291d3937025f620306f"
    )
    assert outcome.status is DeliveryStatus.ACCEPTED
    assert not hasattr(outcome, "consumer_processed")
    assert not hasattr(outcome, "persisted")


def test_delivery_status_reason_matrix_is_closed() -> None:
    attempt = DeliveryAttemptIdentity(
        "collector-output-queue",
        (_materialization_key_text(),),
        0,
    )
    with pytest.raises(ValueError, match="inconsistent"):
        DeliveryOutcome(
            attempt,
            DeliveryStatus.ACCEPTED,
            DeliveryReason.OUTPUT_QUEUE_TIMEOUT,
            datetime(2026, 8, 26, tzinfo=UTC),
            1,
        )


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
