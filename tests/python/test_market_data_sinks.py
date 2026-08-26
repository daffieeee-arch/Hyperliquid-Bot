"""Pure tests for storage-neutral market-data sink contracts."""

import json
from dataclasses import FrozenInstanceError, fields

import pytest

from hyperliquid_bot.data_provenance import (
    HYPERLIQUID_MAINNET_PUBLIC_TRADES,
    CollectorRunId,
    ConnectionSessionIdentity,
    RawRecordId,
)
from hyperliquid_bot.market_data_sinks import (
    NormalizationOutcomeAcceptance,
    NormalizationOutcomeRejected,
    NormalizationOutcomeSink,
    RawRecordAcceptance,
    RawRecordRejected,
    RawRecordSink,
    SinkDestinationId,
    SinkFailureCategory,
)
from hyperliquid_bot.market_event_v3 import NormalizationOutcomeId


def _raw_record_id() -> RawRecordId:
    run_id = CollectorRunId("collector-run-test")
    session = ConnectionSessionIdentity(run_id, 0)
    return RawRecordId(
        json.dumps(
            (
                "raw-record-v1",
                HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id.value,
                run_id.value,
                session.connection_session_id.value,
                0,
                "text",
                "0" * 64,
            ),
            separators=(",", ":"),
        )
    )


def _normalization_outcome_id() -> NormalizationOutcomeId:
    return NormalizationOutcomeId(
        json.dumps(
            (
                "normalization-outcome-v1",
                "normalization-run-test",
                _raw_record_id().value,
                "normalizer-v1",
                "normalizer-commit",
                "control_no_event",
                None,
                "0" * 64,
            ),
            separators=(",", ":"),
        )
    )


def test_destination_id_is_bounded_hidden_immutable_and_hashable() -> None:
    destination = SinkDestinationId("bronze.memory-test")

    assert destination == SinkDestinationId("bronze.memory-test")
    assert hash(destination) == hash(SinkDestinationId("bronze.memory-test"))
    assert "bronze.memory-test" not in repr(destination)
    assert tuple(item.name for item in fields(destination)) == ("value",)
    assert not hasattr(destination, "__dict__")
    with pytest.raises(FrozenInstanceError):
        destination.value = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value,error",
    [
        ("", ValueError),
        ("UPPER", ValueError),
        (" space", ValueError),
        ("private/value", ValueError),
        ("a" * 129, ValueError),
        (1, TypeError),
        (True, TypeError),
    ],
)
def test_destination_id_fails_closed(value: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        SinkDestinationId(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "base",
    (SinkDestinationId, RawRecordAcceptance, NormalizationOutcomeAcceptance),
)
def test_sink_value_contracts_are_closed_against_subclass_bypass(base: type[object]) -> None:
    with pytest.raises(TypeError, match="subclass definition"):

        class _Bypass(base):  # type: ignore[misc,valid-type]
            pass


def test_acceptances_echo_exact_typed_identity_and_destination() -> None:
    destination = SinkDestinationId("audit.test")
    full_integrity = "1" * 64
    raw_acceptance = RawRecordAcceptance(_raw_record_id(), full_integrity, destination)
    outcome_acceptance = NormalizationOutcomeAcceptance(
        _normalization_outcome_id(),
        destination,
    )

    assert raw_acceptance.raw_record_id == _raw_record_id()
    assert raw_acceptance.full_record_integrity_sha256 == full_integrity
    assert raw_acceptance.destination_id is destination
    assert outcome_acceptance.normalization_outcome_id == _normalization_outcome_id()
    assert outcome_acceptance.destination_id is destination
    assert not hasattr(raw_acceptance, "__dict__")
    assert not hasattr(outcome_acceptance, "__dict__")


@pytest.mark.parametrize(
    "factory",
    (
        lambda: RawRecordAcceptance(
            object(),  # type: ignore[arg-type]
            "1" * 64,
            SinkDestinationId("raw.test"),
        ),
        lambda: RawRecordAcceptance(
            _raw_record_id(),
            object(),  # type: ignore[arg-type]
            SinkDestinationId("raw.test"),
        ),
        lambda: RawRecordAcceptance(
            _raw_record_id(),
            "1" * 64,
            object(),  # type: ignore[arg-type]
        ),
        lambda: NormalizationOutcomeAcceptance(
            object(),  # type: ignore[arg-type]
            SinkDestinationId("outcome.test"),
        ),
        lambda: NormalizationOutcomeAcceptance(
            _normalization_outcome_id(),
            object(),  # type: ignore[arg-type]
        ),
    ),
)
def test_acceptances_reject_wrong_runtime_types(factory: object) -> None:
    with pytest.raises(TypeError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize("digest", ("", "A" * 64, "0" * 63, "g" * 64))
def test_raw_acceptance_rejects_noncanonical_full_record_digest(digest: str) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        RawRecordAcceptance(
            _raw_record_id(),
            digest,
            SinkDestinationId("raw.test"),
        )


@pytest.mark.parametrize("rejection", (RawRecordRejected, NormalizationOutcomeRejected))
def test_explicit_rejections_are_closed_argumentless_signals(
    rejection: type[Exception],
) -> None:
    assert rejection().args == ()
    with pytest.raises(TypeError):
        rejection("untrusted marker")
    with pytest.raises(TypeError, match="subclass definition"):

        class _Derived(rejection):  # type: ignore[misc,valid-type]
            pass


def test_sink_failure_categories_are_closed_bounded_codes() -> None:
    assert {item.value for item in SinkFailureCategory} == {
        "raw-explicit-rejection",
        "raw-acceptance-timeout",
        "raw-acceptance-ambiguous",
        "raw-acceptance-invalid",
        "outcome-explicit-rejection",
        "outcome-acceptance-timeout",
        "outcome-acceptance-ambiguous",
        "outcome-acceptance-invalid",
        "outcome-close-timeout",
        "outcome-close-failure",
        "raw-close-timeout",
        "raw-close-failure",
    }


def test_in_process_sink_protocols_require_cancellation_cooperation() -> None:
    for protocol in (RawRecordSink, NormalizationOutcomeSink):
        documentation = protocol.__doc__ or ""
        assert "cancellation-cooperative" in documentation
        assert "cannot forcibly terminate" in documentation
