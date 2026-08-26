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
    AdapterFeedBindingId,
    CollectorRunId,
    ConnectionSessionIdentity,
    CorrelationId,
    CoverageDomain,
    CoverageEpochIdentity,
    CoverageEvidence,
    CoverageEvidenceKind,
    CoverageEvidenceSource,
    CoverageReason,
    CoverageReference,
    CoverageScope,
    CoverageStatus,
    CoverageTransitionId,
    EventActivationRequirement,
    EventCoverage,
    FrameKind,
    InitialActivationEvidenceSource,
    InitialCoverageReason,
    InstrumentSubscriptionBinding,
    MetadataAuthorityId,
    NormalizationBinding,
    NormalizationFailureCategory,
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
    reduce_coverage,
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
    EventFamily,
    FrameNormalizationStatus,
    LogicalSourceKey,
    LogicalSourceKeyId,
    MarketEventEnvelopeV3,
    MaterializationKey,
    MaterializationKeyId,
    NormalizationContext,
    NormalizationEvidence,
    NormalizationOutcome,
    NormalizationOutcomeId,
    ObservationKey,
    ObservationKeyId,
    ObservationProvenance,
    PayloadType,
    RawEventDisposition,
    RawEventNormalizationOutcome,
    RawEventNormalizationOutcomeId,
    RawEventNormalizationScopeBinding,
    RawFrameNormalizationScopeBinding,
    SourceProvenance,
)

_RECEIVED_TIME = datetime(2026, 8, 26, 12, 0, 0, 123456, tzinfo=UTC)
_EVENT_TIME = datetime(2026, 8, 26, 11, 59, 59, 654321, tzinfo=UTC)


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


def _multi_spec_raw_record() -> tuple[
    RawMarketDataRecord,
    SubscriptionSpecIdentity,
    SubscriptionSpecIdentity,
    Instrument,
    Instrument,
]:
    adapter_binding = _adapter_binding()
    btc_spec = _wire_spec("BTCUSDT", request_id=1)
    eth_spec = _wire_spec("ETHUSDT", request_id=2)
    specs = tuple(sorted((btc_spec, eth_spec), key=lambda item: item.subscription_spec_id.value))
    btc_instrument = _instrument()
    eth_instrument = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="ETH",
        quote_asset="USDT",
        venue_market_id="ETHUSDT",
        native_symbol="ETHUSDT",
    )
    instrument_by_spec_id = {
        btc_spec.subscription_spec_id: btc_instrument,
        eth_spec.subscription_spec_id: eth_instrument,
    }
    selector_by_spec_id = {
        btc_spec.subscription_spec_id: "btcusdt@trade",
        eth_spec.subscription_spec_id: "ethusdt@trade",
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

    def reference(domain: CoverageDomain) -> CoverageReference:
        scope = CoverageScope(
            domain=domain,
            feed_product_id=BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id,
            subscription_spec_ids=(spec.subscription_spec_id,),
            canonical_instrument_ids=(selected_instrument.canonical_instrument_id,),
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
        activation_time = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
        epoch = CoverageEpochIdentity(
            scope=scope,
            collector_run_id=selected_collector_run_id,
            epoch_ordinal=0,
            activation_time=activation_time,
            activation_monotonic_ns=100,
        )
        return CoverageReference.initial(
            scope=scope,
            epoch=epoch,
            status=CoverageStatus.COMPLETE,
            initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
            initial_evidence=CoverageEvidence(
                kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
                source=InitialActivationEvidenceSource(
                    connection_session=ConnectionSessionIdentity(
                        selected_collector_run_id,
                        0,
                    ),
                    acknowledged_attempts=(
                        SubscriptionAttemptSnapshot(
                            subscription_attempt=SubscriptionAttemptIdentity(
                                ConnectionSessionIdentity(selected_collector_run_id, 0),
                                spec,
                                0,
                            ),
                            attempt_status=SubscriptionAttemptStatus.ACKNOWLEDGED,
                        ),
                    ),
                ),
                scope=scope,
                epoch=epoch,
                observed_at=activation_time,
                observed_monotonic_ns=100,
            ),
        )

    return EventCoverage(
        bronze_ingress=reference(CoverageDomain.BRONZE_INGRESS),
        silver_normalization=reference(CoverageDomain.SILVER_NORMALIZATION),
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

    def reference(domain: CoverageDomain) -> CoverageReference:
        scope = CoverageScope(
            domain=domain,
            feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            subscription_spec_ids=(spec.subscription_spec_id,),
            canonical_instrument_ids=(instrument.canonical_instrument_id,),
            event_family="trade",
            event_family_schema_version=2,
            payload_type="trade",
        )
        activation = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
        epoch = CoverageEpochIdentity(scope, run_id, 0, activation, 100)
        return CoverageReference.initial(
            scope=scope,
            epoch=epoch,
            status=CoverageStatus.COMPLETE,
            initial_reason=InitialCoverageReason.INITIAL_ACTIVATION,
            initial_evidence=CoverageEvidence(
                kind=CoverageEvidenceKind.INITIAL_ACTIVATION,
                source=InitialActivationEvidenceSource(
                    connection_session=session,
                    acknowledged_attempts=(
                        SubscriptionAttemptSnapshot(
                            subscription_attempt=attempt,
                            attempt_status=SubscriptionAttemptStatus.ACKNOWLEDGED,
                        ),
                    ),
                ),
                scope=scope,
                epoch=epoch,
                observed_at=activation,
                observed_monotonic_ns=100,
            ),
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
            reference(CoverageDomain.BRONZE_INGRESS),
            reference(CoverageDomain.SILVER_NORMALIZATION),
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
) -> tuple[RawMarketDataRecord, CoverageScope, CoverageTransitionId]:
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
    return raw_record, scope, transition.coverage_transition_id


def _source_conflict_transition(
    *,
    source_event_id: SourceEventId,
    raw_event_index: int,
    scope_spec: SubscriptionSpecIdentity | None = None,
    canonical_instrument_id: str | None = None,
) -> tuple[RawMarketDataRecord, CoverageScope, CoverageTransitionId]:
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
    return raw_record, scope, transition.coverage_transition_id


def _unrelated_coverage_transition(
    kind: CoverageEvidenceKind,
) -> tuple[RawMarketDataRecord, CoverageTransitionId]:
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
    return raw_record, transition.coverage_transition_id


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

    transition_raw, transition_scope, transition_id = _normalization_failure_transition(
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
        coverage_transition_ids=(transition_id,),
    )
    assert transitioned.coverage_transition_ids == (transition_id,)
    with pytest.raises(ValueError, match="item count"):
        replace(transitioned, coverage_transition_ids=(transition_id, transition_id))

    assert MAX_DECODED_EVENTS_PER_RAW_RECORD > 1
    assert MAX_NORMALIZATION_OUTCOME_ITEMS > 1
    assert MAX_NORMALIZATION_EVIDENCE_ITEMS > 1
    assert MAX_COVERAGE_TRANSITION_REFERENCES > 1


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
            event_coverage=_coverage(spec=other_spec),
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
    for event_coverage in (
        EventCoverage(foreign.bronze_ingress, local.silver_normalization),
        EventCoverage(local.bronze_ingress, foreign.silver_normalization),
        foreign,
    ):
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
    raw_record, scope, transition_id = _normalization_failure_transition(
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
        coverage_transition_ids=(transition_id,),
    )

    assert outcome.coverage_transition_ids == (transition_id,)


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
    raw_record, spec, _ = _raw_record()
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

    with pytest.raises(ValueError, match="Silver-normalization"):
        NormalizationOutcome(
            normalization_run_id=NormalizationRunId("normalization-fixture"),
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            committed_materialization_keys=(),
            preindex_scope_binding=_preindex_scope_binding(raw_record),
            evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
            coverage_transition_ids=(transition.coverage_transition_id,),
        )


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
    raw_record, transition_id = _unrelated_coverage_transition(evidence_kind)

    with pytest.raises(ValueError, match=r"Silver-normalization|normalization failure"):
        NormalizationOutcome(
            normalization_run_id=NormalizationRunId("normalization-fixture"),
            raw_record_id=raw_record.raw_record_id,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit-fixture",
            frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
            decoded_event_count=None,
            raw_event_outcomes=(),
            committed_materialization_keys=(),
            preindex_scope_binding=_preindex_scope_binding(raw_record),
            evidence=(NormalizationEvidence.PROTOCOL_REJECTION,),
            coverage_transition_ids=(transition_id,),
        )


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
            coverage_transition_ids=(transition.coverage_transition_id,),
        )


def test_normalization_outcome_rejects_failure_transition_for_another_raw_record() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, _, transition_id = _normalization_failure_transition(
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
            coverage_transition_ids=(transition_id,),
        )


def test_normalization_outcome_rejects_contradictory_transition_failure_category() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, _, transition_id = _normalization_failure_transition(
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
            coverage_transition_ids=(transition_id,),
        )


def test_normalization_outcome_binds_failure_transition_to_exact_index() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, valid_transition_id = _normalization_failure_transition(
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
        coverage_transition_ids=(valid_transition_id,),
    )
    assert outcome.coverage_transition_ids == (valid_transition_id,)

    _, _, wrong_index_transition_id = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        category=NormalizationFailureCategory.DECODER_REJECTION,
        source_event_id=SourceEventId("source-fixture"),
    )
    with pytest.raises(ValueError, match="match one rejected frame index"):
        replace(outcome, coverage_transition_ids=(wrong_index_transition_id,))

    _, _, wrong_source_transition_id = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_event_id=SourceEventId("another-source-event"),
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    with pytest.raises(ValueError, match="source identity must exactly match"):
        replace(outcome, coverage_transition_ids=(wrong_source_transition_id,))


def test_indexed_normalization_failure_without_source_identity_binds_exact_lineage() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, transition_id = _normalization_failure_transition(
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
        coverage_transition_ids=(transition_id,),
    )

    assert json.loads(transition_id.value)[5:7] == [
        CoverageStatus.CONFIRMED_INCOMPLETE.value,
        CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE.value,
    ]
    assert outcome.raw_event_outcomes[0].logical_source_key is None


def test_indexed_normalization_failure_source_identity_is_nullable_but_exact() -> None:
    run_id = NormalizationRunId("normalization-fixture")
    raw_record, scope, without_source_transition_id = _normalization_failure_transition(
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
        transition_id: CoverageTransitionId,
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
            coverage_transition_ids=(transition_id,),
        )

    known_logical_source = LogicalSourceKey(
        raw_record.feed_product.feed_product_id,
        SourceEventId("source-fixture"),
    )

    with pytest.raises(ValueError, match="source identity must exactly match"):
        outcome_for(
            replace(no_logical_source, logical_source_key=known_logical_source),
            without_source_transition_id,
        )

    _, _, with_source_transition_id = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_event_id=SourceEventId("source-fixture"),
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    with pytest.raises(ValueError, match="source identity must exactly match"):
        outcome_for(no_logical_source, with_source_transition_id)

    unequal_logical_source = LogicalSourceKey(
        raw_record.feed_product.feed_product_id,
        SourceEventId("different-source-fixture"),
    )
    with pytest.raises(ValueError, match="source identity must exactly match"):
        outcome_for(
            replace(no_logical_source, logical_source_key=unequal_logical_source),
            with_source_transition_id,
        )

    _, _, wrong_index_transition_id = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=1,
        source_identity_established=False,
        category=NormalizationFailureCategory.DECODER_REJECTION,
    )
    with pytest.raises(ValueError, match="match one rejected frame index"):
        outcome_for(no_logical_source, wrong_index_transition_id)

    eth_instrument = Instrument(
        Venue.BINANCE,
        InstrumentType.SPOT,
        "ETH",
        "USDT",
        "ETHUSDT",
        "ETHUSDT",
    )
    _, _, wrong_scope_transition_id = _normalization_failure_transition(
        normalization_run_id=run_id,
        raw_event_index=0,
        source_identity_established=False,
        category=NormalizationFailureCategory.DECODER_REJECTION,
        canonical_instrument_id=eth_instrument.canonical_instrument_id,
    )
    with pytest.raises(ValueError, match="match one rejected frame index"):
        outcome_for(no_logical_source, wrong_scope_transition_id)


def test_normalization_outcome_binds_source_conflict_transition_to_exact_lineage() -> None:
    source_event_id = SourceEventId("source-conflict-fixture")
    raw_record, scope, valid_transition_id = _source_conflict_transition(
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
        coverage_transition_ids=(valid_transition_id,),
    )
    assert outcome.coverage_transition_ids == (valid_transition_id,)

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
        )

    _, _, wrong_source_transition_id = _source_conflict_transition(
        source_event_id=SourceEventId("another-source-event"),
        raw_event_index=0,
    )
    with pytest.raises(ValueError, match="match one conflicting frame index"):
        replace(outcome, coverage_transition_ids=(wrong_source_transition_id,))

    _, _, wrong_index_transition_id = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=1,
    )
    with pytest.raises(ValueError, match="match one conflicting frame index"):
        replace(outcome, coverage_transition_ids=(wrong_index_transition_id,))


@pytest.mark.parametrize("foreign_scope_kind", ["instrument", "subscription-spec"])
def test_source_conflict_transition_cannot_cross_event_scope(
    foreign_scope_kind: str,
) -> None:
    source_event_id = SourceEventId("scope-bound-source-conflict")
    raw_record, scope, transition_id = _source_conflict_transition(
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
        coverage_transition_ids=(transition_id,),
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
    _, _, foreign_transition_id = _source_conflict_transition(
        source_event_id=source_event_id,
        raw_event_index=0,
        scope_spec=foreign_spec,
        canonical_instrument_id=eth_instrument.canonical_instrument_id,
    )

    with pytest.raises(ValueError, match="match one conflicting frame index"):
        replace(outcome, coverage_transition_ids=(foreign_transition_id,))


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
    raw_record, scope, transition_id = _normalization_failure_transition(
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
            coverage_transition_ids=(transition_id,),
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

    assert envelope.envelope_schema_version == 3
