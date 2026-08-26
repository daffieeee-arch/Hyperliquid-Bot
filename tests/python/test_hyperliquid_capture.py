"""Pure tests for Hyperliquid Bronze lineage and outcome construction."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

import hyperliquid_bot.hyperliquid_capture as capture_module
from hyperliquid_bot.contracts import Instrument, InstrumentType, Venue
from hyperliquid_bot.data_provenance import (
    CollectorRunId,
    CoverageDomain,
    FrameKind,
    NormalizationRunId,
    RawMarketDataRecord,
    SourceEventId,
    SubscriptionAttemptIdentity,
    SubscriptionAttemptSnapshot,
    SubscriptionAttemptStatus,
    SubscriptionAttemptTransition,
)
from hyperliquid_bot.hyperliquid_capture import (
    HYPERLIQUID_TRADES_ADAPTER_CODE,
    HyperliquidSessionAttempts,
    ReceivedApplicationMessage,
    application_message_bytes,
    attempt_snapshot_for_coin,
    build_hyperliquid_capture_plan,
    build_raw_market_data_record,
    control_normalization_outcome,
    decoded_wire_payload_context,
    empty_trade_normalization_outcome,
    frame_normalization_outcome,
    full_trade_normalization_scope,
    new_hyperliquid_session_attempts,
    preindex_rejection_normalization_outcome,
    raw_event_outcome,
    raw_event_scope_binding,
    subscription_spec_for_coin,
    trade_normalization_scope_for_coin,
    transition_hyperliquid_attempt,
)
from hyperliquid_bot.market_event_v3 import (
    FrameNormalizationStatus,
    NormalizationEvidence,
    RawEventDisposition,
)

_RECEIVED_TIME = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_COLLECTOR_RUN = CollectorRunId("collector-run-test")
_NORMALIZATION_RUN = NormalizationRunId("normalization-run-test")


def _instrument(symbol: str) -> Instrument:
    return Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=(InstrumentType.SPOT if symbol == "@107" else InstrumentType.PERPETUAL),
        base_asset=("PURR" if symbol == "@107" else symbol.split(":")[-1]),
        quote_asset="USDC",
        venue_market_id=symbol,
        native_symbol=symbol,
    )


def _capture_fixture(
    *symbols: str,
) -> tuple[
    capture_module.HyperliquidCapturePlan,
    HyperliquidSessionAttempts,
    RawMarketDataRecord,
]:
    plan = build_hyperliquid_capture_plan(
        tuple(_instrument(symbol) for symbol in (symbols or ("BTC",)))
    )
    attempts = new_hyperliquid_session_attempts(
        plan,
        collector_run_id=_COLLECTOR_RUN,
        connection_ordinal=0,
    )
    record = build_raw_market_data_record(
        plan,
        attempts,
        collector_run_id=_COLLECTOR_RUN,
        ingress_ordinal=0,
        message=application_message_bytes('{"channel":"pong"}'),
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=100,
        collector_version="collector-v1",
        collector_commit="collector-commit",
    )
    return plan, attempts, record


@pytest.mark.parametrize(
    "message,frame_kind,expected",
    [
        ("", FrameKind.TEXT, b""),
        ("  λ\n", FrameKind.TEXT, "  λ\n".encode()),
        (b"\x00\xff\x80", FrameKind.BINARY, b"\x00\xff\x80"),
    ],
)
def test_application_message_conversion_is_byte_exact_without_reserialization(
    message: object,
    frame_kind: FrameKind,
    expected: bytes,
) -> None:
    converted = application_message_bytes(message)

    assert converted.frame_kind is frame_kind
    assert converted.application_message_bytes == expected
    if expected:
        assert expected not in repr(converted).encode()
    assert not hasattr(converted, "__dict__")
    with pytest.raises(FrozenInstanceError):
        converted.frame_kind = FrameKind.BINARY  # type: ignore[misc]


@pytest.mark.parametrize("message", (bytearray(b"x"), memoryview(b"x"), 1, True, None))
def test_application_message_conversion_requires_exact_recv_runtime_types(
    message: object,
) -> None:
    with pytest.raises(TypeError, match="unsupported runtime type"):
        application_message_bytes(message)


def test_received_text_contract_rejects_invalid_utf8_and_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="valid UTF-8"):
        ReceivedApplicationMessage(FrameKind.TEXT, b"\xff")
    assert (
        ReceivedApplicationMessage(FrameKind.BINARY, b"\xff").application_message_bytes == b"\xff"
    )
    monkeypatch.setattr(capture_module, "MAX_RAW_APPLICATION_MESSAGE_BYTES", 1)
    with pytest.raises(ValueError, match="size bound"):
        ReceivedApplicationMessage(FrameKind.BINARY, b"xx")


def test_plan_is_deterministic_exact_and_supports_all_selector_shapes() -> None:
    first = build_hyperliquid_capture_plan(
        (_instrument("xyz:XYZ100"), _instrument("BTC"), _instrument("@107"))
    )
    reordered = build_hyperliquid_capture_plan(
        (_instrument("@107"), _instrument("xyz:XYZ100"), _instrument("BTC"))
    )

    assert first == reordered
    assert first.adapter_feed_binding.adapter_code == HYPERLIQUID_TRADES_ADAPTER_CODE
    assert tuple(
        binding.source_selector.value for binding in first.subscription_plan.instrument_bindings
    ) == tuple(
        sorted(
            ("BTC", "xyz:XYZ100", "@107"),
            key=lambda symbol: next(
                binding.canonical_components()
                for binding in first.subscription_plan.instrument_bindings
                if binding.source_selector.value == symbol
            ),
        )
    )
    for symbol in ("BTC", "xyz:XYZ100", "@107"):
        spec = subscription_spec_for_coin(first, symbol)
        assert symbol in spec.subscription_spec_canonical_content


def test_plan_rejects_empty_wrong_venue_duplicate_and_subscription_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="built-in tuple"):
        build_hyperliquid_capture_plan([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not be empty"):
        build_hyperliquid_capture_plan(())
    wrong_venue = replace(_instrument("BTC"), venue=Venue.BINANCE)
    with pytest.raises(ValueError, match="all belong"):
        build_hyperliquid_capture_plan((wrong_venue,))
    with pytest.raises(ValueError, match="unique"):
        build_hyperliquid_capture_plan((_instrument("BTC"), _instrument("BTC")))
    monkeypatch.setattr(capture_module, "HYPERLIQUID_MAX_SUBSCRIPTIONS", 1)
    with pytest.raises(ValueError, match="subscription limit"):
        build_hyperliquid_capture_plan((_instrument("BTC"), _instrument("ETH")))


def test_session_attempts_are_complete_ordinal_zero_and_ack_race_safe() -> None:
    plan, pending, _record = _capture_fixture("BTC", "xyz:XYZ100", "@107")

    assert len(pending.snapshots) == 3
    assert all(
        item.attempt_status is SubscriptionAttemptStatus.PENDING for item in pending.snapshots
    )
    assert all(item.subscription_attempt.attempt_ordinal == 0 for item in pending.snapshots)
    started, started_transition = transition_hyperliquid_attempt(
        pending,
        subscription_spec=subscription_spec_for_coin(plan, "BTC"),
        requested_status=SubscriptionAttemptStatus.SEND_STARTED,
    )
    acknowledged, acknowledged_transition = transition_hyperliquid_attempt(
        started,
        subscription_spec=subscription_spec_for_coin(plan, "BTC"),
        requested_status=SubscriptionAttemptStatus.ACKNOWLEDGED,
    )
    duplicate_ack, duplicate_transition = transition_hyperliquid_attempt(
        acknowledged,
        subscription_spec=subscription_spec_for_coin(plan, "BTC"),
        requested_status=SubscriptionAttemptStatus.ACKNOWLEDGED,
    )

    assert type(started_transition) is SubscriptionAttemptTransition
    assert started_transition.previous_status is SubscriptionAttemptStatus.PENDING
    assert started_transition.new_status is SubscriptionAttemptStatus.SEND_STARTED
    assert acknowledged_transition.previous_status is SubscriptionAttemptStatus.SEND_STARTED
    assert acknowledged_transition.new_status is SubscriptionAttemptStatus.ACKNOWLEDGED
    assert duplicate_transition.previous_status is SubscriptionAttemptStatus.ACKNOWLEDGED
    assert duplicate_transition.new_status is SubscriptionAttemptStatus.ACKNOWLEDGED
    assert (
        attempt_snapshot_for_coin(plan, pending, "BTC").attempt_status
        is SubscriptionAttemptStatus.PENDING
    )
    assert (
        attempt_snapshot_for_coin(plan, duplicate_ack, "BTC").attempt_status
        is SubscriptionAttemptStatus.ACKNOWLEDGED
    )
    with pytest.raises(ValueError, match="transition"):
        transition_hyperliquid_attempt(
            acknowledged,
            subscription_spec=subscription_spec_for_coin(plan, "BTC"),
            requested_status=SubscriptionAttemptStatus.SENT,
        )


def test_direct_session_attempt_construction_rejects_incomplete_duplicate_and_nonzero() -> None:
    plan, attempts, _record = _capture_fixture("BTC", "ETH")
    with pytest.raises(ValueError, match="one ordinal-zero"):
        HyperliquidSessionAttempts(
            attempts.connection_session,
            plan.subscription_plan,
            attempts.snapshots[:1],
        )
    duplicate = (attempts.snapshots[0], attempts.snapshots[0])
    with pytest.raises(ValueError):
        HyperliquidSessionAttempts(
            attempts.connection_session,
            plan.subscription_plan,
            duplicate,
        )
    first = attempts.snapshots[0]
    ordinal_one = SubscriptionAttemptSnapshot(
        SubscriptionAttemptIdentity(
            attempts.connection_session,
            first.subscription_spec,
            1,
        ),
        SubscriptionAttemptStatus.PENDING,
    )
    replacement = tuple(
        ordinal_one if item.subscription_spec == first.subscription_spec else item
        for item in attempts.snapshots
    )
    with pytest.raises(ValueError, match="ordinal zero"):
        HyperliquidSessionAttempts(
            attempts.connection_session,
            plan.subscription_plan,
            replacement,
        )


def test_raw_record_preserves_bytes_snapshot_lineage_and_runwide_ordinal() -> None:
    plan, attempts, _record = _capture_fixture("BTC", "xyz:XYZ100")
    message = application_message_bytes(' { "key" : 1, "key" : 2 } ')
    record = build_raw_market_data_record(
        plan,
        attempts,
        collector_run_id=_COLLECTOR_RUN,
        ingress_ordinal=7,
        message=message,
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=777,
        collector_version="collector-v1",
        collector_commit="collector-commit",
    )

    assert record.application_message_bytes == b' { "key" : 1, "key" : 2 } '
    assert record.ingress_ordinal == 7
    assert record.subscription_attempt_snapshots == attempts.snapshots
    assert len(record.subscription_attempt_snapshots) == 2


def test_raw_and_indexed_helpers_reject_cross_plan_lineage() -> None:
    btc_plan, btc_attempts, btc_record = _capture_fixture("BTC")
    larger_plan = build_hyperliquid_capture_plan((_instrument("BTC"), _instrument("ETH")))
    larger_attempts = new_hyperliquid_session_attempts(
        larger_plan,
        collector_run_id=_COLLECTOR_RUN,
        connection_ordinal=0,
    )

    with pytest.raises(ValueError, match="capture subscription plan"):
        build_raw_market_data_record(
            larger_plan,
            btc_attempts,
            collector_run_id=_COLLECTOR_RUN,
            ingress_ordinal=1,
            message=application_message_bytes("{}"),
            received_time=_RECEIVED_TIME,
            received_monotonic_ns=1,
            collector_version="collector-v1",
            collector_commit="collector-commit",
        )
    with pytest.raises(ValueError, match="capture subscription plan"):
        decoded_wire_payload_context(larger_plan, btc_record, ("BTC",))

    larger_record = build_raw_market_data_record(
        larger_plan,
        larger_attempts,
        collector_run_id=_COLLECTOR_RUN,
        ingress_ordinal=1,
        message=application_message_bytes("{}"),
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=1,
        collector_version="collector-v1",
        collector_commit="collector-commit",
    )
    decoded = decoded_wire_payload_context(larger_plan, larger_record, ("BTC",))
    with pytest.raises(ValueError, match="capture subscription plan"):
        raw_event_scope_binding(
            btc_plan,
            larger_record,
            decoded,
            raw_event_index=0,
            coin="BTC",
        )


def test_scope_and_outcome_factories_bind_exact_indexed_and_full_plan_lineage() -> None:
    plan, pending, record = _capture_fixture("BTC", "xyz:XYZ100")
    acknowledged = pending
    for coin in ("BTC", "xyz:XYZ100"):
        acknowledged, _transition = transition_hyperliquid_attempt(
            acknowledged,
            subscription_spec=subscription_spec_for_coin(plan, coin),
            requested_status=SubscriptionAttemptStatus.SEND_STARTED,
        )
        acknowledged, _transition = transition_hyperliquid_attempt(
            acknowledged,
            subscription_spec=subscription_spec_for_coin(plan, coin),
            requested_status=SubscriptionAttemptStatus.ACKNOWLEDGED,
        )
    record = build_raw_market_data_record(
        plan,
        acknowledged,
        collector_run_id=_COLLECTOR_RUN,
        ingress_ordinal=2,
        message=application_message_bytes("{}"),
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=2,
        collector_version="collector-v1",
        collector_commit="collector-commit",
    )
    decoded = decoded_wire_payload_context(plan, record, ("xyz:XYZ100", "BTC"))
    first_scope = raw_event_scope_binding(
        plan,
        record,
        decoded,
        raw_event_index=0,
        coin="xyz:XYZ100",
    )
    second_scope = raw_event_scope_binding(
        plan,
        record,
        decoded,
        raw_event_index=1,
        coin="BTC",
    )

    assert (
        first_scope.subscription_spec_id
        == subscription_spec_for_coin(plan, "xyz:XYZ100").subscription_spec_id
    )
    assert (
        second_scope.subscription_spec_id
        == subscription_spec_for_coin(plan, "BTC").subscription_spec_id
    )
    assert first_scope.raw_event_index == 0
    assert second_scope.raw_event_index == 1
    assert (
        trade_normalization_scope_for_coin(plan, "BTC").domain
        is CoverageDomain.SILVER_NORMALIZATION
    )
    assert len(full_trade_normalization_scope(plan).subscription_spec_ids) == 2

    item = raw_event_outcome(
        raw_record=record,
        normalization_run_id=_NORMALIZATION_RUN,
        scope_binding=first_scope,
        source_event_id=SourceEventId("source-id-test"),
        disposition=RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
    )
    duplicate_frame = frame_normalization_outcome(
        raw_record=record,
        normalization_run_id=_NORMALIZATION_RUN,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit",
        frame_status=FrameNormalizationStatus.DUPLICATES_ONLY,
        decoded_event_count=1,
        raw_event_outcomes=(item,),
    )
    assert duplicate_frame.coverage_transition_ids == ()


def test_control_empty_and_preindex_factories_are_transition_free_and_deterministic() -> None:
    plan, _attempts, record = _capture_fixture("BTC", "xyz:XYZ100")
    control = control_normalization_outcome(
        record,
        normalization_run_id=_NORMALIZATION_RUN,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit",
    )
    empty = empty_trade_normalization_outcome(
        record,
        normalization_run_id=_NORMALIZATION_RUN,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit",
    )
    rejected = preindex_rejection_normalization_outcome(
        plan,
        record,
        normalization_run_id=_NORMALIZATION_RUN,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit",
        evidence=NormalizationEvidence.PROTOCOL_REJECTION,
    )

    assert control.frame_status is FrameNormalizationStatus.CONTROL_NO_EVENT
    assert empty.frame_status is FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME
    assert rejected.frame_status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING
    assert rejected.preindex_scope_binding is not None
    assert rejected.preindex_scope_binding.coverage_scope_id == (
        full_trade_normalization_scope(plan).coverage_scope_id
    )
    assert control.coverage_transition_ids == empty.coverage_transition_ids == ()
    assert rejected.coverage_transition_ids == ()
    assert rejected == preindex_rejection_normalization_outcome(
        plan,
        record,
        normalization_run_id=_NORMALIZATION_RUN,
        normalizer_version="normalizer-v1",
        normalizer_commit="normalizer-commit",
        evidence=NormalizationEvidence.PROTOCOL_REJECTION,
    )
