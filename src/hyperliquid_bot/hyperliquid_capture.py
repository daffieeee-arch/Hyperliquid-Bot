"""Pure Hyperliquid Bronze lineage and normalization-outcome construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .contracts import Instrument, Venue
from .data_provenance import (
    HYPERLIQUID_MAINNET_PUBLIC_TRADES,
    MAX_RAW_APPLICATION_MESSAGE_BYTES,
    CollectorRunId,
    ConnectionSessionIdentity,
    CoverageDomain,
    CoverageScope,
    EventActivationRequirement,
    FrameKind,
    InstrumentSubscriptionBinding,
    NormalizationBinding,
    NormalizationRunId,
    PublicConnectionOption,
    PublicConnectionOptionKind,
    PublicEndpointProfile,
    PublicSourceSelector,
    PublicSourceSelectorKind,
    PublicSubscriptionParameter,
    PublicSubscriptionParameterKind,
    RawMarketDataRecord,
    SourceEventId,
    SubscriptionAttemptIdentity,
    SubscriptionAttemptSnapshot,
    SubscriptionAttemptStatus,
    SubscriptionAttemptTransition,
    SubscriptionPlanIdentity,
    SubscriptionSpecIdentity,
    reduce_subscription_attempt_status,
)
from .market_event_v3 import (
    TRADE_EVENT_FAMILY_SCHEMA_VERSION,
    AdapterFeedBinding,
    DecodedEventSubscriptionBinding,
    DecodedWirePayloadContext,
    EventFamily,
    FrameNormalizationStatus,
    LogicalSourceKey,
    MaterializationKey,
    NormalizationEvidence,
    NormalizationOutcome,
    ObservationKey,
    PayloadType,
    RawEventDisposition,
    RawEventNormalizationOutcome,
    RawEventNormalizationScopeBinding,
    RawFrameNormalizationScopeBinding,
)

HYPERLIQUID_TRADES_ADAPTER_CODE = "hyperliquid-trades-v1"
HYPERLIQUID_MAX_SUBSCRIPTIONS = 1_000


@dataclass(frozen=True, slots=True)
class ReceivedApplicationMessage:
    """Exact successful ordinary-``recv`` application-message boundary."""

    frame_kind: FrameKind
    application_message_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.frame_kind) is not FrameKind:
            raise TypeError("frame_kind must be a FrameKind.")
        if type(self.application_message_bytes) is not bytes:
            raise TypeError("application_message_bytes must be built-in bytes.")
        if len(self.application_message_bytes) > MAX_RAW_APPLICATION_MESSAGE_BYTES:
            raise ValueError("application_message_bytes exceeds the Bronze record size bound.")
        if self.frame_kind is FrameKind.TEXT:
            valid_utf8 = True
            try:
                self.application_message_bytes.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                valid_utf8 = False
            if not valid_utf8:
                raise ValueError("TEXT application_message_bytes must be valid UTF-8.")


@dataclass(frozen=True, slots=True)
class HyperliquidCapturePlan:
    """Stable adapter and subscription lineage for one configured collector."""

    adapter_feed_binding: AdapterFeedBinding
    subscription_plan: SubscriptionPlanIdentity

    def __post_init__(self) -> None:
        if type(self.adapter_feed_binding) is not AdapterFeedBinding:
            raise TypeError("adapter_feed_binding must be an AdapterFeedBinding.")
        if type(self.subscription_plan) is not SubscriptionPlanIdentity:
            raise TypeError("subscription_plan must be a SubscriptionPlanIdentity.")
        if (
            self.subscription_plan.adapter_feed_binding_id
            != self.adapter_feed_binding.adapter_feed_binding_id
        ):
            raise ValueError("subscription plan must use the supplied adapter binding.")
        if (
            self.adapter_feed_binding.adapter_code != HYPERLIQUID_TRADES_ADAPTER_CODE
            or self.adapter_feed_binding.feed_product != HYPERLIQUID_MAINNET_PUBLIC_TRADES
            or self.adapter_feed_binding.venue is not Venue.HYPERLIQUID
            or self.adapter_feed_binding.event_activation_requirement
            is not EventActivationRequirement.ACKNOWLEDGED
            or self.subscription_plan.feed_product_id
            != HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id
        ):
            raise ValueError("capture plan must be the Hyperliquid mainnet public-trades binding.")


@dataclass(frozen=True, slots=True)
class HyperliquidSessionAttempts:
    """One session and its complete immutable current attempt snapshot table."""

    connection_session: ConnectionSessionIdentity
    subscription_plan: SubscriptionPlanIdentity
    snapshots: tuple[SubscriptionAttemptSnapshot, ...]

    def __post_init__(self) -> None:
        if type(self.connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")
        if type(self.subscription_plan) is not SubscriptionPlanIdentity:
            raise TypeError("subscription_plan must be a SubscriptionPlanIdentity.")
        if type(self.snapshots) is not tuple:
            raise TypeError("snapshots must be a built-in tuple.")
        if any(type(item) is not SubscriptionAttemptSnapshot for item in self.snapshots):
            raise TypeError("snapshots must contain SubscriptionAttemptSnapshot values.")
        expected = tuple(sorted(self.snapshots, key=_snapshot_sort_key))
        if self.snapshots != expected:
            raise ValueError("snapshots must be sorted by canonical spec and attempt identity.")
        if any(
            snapshot.subscription_attempt.connection_session != self.connection_session
            for snapshot in self.snapshots
        ):
            raise ValueError("every snapshot must belong to the supplied connection session.")
        if any(snapshot.subscription_attempt.attempt_ordinal != 0 for snapshot in self.snapshots):
            raise ValueError("collector subscription attempts must use ordinal zero.")
        expected_spec_ids = {
            spec.subscription_spec_id for spec in self.subscription_plan.subscription_specs
        }
        actual_spec_ids = {
            snapshot.subscription_spec.subscription_spec_id for snapshot in self.snapshots
        }
        actual_attempt_ids = {
            snapshot.subscription_attempt.subscription_attempt_id for snapshot in self.snapshots
        }
        if (
            actual_spec_ids != expected_spec_ids
            or len(self.snapshots) != len(expected_spec_ids)
            or len(actual_attempt_ids) != len(self.snapshots)
        ):
            raise ValueError("snapshots must contain one ordinal-zero attempt per plan spec.")


def application_message_bytes(message: object) -> ReceivedApplicationMessage:
    """Preserve ordinary-``recv`` TEXT/BINARY identity without reserialization."""

    if type(message) is str:
        return ReceivedApplicationMessage(FrameKind.TEXT, message.encode("utf-8"))
    if type(message) is bytes:
        return ReceivedApplicationMessage(FrameKind.BINARY, message)
    raise TypeError("successful WebSocket application message has an unsupported runtime type.")


def build_hyperliquid_capture_plan(
    instruments: tuple[Instrument, ...],
) -> HyperliquidCapturePlan:
    """Build one deterministic public-trades plan from exact native symbols."""

    if type(instruments) is not tuple:
        raise TypeError("instruments must be a built-in tuple.")
    if not instruments:
        raise ValueError("instruments must not be empty.")
    if len(instruments) > HYPERLIQUID_MAX_SUBSCRIPTIONS:
        raise ValueError("instruments exceed Hyperliquid's subscription limit.")
    if any(type(instrument) is not Instrument for instrument in instruments):
        raise TypeError("instruments must contain only Instrument values.")
    if any(instrument.venue is not Venue.HYPERLIQUID for instrument in instruments):
        raise ValueError("capture plan instruments must all belong to Hyperliquid.")
    symbols = tuple(instrument.native_symbol for instrument in instruments)
    if len(set(symbols)) != len(symbols):
        raise ValueError("capture plan native symbols must be unique.")

    adapter = AdapterFeedBinding(
        adapter_code=HYPERLIQUID_TRADES_ADAPTER_CODE,
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        venue=Venue.HYPERLIQUID,
        event_activation_requirement=EventActivationRequirement.ACKNOWLEDGED,
    )
    spec_by_symbol = {
        instrument.native_symbol: SubscriptionSpecIdentity(
            feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
            wire_method="subscribe",
            wire_subscription_type="trades",
            wire_parameters=(
                PublicSubscriptionParameter(
                    PublicSubscriptionParameterKind.HYPERLIQUID_COIN,
                    instrument.native_symbol,
                ),
            ),
        )
        for instrument in instruments
    }
    specs = tuple(sorted(spec_by_symbol.values(), key=lambda item: item.subscription_spec_id.value))
    instrument_bindings = tuple(
        sorted(
            (
                InstrumentSubscriptionBinding(
                    instrument=instrument,
                    source_selector=PublicSourceSelector(
                        PublicSourceSelectorKind.HYPERLIQUID_COIN,
                        instrument.native_symbol,
                    ),
                    subscription_spec=spec_by_symbol[instrument.native_symbol],
                    adapter_profile=HYPERLIQUID_TRADES_ADAPTER_CODE,
                )
                for instrument in instruments
            ),
            key=lambda item: item.canonical_components(),
        )
    )
    normalization_bindings = tuple(
        sorted(
            (
                NormalizationBinding(
                    subscription_spec_id=spec.subscription_spec_id,
                    adapter_profile=HYPERLIQUID_TRADES_ADAPTER_CODE,
                    event_family=EventFamily.TRADE.value,
                    event_family_schema_version=TRADE_EVENT_FAMILY_SCHEMA_VERSION,
                    payload_type=PayloadType.TRADE.value,
                )
                for spec in specs
            ),
            key=lambda item: item.subscription_spec_id.value,
        )
    )
    plan = SubscriptionPlanIdentity(
        feed_product_id=HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id,
        adapter_feed_binding_id=adapter.adapter_feed_binding_id,
        subscription_specs=specs,
        instrument_bindings=instrument_bindings,
        normalization_bindings=normalization_bindings,
        connection_wire_options=(
            PublicConnectionOption(
                PublicConnectionOptionKind.ENDPOINT_PROFILE,
                PublicEndpointProfile.HYPERLIQUID_PRODUCTION_MAINNET,
            ),
        ),
    )
    return HyperliquidCapturePlan(adapter, plan)


def subscription_spec_for_coin(
    capture_plan: HyperliquidCapturePlan,
    coin: str,
) -> SubscriptionSpecIdentity:
    """Resolve an exact Hyperliquid coin to exactly one plan wire spec."""

    if type(capture_plan) is not HyperliquidCapturePlan:
        raise TypeError("capture_plan must be a HyperliquidCapturePlan.")
    if type(coin) is not str:
        raise TypeError("coin must be a built-in string.")
    matches = tuple(
        binding.subscription_spec
        for binding in capture_plan.subscription_plan.instrument_bindings
        if binding.source_selector.value == coin
    )
    if len(matches) != 1:
        raise LookupError("coin must resolve to exactly one subscription spec.")
    return matches[0]


def instrument_binding_for_coin(
    capture_plan: HyperliquidCapturePlan,
    coin: str,
) -> InstrumentSubscriptionBinding:
    """Resolve an exact coin to its validated instrument and selector binding."""

    if type(capture_plan) is not HyperliquidCapturePlan:
        raise TypeError("capture_plan must be a HyperliquidCapturePlan.")
    if type(coin) is not str:
        raise TypeError("coin must be a built-in string.")
    matches = tuple(
        binding
        for binding in capture_plan.subscription_plan.instrument_bindings
        if binding.source_selector.value == coin
    )
    if len(matches) != 1:
        raise LookupError("coin must resolve to exactly one instrument binding.")
    return matches[0]


def new_hyperliquid_session_attempts(
    capture_plan: HyperliquidCapturePlan,
    *,
    collector_run_id: CollectorRunId,
    connection_ordinal: int,
) -> HyperliquidSessionAttempts:
    """Create every ordinal-zero PENDING attempt before a receiver starts."""

    if type(capture_plan) is not HyperliquidCapturePlan:
        raise TypeError("capture_plan must be a HyperliquidCapturePlan.")
    session = ConnectionSessionIdentity(collector_run_id, connection_ordinal)
    snapshots = tuple(
        sorted(
            (
                SubscriptionAttemptSnapshot(
                    SubscriptionAttemptIdentity(session, spec, 0),
                    SubscriptionAttemptStatus.PENDING,
                )
                for spec in capture_plan.subscription_plan.subscription_specs
            ),
            key=_snapshot_sort_key,
        )
    )
    return HyperliquidSessionAttempts(session, capture_plan.subscription_plan, snapshots)


def transition_hyperliquid_attempt(
    session_attempts: HyperliquidSessionAttempts,
    *,
    subscription_spec: SubscriptionSpecIdentity,
    requested_status: SubscriptionAttemptStatus,
) -> tuple[HyperliquidSessionAttempts, SubscriptionAttemptTransition]:
    """Apply and return one immutable ACK-race-safe attempt transition."""

    if type(session_attempts) is not HyperliquidSessionAttempts:
        raise TypeError("session_attempts must be a HyperliquidSessionAttempts.")
    if type(subscription_spec) is not SubscriptionSpecIdentity:
        raise TypeError("subscription_spec must be a SubscriptionSpecIdentity.")
    matches = tuple(
        index
        for index, snapshot in enumerate(session_attempts.snapshots)
        if snapshot.subscription_spec.subscription_spec_id == subscription_spec.subscription_spec_id
    )
    if len(matches) != 1:
        raise LookupError("subscription spec must match exactly one session attempt.")
    index = matches[0]
    transition, replacement = reduce_subscription_attempt_status(
        session_attempts.snapshots[index],
        requested_status,
    )
    snapshots = list(session_attempts.snapshots)
    snapshots[index] = replacement
    return (
        HyperliquidSessionAttempts(
            session_attempts.connection_session,
            session_attempts.subscription_plan,
            tuple(sorted(snapshots, key=_snapshot_sort_key)),
        ),
        transition,
    )


def attempt_snapshot_for_coin(
    capture_plan: HyperliquidCapturePlan,
    session_attempts: HyperliquidSessionAttempts,
    coin: str,
) -> SubscriptionAttemptSnapshot:
    """Return the one current snapshot for an exact configured coin."""

    spec = subscription_spec_for_coin(capture_plan, coin)
    matches = tuple(
        snapshot
        for snapshot in session_attempts.snapshots
        if snapshot.subscription_spec.subscription_spec_id == spec.subscription_spec_id
    )
    if len(matches) != 1:
        raise LookupError("coin must match exactly one current attempt snapshot.")
    return matches[0]


def build_raw_market_data_record(
    capture_plan: HyperliquidCapturePlan,
    session_attempts: HyperliquidSessionAttempts,
    *,
    collector_run_id: CollectorRunId,
    ingress_ordinal: int,
    message: ReceivedApplicationMessage,
    received_time: datetime,
    received_monotonic_ns: int,
    collector_version: str,
    collector_commit: str,
) -> RawMarketDataRecord:
    """Construct one complete raw record from explicit capture facts."""

    if type(capture_plan) is not HyperliquidCapturePlan:
        raise TypeError("capture_plan must be a HyperliquidCapturePlan.")
    if type(session_attempts) is not HyperliquidSessionAttempts:
        raise TypeError("session_attempts must be a HyperliquidSessionAttempts.")
    if type(message) is not ReceivedApplicationMessage:
        raise TypeError("message must be a ReceivedApplicationMessage.")
    if session_attempts.subscription_plan != capture_plan.subscription_plan:
        raise ValueError("session attempts must belong to the capture subscription plan.")
    expected_specs = {
        spec.subscription_spec_id for spec in capture_plan.subscription_plan.subscription_specs
    }
    actual_specs = {
        snapshot.subscription_spec.subscription_spec_id for snapshot in session_attempts.snapshots
    }
    if actual_specs != expected_specs or len(session_attempts.snapshots) != len(expected_specs):
        raise ValueError("raw capture requires exactly one snapshot for every plan spec.")
    return RawMarketDataRecord(
        feed_product=HYPERLIQUID_MAINNET_PUBLIC_TRADES,
        collector_run_id=collector_run_id,
        connection_session=session_attempts.connection_session,
        subscription_plan=capture_plan.subscription_plan,
        subscription_attempt_snapshots=session_attempts.snapshots,
        ingress_ordinal=ingress_ordinal,
        frame_kind=message.frame_kind,
        application_message_bytes=message.application_message_bytes,
        received_time=received_time,
        received_monotonic_ns=received_monotonic_ns,
        collector_version=collector_version,
        collector_commit=collector_commit,
    )


def full_trade_normalization_scope(capture_plan: HyperliquidCapturePlan) -> CoverageScope:
    """Return the complete trade-v2 plan scope used by pre-index rejection."""

    plan = capture_plan.subscription_plan
    return CoverageScope(
        domain=CoverageDomain.SILVER_NORMALIZATION,
        feed_product_id=plan.feed_product_id,
        subscription_spec_ids=tuple(
            sorted(
                (spec.subscription_spec_id for spec in plan.subscription_specs),
                key=lambda item: item.value,
            )
        ),
        canonical_instrument_ids=tuple(
            sorted(binding.canonical_instrument_id for binding in plan.instrument_bindings)
        ),
        event_family=EventFamily.TRADE.value,
        event_family_schema_version=TRADE_EVENT_FAMILY_SCHEMA_VERSION,
        payload_type=PayloadType.TRADE.value,
    )


def trade_normalization_scope_for_coin(
    capture_plan: HyperliquidCapturePlan,
    coin: str,
) -> CoverageScope:
    """Return the exact coin-specific Silver-normalization scope."""

    binding = instrument_binding_for_coin(capture_plan, coin)
    return CoverageScope(
        domain=CoverageDomain.SILVER_NORMALIZATION,
        feed_product_id=capture_plan.subscription_plan.feed_product_id,
        subscription_spec_ids=(binding.subscription_spec_id,),
        canonical_instrument_ids=(binding.canonical_instrument_id,),
        event_family=EventFamily.TRADE.value,
        event_family_schema_version=TRADE_EVENT_FAMILY_SCHEMA_VERSION,
        payload_type=PayloadType.TRADE.value,
    )


def decoded_wire_payload_context(
    capture_plan: HyperliquidCapturePlan,
    raw_record: RawMarketDataRecord,
    coins: tuple[str, ...],
) -> DecodedWirePayloadContext:
    """Bind every reliable wire index to its exact coin-specific attempt."""

    if type(coins) is not tuple:
        raise TypeError("coins must be a built-in tuple.")
    if raw_record.subscription_plan != capture_plan.subscription_plan:
        raise ValueError("raw record must belong to the capture subscription plan.")
    bindings: list[DecodedEventSubscriptionBinding] = []
    for index, coin in enumerate(coins):
        spec = subscription_spec_for_coin(capture_plan, coin)
        snapshots = tuple(
            snapshot
            for snapshot in raw_record.subscription_attempt_snapshots
            if snapshot.subscription_spec.subscription_spec_id == spec.subscription_spec_id
        )
        if len(snapshots) != 1:
            raise LookupError("decoded coin must match one raw subscription attempt.")
        bindings.append(
            DecodedEventSubscriptionBinding(
                raw_event_index=index,
                subscription_spec=spec,
                subscription_attempt=snapshots[0].subscription_attempt,
            )
        )
    return DecodedWirePayloadContext(
        raw_record_id=raw_record.raw_record_id,
        decoded_event_count=len(coins),
        event_bindings=tuple(bindings),
    )


def raw_event_scope_binding(
    capture_plan: HyperliquidCapturePlan,
    raw_record: RawMarketDataRecord,
    decoded_payload: DecodedWirePayloadContext,
    *,
    raw_event_index: int,
    coin: str,
) -> RawEventNormalizationScopeBinding:
    """Derive one exact raw/spec/attempt/instrument scope binding."""

    if raw_record.subscription_plan != capture_plan.subscription_plan:
        raise ValueError("raw record must belong to the capture subscription plan.")
    instrument_binding = instrument_binding_for_coin(capture_plan, coin)
    return RawEventNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        decoded_wire_payload=decoded_payload,
        raw_event_index=raw_event_index,
        source_selector=instrument_binding.source_selector,
        coverage_scope=trade_normalization_scope_for_coin(capture_plan, coin),
    )


def raw_event_outcome(
    *,
    raw_record: RawMarketDataRecord,
    normalization_run_id: NormalizationRunId,
    scope_binding: RawEventNormalizationScopeBinding,
    source_event_id: SourceEventId | None,
    disposition: RawEventDisposition,
    evidence: NormalizationEvidence | None = None,
) -> RawEventNormalizationOutcome:
    """Construct one closed index disposition without producing a v3 event."""

    observation = ObservationKey(raw_record.raw_record_id, scope_binding.raw_event_index)
    logical = (
        LogicalSourceKey(raw_record.feed_product.feed_product_id, source_event_id)
        if source_event_id is not None
        else None
    )
    materialization = None
    if disposition is RawEventDisposition.MATERIALIZED_NEW:
        materialization = MaterializationKey(
            normalization_run_id=normalization_run_id,
            observation_key=observation,
            event_family=EventFamily.TRADE,
            event_family_schema_version=TRADE_EVENT_FAMILY_SCHEMA_VERSION,
            payload_type=PayloadType.TRADE,
        )
    return RawEventNormalizationOutcome(
        observation_key=observation,
        normalization_scope_binding=scope_binding,
        disposition=disposition,
        logical_source_key=logical,
        materialization_key=materialization,
        evidence=evidence,
    )


def frame_normalization_outcome(
    *,
    raw_record: RawMarketDataRecord,
    normalization_run_id: NormalizationRunId,
    normalizer_version: str,
    normalizer_commit: str,
    frame_status: FrameNormalizationStatus,
    decoded_event_count: int | None,
    raw_event_outcomes: tuple[RawEventNormalizationOutcome, ...] = (),
    evidence: tuple[NormalizationEvidence, ...] = (),
    preindex_scope_binding: RawFrameNormalizationScopeBinding | None = None,
) -> NormalizationOutcome:
    """Construct one frame-atomic outcome with no runtime coverage transition."""

    return NormalizationOutcome(
        normalization_run_id=normalization_run_id,
        raw_record_id=raw_record.raw_record_id,
        normalizer_version=normalizer_version,
        normalizer_commit=normalizer_commit,
        frame_status=frame_status,
        decoded_event_count=decoded_event_count,
        raw_event_outcomes=raw_event_outcomes,
        committed_materialization_keys=tuple(
            outcome.materialization_key
            for outcome in raw_event_outcomes
            if outcome.materialization_key is not None
        ),
        preindex_scope_binding=preindex_scope_binding,
        evidence=evidence,
        coverage_transition_ids=(),
    )


def control_normalization_outcome(
    raw_record: RawMarketDataRecord,
    *,
    normalization_run_id: NormalizationRunId,
    normalizer_version: str,
    normalizer_commit: str,
) -> NormalizationOutcome:
    """Construct an outcome for a recognized control message."""

    return frame_normalization_outcome(
        raw_record=raw_record,
        normalization_run_id=normalization_run_id,
        normalizer_version=normalizer_version,
        normalizer_commit=normalizer_commit,
        frame_status=FrameNormalizationStatus.CONTROL_NO_EVENT,
        decoded_event_count=None,
    )


def empty_trade_normalization_outcome(
    raw_record: RawMarketDataRecord,
    *,
    normalization_run_id: NormalizationRunId,
    normalizer_version: str,
    normalizer_commit: str,
) -> NormalizationOutcome:
    """Construct an outcome for a valid empty trades array."""

    return frame_normalization_outcome(
        raw_record=raw_record,
        normalization_run_id=normalization_run_id,
        normalizer_version=normalizer_version,
        normalizer_commit=normalizer_commit,
        frame_status=FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME,
        decoded_event_count=0,
    )


def preindex_rejection_normalization_outcome(
    capture_plan: HyperliquidCapturePlan,
    raw_record: RawMarketDataRecord,
    *,
    normalization_run_id: NormalizationRunId,
    normalizer_version: str,
    normalizer_commit: str,
    evidence: NormalizationEvidence,
) -> NormalizationOutcome:
    """Bind an unindexed rejection to the complete relevant trade-v2 plan scope."""

    scope_binding = RawFrameNormalizationScopeBinding.from_raw_record(
        raw_record=raw_record,
        coverage_scope=full_trade_normalization_scope(capture_plan),
    )
    return frame_normalization_outcome(
        raw_record=raw_record,
        normalization_run_id=normalization_run_id,
        normalizer_version=normalizer_version,
        normalizer_commit=normalizer_commit,
        frame_status=FrameNormalizationStatus.REJECTED_BEFORE_INDEXING,
        decoded_event_count=None,
        evidence=(evidence,),
        preindex_scope_binding=scope_binding,
    )


def _snapshot_sort_key(snapshot: SubscriptionAttemptSnapshot) -> tuple[str, str]:
    return (
        snapshot.subscription_spec.subscription_spec_id.value,
        snapshot.subscription_attempt.subscription_attempt_id.value,
    )
