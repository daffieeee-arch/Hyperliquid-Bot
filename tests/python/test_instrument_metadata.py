"""Deterministic tests for dormant point-in-time instrument metadata contracts."""

import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
from typing import cast

import pytest

import hyperliquid_bot.instrument_metadata as instrument_metadata_module
from hyperliquid_bot.contracts import Instrument, InstrumentType, Venue
from hyperliquid_bot.data_provenance import (
    MAX_CANONICAL_INSTRUMENT_ID_LENGTH,
    MAX_INSTRUMENT_PRICE_REFERENCES,
    MAX_METADATA_CATALOGUE_ITEMS,
    InstrumentMetadataObservationId,
    InstrumentSpecificationId,
    MetadataAuthorityId,
    RawRecordId,
)
from hyperliquid_bot.instrument_metadata import (
    ContractForm,
    InstrumentMetadataObservation,
    InstrumentSpecification,
    MetadataEffectiveBasis,
    PriceReference,
    PriceReferenceRole,
    QuantityUnit,
    ResolvedInstrumentMetadata,
    canonical_decimal,
    select_instrument_metadata,
    validate_instrument_metadata_catalogue,
)


def _utc(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, 0, 0, 123456, tzinfo=UTC)


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


def _spot(*, native_symbol: str = "BTCUSDT") -> Instrument:
    return Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id="BTCUSDT",
        native_symbol=native_symbol,
    )


def _future(*, native_symbol: str = "BTCUSDT_260925") -> Instrument:
    return Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.FUTURE,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id="BTCUSDT_260925",
        native_symbol=native_symbol,
        contract_expiry=date(2026, 9, 25),
    )


def _spot_specification() -> InstrumentSpecification:
    return InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )


def _future_specification(*, multiplier: str = "0.00100") -> InstrumentSpecification:
    return InstrumentSpecification(
        instrument=_future(),
        quantity_unit=QuantityUnit.CONTRACTS,
        contract_form=ContractForm.LINEAR,
        contract_multiplier=Decimal(multiplier),
        multiplier_asset="BTC",
        settlement_asset="USDT",
        last_trading_time=datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
        settlement_time=datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
        price_references=(
            PriceReference(PriceReferenceRole.ORACLE, "BINANCE-BTC-ORACLE"),
            PriceReference(PriceReferenceRole.INDEX, "BINANCE-BTC-INDEX"),
            PriceReference(PriceReferenceRole.MARK, "BINANCE-BTC-MARK"),
        ),
    )


def _observation(
    specification: InstrumentSpecification,
    *,
    authority: str = "binance-production-catalog-v1",
    effective_from: datetime | None = None,
    observed_at: datetime | None = None,
    basis: MetadataEffectiveBasis = MetadataEffectiveBasis.SOURCE_DECLARED,
    source_declared_until: datetime | None = None,
    raw_record_id: RawRecordId | None = None,
) -> InstrumentMetadataObservation:
    return InstrumentMetadataObservation(
        canonical_instrument_id=specification.instrument.canonical_instrument_id,
        instrument_specification_id=specification.instrument_specification_id,
        metadata_authority_id=MetadataAuthorityId(authority),
        effective_from=effective_from or _utc(1),
        effective_basis=basis,
        observed_at=observed_at or _utc(1, 1),
        source_declared_until=source_declared_until,
        raw_record_id=raw_record_id,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1"), "1"),
        (Decimal("1.0"), "1"),
        (Decimal("1.2300"), "1.23"),
        (Decimal("0.00100"), "0.001"),
        (Decimal("1000"), "1000"),
        (Decimal("1E+2"), "100"),
        (Decimal("-0"), "0"),
        (Decimal("-0.000"), "0"),
        (Decimal("-12.3400"), "-12.34"),
        (Decimal("123000E-3"), "123"),
    ],
)
def test_canonical_decimal_has_exact_fixed_point_cases(value: Decimal, expected: str) -> None:
    assert canonical_decimal(value) == expected


def test_canonical_decimal_is_independent_of_active_context() -> None:
    value = Decimal("123456789.123456789000")

    with localcontext(Context(prec=1, Emin=-1, Emax=1)):
        assert canonical_decimal(value) == "123456789.123456789"


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param("1", id="string"),
        pytest.param(1, id="integer"),
        pytest.param(1.0, id="float"),
        pytest.param(True, id="boolean"),
    ],
)
def test_canonical_decimal_requires_an_exact_decimal(invalid: object) -> None:
    with pytest.raises(TypeError):
        canonical_decimal(invalid)


@pytest.mark.parametrize(
    "invalid",
    [
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1E-129"),
        Decimal("1E+129"),
        Decimal("1" * 129),
        Decimal("-" + ("9" * 128) + "E+128"),
    ],
)
def test_canonical_decimal_rejects_nonfinite_or_bounded_expansion_violations(
    invalid: Decimal,
) -> None:
    with pytest.raises(ValueError):
        canonical_decimal(invalid)


def test_signed_zero_with_out_of_range_exponent_fails_before_zero_canonicalization() -> None:
    with pytest.raises(ValueError):
        canonical_decimal(Decimal("-0E+129"))


def test_positive_exponent_bound_counts_coefficient_digits_before_expansion() -> None:
    specification = _future_specification(multiplier="1E+128")

    assert canonical_decimal(specification.contract_multiplier) == "1" + ("0" * 128)
    assert '"' + ("1" + ("0" * 128)) + '"' in (
        specification.instrument_specification_canonical_content
    )


def test_decimal_bounds_accept_exact_minimum_exponent_and_maximum_text_length() -> None:
    assert canonical_decimal(Decimal("1E-128")) == "0." + ("0" * 127) + "1"

    maximum_text = canonical_decimal(Decimal(("9" * 128) + "E+128"))
    assert len(maximum_text) == 256
    assert maximum_text == ("9" * 128) + ("0" * 128)


def test_spot_specification_has_byte_exact_content_addressed_identity() -> None:
    specification = _spot_specification()

    assert specification.instrument_specification_canonical_content == (
        '["instrument-specification-content-v1",'
        '"[\\"instrument-v1\\",\\"binance\\",\\"spot\\",\\"BTCUSDT\\",'
        '\\"BTC\\",\\"USDT\\",null]",'
        '"base-asset","not-applicable",null,null,null,null,null,null,[]]'
    )
    assert specification.instrument_specification_id.value == (
        '["instrument-specification-v1",'
        '"6143639f50df513b389cc729088c4edba42dacb0c068557d242570f707fd2570",'
        '179,"2cf820e3a23d709910ae7e516e4a7fdd42da0d466f51497b3d4e15a6b63edb44"]'
    )
    assert specification.contract_expiry is None
    assert type(specification.instrument_specification_id) is InstrumentSpecificationId


def test_future_specification_serializes_decimal_dates_times_and_sorted_references() -> None:
    specification = _future_specification()

    assert specification.contract_multiplier == Decimal("0.00100")
    assert specification.contract_expiry == date(2026, 9, 25)
    assert [reference.role for reference in specification.price_references] == [
        PriceReferenceRole.INDEX,
        PriceReferenceRole.MARK,
        PriceReferenceRole.ORACLE,
    ]
    assert specification.instrument_specification_canonical_content == (
        '["instrument-specification-content-v1",'
        '"[\\"instrument-v1\\",\\"binance\\",\\"future\\",'
        '\\"BTCUSDT_260925\\",\\"BTC\\",\\"USDT\\",\\"2026-09-25\\"]",'
        '"contracts","linear","0.001","BTC","USDT","2026-09-25",'
        '"2026-09-25T08:00:00.000000Z","2026-09-25T09:00:00.000000Z",'
        '[["index","BINANCE-BTC-INDEX"],["mark","BINANCE-BTC-MARK"],'
        '["oracle","BINANCE-BTC-ORACLE"]]]'
    )
    assert specification.instrument_specification_id.value == (
        '["instrument-specification-v1",'
        '"1b54e6f1b6859bf47179be555ead67bbc2835b1262284dda877591b36e7d4976",'
        '342,"7e1df708ce45e8741f193526a4018cec552569b19e642e542507e14d2f6cf541"]'
    )


def test_stored_specification_recomputes_content_digest_and_bounded_identity() -> None:
    specification = _future_specification()

    restored = InstrumentSpecification.from_stored(
        instrument_specification_canonical_content=(
            specification.instrument_specification_canonical_content
        ),
        expected_instrument_specification_id=specification.instrument_specification_id,
        native_symbol=specification.instrument.native_symbol,
    )
    assert restored == specification
    assert len(specification.instrument_specification_id.value) < 512

    changed = json.loads(specification.instrument_specification_canonical_content)
    changed[4] = "1"
    with pytest.raises(ValueError, match="integrity verification"):
        InstrumentSpecification.from_stored(
            instrument_specification_canonical_content=json.dumps(
                changed, ensure_ascii=True, separators=(",", ":")
            ),
            expected_instrument_specification_id=specification.instrument_specification_id,
            native_symbol=specification.instrument.native_symbol,
        )


def test_malformed_stored_content_is_absent_from_bounded_exception_surface() -> None:
    marker = "persisted-specification-private-sentinel"
    malformed = f'["{marker}"'
    specification = _spot_specification()

    with pytest.raises(ValueError, match="canonical compact JSON array") as captured:
        InstrumentSpecification.from_stored(
            instrument_specification_canonical_content=malformed,
            expected_instrument_specification_id=specification.instrument_specification_id,
            native_symbol=specification.instrument.native_symbol,
        )

    _assert_bounded_exception_surface_excludes(captured.value, marker, malformed)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("last_trading_time", "settlement_time"),
    [
        (
            datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
            datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
            datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
        ),
    ],
)
def test_derivative_settlement_may_follow_or_equal_last_trading_time(
    last_trading_time: datetime,
    settlement_time: datetime,
) -> None:
    specification = replace(
        _future_specification(),
        last_trading_time=last_trading_time,
        settlement_time=settlement_time,
    )

    assert specification.settlement_time >= specification.last_trading_time  # type: ignore[operator]
    assert InstrumentSpecificationId(specification.instrument_specification_id.value) == (
        specification.instrument_specification_id
    )


def test_derivative_settlement_cannot_precede_last_trading_time() -> None:
    last = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    settlement = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="must not precede"):
        replace(
            _future_specification(),
            last_trading_time=last,
            settlement_time=settlement,
        )

    stored = _future_specification()
    components = json.loads(stored.instrument_specification_canonical_content)
    components[8] = "2026-09-25T09:00:00.000000Z"
    components[9] = "2026-09-25T08:00:00.000000Z"
    with pytest.raises(ValueError, match="must not precede"):
        InstrumentSpecification.from_stored(
            instrument_specification_canonical_content=json.dumps(
                components, ensure_ascii=True, separators=(",", ":")
            ),
            expected_instrument_specification_id=stored.instrument_specification_id,
            native_symbol=stored.instrument.native_symbol,
        )


def test_persisted_specification_id_revalidates_decimal_source_bounds() -> None:
    valid = _future_specification(multiplier=("9" * 128) + "E+128")
    assert (
        InstrumentSpecification.from_stored(
            instrument_specification_canonical_content=(
                valid.instrument_specification_canonical_content
            ),
            expected_instrument_specification_id=valid.instrument_specification_id,
            native_symbol=valid.instrument.native_symbol,
        )
        == valid
    )

    for invalid in (
        "9" * 129,
        "0." + ("0" * 128) + "1",
    ):
        stored = _future_specification()
        components = json.loads(stored.instrument_specification_canonical_content)
        components[4] = invalid
        with pytest.raises(ValueError):
            InstrumentSpecification.from_stored(
                instrument_specification_canonical_content=json.dumps(
                    components, ensure_ascii=True, separators=(",", ":")
                ),
                expected_instrument_specification_id=stored.instrument_specification_id,
                native_symbol=stored.instrument.native_symbol,
            )


@pytest.mark.parametrize(
    "components",
    [
        (
            '["instrument-v1","binance","spot","BTCUSDT","BTC","USDT",null]',
            "contracts",
            "linear",
            "1",
            "BTC",
            "USDT",
            None,
            None,
            None,
        ),
        (
            '["instrument-v1","hyperliquid","perpetual","BTC","BTC","USDC",null]',
            "contracts",
            "linear",
            None,
            "BTC",
            "USDC",
            None,
            None,
            None,
        ),
        (
            '["instrument-v1","hyperliquid","perpetual","BTC","BTC","USDC",null]',
            "base-asset",
            "linear",
            "1",
            "BTC",
            "USDC",
            None,
            None,
            None,
        ),
        (
            '["instrument-v1","binance","spot","BTCUSDT","BTC","USDT",null]',
            "base-asset",
            "not-applicable",
            None,
            None,
            "USDT",
            None,
            None,
            None,
        ),
        (
            '["instrument-v1","binance","spot","BTCUSDT","BTC","USDT",null]',
            "base-asset",
            "not-applicable",
            None,
            None,
            None,
            "2026-09-25",
            None,
            None,
        ),
    ],
)
def test_persisted_specification_ids_revalidate_cross_field_semantics(
    components: tuple[str | None, ...],
) -> None:
    (
        instrument_id,
        quantity,
        form,
        multiplier,
        multiplier_asset,
        settlement,
        expiry,
        last,
        settled,
    ) = components
    canonical_content = json.dumps(
        [
            "instrument-specification-content-v1",
            instrument_id,
            quantity,
            form,
            multiplier,
            multiplier_asset,
            settlement,
            expiry,
            last,
            settled,
            [],
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError):
        InstrumentSpecification.from_stored(
            instrument_specification_canonical_content=canonical_content,
            expected_instrument_specification_id=(
                _spot_specification().instrument_specification_id
            ),
            native_symbol="BTC",
        )


def test_native_symbol_alias_does_not_change_specification_identity() -> None:
    first = _spot_specification()
    second = InstrumentSpecification(
        instrument=_spot(native_symbol="btc-usdt-adapter-alias"),
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )

    assert first.instrument.native_symbol != second.instrument.native_symbol
    assert first.instrument_specification_id == second.instrument_specification_id
    assert first == second
    assert hash(first) == hash(second)


def test_specification_is_frozen_slotted_and_hashable() -> None:
    specification = _spot_specification()

    assert "__dict__" not in {item.name for item in fields(specification)}
    assert not hasattr(specification, "__dict__")
    assert hash(specification) == hash(_spot_specification())
    with pytest.raises(FrozenInstanceError):
        specification.quantity_unit = QuantityUnit.QUOTE_ASSET  # type: ignore[misc]


@pytest.mark.parametrize("tampered_field", ["canonical_instrument_id", "base_asset"])
def test_specification_revalidates_exact_instrument_identity(tampered_field: str) -> None:
    instrument = _spot()
    if tampered_field == "canonical_instrument_id":
        object.__setattr__(instrument, tampered_field, "invalid-canonical-instrument")
    else:
        object.__setattr__(instrument, tampered_field, "ETH")

    with pytest.raises(ValueError):
        InstrumentSpecification(
            instrument=instrument,
            quantity_unit=QuantityUnit.BASE_ASSET,
            contract_form=ContractForm.NOT_APPLICABLE,
        )


def test_contract_expiry_cannot_be_supplied_separately_from_instrument() -> None:
    with pytest.raises(TypeError, match="contract_expiry"):
        InstrumentSpecification(  # type: ignore[call-arg]
            instrument=_future(),
            quantity_unit=QuantityUnit.CONTRACTS,
            contract_form=ContractForm.LINEAR,
            contract_multiplier=Decimal("1"),
            multiplier_asset="BTC",
            settlement_asset="USDT",
            contract_expiry=date(2026, 9, 26),
        )


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"instrument": object()}, TypeError),
        ({"quantity_unit": "base-asset"}, TypeError),
        ({"contract_form": "not-applicable"}, TypeError),
        ({"contract_multiplier": "1"}, TypeError),
        ({"contract_multiplier": Decimal("NaN")}, ValueError),
        ({"contract_multiplier": Decimal("0")}, ValueError),
        ({"contract_multiplier": Decimal("-1")}, ValueError),
        ({"multiplier_asset": ""}, ValueError),
        ({"last_trading_time": "2026-09-25T08:00:00Z"}, TypeError),
        ({"last_trading_time": datetime(2026, 9, 25, 8, 0)}, ValueError),
        (
            {"last_trading_time": datetime(2026, 9, 25, 8, 0, tzinfo=timezone(timedelta(hours=1)))},
            ValueError,
        ),
        ({"price_references": []}, TypeError),
        ({"price_references": (object(),)}, TypeError),
    ],
)
def test_specification_revalidates_invalid_runtime_values(
    changes: dict[str, object], error: type[Exception]
) -> None:
    arguments: dict[str, object] = {
        "instrument": _future(),
        "quantity_unit": QuantityUnit.CONTRACTS,
        "contract_form": ContractForm.LINEAR,
        "contract_multiplier": Decimal("1"),
        "multiplier_asset": "BTC",
        "settlement_asset": "USDT",
    }
    arguments.update(changes)

    with pytest.raises(error):
        InstrumentSpecification(**arguments)  # type: ignore[arg-type]


def test_spot_and_derivative_semantic_invariants_fail_closed() -> None:
    with pytest.raises(ValueError, match="spot instruments require"):
        InstrumentSpecification(
            instrument=_spot(),
            quantity_unit=QuantityUnit.BASE_ASSET,
            contract_form=ContractForm.LINEAR,
        )
    with pytest.raises(ValueError, match="settlement asset"):
        InstrumentSpecification(
            instrument=_future(),
            quantity_unit=QuantityUnit.BASE_ASSET,
            contract_form=ContractForm.LINEAR,
        )
    with pytest.raises(ValueError, match="contract quantities require"):
        InstrumentSpecification(
            instrument=_future(),
            quantity_unit=QuantityUnit.CONTRACTS,
            contract_form=ContractForm.LINEAR,
            settlement_asset="USDT",
        )


def test_duplicate_price_references_fail_closed() -> None:
    duplicate = PriceReference(PriceReferenceRole.INDEX, "BTC-INDEX")
    with pytest.raises(ValueError, match="unique"):
        InstrumentSpecification(
            instrument=_spot(),
            quantity_unit=QuantityUnit.BASE_ASSET,
            contract_form=ContractForm.NOT_APPLICABLE,
            price_references=(duplicate, duplicate),
        )


def test_price_reference_owner_accepts_its_literal_bound_and_rejects_plus_one() -> None:
    roles = tuple(PriceReferenceRole)
    references = tuple(
        PriceReference(roles[index % len(roles)], f"REFERENCE-{index:02d}")
        for index in range(MAX_INSTRUMENT_PRICE_REFERENCES)
    )
    specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
        price_references=references,
    )
    assert len(specification.price_references) == MAX_INSTRUMENT_PRICE_REFERENCES

    with pytest.raises(ValueError, match="item count"):
        replace(
            specification,
            price_references=(
                *references,
                PriceReference(PriceReferenceRole.INDEX, "REFERENCE-OVERFLOW"),
            ),
        )


def test_metadata_catalogue_owners_accept_literal_bounds_and_reject_plus_one() -> None:
    specification = _spot_specification()
    observation = _observation(specification)
    specifications = (specification,) * MAX_METADATA_CATALOGUE_ITEMS
    observations = (observation,) * MAX_METADATA_CATALOGUE_ITEMS

    validate_instrument_metadata_catalogue(specifications, (observation,))
    validate_instrument_metadata_catalogue((specification,), observations)
    with pytest.raises(ValueError, match="item count"):
        validate_instrument_metadata_catalogue((*specifications, specification), (observation,))
    with pytest.raises(ValueError, match="item count"):
        validate_instrument_metadata_catalogue((specification,), (*observations, observation))


@pytest.mark.parametrize(
    ("role", "reference_id", "error"),
    [
        pytest.param("index", "BTC-INDEX", TypeError, id="role-string"),
        pytest.param(PriceReferenceRole.INDEX, 1, TypeError, id="id-integer"),
        pytest.param(PriceReferenceRole.INDEX, "", ValueError, id="empty-id"),
    ],
)
def test_price_reference_revalidates_exact_runtime_types(
    role: object, reference_id: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        PriceReference(
            role=cast(PriceReferenceRole, role),
            reference_id=cast(str, reference_id),
        )


def test_observation_has_byte_exact_versioned_identity() -> None:
    specification = _spot_specification()
    observation = _observation(
        specification,
        effective_from=_utc(1),
        observed_at=_utc(2),
        source_declared_until=_utc(5),
    )

    expected = (
        "instrument-metadata-observation-v1",
        specification.instrument.canonical_instrument_id,
        specification.instrument_specification_id.value,
        "binance-production-catalog-v1",
        "2026-01-01T00:00:00.123456Z",
        "source-declared",
        "2026-01-05T00:00:00.123456Z",
        "2026-01-02T00:00:00.123456Z",
        None,
    )
    assert observation.instrument_metadata_observation_id.value == json.dumps(
        expected,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert type(observation.instrument_metadata_observation_id) is (InstrumentMetadataObservationId)


def test_observation_is_frozen_slotted_and_hashable() -> None:
    observation = _observation(_spot_specification())

    assert not hasattr(observation, "__dict__")
    assert hash(observation) == hash(_observation(_spot_specification()))
    with pytest.raises(FrozenInstanceError):
        observation.observed_at = _utc(2)  # type: ignore[misc]


@pytest.mark.parametrize("market_character", ["M", "\\"])
def test_long_existing_instrument_identity_remains_valid_through_metadata_selection(
    market_character: str,
) -> None:
    probe = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.SPOT,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id=market_character,
        native_symbol="public-adapter-alias",
    )
    second = replace(probe, venue_market_id=market_character * 2)
    encoded_width = len(second.canonical_instrument_id) - len(probe.canonical_instrument_id)
    fixed_length = len(probe.canonical_instrument_id) - encoded_width
    market_length = (MAX_CANONICAL_INSTRUMENT_ID_LENGTH - fixed_length) // encoded_width
    instrument = replace(
        probe,
        venue_market_id=market_character * market_length,
    )
    assert len(instrument.canonical_instrument_id) <= MAX_CANONICAL_INSTRUMENT_ID_LENGTH
    assert len(instrument.canonical_instrument_id) > (
        MAX_CANONICAL_INSTRUMENT_ID_LENGTH - encoded_width
    )
    specification = InstrumentSpecification(
        instrument=instrument,
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    observation = _observation(specification)

    validate_instrument_metadata_catalogue((specification,), (observation,))
    selected = select_instrument_metadata(
        canonical_instrument_id=instrument.canonical_instrument_id,
        metadata_authority_id=observation.metadata_authority_id,
        event_time=_utc(3),
        raw_received_time=_utc(3),
        specifications=(specification,),
        observations=(observation,),
    )

    assert selected.specification.instrument == instrument
    assert selected.observation == observation
    assert len(specification.instrument_specification_id.value) < 512


def test_first_observed_requires_equal_effective_and_observed_times() -> None:
    specification = _spot_specification()
    valid = _observation(
        specification,
        effective_from=_utc(2),
        observed_at=_utc(2),
        basis=MetadataEffectiveBasis.FIRST_OBSERVED,
    )
    assert valid.effective_from == valid.observed_at

    with pytest.raises(ValueError, match="effective_from == observed_at"):
        _observation(
            specification,
            effective_from=_utc(1),
            observed_at=_utc(2),
            basis=MetadataEffectiveBasis.FIRST_OBSERVED,
        )


def test_source_declared_boundary_may_predate_observation() -> None:
    observation = _observation(
        _spot_specification(),
        effective_from=_utc(1),
        observed_at=_utc(5),
    )

    assert observation.effective_from < observation.observed_at


def test_declared_ending_must_follow_effective_boundary() -> None:
    specification = _spot_specification()
    with pytest.raises(ValueError, match="later than effective_from"):
        _observation(
            specification,
            effective_from=_utc(2),
            observed_at=_utc(2),
            source_declared_until=_utc(2),
        )


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"canonical_instrument_id": 1}, TypeError),
        ({"instrument_specification_id": "specification"}, TypeError),
        ({"metadata_authority_id": "authority"}, TypeError),
        ({"effective_from": "2026-01-01T00:00:00Z"}, TypeError),
        ({"effective_from": datetime(2026, 1, 1)}, ValueError),
        ({"effective_basis": "source-declared"}, TypeError),
        ({"observed_at": True}, TypeError),
        ({"source_declared_until": datetime(2026, 1, 5)}, ValueError),
        ({"raw_record_id": "raw-record"}, TypeError),
    ],
)
def test_observation_revalidates_exact_runtime_types(
    changes: dict[str, object], error: type[Exception]
) -> None:
    specification = _spot_specification()
    arguments: dict[str, object] = {
        "canonical_instrument_id": specification.instrument.canonical_instrument_id,
        "instrument_specification_id": specification.instrument_specification_id,
        "metadata_authority_id": MetadataAuthorityId("authority"),
        "effective_from": _utc(1),
        "effective_basis": MetadataEffectiveBasis.SOURCE_DECLARED,
        "observed_at": _utc(2),
    }
    arguments.update(changes)

    with pytest.raises(error):
        InstrumentMetadataObservation(**arguments)  # type: ignore[arg-type]


def test_identical_reobservations_at_same_authority_boundary_are_allowed() -> None:
    specification = _spot_specification()
    first = _observation(specification, observed_at=_utc(2))
    second = _observation(specification, observed_at=_utc(3))

    validate_instrument_metadata_catalogue((specification,), (first, second))
    resolved = select_instrument_metadata(
        canonical_instrument_id=specification.instrument.canonical_instrument_id,
        metadata_authority_id=first.metadata_authority_id,
        event_time=_utc(4),
        raw_received_time=_utc(4, 1),
        specifications=(specification,),
        observations=(second, first),
    )

    assert resolved.observation == first


@pytest.mark.parametrize("reverse", [False, True])
def test_selector_rejects_order_dependent_native_aliases_for_one_specification_id(
    reverse: bool,
) -> None:
    first = _spot_specification()
    alternate = InstrumentSpecification(
        instrument=_spot(native_symbol="alternate-public-alias"),
        quantity_unit=QuantityUnit.BASE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    assert alternate.instrument_specification_id == first.instrument_specification_id
    observation = _observation(first)
    specifications = (alternate, first) if reverse else (first, alternate)

    with pytest.raises(ValueError, match="conflicting content"):
        select_instrument_metadata(
            canonical_instrument_id=first.instrument.canonical_instrument_id,
            metadata_authority_id=observation.metadata_authority_id,
            event_time=_utc(3),
            raw_received_time=_utc(3),
            specifications=specifications,
            observations=(observation,),
        )


@pytest.mark.parametrize("conflict_kind", ["specification", "basis", "ending"])
def test_catalogue_rejects_exact_authority_boundary_conflicts(conflict_kind: str) -> None:
    first_specification = _spot_specification()
    alternate_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    first = _observation(
        first_specification,
        effective_from=_utc(2),
        observed_at=_utc(2),
        basis=MetadataEffectiveBasis.FIRST_OBSERVED,
        source_declared_until=_utc(5),
    )
    if conflict_kind == "specification":
        second = _observation(
            alternate_specification,
            effective_from=_utc(2),
            observed_at=_utc(2),
            basis=MetadataEffectiveBasis.FIRST_OBSERVED,
            source_declared_until=_utc(5),
        )
    elif conflict_kind == "basis":
        second = _observation(
            first_specification,
            effective_from=_utc(2),
            observed_at=_utc(3),
            basis=MetadataEffectiveBasis.SOURCE_DECLARED,
            source_declared_until=_utc(5),
        )
    else:
        second = _observation(
            first_specification,
            effective_from=_utc(2),
            observed_at=_utc(2),
            basis=MetadataEffectiveBasis.FIRST_OBSERVED,
            source_declared_until=_utc(6),
        )

    with pytest.raises(ValueError, match="conflicting content"):
        validate_instrument_metadata_catalogue(
            (first_specification, alternate_specification), (first, second)
        )


def test_selector_ignores_conflict_hidden_beyond_as_of_visibility() -> None:
    first_specification = _spot_specification()
    conflicting_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    visible = _observation(
        first_specification,
        effective_from=_utc(1),
        observed_at=_utc(2),
    )
    later_conflict = _observation(
        conflicting_specification,
        effective_from=_utc(1),
        observed_at=_utc(10),
    )

    selected = select_instrument_metadata(
        canonical_instrument_id=first_specification.instrument.canonical_instrument_id,
        metadata_authority_id=visible.metadata_authority_id,
        event_time=_utc(3),
        raw_received_time=_utc(5),
        specifications=(first_specification, conflicting_specification),
        observations=(visible, later_conflict),
    )

    assert selected.specification == first_specification
    with pytest.raises(ValueError, match="conflicting content"):
        validate_instrument_metadata_catalogue(
            (first_specification, conflicting_specification),
            (visible, later_conflict),
        )


def test_selector_ignores_unlisted_specifications_outside_the_visible_slice() -> None:
    visible_specification = _spot_specification()
    excluded_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    visible = _observation(visible_specification, observed_at=_utc(2))
    invisible_future = _observation(
        excluded_specification,
        observed_at=_utc(10),
    )
    foreign_authority = _observation(
        excluded_specification,
        authority="independent-authority",
        observed_at=_utc(2),
    )

    selected = select_instrument_metadata(
        canonical_instrument_id=visible_specification.instrument.canonical_instrument_id,
        metadata_authority_id=visible.metadata_authority_id,
        event_time=_utc(3),
        raw_received_time=_utc(5),
        specifications=(visible_specification,),
        observations=(visible, invisible_future, foreign_authority),
    )

    assert selected.specification == visible_specification


def test_selector_fails_closed_on_visible_target_authority_conflict_only() -> None:
    first_specification = _spot_specification()
    conflicting_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    visible = _observation(first_specification, observed_at=_utc(2))
    visible_conflict = _observation(conflicting_specification, observed_at=_utc(3))

    with pytest.raises(ValueError, match="conflicting content"):
        select_instrument_metadata(
            canonical_instrument_id=first_specification.instrument.canonical_instrument_id,
            metadata_authority_id=visible.metadata_authority_id,
            event_time=_utc(4),
            raw_received_time=_utc(5),
            specifications=(first_specification, conflicting_specification),
            observations=(visible, visible_conflict),
        )

    other_authority_conflict = replace(
        visible_conflict,
        metadata_authority_id=MetadataAuthorityId("independent-authority"),
    )
    selected = select_instrument_metadata(
        canonical_instrument_id=first_specification.instrument.canonical_instrument_id,
        metadata_authority_id=visible.metadata_authority_id,
        event_time=_utc(4),
        raw_received_time=_utc(5),
        specifications=(first_specification, conflicting_specification),
        observations=(visible, other_authority_conflict),
    )

    assert selected.specification == first_specification


def test_catalogue_rejects_source_declared_interval_overlap() -> None:
    specification = _spot_specification()
    first = _observation(
        specification,
        effective_from=_utc(1),
        observed_at=_utc(1, 1),
        source_declared_until=_utc(5),
    )
    second = _observation(
        specification,
        effective_from=_utc(4),
        observed_at=_utc(4, 1),
    )

    with pytest.raises(ValueError, match="must not overlap"):
        validate_instrument_metadata_catalogue((specification,), (first, second))


def test_selector_uses_only_visible_observations_without_lookahead() -> None:
    base_specification = _spot_specification()
    revised_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    authority = MetadataAuthorityId("binance-production-catalog-v1")
    original = _observation(
        base_specification,
        effective_from=_utc(1),
        observed_at=_utc(1, 1),
    )
    retrospective = _observation(
        revised_specification,
        effective_from=_utc(5),
        observed_at=_utc(10),
    )

    before_new_observation = select_instrument_metadata(
        canonical_instrument_id=_spot().canonical_instrument_id,
        metadata_authority_id=authority,
        event_time=_utc(6),
        raw_received_time=_utc(8),
        specifications=(base_specification, revised_specification),
        observations=(original, retrospective),
    )
    after_new_observation = select_instrument_metadata(
        canonical_instrument_id=_spot().canonical_instrument_id,
        metadata_authority_id=authority,
        event_time=_utc(6),
        raw_received_time=_utc(11),
        specifications=(base_specification, revised_specification),
        observations=(original, retrospective),
    )

    assert before_new_observation.specification == base_specification
    assert before_new_observation.effective_until is None
    assert after_new_observation.specification == revised_specification


def test_selector_never_merges_unrequested_metadata_authorities() -> None:
    specification = _spot_specification()
    first = _observation(specification, authority="authority-a")
    second = _observation(specification, authority="authority-b")

    selected = select_instrument_metadata(
        canonical_instrument_id=specification.instrument.canonical_instrument_id,
        metadata_authority_id=MetadataAuthorityId("authority-b"),
        event_time=_utc(3),
        raw_received_time=_utc(3),
        specifications=(specification,),
        observations=(first, second),
    )

    assert selected.observation.metadata_authority_id == MetadataAuthorityId("authority-b")


def test_catalogue_scopes_conflicts_and_intervals_by_authority() -> None:
    first_specification = _spot_specification()
    second_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    authority_a = _observation(
        first_specification,
        authority="authority-a",
        effective_from=_utc(1),
        observed_at=_utc(1),
        source_declared_until=_utc(8),
    )
    authority_b = _observation(
        second_specification,
        authority="authority-b",
        effective_from=_utc(2),
        observed_at=_utc(2),
    )

    validate_instrument_metadata_catalogue(
        (first_specification, second_specification), (authority_a, authority_b)
    )


def test_selector_returns_exact_half_open_interval() -> None:
    first_specification = _spot_specification()
    second_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    first = _observation(
        first_specification,
        effective_from=_utc(1),
        observed_at=_utc(1),
    )
    second = _observation(
        second_specification,
        effective_from=_utc(5),
        observed_at=_utc(5),
    )

    before = select_instrument_metadata(
        canonical_instrument_id=_spot().canonical_instrument_id,
        metadata_authority_id=first.metadata_authority_id,
        event_time=_utc(4),
        raw_received_time=_utc(6),
        specifications=(first_specification, second_specification),
        observations=(first, second),
    )
    at_boundary = select_instrument_metadata(
        canonical_instrument_id=_spot().canonical_instrument_id,
        metadata_authority_id=first.metadata_authority_id,
        event_time=_utc(5),
        raw_received_time=_utc(6),
        specifications=(first_specification, second_specification),
        observations=(first, second),
    )

    assert before.specification == first_specification
    assert before.effective_until == _utc(5)
    assert at_boundary.specification == second_specification


def test_selector_fails_when_metadata_is_unknown_not_visible_or_in_a_gap() -> None:
    specification = _spot_specification()
    observation = _observation(
        specification,
        effective_from=_utc(2),
        observed_at=_utc(4),
        source_declared_until=_utc(6),
    )
    common: dict[str, object] = {
        "metadata_authority_id": observation.metadata_authority_id,
        "specifications": (specification,),
        "observations": (observation,),
    }

    with pytest.raises(ValueError, match="no visible metadata"):
        select_instrument_metadata(
            canonical_instrument_id=specification.instrument.canonical_instrument_id,
            event_time=_utc(3),
            raw_received_time=_utc(3),
            **common,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="no metadata interval"):
        select_instrument_metadata(
            canonical_instrument_id=specification.instrument.canonical_instrument_id,
            event_time=_utc(7),
            raw_received_time=_utc(7),
            **common,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="canonical compact JSON array"):
        select_instrument_metadata(
            canonical_instrument_id="unknown-instrument-v1",
            event_time=_utc(3),
            raw_received_time=_utc(7),
            **common,  # type: ignore[arg-type]
        )


def test_resolved_metadata_is_selector_backed_frozen_slotted_and_not_publicly_constructible() -> (
    None
):
    specification = _spot_specification()
    observation = _observation(specification)
    resolved = select_instrument_metadata(
        canonical_instrument_id=specification.instrument.canonical_instrument_id,
        metadata_authority_id=observation.metadata_authority_id,
        event_time=_utc(3),
        raw_received_time=_utc(3),
        specifications=(specification,),
        observations=(observation,),
    )

    assert resolved.specification == specification
    assert not hasattr(ResolvedInstrumentMetadata, "_from_selector")
    assert not hasattr(instrument_metadata_module, "_resolved_metadata_from_selection")
    assert not hasattr(resolved, "__dict__")
    assert hash(resolved) == hash(resolved)
    with pytest.raises(TypeError, match="select_instrument_metadata"):
        ResolvedInstrumentMetadata(
            specification=specification,
            observation=observation,
            event_time=_utc(3),
            raw_received_time=_utc(3),
            effective_until=None,
        )
    with pytest.raises(TypeError, match="select_instrument_metadata"):
        replace(resolved, event_time=_utc(4))


def test_selector_respects_the_source_declared_ending() -> None:
    specification = _spot_specification()
    observation = _observation(specification, source_declared_until=_utc(5))
    resolved = select_instrument_metadata(
        canonical_instrument_id=specification.instrument.canonical_instrument_id,
        metadata_authority_id=observation.metadata_authority_id,
        event_time=_utc(3),
        raw_received_time=_utc(3),
        specifications=(specification,),
        observations=(observation,),
    )

    assert resolved.effective_until == _utc(5)
    with pytest.raises(ValueError, match="no metadata interval"):
        select_instrument_metadata(
            canonical_instrument_id=specification.instrument.canonical_instrument_id,
            metadata_authority_id=observation.metadata_authority_id,
            event_time=_utc(3),
            raw_received_time=_utc(3),
            specifications=(specification,),
            observations=(replace(observation, source_declared_until=_utc(2)),),
        )


def test_catalogue_requires_explicit_finite_builtin_sequences_and_exact_items() -> None:
    specification = _spot_specification()
    observation = _observation(specification)

    with pytest.raises(TypeError, match="exact list or tuple"):
        validate_instrument_metadata_catalogue(
            cast(list[InstrumentSpecification], {specification}), (observation,)
        )
    with pytest.raises(TypeError, match="InstrumentSpecification"):
        validate_instrument_metadata_catalogue(
            cast(list[InstrumentSpecification], [object()]), (observation,)
        )
    with pytest.raises(TypeError, match="InstrumentMetadataObservation"):
        validate_instrument_metadata_catalogue(
            (specification,), cast(list[InstrumentMetadataObservation], [object()])
        )


def test_catalogue_rejects_unknown_specification_and_constructor_rejects_mismatch() -> None:
    specification = _spot_specification()
    observation = _observation(specification)
    unlisted_specification = InstrumentSpecification(
        instrument=_spot(),
        quantity_unit=QuantityUnit.QUOTE_ASSET,
        contract_form=ContractForm.NOT_APPLICABLE,
    )
    unknown = replace(
        observation,
        instrument_specification_id=unlisted_specification.instrument_specification_id,
    )

    with pytest.raises(ValueError, match="unknown specification"):
        validate_instrument_metadata_catalogue((specification,), (unknown,))
    with pytest.raises(ValueError, match="invalid canonical components"):
        replace(observation, canonical_instrument_id=_future().canonical_instrument_id)
