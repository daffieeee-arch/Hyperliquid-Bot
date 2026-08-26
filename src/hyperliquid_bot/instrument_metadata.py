"""Pure point-in-time instrument metadata contracts.

The contracts in this module are dormant during Phase 1A-3B1A. They describe
and validate explicitly supplied metadata catalogues without reading clocks,
registries, files, or network resources.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Final, Self

from hyperliquid_bot.contracts import Instrument, InstrumentType
from hyperliquid_bot.data_provenance import (
    MAX_INSTRUMENT_PRICE_REFERENCES,
    MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH,
    MAX_METADATA_CATALOGUE_ITEMS,
    InstrumentMetadataObservationId,
    InstrumentSpecificationId,
    MetadataAuthorityId,
    RawRecordId,
    canonical_json_array,
    canonical_utc_datetime,
    instrument_from_canonical_id,
    parse_canonical_json_array,
    require_collection_size,
    require_text,
    sha256_hex,
    validate_canonical_instrument_id,
)

_DECIMAL_MAX_COEFFICIENT_DIGITS: Final = 128
_DECIMAL_MIN_EXPONENT: Final = -128
_DECIMAL_MAX_EXPONENT: Final = 128
_DECIMAL_MAX_CANONICAL_LENGTH: Final = 256
_METADATA_TEXT_MAX_LENGTH: Final = 256
_POSITIVE_CANONICAL_DECIMAL_PATTERN: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")


class QuantityUnit(StrEnum):
    """Unit represented by a normalized public-market-data quantity."""

    BASE_ASSET = "base-asset"
    QUOTE_ASSET = "quote-asset"
    CONTRACTS = "contracts"


class ContractForm(StrEnum):
    """Economic form of the instrument contract."""

    NOT_APPLICABLE = "not-applicable"
    LINEAR = "linear"
    INVERSE = "inverse"


class PriceReferenceRole(StrEnum):
    """Bounded semantic role of a point-in-time price reference."""

    MARK = "mark"
    INDEX = "index"
    ORACLE = "oracle"


class MetadataEffectiveBasis(StrEnum):
    """Evidence basis for an observation's effective boundary."""

    SOURCE_DECLARED = "source-declared"
    FIRST_OBSERVED = "first-observed"


def _stored_text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("stored instrument specification contains an invalid text component.")
    return value


def _stored_optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _stored_text(value)


def _stored_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = _stored_text(value)
    if (
        len(text) > _DECIMAL_MAX_CANONICAL_LENGTH
        or text == "0"
        or _POSITIVE_CANONICAL_DECIMAL_PATTERN.fullmatch(text) is None
    ):
        raise ValueError("stored instrument specification has invalid canonical Decimal text.")
    integer_portion, separator, fractional_portion = text.partition(".")
    if separator:
        if len(fractional_portion) > -_DECIMAL_MIN_EXPONENT:
            raise ValueError("stored instrument specification has invalid canonical Decimal text.")
        coefficient = f"{integer_portion}{fractional_portion}".lstrip("0")
        exponent = -len(fractional_portion)
    else:
        trailing_zeroes = len(integer_portion) - len(integer_portion.rstrip("0"))
        exponent = min(trailing_zeroes, _DECIMAL_MAX_EXPONENT)
        coefficient = integer_portion[: len(integer_portion) - exponent]
    if not coefficient or len(coefficient) > _DECIMAL_MAX_COEFFICIENT_DIGITS:
        raise ValueError("stored instrument specification has invalid canonical Decimal text.")
    parsed = Decimal((0, tuple(int(digit) for digit in coefficient), exponent))
    if canonical_decimal(parsed) != text:
        raise ValueError("stored instrument specification has invalid canonical Decimal text.")
    return parsed


def _stored_utc(value: object) -> datetime | None:
    if value is None:
        return None
    text = _stored_text(value)
    parsed: datetime | None = None
    if len(text) == 27 and text.endswith("Z"):
        try:
            parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
        except ValueError:
            pass
    if parsed is None or canonical_utc_datetime(parsed, field_name="stored_time") != text:
        raise ValueError("stored instrument specification has invalid canonical UTC text.")
    return parsed


def _require_bounded_text(value: object, *, field_name: str) -> str:
    text = require_text(value, field_name=field_name)
    if len(text) > _METADATA_TEXT_MAX_LENGTH:
        raise ValueError(
            f"{field_name} must contain at most {_METADATA_TEXT_MAX_LENGTH} characters."
        )
    return text


def _require_utc(value: object, *, field_name: str) -> datetime:
    canonical_utc_datetime(value, field_name=field_name)
    assert type(value) is datetime
    return value.replace(tzinfo=UTC)


def canonical_decimal(value: object) -> str:
    """Return bounded, context-independent fixed-point Decimal text.

    The canonicalizer operates solely on ``Decimal.as_tuple()``. It never uses
    float conversion, arithmetic, rounding, ``quantize()``, or ``normalize()``.
    This keeps the result independent of the active Decimal context.
    """

    if type(value) is not Decimal:
        raise TypeError("value must be an exact Decimal.")
    if not value.is_finite():
        raise ValueError("value must be finite.")

    decimal_tuple = value.as_tuple()
    digits = decimal_tuple.digits
    exponent = decimal_tuple.exponent
    if type(exponent) is not int:
        raise ValueError("value must have a finite integer exponent.")
    if len(digits) > _DECIMAL_MAX_COEFFICIENT_DIGITS:
        raise ValueError(
            f"value must contain at most {_DECIMAL_MAX_COEFFICIENT_DIGITS} coefficient digits."
        )
    if exponent < _DECIMAL_MIN_EXPONENT or exponent > _DECIMAL_MAX_EXPONENT:
        raise ValueError(
            "value exponent must be between "
            f"{_DECIMAL_MIN_EXPONENT} and {_DECIMAL_MAX_EXPONENT} inclusive."
        )
    if value.is_zero():
        return "0"

    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        magnitude = coefficient + ("0" * exponent)
    else:
        decimal_index = len(coefficient) + exponent
        if decimal_index > 0:
            integer_portion = coefficient[:decimal_index]
            fractional_portion = coefficient[decimal_index:]
        else:
            integer_portion = "0"
            fractional_portion = ("0" * -decimal_index) + coefficient
        fractional_portion = fractional_portion.rstrip("0")
        magnitude = (
            integer_portion if not fractional_portion else f"{integer_portion}.{fractional_portion}"
        )

    canonical = f"-{magnitude}" if decimal_tuple.sign else magnitude
    if len(canonical) > _DECIMAL_MAX_CANONICAL_LENGTH:
        raise ValueError(
            "canonical Decimal text must contain at most "
            f"{_DECIMAL_MAX_CANONICAL_LENGTH} characters."
        )
    return canonical


@dataclass(frozen=True, slots=True)
class PriceReference:
    """Typed relation to a stable source-defined mark, index, or oracle."""

    role: PriceReferenceRole
    reference_id: str

    def __post_init__(self) -> None:
        if type(self.role) is not PriceReferenceRole:
            raise TypeError("role must be a PriceReferenceRole.")
        _require_bounded_text(self.reference_id, field_name="reference_id")

    def canonical_components(self) -> tuple[str, str]:
        """Return the exact nested identity components for this reference."""

        return (self.role.value, self.reference_id)


@dataclass(frozen=True, slots=True)
class InstrumentSpecification:
    """Content-addressed interpretation of quantities and derivative terms.

    ``instrument_specification_canonical_content`` is the exact canonical JSON array:

    ``["instrument-specification-content-v1", canonical_instrument_id, quantity_unit,
    contract_form, contract_multiplier, multiplier_asset, settlement_asset,
    contract_expiry, last_trading_time, settlement_time, price_references]``.

    The bounded ``instrument_specification_id`` commits to the SHA-256 of the
    canonical instrument ID, the exact content length and the content SHA-256.
    Full stored content must therefore be retained and verified separately.
    """

    instrument: Instrument
    quantity_unit: QuantityUnit
    contract_form: ContractForm
    contract_multiplier: Decimal | None = None
    multiplier_asset: str | None = None
    settlement_asset: str | None = None
    last_trading_time: datetime | None = None
    settlement_time: datetime | None = None
    price_references: tuple[PriceReference, ...] = ()
    instrument_specification_canonical_content: str = field(init=False, repr=False)
    instrument_specification_content_sha256: str = field(init=False)
    instrument_specification_id: InstrumentSpecificationId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.instrument) is not Instrument:
            raise TypeError("instrument must be an Instrument.")
        reconstructed = instrument_from_canonical_id(
            self.instrument.canonical_instrument_id,
            native_symbol=self.instrument.native_symbol,
        )
        if (
            reconstructed.venue is not self.instrument.venue
            or reconstructed.instrument_type is not self.instrument.instrument_type
            or reconstructed.base_asset != self.instrument.base_asset
            or reconstructed.quote_asset != self.instrument.quote_asset
            or reconstructed.venue_market_id != self.instrument.venue_market_id
            or reconstructed.native_symbol != self.instrument.native_symbol
            or reconstructed.contract_expiry != self.instrument.contract_expiry
        ):
            raise ValueError("instrument fields must exactly match canonical instrument identity.")
        if type(self.quantity_unit) is not QuantityUnit:
            raise TypeError("quantity_unit must be a QuantityUnit.")
        if type(self.contract_form) is not ContractForm:
            raise TypeError("contract_form must be a ContractForm.")

        multiplier = self.contract_multiplier
        multiplier_text: str | None = None
        if multiplier is not None:
            multiplier_text = canonical_decimal(multiplier)
            if multiplier <= 0:
                raise ValueError("contract_multiplier must be greater than zero.")

        multiplier_asset = self.multiplier_asset
        if multiplier_asset is not None:
            multiplier_asset = _require_bounded_text(
                multiplier_asset, field_name="multiplier_asset"
            )
        settlement_asset = self.settlement_asset
        if settlement_asset is not None:
            settlement_asset = _require_bounded_text(
                settlement_asset, field_name="settlement_asset"
            )

        if self.quantity_unit is QuantityUnit.CONTRACTS:
            if multiplier is None or multiplier_asset is None:
                raise ValueError(
                    "contract quantities require contract_multiplier and multiplier_asset."
                )
        elif multiplier is not None or multiplier_asset is not None:
            raise ValueError(
                "contract_multiplier and multiplier_asset require a contracts quantity unit."
            )

        if self.instrument.instrument_type is InstrumentType.SPOT:
            if self.contract_form is not ContractForm.NOT_APPLICABLE:
                raise ValueError("spot instruments require a not-applicable contract form.")
            if self.quantity_unit is QuantityUnit.CONTRACTS:
                raise ValueError("spot instrument quantities cannot use contract units.")
            if settlement_asset is not None:
                raise ValueError("spot instruments cannot define a settlement asset.")
            if self.last_trading_time is not None or self.settlement_time is not None:
                raise ValueError(
                    "spot instruments cannot define last-trading or settlement timestamps."
                )
        else:
            if self.contract_form not in (ContractForm.LINEAR, ContractForm.INVERSE):
                raise ValueError(
                    "derivative instruments require a linear or inverse contract form."
                )
            if settlement_asset is None:
                raise ValueError("derivative instruments require a settlement asset.")

        last_trading_time = self.last_trading_time
        if last_trading_time is not None:
            last_trading_time = _require_utc(last_trading_time, field_name="last_trading_time")
        settlement_time = self.settlement_time
        if settlement_time is not None:
            settlement_time = _require_utc(settlement_time, field_name="settlement_time")
        if (
            last_trading_time is not None
            and settlement_time is not None
            and settlement_time < last_trading_time
        ):
            raise ValueError("settlement_time must not precede last_trading_time.")

        if type(self.price_references) is not tuple:
            raise TypeError("price_references must be an exact tuple.")
        require_collection_size(
            self.price_references,
            field_name="price_references",
            maximum_items=MAX_INSTRUMENT_PRICE_REFERENCES,
        )
        for reference in self.price_references:
            if type(reference) is not PriceReference:
                raise TypeError("price_references must contain only PriceReference values.")
        sorted_references = tuple(
            sorted(self.price_references, key=lambda item: item.canonical_components())
        )
        if len(set(sorted_references)) != len(sorted_references):
            raise ValueError("price_references must be unique.")

        object.__setattr__(self, "multiplier_asset", multiplier_asset)
        object.__setattr__(self, "settlement_asset", settlement_asset)
        object.__setattr__(self, "last_trading_time", last_trading_time)
        object.__setattr__(self, "settlement_time", settlement_time)
        object.__setattr__(self, "price_references", sorted_references)

        content_preimage = (
            "instrument-specification-content-v1",
            self.instrument.canonical_instrument_id,
            self.quantity_unit.value,
            self.contract_form.value,
            multiplier_text,
            multiplier_asset,
            settlement_asset,
            self.contract_expiry.isoformat() if self.contract_expiry is not None else None,
            (
                canonical_utc_datetime(last_trading_time, field_name="last_trading_time")
                if last_trading_time is not None
                else None
            ),
            (
                canonical_utc_datetime(settlement_time, field_name="settlement_time")
                if settlement_time is not None
                else None
            ),
            tuple(reference.canonical_components() for reference in sorted_references),
        )
        canonical_content = canonical_json_array(
            content_preimage,
            maximum_length=MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH,
        )
        content_sha256 = sha256_hex(
            canonical_content.encode("utf-8"),
            field_name="instrument_specification_canonical_content",
        )
        canonical_instrument_id_sha256 = sha256_hex(
            self.instrument.canonical_instrument_id.encode("utf-8"),
            field_name="canonical_instrument_id",
        )
        identifier = InstrumentSpecificationId(
            canonical_json_array(
                (
                    "instrument-specification-v1",
                    canonical_instrument_id_sha256,
                    len(canonical_content),
                    content_sha256,
                )
            )
        )
        object.__setattr__(
            self,
            "instrument_specification_canonical_content",
            canonical_content,
        )
        object.__setattr__(
            self,
            "instrument_specification_content_sha256",
            content_sha256,
        )
        object.__setattr__(
            self,
            "instrument_specification_id",
            identifier,
        )

    @classmethod
    def from_stored(
        cls,
        *,
        instrument_specification_canonical_content: object,
        expected_instrument_specification_id: object,
        native_symbol: object,
    ) -> Self:
        """Reconstruct and verify all derived fields from persisted content."""

        if type(expected_instrument_specification_id) is not InstrumentSpecificationId:
            raise TypeError(
                "expected_instrument_specification_id must be an InstrumentSpecificationId."
            )
        components = parse_canonical_json_array(
            instrument_specification_canonical_content,
            field_name="instrument_specification_canonical_content",
            maximum_length=MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH,
        )
        if len(components) != 11 or components[0] != "instrument-specification-content-v1":
            raise ValueError("stored instrument specification has invalid canonical components.")
        instrument = instrument_from_canonical_id(
            components[1],
            native_symbol=native_symbol,
        )
        quantity_unit: QuantityUnit | None = None
        contract_form: ContractForm | None = None
        try:
            quantity_unit = QuantityUnit(_stored_text(components[2]))
            contract_form = ContractForm(_stored_text(components[3]))
        except ValueError:
            pass
        if quantity_unit is None or contract_form is None:
            raise ValueError("stored instrument specification has unsupported semantic codes.")
        price_rows = components[10]
        if type(price_rows) is not tuple:
            raise ValueError("stored instrument specification has invalid price references.")
        require_collection_size(
            price_rows,
            field_name="price_references",
            maximum_items=MAX_INSTRUMENT_PRICE_REFERENCES,
        )
        price_references: list[PriceReference] = []
        for row in price_rows:
            if type(row) is not tuple or len(row) != 2:
                raise ValueError("stored instrument specification has invalid price references.")
            role: PriceReferenceRole | None = None
            try:
                role = PriceReferenceRole(_stored_text(row[0]))
            except ValueError:
                pass
            if role is None:
                raise ValueError("stored instrument specification has invalid price references.")
            price_references.append(PriceReference(role, _stored_text(row[1])))

        candidate = cls(
            instrument=instrument,
            quantity_unit=quantity_unit,
            contract_form=contract_form,
            contract_multiplier=_stored_decimal(components[4]),
            multiplier_asset=_stored_optional_text(components[5]),
            settlement_asset=_stored_optional_text(components[6]),
            last_trading_time=_stored_utc(components[8]),
            settlement_time=_stored_utc(components[9]),
            price_references=tuple(price_references),
        )
        expected_expiry = (
            instrument.contract_expiry.isoformat()
            if instrument.contract_expiry is not None
            else None
        )
        if components[7] != expected_expiry:
            raise ValueError("stored contract expiry does not match instrument identity.")
        if (
            candidate.instrument_specification_canonical_content
            != instrument_specification_canonical_content
            or candidate.instrument_specification_id != expected_instrument_specification_id
        ):
            raise ValueError("stored instrument specification integrity verification failed.")
        return candidate

    @property
    def contract_expiry(self) -> date | None:
        """Return expiry metadata exclusively from the immutable Instrument."""

        return self.instrument.contract_expiry


@dataclass(frozen=True, slots=True)
class InstrumentMetadataObservation:
    """Append-only point-in-time assertion by one metadata authority.

    ``instrument_metadata_observation_id`` is the exact canonical JSON array:

    ``["instrument-metadata-observation-v1", canonical_instrument_id,
    instrument_specification_id, metadata_authority_id, effective_from,
    effective_basis, source_declared_until, observed_at, raw_record_id]``.
    """

    canonical_instrument_id: str
    instrument_specification_id: InstrumentSpecificationId
    metadata_authority_id: MetadataAuthorityId
    effective_from: datetime
    effective_basis: MetadataEffectiveBasis
    observed_at: datetime
    source_declared_until: datetime | None = None
    raw_record_id: RawRecordId | None = None
    instrument_metadata_observation_id: InstrumentMetadataObservationId = field(init=False)

    def __post_init__(self) -> None:
        canonical_instrument_id = validate_canonical_instrument_id(
            self.canonical_instrument_id,
            field_name="canonical_instrument_id",
        )
        if type(self.instrument_specification_id) is not InstrumentSpecificationId:
            raise TypeError("instrument_specification_id must be an InstrumentSpecificationId.")
        if type(self.metadata_authority_id) is not MetadataAuthorityId:
            raise TypeError("metadata_authority_id must be a MetadataAuthorityId.")
        if type(self.effective_basis) is not MetadataEffectiveBasis:
            raise TypeError("effective_basis must be a MetadataEffectiveBasis.")
        if self.raw_record_id is not None and type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId or None.")

        effective_from = _require_utc(self.effective_from, field_name="effective_from")
        observed_at = _require_utc(self.observed_at, field_name="observed_at")
        source_declared_until = self.source_declared_until
        if source_declared_until is not None:
            source_declared_until = _require_utc(
                source_declared_until, field_name="source_declared_until"
            )
            if source_declared_until <= effective_from:
                raise ValueError("source_declared_until must be later than effective_from.")
        if (
            self.effective_basis is MetadataEffectiveBasis.FIRST_OBSERVED
            and effective_from != observed_at
        ):
            raise ValueError("first-observed metadata requires effective_from == observed_at.")

        object.__setattr__(self, "canonical_instrument_id", canonical_instrument_id)
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "source_declared_until", source_declared_until)

        preimage = (
            "instrument-metadata-observation-v1",
            canonical_instrument_id,
            self.instrument_specification_id.value,
            self.metadata_authority_id.value,
            canonical_utc_datetime(effective_from, field_name="effective_from"),
            self.effective_basis.value,
            (
                canonical_utc_datetime(source_declared_until, field_name="source_declared_until")
                if source_declared_until is not None
                else None
            ),
            canonical_utc_datetime(observed_at, field_name="observed_at"),
            self.raw_record_id.value if self.raw_record_id is not None else None,
        )
        object.__setattr__(
            self,
            "instrument_metadata_observation_id",
            InstrumentMetadataObservationId(canonical_json_array(preimage)),
        )


@dataclass(frozen=True, slots=True, init=False)
class ResolvedInstrumentMetadata:
    """A point-in-time result constructible only by the pure selector."""

    specification: InstrumentSpecification
    observation: InstrumentMetadataObservation
    event_time: datetime
    raw_received_time: datetime
    effective_until: datetime | None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use select_instrument_metadata().")


def _require_specifications(
    specifications: Sequence[InstrumentSpecification],
) -> tuple[InstrumentSpecification, ...]:
    if type(specifications) not in (list, tuple):
        raise TypeError("specifications must be an exact list or tuple.")
    require_collection_size(
        specifications,
        field_name="specifications",
        maximum_items=MAX_METADATA_CATALOGUE_ITEMS,
    )
    result = tuple(specifications)
    for specification in result:
        if type(specification) is not InstrumentSpecification:
            raise TypeError("specifications must contain only InstrumentSpecification values.")
    return result


def _require_observations(
    observations: Sequence[InstrumentMetadataObservation],
) -> tuple[InstrumentMetadataObservation, ...]:
    if type(observations) not in (list, tuple):
        raise TypeError("observations must be an exact list or tuple.")
    require_collection_size(
        observations,
        field_name="observations",
        maximum_items=MAX_METADATA_CATALOGUE_ITEMS,
    )
    result = tuple(observations)
    for observation in result:
        if type(observation) is not InstrumentMetadataObservation:
            raise TypeError("observations must contain only InstrumentMetadataObservation values.")
    return result


def _catalogue_maps(
    specifications: tuple[InstrumentSpecification, ...],
    observations: tuple[InstrumentMetadataObservation, ...],
) -> dict[str, InstrumentSpecification]:
    specifications_by_id: dict[str, InstrumentSpecification] = {}
    for specification in specifications:
        identifier = specification.instrument_specification_id.value
        existing = specifications_by_id.get(identifier)
        if existing is not None and (
            existing != specification
            or existing.instrument.native_symbol != specification.instrument.native_symbol
        ):
            raise ValueError("one specification ID cannot describe conflicting content.")
        specifications_by_id[identifier] = specification

    assertions_by_boundary: dict[tuple[str, str, datetime], tuple[str, str, str | None]] = {}
    for observation in observations:
        referenced_specification = specifications_by_id.get(
            observation.instrument_specification_id.value
        )
        if referenced_specification is None:
            raise ValueError("metadata observation references an unknown specification.")
        if (
            referenced_specification.instrument.canonical_instrument_id
            != observation.canonical_instrument_id
        ):
            raise ValueError("metadata observation references the wrong canonical instrument.")

        boundary = (
            observation.canonical_instrument_id,
            observation.metadata_authority_id.value,
            observation.effective_from,
        )
        assertion = (
            observation.instrument_specification_id.value,
            observation.effective_basis.value,
            (
                canonical_utc_datetime(
                    observation.source_declared_until,
                    field_name="source_declared_until",
                )
                if observation.source_declared_until is not None
                else None
            ),
        )
        existing_assertion = assertions_by_boundary.get(boundary)
        if existing_assertion is not None and existing_assertion != assertion:
            raise ValueError(
                "one metadata authority cannot assert conflicting content at one boundary."
            )
        assertions_by_boundary[boundary] = assertion

    grouped_boundaries: dict[tuple[str, str], list[InstrumentMetadataObservation]] = {}
    for observation in observations:
        key = (observation.canonical_instrument_id, observation.metadata_authority_id.value)
        grouped_boundaries.setdefault(key, []).append(observation)
    for group in grouped_boundaries.values():
        unique_by_boundary: dict[datetime, InstrumentMetadataObservation] = {}
        for observation in group:
            unique_by_boundary.setdefault(observation.effective_from, observation)
        ordered = sorted(unique_by_boundary.values(), key=lambda item: item.effective_from)
        for current, following in pairwise(ordered):
            if (
                current.source_declared_until is not None
                and current.source_declared_until > following.effective_from
            ):
                raise ValueError("source-declared metadata intervals must not overlap.")

    return specifications_by_id


def validate_instrument_metadata_catalogue(
    specifications: Sequence[InstrumentSpecification],
    observations: Sequence[InstrumentMetadataObservation],
) -> None:
    """Validate every locally checkable relationship in an explicit catalogue.

    Passing an explicit finite catalogue is the only way this phase claims
    catalogue-wide uniqueness and non-overlap. The function performs no lookup
    outside that supplied collection.
    """

    specification_values = _require_specifications(specifications)
    observation_values = _require_observations(observations)
    _catalogue_maps(specification_values, observation_values)


def select_instrument_metadata(
    *,
    canonical_instrument_id: str,
    metadata_authority_id: MetadataAuthorityId,
    event_time: datetime,
    raw_received_time: datetime,
    specifications: Sequence[InstrumentSpecification],
    observations: Sequence[InstrumentMetadataObservation],
) -> ResolvedInstrumentMetadata:
    """Select exactly one visible metadata interval without look-ahead.

    Selection is restricted to the explicitly requested authority. Assertions
    from unrelated authorities are never merged implicitly. Only observations
    known at ``raw_received_time`` participate in interval construction.
    """

    instrument_id = validate_canonical_instrument_id(
        canonical_instrument_id,
        field_name="canonical_instrument_id",
    )
    if type(metadata_authority_id) is not MetadataAuthorityId:
        raise TypeError("metadata_authority_id must be a MetadataAuthorityId.")
    selected_event_time = _require_utc(event_time, field_name="event_time")
    selected_received_time = _require_utc(raw_received_time, field_name="raw_received_time")
    specification_values = _require_specifications(specifications)
    observation_values = _require_observations(observations)

    visible_selected_observations = tuple(
        observation
        for observation in observation_values
        if observation.observed_at <= selected_received_time
        and observation.canonical_instrument_id == instrument_id
        and observation.metadata_authority_id == metadata_authority_id
    )
    if not visible_selected_observations:
        raise ValueError("no visible metadata exists for the requested instrument and authority.")
    visible_specification_ids = {
        observation.instrument_specification_id for observation in visible_selected_observations
    }
    visible_selected_specifications = tuple(
        specification
        for specification in specification_values
        if specification.instrument_specification_id in visible_specification_ids
    )
    specifications_by_id = _catalogue_maps(
        visible_selected_specifications,
        visible_selected_observations,
    )
    candidates = list(visible_selected_observations)

    representative_by_boundary: dict[datetime, InstrumentMetadataObservation] = {}
    for observation in sorted(
        candidates,
        key=lambda item: (
            item.effective_from,
            item.observed_at,
            item.instrument_metadata_observation_id.value,
        ),
    ):
        representative_by_boundary.setdefault(observation.effective_from, observation)
    ordered = sorted(representative_by_boundary.values(), key=lambda item: item.effective_from)

    selected: InstrumentMetadataObservation | None = None
    selected_until: datetime | None = None
    for index, observation in enumerate(ordered):
        following_boundary = ordered[index + 1].effective_from if index + 1 < len(ordered) else None
        effective_until = observation.source_declared_until or following_boundary
        if observation.effective_from <= selected_event_time and (
            effective_until is None or selected_event_time < effective_until
        ):
            if selected is not None:
                raise ValueError("multiple metadata intervals match event_time.")
            selected = observation
            selected_until = effective_until

    if selected is None:
        raise ValueError("no metadata interval is effective for event_time.")
    specification = specifications_by_id[selected.instrument_specification_id.value]
    instance = object.__new__(ResolvedInstrumentMetadata)
    object.__setattr__(instance, "specification", specification)
    object.__setattr__(instance, "observation", selected)
    object.__setattr__(instance, "event_time", selected_event_time)
    object.__setattr__(instance, "raw_received_time", selected_received_time)
    object.__setattr__(instance, "effective_until", selected_until)
    return instance
