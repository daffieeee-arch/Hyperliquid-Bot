"""Pure market-data provenance and coverage contracts.

The Bronze records and subscription identities in this module are used by the
active schema-v2 capture path.  The coverage-mutation contracts remain dormant
until Phase 1A-3B1C-2.  Nothing here performs I/O or produces a schema-v3 event;
all values come from callers or pure deterministic derivation.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import ClassVar, Final, Self

from hyperliquid_bot.contracts import Instrument, InstrumentType, Venue

type CanonicalScalar = str | int | bool | None
type CanonicalValue = CanonicalScalar | tuple[CanonicalValue, ...]

_CODE_PATTERN: Final = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_POSITIVE_CANONICAL_DECIMAL_PATTERN: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")

# Every value copied or serialized by the dormant spine has a finite bound.
# The generous identifier limits retain long canonical Instrument identities;
# high-cardinality plan and outcome content is content-addressed separately.
MAX_OPAQUE_IDENTIFIER_LENGTH: Final = 4_096
MAX_CANONICAL_IDENTIFIER_LENGTH: Final = 8_388_608
MAX_CANONICAL_INSTRUMENT_ID_LENGTH: Final = 1_048_576
MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH: Final = 4_194_304
MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH: Final = 16_777_216
MAX_CANONICAL_SCALAR_TEXT_LENGTH: Final = 8_388_608
MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH: Final = 2_097_152
MAX_CAPABILITIES: Final = 7
MAX_BINANCE_STREAMS: Final = 1_024
MAX_WIRE_PARAMETERS: Final = 2
MAX_SUBSCRIPTION_SPECS: Final = 1_024
MAX_INSTRUMENT_BINDINGS: Final = 1_024
MAX_NORMALIZATION_BINDINGS: Final = 1_024
MAX_CONNECTION_WIRE_OPTIONS: Final = 1
MAX_SUBSCRIPTION_ATTEMPT_SNAPSHOTS: Final = 4_096
MAX_SUBSCRIPTION_ATTEMPTS: Final = 65_536
MAX_RAW_RECORD_SEQUENCE: Final = 65_536
MAX_COVERAGE_SCOPE_MEMBERS: Final = 1_024
MAX_COVERAGE_SCOPE_MERKLE_SIBLINGS: Final = 10
MAX_DELIVERY_BATCH_ITEMS: Final = 4_096
MAX_INSTRUMENT_PRICE_REFERENCES: Final = 32
MAX_METADATA_CATALOGUE_ITEMS: Final = 65_536
MAX_SOURCE_TIME_FACTS: Final = 32
MAX_SOURCE_SEQUENCE_RANGES: Final = 32
MAX_DECODED_EVENTS_PER_RAW_RECORD: Final = 4_096
MAX_NORMALIZATION_OUTCOME_ITEMS: Final = 4_096
MAX_NORMALIZATION_EVIDENCE_ITEMS: Final = 8
MAX_COVERAGE_TRANSITION_REFERENCES: Final = 1_024
MAX_COVERAGE_MUTATION_TARGETS: Final = 4_096
MAX_RAW_APPLICATION_MESSAGE_BYTES: Final = 1_048_576
MAX_CANONICAL_NESTING_DEPTH: Final = 32
MAX_CANONICAL_VALUE_NODES: Final = 100_000
MAX_UNSIGNED_64: Final = (1 << 64) - 1
MAX_COLLECTION_BOUND: Final = 65_536

_RAW_COVERAGE_FANOUT_BINDING_ID_VERSION: Final = "raw-coverage-fanout-binding-v1"
_RAW_COVERAGE_FANOUT_BINDING_CONTENT_VERSION: Final = "raw-coverage-fanout-binding-content-v1"
_RAW_COVERAGE_SNAPSHOT_CONTENT_VERSION: Final = "raw-coverage-fanout-snapshot-content-v1"
_IDENTIFIED_REJECTION_FANOUT_CONTENT_VERSION: Final = "coverage-fanout-proof-content-v2"
_IDENTIFIED_REJECTION_FANOUT_ID_VERSION: Final = "coverage-fanout-proof-v2"
_IDENTIFIED_REJECTION_SOURCE_VERSION: Final = "exact-identified-rejections-v1"
_IDENTIFIED_REJECTION_TARGET_VERSION: Final = "exact-identified-rejection-target-v1"
_IDENTIFIED_REJECTION_ACK_TARGET_VERSION: Final = "acknowledged-routed-target-v1"
_NORMALIZATION_OUTCOME_FRAME_STATUS_CODES: Final = frozenset(
    {
        "control_no_event",
        "valid_empty_market_frame",
        "materialized",
        "duplicates_only",
        "mixed_success",
        "rejected_before_indexing",
        "rejected_after_indexing",
        "source_event_conflict",
    }
)
_EXACT_ROUTED_NORMALIZATION_OUTCOME_FRAME_STATUS_CODES: Final = frozenset(
    {
        "materialized",
        "duplicates_only",
        "mixed_success",
        "rejected_after_indexing",
        "source_event_conflict",
    }
)
_PLAN_SLICE_NORMALIZATION_OUTCOME_FRAME_STATUS_CODES: Final = frozenset(
    {"rejected_before_indexing"}
)

_JSON_LOAD_FAILED: Final = object()


def _safe_diagnostic_label(value: object) -> str:
    """Return a bounded trusted label without retaining caller-controlled text."""

    del value
    return "value"


def require_text(value: object, *, field_name: str, maximum_length: int = 256) -> str:
    """Return exact printable text after bounded fail-closed validation."""

    field_name = _safe_diagnostic_label(field_name)
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    if type(maximum_length) is not int:
        raise TypeError("maximum_length must be a built-in integer.")
    if maximum_length <= 0 or maximum_length > MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH:
        raise ValueError("maximum_length is outside its supported finite bound.")
    if len(value) > maximum_length:
        raise ValueError(f"{field_name} exceeds its maximum length.")
    if not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{field_name} must be non-empty printable text without outer whitespace.")
    return value


def require_code(value: object, *, field_name: str) -> str:
    """Validate a bounded stable catalogue code."""

    field_name = _safe_diagnostic_label(field_name)
    text = require_text(value, field_name=field_name, maximum_length=128)
    if _CODE_PATTERN.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase stable catalogue code.")
    return text


def require_nonnegative_int(value: object, *, field_name: str) -> int:
    """Validate one exact non-negative integer within an unsigned-64 bound."""

    field_name = _safe_diagnostic_label(field_name)
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    if value > MAX_UNSIGNED_64:
        raise ValueError(f"{field_name} exceeds the supported unsigned-64 bound.")
    return value


def require_sha256(value: object, *, field_name: str) -> str:
    """Validate a lowercase hexadecimal SHA-256 digest."""

    field_name = _safe_diagnostic_label(field_name)
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest.")
    return value


def sha256_hex(value: object, *, field_name: str = "value") -> str:
    """Hash exact built-in bytes without accepting bytes-like aliases."""

    field_name = _safe_diagnostic_label(field_name)
    if type(value) is not bytes:
        raise TypeError(f"{field_name} must be built-in bytes.")
    return hashlib.sha256(value).hexdigest()


def canonical_utc_datetime(value: object, *, field_name: str) -> str:
    """Serialize an exact built-in zero-offset datetime with six fractional digits.

    The year is rendered explicitly rather than via platform-dependent ``%Y``.
    """

    field_name = _safe_diagnostic_label(field_name)
    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a built-in datetime.")
    valid_zero_offset = False
    if value.tzinfo is not None:
        try:
            valid_zero_offset = value.utcoffset() == timedelta(0)
        except Exception:
            pass
    if not valid_zero_offset:
        raise ValueError(f"{field_name} must be timezone-aware with a zero UTC offset.")
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}Z"
    )


def _canonical_value(
    value: object,
    *,
    field_name: str,
    depth: int = 0,
    remaining_nodes: list[int] | None = None,
) -> CanonicalValue:
    if depth > MAX_CANONICAL_NESTING_DEPTH:
        raise ValueError(f"{field_name} exceeds the canonical nesting-depth bound.")
    budget = [MAX_CANONICAL_VALUE_NODES] if remaining_nodes is None else remaining_nodes
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError(f"{field_name} exceeds the canonical value-node bound.")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value) > MAX_CANONICAL_SCALAR_TEXT_LENGTH:
            raise ValueError(f"{field_name} exceeds the canonical scalar-text bound.")
        return value
    if type(value) is int:
        if abs(value) > MAX_UNSIGNED_64:
            raise ValueError(f"{field_name} exceeds the canonical integer bound.")
        return value
    if isinstance(value, _Identifier):
        identifier_value = value.value
        if (
            type(identifier_value) is not str
            or len(identifier_value) > MAX_CANONICAL_SCALAR_TEXT_LENGTH
        ):
            raise ValueError(f"{field_name} contains an invalid identifier scalar.")
        return identifier_value
    if isinstance(value, StrEnum):
        enum_value = value.value
        if type(enum_value) is not str or len(enum_value) > MAX_CANONICAL_SCALAR_TEXT_LENGTH:
            raise ValueError(f"{field_name} contains an invalid enum scalar.")
        return enum_value
    if type(value) is tuple:
        if len(value) > MAX_COLLECTION_BOUND:
            raise ValueError(f"{field_name} exceeds the canonical array-item bound.")
        return tuple(
            _canonical_value(
                component,
                field_name=f"{field_name}[{index}]",
                depth=depth + 1,
                remaining_nodes=budget,
            )
            for index, component in enumerate(value)
        )
    raise TypeError(f"{field_name} must use canonical scalars, typed identifiers, enums or tuples.")


def canonical_json_array(
    components: tuple[object, ...],
    *,
    maximum_length: int = MAX_CANONICAL_IDENTIFIER_LENGTH,
) -> str:
    """Encode a bounded ordered preimage without maps or unordered collections."""

    if type(components) is not tuple:
        raise TypeError("components must be a built-in tuple.")
    if len(components) > MAX_COLLECTION_BOUND:
        raise ValueError("canonical JSON exceeds the top-level array-item bound.")
    if type(maximum_length) is not int:
        raise TypeError("maximum_length must be a built-in integer.")
    if maximum_length <= 0 or maximum_length > MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH:
        raise ValueError("maximum_length is outside its supported finite bound.")
    budget = [MAX_CANONICAL_VALUE_NODES]
    canonical = tuple(
        _canonical_value(
            component,
            field_name=f"components[{index}]",
            remaining_nodes=budget,
        )
        for index, component in enumerate(components)
    )
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    chunks: list[str] = []
    encoded_length = 0
    for chunk in encoder.iterencode(canonical):
        encoded_length += len(chunk)
        if encoded_length > maximum_length:
            raise ValueError("canonical JSON text exceeds its serialized-size bound.")
        chunks.append(chunk)
    return "".join(chunks)


def _reject_json_number(value: str) -> None:
    del value
    raise ValueError("canonical identifiers cannot contain floating-point JSON numbers.")


def _load_json_without_linked_parser_exception(text: str) -> object:
    """Parse JSON without linking the parser exception to the bounded domain error.

    The low-level exception is consumed inside this helper.  Callers raise their
    bounded domain error only after this function and its ``except`` block have
    returned, so neither ``JSONDecodeError.doc`` nor its exception object is linked
    through ``__cause__`` or ``__context__``.  Internal traceback frames remain a
    private implementation detail and must never cross the future runtime boundary.
    """

    loaded: object = _JSON_LOAD_FAILED
    try:
        loaded = json.loads(
            text,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        pass
    return loaded


def parse_canonical_json_array(
    value: object,
    *,
    field_name: str,
    maximum_length: int = MAX_CANONICAL_IDENTIFIER_LENGTH,
) -> tuple[CanonicalValue, ...]:
    """Return sanitized, byte-canonical JSON-array components.

    Rejected text is never interpolated into the bounded error surface or linked
    through a low-level parser exception. ``maximum_length`` is checked first.
    Internal exceptions and tracebacks are private and are not safe export values.
    """

    field_name = _safe_diagnostic_label(field_name)
    if type(maximum_length) is not int:
        raise TypeError("maximum_length must be a built-in integer.")
    if maximum_length <= 0 or maximum_length > MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH:
        raise ValueError("maximum_length is outside its supported finite bound.")
    text = require_text(value, field_name=field_name, maximum_length=maximum_length)
    loaded = _load_json_without_linked_parser_exception(text)
    if loaded is _JSON_LOAD_FAILED or type(loaded) is not list:
        raise ValueError(f"{field_name} must be a canonical compact JSON array.")
    if len(loaded) > MAX_COLLECTION_BOUND:
        raise ValueError("canonical JSON exceeds the top-level array-item bound.")
    budget = [MAX_CANONICAL_VALUE_NODES]
    components = tuple(
        _loaded_json_value(component, remaining_nodes=budget) for component in loaded
    )
    if canonical_json_array(components, maximum_length=maximum_length) != text:
        raise ValueError(f"{field_name} must use exact canonical JSON encoding.")
    return components


def require_collection_size(
    value: object,
    *,
    field_name: str,
    maximum_items: int,
    minimum_items: int = 0,
) -> None:
    """Validate a collection's count before iteration, copying, sorting or hashing."""

    field_name = _safe_diagnostic_label(field_name)
    if type(maximum_items) is not int or type(minimum_items) is not int:
        raise TypeError("collection bounds must be built-in integers.")
    if minimum_items < 0 or maximum_items < minimum_items or maximum_items > MAX_COLLECTION_BOUND:
        raise ValueError("collection bounds are outside the supported finite range.")
    if type(value) is list:
        count = len(value)
    elif type(value) is tuple:
        count = len(value)
    else:
        raise TypeError(f"{field_name} must be an exact finite list or tuple.")
    if count < minimum_items or count > maximum_items:
        raise ValueError(f"{field_name} item count is outside its supported finite bound.")


def _loaded_json_value(
    value: object,
    *,
    depth: int = 0,
    remaining_nodes: list[int] | None = None,
) -> CanonicalValue:
    if depth > MAX_CANONICAL_NESTING_DEPTH:
        raise ValueError("canonical JSON exceeds the nesting-depth bound.")
    budget = [MAX_CANONICAL_VALUE_NODES] if remaining_nodes is None else remaining_nodes
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("canonical JSON exceeds the value-node bound.")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value) > MAX_CANONICAL_SCALAR_TEXT_LENGTH:
            raise ValueError("canonical JSON scalar text exceeds its finite bound.")
        return value
    if type(value) is int:
        if abs(value) > MAX_UNSIGNED_64:
            raise ValueError("canonical JSON integer exceeds its finite bound.")
        return value
    if type(value) is list:
        if len(value) > MAX_COLLECTION_BOUND:
            raise ValueError("canonical JSON exceeds the array-item bound.")
        return tuple(
            _loaded_json_value(
                component,
                depth=depth + 1,
                remaining_nodes=budget,
            )
            for component in value
        )
    raise ValueError("canonical identifiers must contain only JSON scalars and arrays.")


def _wire_parameter_value(value: object, *, field_name: str) -> CanonicalValue:
    """Validate one exact JSON wire value while retaining scalar and array types."""

    if value is None or type(value) in (int, bool):
        return value  # type: ignore[return-value]
    if type(value) is str:
        return require_text(value, field_name=field_name, maximum_length=1024)
    if type(value) is tuple:
        return tuple(
            _wire_parameter_value(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{field_name} must be an exact JSON scalar or nested built-in tuple.")


def _component_text(value: CanonicalValue, *, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a JSON string.")
    return value


def _component_nonnegative_int(value: CanonicalValue, *, field_name: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_UNSIGNED_64:
        raise ValueError(f"{field_name} must be a bounded non-negative JSON integer.")
    return value


def _component_tuple(value: CanonicalValue, *, field_name: str) -> tuple[CanonicalValue, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field_name} must be a JSON array.")
    return value


def _require_component_count(
    components: tuple[CanonicalValue, ...],
    expected: int,
    *,
    identifier_name: str,
) -> None:
    if type(components) is not tuple:
        raise TypeError("canonical identifier components must be a built-in tuple.")
    if type(expected) is not int or expected < 0:
        raise ValueError("canonical identifier component count is invalid.")
    identifier_name = _safe_diagnostic_label(identifier_name)
    if len(components) != expected:
        raise ValueError(f"{identifier_name} must contain exactly {expected} components.")


def _canonical_utc_component(value: CanonicalValue, *, field_name: str) -> str:
    text = _component_text(value, field_name=field_name)
    if len(text) != 27 or text[4] != "-" or text[-1] != "Z":
        raise ValueError(f"{field_name} must use exact UTC canonical text.")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError:
        pass
    if parsed is None:
        raise ValueError(f"{field_name} must use exact UTC canonical text.")
    if canonical_utc_datetime(parsed, field_name=field_name) != text:
        raise ValueError(f"{field_name} must use exact UTC canonical text.")
    return text


def _instrument_from_canonical_component(
    value: CanonicalValue,
    *,
    field_name: str,
    native_symbol: str | None = None,
) -> Instrument:
    text = _component_text(value, field_name=field_name)
    components = parse_canonical_json_array(
        text,
        field_name=field_name,
        maximum_length=MAX_CANONICAL_INSTRUMENT_ID_LENGTH,
    )
    if len(components) != 7:
        raise ValueError(f"{field_name} must be a canonical instrument-v1 ID.")
    if components[0] != "instrument-v1":
        raise ValueError(f"{field_name} must use the instrument-v1 version tag.")
    venue: Venue | None = None
    instrument_type: InstrumentType | None = None
    try:
        venue = Venue(_component_text(components[1], field_name=f"{field_name}.venue"))
        instrument_type = InstrumentType(
            _component_text(components[2], field_name=f"{field_name}.instrument_type")
        )
    except ValueError:
        pass
    if venue is None or instrument_type is None:
        raise ValueError(f"{field_name} contains an unsupported instrument enum.")
    expiry = components[6]
    contract_expiry: date | None = None
    if expiry is not None:
        expiry_text = _component_text(expiry, field_name=f"{field_name}.contract_expiry")
        try:
            contract_expiry = date.fromisoformat(expiry_text)
            if contract_expiry.isoformat() != expiry_text:
                raise ValueError
        except ValueError:
            contract_expiry = None
        if contract_expiry is None:
            raise ValueError(f"{field_name}.contract_expiry must be an ISO date.")
    if instrument_type is InstrumentType.FUTURE and expiry is None:
        raise ValueError(f"{field_name} future identity requires contract expiry.")
    if instrument_type is not InstrumentType.FUTURE and expiry is not None:
        raise ValueError(f"{field_name} non-future identity forbids contract expiry.")
    instrument: Instrument | None = None
    try:
        instrument = Instrument(
            venue=venue,
            instrument_type=instrument_type,
            venue_market_id=_component_text(
                components[3], field_name=f"{field_name}.venue_market_id"
            ),
            base_asset=_component_text(components[4], field_name=f"{field_name}.base_asset"),
            quote_asset=_component_text(components[5], field_name=f"{field_name}.quote_asset"),
            native_symbol=(
                _component_text(components[3], field_name=f"{field_name}.venue_market_id")
                if native_symbol is None
                else require_text(
                    native_symbol,
                    field_name=f"{field_name}.native_symbol",
                    maximum_length=1024,
                )
            ),
            contract_expiry=contract_expiry,
        )
    except (TypeError, ValueError):
        pass
    if instrument is None:
        raise ValueError(f"{field_name} violates the instrument-v1 contract.")
    if instrument.canonical_instrument_id != text:
        raise ValueError(f"{field_name} must exactly match its reconstructed instrument-v1 ID.")
    return instrument


def validate_canonical_instrument_id(
    value: object,
    *,
    field_name: str = "canonical_instrument_id",
) -> str:
    """Return one structurally valid, byte-exact ``instrument-v1`` identity.

    The existing :class:`Instrument` contract deliberately has no generic
    metadata-text length limit.  This validator therefore reconstructs that
    contract instead of imposing the 256-character bound used for ordinary
    metadata labels.
    """

    field_name = _safe_diagnostic_label(field_name)
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    return _instrument_from_canonical_component(
        value, field_name=field_name
    ).canonical_instrument_id


def instrument_from_canonical_id(
    value: object,
    *,
    native_symbol: object,
) -> Instrument:
    """Reconstruct one exact Instrument from persisted canonical identity.

    ``native_symbol`` remains an explicitly supplied adapter alias and is never
    inferred from the venue-market identity.
    """

    if type(value) is not str:
        raise TypeError("canonical instrument identity must be a built-in string.")
    if type(native_symbol) is not str:
        raise TypeError("native symbol must be a built-in string.")
    return _instrument_from_canonical_component(
        value,
        field_name="canonical_instrument_id",
        native_symbol=native_symbol,
    )


def _positive_canonical_decimal_component(value: CanonicalValue, *, field_name: str) -> str:
    """Validate canonical multiplier text against the Decimal source bounds."""

    text = _component_text(value, field_name=field_name)
    if (
        not text
        or len(text) > 256
        or _POSITIVE_CANONICAL_DECIMAL_PATTERN.fullmatch(text) is None
        or text == "0"
    ):
        raise ValueError(f"{field_name} must be canonical positive Decimal text.")

    integer_portion, separator, fractional_portion = text.partition(".")
    if separator:
        if len(fractional_portion) > 128:
            raise ValueError(f"{field_name} exceeds the minimum Decimal exponent.")
        coefficient = f"{integer_portion}{fractional_portion}".lstrip("0")
        if len(coefficient) > 128:
            raise ValueError(f"{field_name} exceeds the coefficient-digit limit.")
    else:
        trailing_zeroes = len(integer_portion) - len(integer_portion.rstrip("0"))
        representable_exponent = min(trailing_zeroes, 128)
        if len(integer_portion) - representable_exponent > 128:
            raise ValueError(f"{field_name} exceeds the coefficient-digit limit.")
    return text


def _canonical_materialization_key_component(
    value: CanonicalValue,
    *,
    field_name: str,
) -> str:
    """Validate the lower-layer structure of a materialization-key-v1 text."""

    text = _component_text(value, field_name=field_name)
    components = parse_canonical_json_array(text, field_name=field_name)
    if len(components) != 6:
        raise ValueError(f"{field_name} must be a canonical materialization-key-v1 ID.")
    if components[0] != "materialization-key-v1":
        raise ValueError(f"{field_name} must use the materialization-key-v1 version tag.")
    NormalizationRunId(_component_text(components[1], field_name="normalization_run_id"))
    observation_text = _component_text(components[2], field_name="observation_key_id")
    observation = parse_canonical_json_array(
        observation_text,
        field_name="observation_key_id",
    )
    if len(observation) != 3 or observation[0] != "observation-key-v1":
        raise ValueError("observation_key_id must be canonical observation-key-v1 text.")
    RawRecordId(_component_text(observation[1], field_name="observation.raw_record_id"))
    _component_nonnegative_int(observation[2], field_name="observation.raw_event_index")
    require_code(
        _component_text(components[3], field_name="event_family"),
        field_name="event_family",
    )
    family_version = _component_nonnegative_int(
        components[4], field_name="event_family_schema_version"
    )
    if family_version == 0:
        raise ValueError("event_family_schema_version must be positive.")
    require_code(
        _component_text(components[5], field_name="payload_type"),
        field_name="payload_type",
    )
    return text


class ValidationFailureCategory(StrEnum):
    """Closed categories safe to expose beyond a private validation boundary."""

    INVALID_RUNTIME_TYPE = "invalid-runtime-type"
    INVALID_VALUE = "invalid-value"
    RESOURCE_BOUND_EXCEEDED = "resource-bound-exceeded"
    MALFORMED_CANONICAL_INPUT = "malformed-canonical-input"
    INTEGRITY_MISMATCH = "integrity-mismatch"
    INVARIANT_VIOLATION = "invariant-violation"
    LOCAL_VALIDATION_FAILURE = "local-validation-failure"


@dataclass(frozen=True, slots=True)
class SanitizedValidationFailure:
    """Category-only value safe for future external storage, logging or health.

    Internal validation exceptions, tracebacks and constructor locals are private
    implementation details. Phase 1A-3B1B must classify inside its private catch
    boundary, discard the caught exception, and expose only this immutable value.
    """

    category: ValidationFailureCategory

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("sanitized validation failures do not support subclass definition.")

    def __post_init__(self) -> None:
        if type(self) is not SanitizedValidationFailure:
            raise TypeError("sanitized validation failures do not support subclass construction.")
        if type(self.category) is not ValidationFailureCategory:
            raise TypeError("category must be a ValidationFailureCategory.")


@dataclass(frozen=True, slots=True)
class _Identifier:
    """Base for distinct immutable opaque or canonical identifier wrappers."""

    value: str

    def __post_init__(self) -> None:
        require_text(
            self.value,
            field_name=type(self).__name__,
            maximum_length=MAX_OPAQUE_IDENTIFIER_LENGTH,
        )


@dataclass(frozen=True, slots=True)
class _CanonicalIdentifier(_Identifier):
    """Canonical array identifier with one explicit finite serialized-size bound."""

    VERSION_TAG: ClassVar[str]

    def __post_init__(self) -> None:
        components = parse_canonical_json_array(
            self.value,
            field_name="canonical identifier",
            maximum_length=MAX_CANONICAL_IDENTIFIER_LENGTH,
        )
        if not components or components[0] != self.VERSION_TAG:
            raise ValueError("canonical identifier has an unexpected version tag.")
        invalid_components = False
        try:
            self._validate_components(components)
        except (TypeError, ValueError):
            invalid_components = True
        if invalid_components:
            raise ValueError("canonical identifier contains invalid canonical components.")

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        """Validate the exact persisted component schema for this ID type."""

        del components


@dataclass(frozen=True, slots=True)
class FeedProductId(_CanonicalIdentifier):
    """Canonical ``feed-product-v1`` identity text."""

    VERSION_TAG: ClassVar = "feed-product-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 9, identifier_name=type(self).__name__)
        valid_enums = True
        try:
            Venue(_component_text(components[1], field_name="venue"))
            FeedAccessRequirement(_component_text(components[5], field_name="access_requirement"))
            FeedEntitlementClass(_component_text(components[6], field_name="entitlement_class"))
            FeedTransport(_component_text(components[7], field_name="transport"))
            WireEncoding(_component_text(components[8], field_name="wire_encoding"))
        except ValueError:
            valid_enums = False
        if not valid_enums:
            raise ValueError("FeedProductId contains an unsupported enum value.")
        require_code(
            _component_text(components[2], field_name="source_environment"),
            field_name="source_environment",
        )
        require_code(
            _component_text(components[3], field_name="source_network"),
            field_name="source_network",
        )
        require_code(
            _component_text(components[4], field_name="product_code"),
            field_name="product_code",
        )


@dataclass(frozen=True, slots=True)
class FeedCapabilitySetId(_CanonicalIdentifier):
    """Canonical ``feed-capability-set-v1`` identity text."""

    VERSION_TAG: ClassVar = "feed-capability-set-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 3, identifier_name=type(self).__name__)
        FeedProductId(_component_text(components[1], field_name="feed_product_id"))
        capabilities = _component_tuple(components[2], field_name="capabilities")
        require_collection_size(
            capabilities,
            field_name="capabilities",
            maximum_items=MAX_CAPABILITIES,
        )
        values: tuple[FeedCapabilityCode, ...] | None = None
        try:
            values = tuple(
                FeedCapabilityCode(_component_text(item, field_name="capability"))
                for item in capabilities
            )
        except ValueError:
            pass
        if values is None:
            raise ValueError("FeedCapabilitySetId contains an unsupported capability.")
        if values != tuple(sorted(set(values), key=lambda item: item.value)):
            raise ValueError("FeedCapabilitySetId capabilities must be sorted and unique.")


@dataclass(frozen=True, slots=True)
class FeedCapabilitiesObservationId(_CanonicalIdentifier):
    """Canonical ``feed-capabilities-observation-v1`` identity text."""

    VERSION_TAG: ClassVar = "feed-capabilities-observation-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 6, identifier_name=type(self).__name__)
        FeedCapabilitySetId(_component_text(components[1], field_name="capability_set_id"))
        effective = _canonical_utc_component(components[2], field_name="effective_from")
        valid_basis = True
        try:
            EffectiveBoundaryBasis(_component_text(components[3], field_name="effective_basis"))
        except ValueError:
            valid_basis = False
        if not valid_basis:
            raise ValueError("unsupported effective boundary basis.")
        declared_until = components[4]
        if declared_until is not None:
            declared = _canonical_utc_component(declared_until, field_name="source_declared_until")
            if declared <= effective:
                raise ValueError("source_declared_until must be later than effective_from.")
        observed = _canonical_utc_component(components[5], field_name="observed_at")
        if components[3] == "first-observed" and effective != observed:
            raise ValueError("first-observed capability boundary must equal observed_at.")


@dataclass(frozen=True, slots=True)
class CollectorRunId(_Identifier):
    """Caller-supplied opaque identity for one logical collector run."""


@dataclass(frozen=True, slots=True)
class ConnectionSessionId(_CanonicalIdentifier):
    """Canonical ``connection-session-v1`` identity text."""

    VERSION_TAG: ClassVar = "connection-session-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 3, identifier_name=type(self).__name__)
        CollectorRunId(_component_text(components[1], field_name="collector_run_id"))
        _component_nonnegative_int(components[2], field_name="connection_ordinal")


@dataclass(frozen=True, slots=True)
class AdapterFeedBindingId(_CanonicalIdentifier):
    """Canonical ``adapter-feed-binding-v1`` identity text."""

    VERSION_TAG: ClassVar = "adapter-feed-binding-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 5, identifier_name=type(self).__name__)
        require_code(
            _component_text(components[1], field_name="adapter_code"),
            field_name="adapter_code",
        )
        feed = FeedProductId(_component_text(components[2], field_name="feed_product_id"))
        venue: Venue | None = None
        try:
            venue = Venue(_component_text(components[3], field_name="venue"))
        except ValueError:
            pass
        if venue is None:
            raise ValueError("AdapterFeedBindingId contains an unsupported venue.")
        if json.loads(feed.value)[1] != venue.value:
            raise ValueError("AdapterFeedBindingId venue must match its feed product.")
        valid_activation = True
        try:
            EventActivationRequirement(
                _component_text(components[4], field_name="event_activation_requirement")
            )
        except ValueError:
            valid_activation = False
        if not valid_activation:
            raise ValueError("AdapterFeedBindingId contains an unsupported activation requirement.")


@dataclass(frozen=True, slots=True)
class SubscriptionPlanId(_CanonicalIdentifier):
    """Bounded content-addressed ``subscription-plan-v1`` identity text."""

    VERSION_TAG: ClassVar = "subscription-plan-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        feed = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
        adapter = AdapterFeedBindingId(
            _component_text(components[2], field_name="adapter_feed_binding_id")
        )
        if json.loads(adapter.value)[2] != feed.value:
            raise ValueError("subscription plan adapter binding must match feed product.")
        require_sha256(
            _component_text(components[3], field_name="subscription_plan_content_sha256"),
            field_name="subscription_plan_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class SubscriptionSpecId(_CanonicalIdentifier):
    """Bounded content-addressed ``subscription-spec-v1`` identity text."""

    VERSION_TAG: ClassVar = "subscription-spec-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 6, identifier_name=type(self).__name__)
        feed = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
        method = require_text(
            _component_text(components[2], field_name="wire_method"),
            field_name="wire_method",
            maximum_length=128,
        )
        subscription_type = require_text(
            _component_text(components[3], field_name="wire_subscription_type"),
            field_name="wire_subscription_type",
            maximum_length=128,
        )
        parameter_count = _component_nonnegative_int(
            components[4],
            field_name="wire_parameter_count",
        )
        if parameter_count == 0 or parameter_count > MAX_WIRE_PARAMETERS:
            raise ValueError("wire parameter count is outside its finite bound.")
        require_sha256(
            _component_text(components[5], field_name="subscription_spec_content_sha256"),
            field_name="subscription_spec_content_sha256",
        )
        _validate_public_subscription_summary(
            feed_product_id=feed,
            wire_method=method,
            wire_subscription_type=subscription_type,
            parameter_count=parameter_count,
        )


@dataclass(frozen=True, slots=True)
class SubscriptionAttemptId(_CanonicalIdentifier):
    """Canonical ``subscription-attempt-v1`` identity text."""

    VERSION_TAG: ClassVar = "subscription-attempt-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        ConnectionSessionId(_component_text(components[1], field_name="connection_session_id"))
        SubscriptionSpecId(_component_text(components[2], field_name="subscription_spec_id"))
        _component_nonnegative_int(components[3], field_name="attempt_ordinal")


@dataclass(frozen=True, slots=True)
class RawRecordId(_CanonicalIdentifier):
    """Canonical ``raw-record-v1`` locator-plus-payload identity text."""

    VERSION_TAG: ClassVar = "raw-record-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 7, identifier_name=type(self).__name__)
        feed = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
        run = CollectorRunId(_component_text(components[2], field_name="collector_run_id"))
        session = ConnectionSessionId(
            _component_text(components[3], field_name="connection_session_id")
        )
        session_components = json.loads(session.value)
        if session_components[1] != run.value:
            raise ValueError("raw record session must belong to collector run.")
        del feed
        _component_nonnegative_int(components[4], field_name="ingress_ordinal")
        valid_frame_kind = True
        try:
            FrameKind(_component_text(components[5], field_name="frame_kind"))
        except ValueError:
            valid_frame_kind = False
        if not valid_frame_kind:
            raise ValueError("RawRecordId contains an unsupported frame kind.")
        require_sha256(
            _component_text(components[6], field_name="payload_sha256"),
            field_name="payload_sha256",
        )


@dataclass(frozen=True, slots=True)
class NormalizationRunId(_Identifier):
    """Caller-supplied opaque identity for one normalization run."""


@dataclass(frozen=True, slots=True)
class NormalizationOutcomeId(_CanonicalIdentifier):
    """Canonical identity for one complete frame-normalization outcome.

    Exact preimage::

        ["normalization-outcome-v1", normalization_run_id, raw_record_id,
         normalizer_version, normalizer_commit, frame_status,
         decoded_event_count_or_null, normalization_outcome_content_sha256]

    The identity lives in the lower provenance module so post-outcome sink
    evidence can reference the exact attempted outcome without reversing the
    module import direction.  ``market_event_v3`` re-exports this same type.
    """

    VERSION_TAG: ClassVar = "normalization-outcome-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 8, identifier_name=type(self).__name__)
        NormalizationRunId(_component_text(components[1], field_name="normalization_run_id"))
        RawRecordId(_component_text(components[2], field_name="raw_record_id"))
        require_text(components[3], field_name="normalizer_version")
        require_text(components[4], field_name="normalizer_commit")
        frame_status = _component_text(components[5], field_name="frame_status")
        if frame_status not in _NORMALIZATION_OUTCOME_FRAME_STATUS_CODES:
            raise ValueError("normalization outcome has an unsupported frame status.")
        if components[6] is not None:
            decoded_count = _component_nonnegative_int(
                components[6],
                field_name="decoded_event_count",
            )
            if decoded_count > MAX_DECODED_EVENTS_PER_RAW_RECORD:
                raise ValueError("normalization outcome decoded count is outside its bound.")
        require_sha256(
            _component_text(
                components[7],
                field_name="normalization_outcome_content_sha256",
            ),
            field_name="normalization_outcome_content_sha256",
        )


def _normalization_outcome_id_details(
    normalization_outcome_id: NormalizationOutcomeId,
) -> tuple[NormalizationRunId, RawRecordId, str]:
    components = parse_canonical_json_array(
        normalization_outcome_id.value,
        field_name="normalization_outcome_id",
    )
    return (
        NormalizationRunId(_component_text(components[1], field_name="normalization_run_id")),
        RawRecordId(_component_text(components[2], field_name="raw_record_id")),
        _component_text(components[5], field_name="frame_status"),
    )


@dataclass(frozen=True, slots=True)
class InstrumentSpecificationId(_CanonicalIdentifier):
    """Bounded digest identity for validated instrument-specification content.

    Exact preimage: ``["instrument-specification-v1",
    canonical_instrument_id_sha256, canonical_content_length,
    canonical_content_sha256]``.
    """

    VERSION_TAG: ClassVar = "instrument-specification-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        require_sha256(
            _component_text(components[1], field_name="canonical_instrument_id_sha256"),
            field_name="canonical_instrument_id_sha256",
        )
        content_length = _component_nonnegative_int(
            components[2], field_name="canonical_content_length"
        )
        if content_length == 0 or content_length > MAX_INSTRUMENT_SPECIFICATION_CONTENT_LENGTH:
            raise ValueError("instrument specification content length is outside its bound.")
        require_sha256(
            _component_text(components[3], field_name="canonical_content_sha256"),
            field_name="canonical_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class InstrumentMetadataObservationId(_CanonicalIdentifier):
    """Canonical identity for a point-in-time metadata observation."""

    VERSION_TAG: ClassVar = "instrument-metadata-observation-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 9, identifier_name=type(self).__name__)
        instrument = _instrument_from_canonical_component(
            components[1], field_name="canonical_instrument_id"
        )
        specification_id = InstrumentSpecificationId(
            _component_text(components[2], field_name="instrument_specification_id")
        )
        specification_components = parse_canonical_json_array(
            specification_id.value,
            field_name="instrument_specification_id",
        )
        instrument_sha256 = sha256_hex(
            instrument.canonical_instrument_id.encode("utf-8"),
            field_name="canonical_instrument_id",
        )
        if specification_components[1] != instrument_sha256:
            raise ValueError("metadata observation instrument must match its specification.")
        MetadataAuthorityId(_component_text(components[3], field_name="metadata_authority_id"))
        effective = _canonical_utc_component(components[4], field_name="effective_from")
        basis = _component_text(components[5], field_name="effective_basis")
        if basis not in {"source-declared", "first-observed"}:
            raise ValueError("unsupported metadata effective basis.")
        declared_until = components[6]
        if declared_until is not None:
            declared = _canonical_utc_component(declared_until, field_name="source_declared_until")
            if declared <= effective:
                raise ValueError("source_declared_until must be later than effective_from.")
        observed = _canonical_utc_component(components[7], field_name="observed_at")
        if basis == "first-observed" and effective != observed:
            raise ValueError("first-observed metadata requires effective_from == observed_at.")
        raw_record = components[8]
        if raw_record is not None:
            RawRecordId(_component_text(raw_record, field_name="raw_record_id"))


@dataclass(frozen=True, slots=True)
class MetadataAuthorityId(_Identifier):
    """Caller-supplied stable metadata authority identity."""


@dataclass(frozen=True, slots=True)
class CoverageScopeId(_CanonicalIdentifier):
    """Bounded content-addressed ``coverage-scope-v1`` identity text."""

    VERSION_TAG: ClassVar = "coverage-scope-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 10, identifier_name=type(self).__name__)
        domain: CoverageDomain | None = None
        try:
            domain = CoverageDomain(_component_text(components[1], field_name="coverage_domain"))
        except ValueError:
            pass
        if domain is None:
            raise ValueError("unsupported coverage domain.")
        feed = FeedProductId(_component_text(components[2], field_name="feed_product_id"))
        require_code(
            _component_text(components[3], field_name="event_family"),
            field_name="event_family",
        )
        family_version = _component_nonnegative_int(
            components[4], field_name="event_family_schema_version"
        )
        if family_version == 0:
            raise ValueError("event_family_schema_version must be positive.")
        require_code(
            _component_text(components[5], field_name="payload_type"),
            field_name="payload_type",
        )
        spec_count = _component_nonnegative_int(components[6], field_name="subscription_spec_count")
        instrument_count = _component_nonnegative_int(
            components[7], field_name="canonical_instrument_count"
        )
        if (
            spec_count == 0
            or spec_count > MAX_COVERAGE_SCOPE_MEMBERS
            or instrument_count == 0
            or instrument_count > MAX_COVERAGE_SCOPE_MEMBERS
        ):
            raise ValueError("coverage scope member counts are outside their finite bounds.")
        require_sha256(
            _component_text(components[8], field_name="subscription_spec_members_sha256"),
            field_name="subscription_spec_members_sha256",
        )
        require_sha256(
            _component_text(components[9], field_name="instrument_members_sha256"),
            field_name="instrument_members_sha256",
        )
        del feed


@dataclass(frozen=True, slots=True)
class CoverageEpochId(_CanonicalIdentifier):
    """Canonical ``coverage-epoch-v1`` identity text."""

    VERSION_TAG: ClassVar = "coverage-epoch-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 6, identifier_name=type(self).__name__)
        CoverageScopeId(_component_text(components[1], field_name="coverage_scope_id"))
        CollectorRunId(_component_text(components[2], field_name="collector_run_id"))
        _component_nonnegative_int(components[3], field_name="epoch_ordinal")
        _canonical_utc_component(components[4], field_name="activation_time")
        _component_nonnegative_int(components[5], field_name="activation_monotonic_ns")


@dataclass(frozen=True, slots=True)
class CoverageEvidenceId(_CanonicalIdentifier):
    """Bounded canonical identity for one typed, epoch-bound evidence record."""

    VERSION_TAG: ClassVar = "coverage-evidence-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _validate_coverage_evidence_id_components(components)


@dataclass(frozen=True, slots=True)
class NormalizationFailureEvidenceId(_CanonicalIdentifier):
    """Canonical lower-layer identity usable without importing market_event_v3."""

    VERSION_TAG: ClassVar = "normalization-failure-evidence-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 7, identifier_name=type(self).__name__)
        RawRecordId(_component_text(components[1], field_name="raw_record_id"))
        NormalizationRunId(_component_text(components[2], field_name="normalization_run_id"))
        if components[3] is not None:
            _component_nonnegative_int(components[3], field_name="raw_event_index")
            if components[4] is not None:
                SourceEventId(_component_text(components[4], field_name="source_event_id"))
        elif components[4] is not None:
            raise ValueError("pre-index normalization failure cannot name a source event.")
        category = _component_text(components[5], field_name="failure_category")
        if category not in {item.value for item in NormalizationFailureCategory}:
            raise ValueError("normalization failure evidence has an unsupported category.")
        CoverageScopeId(_component_text(components[6], field_name="identified_coverage_scope_id"))


@dataclass(frozen=True, slots=True)
class CoverageTransitionId(_CanonicalIdentifier):
    """Canonical ``coverage-transition-v1`` identity text."""

    VERSION_TAG: ClassVar = "coverage-transition-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 8, identifier_name=type(self).__name__)
        scope = CoverageScopeId(_component_text(components[1], field_name="coverage_scope_id"))
        epoch = CoverageEpochId(_component_text(components[2], field_name="coverage_epoch_id"))
        if json.loads(epoch.value)[1] != scope.value:
            raise ValueError("coverage transition epoch must belong to its scope.")
        ordinal = _component_nonnegative_int(components[3], field_name="transition_ordinal")
        if ordinal == 0:
            raise ValueError("coverage transition ordinal must be positive.")
        try:
            previous = CoverageStatus(_component_text(components[4], field_name="previous_status"))
            current = CoverageStatus(_component_text(components[5], field_name="new_status"))
            reason = CoverageReason(_component_text(components[6], field_name="coverage_reason"))
        except ValueError:
            previous = None
            current = None
            reason = None
        if previous is None or current is None or reason is None:
            raise ValueError("coverage transition contains an unsupported enum value.")
        if previous is current:
            raise ValueError("coverage transition must change status.")
        evidence = CoverageEvidenceId(
            _component_text(components[7], field_name="coverage_evidence_id")
        )
        evidence_components = parse_canonical_json_array(
            evidence.value,
            field_name="coverage_evidence_id",
        )
        if evidence_components[1] != scope.value or evidence_components[2] != epoch.value:
            raise ValueError("coverage transition evidence must belong to its scope and epoch.")
        scope_components = json.loads(scope.value)
        _validate_coverage_transition_semantics(
            domain=CoverageDomain(scope_components[1]),
            previous_status=previous,
            new_status=current,
            reason=reason,
            evidence_kind=CoverageEvidenceKind(
                _component_text(evidence_components[4], field_name="coverage_evidence_kind")
            ),
        )
        if evidence_components[4] == CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION.value:
            source = _component_tuple(
                evidence_components[5], field_name="upstream_transition_source"
            )
            committed = CommittedCoverageStateId(
                _component_text(source[1], field_name="committed_coverage_state_id")
            )
            upstream_details = _committed_coverage_state_id_details(committed)
            if current is not upstream_details[3]:
                raise ValueError("downstream transition status must match upstream status.")
            if (
                _component_nonnegative_int(
                    evidence_components[7], field_name="evidence_observed_monotonic_ns"
                )
                < upstream_details[6]
            ):
                raise ValueError("downstream transition cannot precede upstream evidence.")


@dataclass(frozen=True, slots=True)
class DeliveryAttemptId(_CanonicalIdentifier):
    """Canonical ``delivery-attempt-v1`` identity text."""

    VERSION_TAG: ClassVar = "delivery-attempt-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        require_code(
            _component_text(components[1], field_name="destination_id"),
            field_name="destination_id",
        )
        DeliveryBatchId(_component_text(components[2], field_name="delivery_batch_id"))
        _component_nonnegative_int(components[3], field_name="attempt_ordinal")


@dataclass(frozen=True, slots=True)
class DeliveryBatchId(_CanonicalIdentifier):
    """Bounded content-addressed ``delivery-batch-v1`` identity text."""

    VERSION_TAG: ClassVar = "delivery-batch-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 3, identifier_name=type(self).__name__)
        item_count = _component_nonnegative_int(components[1], field_name="item_count")
        if item_count == 0 or item_count > MAX_DELIVERY_BATCH_ITEMS:
            raise ValueError("delivery batch item_count is outside its supported bound.")
        require_sha256(
            _component_text(components[2], field_name="delivery_batch_content_sha256"),
            field_name="delivery_batch_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class SourceEventId(_Identifier):
    """Stable adapter-defined source-event identity, retained byte-for-byte."""


@dataclass(frozen=True, slots=True)
class SourceTransactionId(_Identifier):
    """Optional venue transaction provenance; not a global event key."""


@dataclass(frozen=True, slots=True)
class CorrelationId(_Identifier):
    """Optional caller-supplied operational correlation identity."""


class FeedAccessRequirement(StrEnum):
    """Access required by a feed product, never caller authorization."""

    PUBLIC_UNAUTHENTICATED = "public-unauthenticated"
    AUTHENTICATED_READ_ONLY = "authenticated-read-only"


class FeedEntitlementClass(StrEnum):
    """Stable product entitlement class, not credential state."""

    PUBLIC = "public"
    STANDARD = "standard"
    PRO = "pro"
    INSTITUTIONAL = "institutional"
    NODE_OPERATOR = "node-operator"


class FeedTransport(StrEnum):
    """Transport family required by a feed product."""

    WEBSOCKET = "websocket"
    HTTP_STREAM = "http-stream"
    MULTICAST = "multicast"
    FIX = "fix"


class WireEncoding(StrEnum):
    """Application wire encoding of a feed product."""

    JSON_TEXT = "json-text"
    JSON_BINARY = "json-binary"
    SBE = "sbe"
    FIX_TAG_VALUE = "fix-tagvalue"


@dataclass(frozen=True, slots=True)
class FeedProductIdentity:
    """Identity for one feed product, independent of caller authorization.

    ``feed_product_id`` preimage, in exact order::

        ["feed-product-v1", venue, source_environment, source_network,
         product_code, access_requirement, entitlement_class, transport,
         wire_encoding]
    """

    venue: Venue
    source_environment: str
    source_network: str
    product_code: str
    access_requirement: FeedAccessRequirement
    entitlement_class: FeedEntitlementClass
    transport: FeedTransport
    wire_encoding: WireEncoding
    feed_product_id: FeedProductId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.venue) is not Venue:
            raise TypeError("venue must be a Venue.")
        environment = require_code(self.source_environment, field_name="source_environment")
        network = require_code(self.source_network, field_name="source_network")
        product = require_code(self.product_code, field_name="product_code")
        if type(self.access_requirement) is not FeedAccessRequirement:
            raise TypeError("access_requirement must be a FeedAccessRequirement.")
        if type(self.entitlement_class) is not FeedEntitlementClass:
            raise TypeError("entitlement_class must be a FeedEntitlementClass.")
        if type(self.transport) is not FeedTransport:
            raise TypeError("transport must be a FeedTransport.")
        if type(self.wire_encoding) is not WireEncoding:
            raise TypeError("wire_encoding must be a WireEncoding.")
        object.__setattr__(
            self,
            "feed_product_id",
            FeedProductId(
                canonical_json_array(
                    (
                        "feed-product-v1",
                        self.venue.value,
                        environment,
                        network,
                        product,
                        self.access_requirement.value,
                        self.entitlement_class.value,
                        self.transport.value,
                        self.wire_encoding.value,
                    )
                )
            ),
        )


HYPERLIQUID_MAINNET_PUBLIC_TRADES: Final = FeedProductIdentity(
    venue=Venue.HYPERLIQUID,
    source_environment="production",
    source_network="mainnet",
    product_code="public-websocket-market-data",
    access_requirement=FeedAccessRequirement.PUBLIC_UNAUTHENTICATED,
    entitlement_class=FeedEntitlementClass.PUBLIC,
    transport=FeedTransport.WEBSOCKET,
    wire_encoding=WireEncoding.JSON_TEXT,
)

BINANCE_MAINNET_SPOT_JSON_STREAMS: Final = FeedProductIdentity(
    venue=Venue.BINANCE,
    source_environment="production",
    source_network="mainnet",
    product_code="spot-json-market-streams",
    access_requirement=FeedAccessRequirement.PUBLIC_UNAUTHENTICATED,
    entitlement_class=FeedEntitlementClass.PUBLIC,
    transport=FeedTransport.WEBSOCKET,
    wire_encoding=WireEncoding.JSON_TEXT,
)


class EventActivationRequirement(StrEnum):
    """Attempt state required before a feed binding may materialize an event."""

    ACKNOWLEDGED = "acknowledged"


class PublicSubscriptionParameterKind(StrEnum):
    """Closed public wire semantics supported by the dormant catalogue."""

    HYPERLIQUID_COIN = "hyperliquid-coin"
    BINANCE_REQUEST_ID = "binance-request-id"
    BINANCE_STREAMS = "binance-streams"


class PublicSourceSelectorKind(StrEnum):
    """Closed adapter selectors that may bind a public wire spec to an instrument."""

    HYPERLIQUID_COIN = "hyperliquid-coin"
    BINANCE_SPOT_TRADE_STREAM = "binance-spot-trade-stream"


def _validate_binance_spot_trade_stream(value: object) -> str:
    """Validate the closed public ``<lowercase-symbol>@trade`` stream shape."""

    stream = require_text(value, field_name="public stream name", maximum_length=1024)
    symbol = stream.removesuffix("@trade")
    if not symbol or stream != f"{symbol.lower()}@trade":
        raise ValueError("Binance Spot public streams must use lowercase <symbol>@trade.")
    return stream


@dataclass(frozen=True, slots=True)
class PublicSourceSelector:
    """Exact public source selector; never a credential or runtime control."""

    kind: PublicSourceSelectorKind
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not PublicSourceSelectorKind:
            raise TypeError("kind must be a PublicSourceSelectorKind.")
        selector = require_text(
            self.value,
            field_name="public source selector",
            maximum_length=1024,
        )
        if self.kind is PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM:
            _validate_binance_spot_trade_stream(selector)

    def canonical_components(self) -> tuple[str, str, str]:
        """Return the closed versioned nested selector preimage."""

        return ("public-source-selector-v1", self.kind.value, self.value)


@dataclass(frozen=True, slots=True)
class PublicSubscriptionParameter:
    """One explicitly allowed non-secret public subscription parameter.

    The semantic kind determines the exact wire name and runtime value shape;
    callers cannot introduce arbitrary parameter names such as authentication,
    account, signature, credential, endpoint or lifecycle controls.
    """

    kind: PublicSubscriptionParameterKind
    value: CanonicalValue = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not PublicSubscriptionParameterKind:
            raise TypeError("kind must be a PublicSubscriptionParameterKind.")
        if self.kind is PublicSubscriptionParameterKind.HYPERLIQUID_COIN:
            require_text(self.value, field_name="public coin", maximum_length=1024)
        elif self.kind is PublicSubscriptionParameterKind.BINANCE_REQUEST_ID:
            require_nonnegative_int(self.value, field_name="public integer parameter")
        elif self.kind is PublicSubscriptionParameterKind.BINANCE_STREAMS:
            if type(self.value) is not tuple or not self.value:
                raise TypeError("public stream parameters must be a non-empty built-in tuple.")
            require_collection_size(
                self.value,
                field_name="public stream parameters",
                maximum_items=MAX_BINANCE_STREAMS,
                minimum_items=1,
            )
            streams = tuple(_validate_binance_spot_trade_stream(item) for item in self.value)
            if streams != tuple(sorted(set(streams))):
                raise ValueError("public stream names must be sorted and unique.")
        else:  # pragma: no cover - exhaustive enum guard
            raise AssertionError("unhandled public subscription parameter kind")

    def canonical_wire_row(self) -> tuple[str, CanonicalValue]:
        """Return the exact public wire name and already validated value."""

        wire_name = {
            PublicSubscriptionParameterKind.HYPERLIQUID_COIN: "coin",
            PublicSubscriptionParameterKind.BINANCE_REQUEST_ID: "id",
            PublicSubscriptionParameterKind.BINANCE_STREAMS: "params",
        }[self.kind]
        return wire_name, self.value


class PublicConnectionOptionKind(StrEnum):
    """Closed non-operational connection semantics admitted to plan identity."""

    ENDPOINT_PROFILE = "endpoint-profile"


class PublicEndpointProfile(StrEnum):
    """Stable public endpoint catalogue profiles; never secret endpoint text."""

    HYPERLIQUID_PRODUCTION_MAINNET = "hyperliquid-production-mainnet-public"
    BINANCE_PRODUCTION_SPOT = "binance-production-spot-public"


@dataclass(frozen=True, slots=True)
class PublicConnectionOption:
    """One closed public-semantic connection option for plan identity."""

    kind: PublicConnectionOptionKind
    value: PublicEndpointProfile = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not PublicConnectionOptionKind:
            raise TypeError("kind must be a PublicConnectionOptionKind.")
        if type(self.value) is not PublicEndpointProfile:
            raise TypeError("value must be a PublicEndpointProfile.")

    def canonical_wire_row(self) -> tuple[str, str]:
        """Return the exact closed option name and public catalogue value."""

        return self.kind.value, self.value.value


def _validate_public_subscription_rows(
    *,
    feed_product_id: FeedProductId,
    wire_method: str,
    wire_subscription_type: str,
    wire_parameter_rows: tuple[tuple[str, CanonicalValue], ...],
) -> None:
    """Fail closed unless one exact supported public request shape is supplied."""

    names = tuple(name for name, _ in wire_parameter_rows)
    if feed_product_id == HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id:
        if wire_method != "subscribe" or wire_subscription_type != "trades":
            raise ValueError("unsupported public subscription request.")
        if names != ("coin",):
            raise ValueError("unsupported public subscription parameter shape.")
        require_text(wire_parameter_rows[0][1], field_name="public coin", maximum_length=1024)
        return

    if feed_product_id == BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id:
        if wire_method != "SUBSCRIBE" or wire_subscription_type != "@trade":
            raise ValueError("unsupported public subscription request.")
        if names != ("id", "params"):
            raise ValueError("unsupported public subscription parameter shape.")
        require_nonnegative_int(wire_parameter_rows[0][1], field_name="public request id")
        streams = wire_parameter_rows[1][1]
        if type(streams) is not tuple or not streams:
            raise ValueError("public streams must be a non-empty JSON array.")
        require_collection_size(
            streams,
            field_name="public streams",
            maximum_items=MAX_BINANCE_STREAMS,
            minimum_items=1,
        )
        validated_streams = tuple(_validate_binance_spot_trade_stream(item) for item in streams)
        if validated_streams != tuple(sorted(set(validated_streams))):
            raise ValueError("public stream names must be sorted and unique.")
        return

    raise ValueError("feed product has no supported public subscription contract.")


def _validate_public_subscription_summary(
    *,
    feed_product_id: FeedProductId,
    wire_method: str,
    wire_subscription_type: str,
    parameter_count: int,
) -> None:
    """Validate every closed semantic available in the bounded spec ID summary."""

    summary = (wire_method, wire_subscription_type, parameter_count)
    if feed_product_id == HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id:
        if summary != ("subscribe", "trades", 1):
            raise ValueError("unsupported Hyperliquid public subscription summary.")
        return
    if feed_product_id == BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id:
        if summary != ("SUBSCRIBE", "@trade", 2):
            raise ValueError("unsupported Binance public subscription summary.")
        return
    raise ValueError("feed product has no supported public subscription summary.")


def _validate_public_connection_option_rows(
    *,
    feed_product_id: FeedProductId,
    option_rows: tuple[tuple[str, str], ...],
) -> None:
    """Validate the one closed endpoint-profile option without endpoint secrets."""

    if (
        len(option_rows) != 1
        or option_rows[0][0] != PublicConnectionOptionKind.ENDPOINT_PROFILE.value
    ):
        raise ValueError("a subscription plan requires one public endpoint profile.")
    expected_profile = {
        HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id: (
            PublicEndpointProfile.HYPERLIQUID_PRODUCTION_MAINNET.value
        ),
        BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id: (
            PublicEndpointProfile.BINANCE_PRODUCTION_SPOT.value
        ),
    }.get(feed_product_id)
    if expected_profile is None or option_rows[0][1] != expected_profile:
        raise ValueError("public endpoint profile does not match the feed product.")


class FeedCapabilityCode(StrEnum):
    """Bounded descriptive feed capabilities; these never authorize access."""

    TRADES = "trades"
    BBO = "bbo"
    BOOK_SNAPSHOT = "book-snapshot"
    BOOK_DELTA = "book-delta"
    FUNDING = "funding"
    OPEN_INTEREST = "open-interest"
    LIQUIDATIONS = "liquidations"


class EffectiveBoundaryBasis(StrEnum):
    """Basis for a catalogue observation's effective boundary."""

    SOURCE_DECLARED = "source-declared"
    FIRST_OBSERVED = "first-observed"


@dataclass(frozen=True, slots=True)
class FeedCapabilitySet:
    """Content-addressed, sorted capability set.

    ID preimage: ``["feed-capability-set-v1", feed_product_id,
    [sorted capability codes]]``.
    """

    feed_product_id: FeedProductId
    capabilities: tuple[FeedCapabilityCode, ...]
    capability_set_id: FeedCapabilitySetId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.capabilities) is not tuple:
            raise TypeError("capabilities must be a built-in tuple.")
        require_collection_size(
            self.capabilities,
            field_name="capabilities",
            maximum_items=MAX_CAPABILITIES,
        )
        if any(type(capability) is not FeedCapabilityCode for capability in self.capabilities):
            raise TypeError("capabilities must contain only FeedCapabilityCode values.")
        expected = tuple(sorted(set(self.capabilities), key=lambda item: item.value))
        if self.capabilities != expected:
            raise ValueError("capabilities must be sorted and unique.")
        object.__setattr__(
            self,
            "capability_set_id",
            FeedCapabilitySetId(
                canonical_json_array(
                    (
                        "feed-capability-set-v1",
                        self.feed_product_id,
                        tuple(item.value for item in self.capabilities),
                    )
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class FeedCapabilitiesObservation:
    """Append-only point-in-time observation of feed capabilities.

    ID preimage: ``["feed-capabilities-observation-v1", capability_set_id,
    effective_from, basis, source_declared_until, observed_at]``.
    """

    capability_set: FeedCapabilitySet
    effective_from: datetime
    effective_basis: EffectiveBoundaryBasis
    source_declared_until: datetime | None
    observed_at: datetime
    observation_id: FeedCapabilitiesObservationId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.capability_set) is not FeedCapabilitySet:
            raise TypeError("capability_set must be a FeedCapabilitySet.")
        effective = canonical_utc_datetime(self.effective_from, field_name="effective_from")
        observed = canonical_utc_datetime(self.observed_at, field_name="observed_at")
        effective_datetime = datetime.fromisoformat(effective.replace("Z", "+00:00"))
        observed_datetime = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if type(self.effective_basis) is not EffectiveBoundaryBasis:
            raise TypeError("effective_basis must be an EffectiveBoundaryBasis.")
        declared_until: str | None = None
        if self.source_declared_until is not None:
            declared_until = canonical_utc_datetime(
                self.source_declared_until, field_name="source_declared_until"
            )
            declared_until_datetime = datetime.fromisoformat(declared_until.replace("Z", "+00:00"))
            if declared_until_datetime <= effective_datetime:
                raise ValueError("source_declared_until must be later than effective_from.")
        if self.effective_basis is EffectiveBoundaryBasis.FIRST_OBSERVED and effective != observed:
            raise ValueError("first-observed capability boundary must equal observed_at.")
        object.__setattr__(self, "effective_from", effective_datetime)
        object.__setattr__(self, "observed_at", observed_datetime)
        if declared_until is not None:
            object.__setattr__(
                self,
                "source_declared_until",
                datetime.fromisoformat(declared_until.replace("Z", "+00:00")),
            )
        object.__setattr__(
            self,
            "observation_id",
            FeedCapabilitiesObservationId(
                canonical_json_array(
                    (
                        "feed-capabilities-observation-v1",
                        self.capability_set.capability_set_id,
                        effective,
                        self.effective_basis.value,
                        declared_until,
                        observed,
                    )
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ConnectionSessionIdentity:
    """One transport session within a caller-identified collector run.

    ID preimage: ``["connection-session-v1", collector_run_id,
    connection_ordinal]``.
    """

    collector_run_id: CollectorRunId
    connection_ordinal: int
    connection_session_id: ConnectionSessionId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.collector_run_id) is not CollectorRunId:
            raise TypeError("collector_run_id must be a CollectorRunId.")
        ordinal = require_nonnegative_int(self.connection_ordinal, field_name="connection_ordinal")
        object.__setattr__(
            self,
            "connection_session_id",
            ConnectionSessionId(
                canonical_json_array(("connection-session-v1", self.collector_run_id, ordinal))
            ),
        )


def validate_subscription_spec_canonical_content(value: object) -> str:
    """Validate complete bounded public wire-spec content retained beside its ID."""

    components = parse_canonical_json_array(
        value,
        field_name="subscription_spec_canonical_content",
        maximum_length=MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
    )
    assert type(value) is str
    _require_component_count(
        components,
        5,
        identifier_name="subscription spec canonical content",
    )
    if components[0] != "subscription-spec-content-v1":
        raise ValueError("subscription spec content has an unsupported version tag.")
    feed = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
    method = require_text(
        _component_text(components[2], field_name="wire_method"),
        field_name="wire_method",
        maximum_length=128,
    )
    subscription_type = require_text(
        _component_text(components[3], field_name="wire_subscription_type"),
        field_name="wire_subscription_type",
        maximum_length=128,
    )
    parameter_values = _component_tuple(components[4], field_name="wire_parameters")
    require_collection_size(
        parameter_values,
        field_name="wire_parameters",
        maximum_items=MAX_WIRE_PARAMETERS,
        minimum_items=1,
    )
    validated: list[tuple[str, CanonicalValue]] = []
    for parameter_value in parameter_values:
        parameter = _component_tuple(parameter_value, field_name="wire_parameter")
        if len(parameter) != 2:
            raise ValueError("wire parameter must contain exactly two components.")
        validated.append(
            (
                require_text(
                    _component_text(parameter[0], field_name="wire_parameter_name"),
                    field_name="wire_parameter_name",
                    maximum_length=128,
                ),
                _wire_parameter_value(
                    parameter[1],
                    field_name="wire_parameter_value",
                ),
            )
        )
    rows = tuple(validated)
    names = tuple(name for name, _ in rows)
    if len(set(names)) != len(names) or rows != tuple(sorted(rows, key=lambda item: item[0])):
        raise ValueError("wire parameters must be sorted and unique.")
    _validate_public_subscription_rows(
        feed_product_id=feed,
        wire_method=method,
        wire_subscription_type=subscription_type,
        wire_parameter_rows=rows,
    )
    return value


def subscription_spec_id_from_canonical_content(value: object) -> SubscriptionSpecId:
    """Derive the bounded public wire-spec identity from validated full content."""

    content = validate_subscription_spec_canonical_content(value)
    components = parse_canonical_json_array(
        content,
        field_name="subscription_spec_canonical_content",
        maximum_length=MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
    )
    return SubscriptionSpecId(
        canonical_json_array(
            (
                "subscription-spec-v1",
                _component_text(components[1], field_name="feed_product_id"),
                _component_text(components[2], field_name="wire_method"),
                _component_text(components[3], field_name="wire_subscription_type"),
                len(_component_tuple(components[4], field_name="wire_parameters")),
                sha256_hex(content.encode("utf-8"), field_name="subscription_spec_content"),
            )
        )
    )


@dataclass(frozen=True, slots=True)
class SubscriptionSpecIdentity:
    """Exactly one outbound wire subscription request.

    Full content preimage: ``["subscription-spec-content-v1",
    feed_product_id, wire_method, wire_subscription_type,
    [[parameter-name, exact-value], ...]]``. Bounded ID preimage:
    ``["subscription-spec-v1", feed_product_id, wire_method,
    wire_subscription_type, parameter_count, content_sha256]``.
    Event family, family version and payload type are intentionally absent.
    """

    feed_product_id: FeedProductId
    wire_method: str
    wire_subscription_type: str
    wire_parameters: tuple[PublicSubscriptionParameter, ...]
    subscription_spec_canonical_content: str = field(init=False, repr=False)
    subscription_spec_content_sha256: str = field(init=False)
    subscription_spec_id: SubscriptionSpecId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        method = require_text(self.wire_method, field_name="wire_method", maximum_length=128)
        subscription_type = require_text(
            self.wire_subscription_type,
            field_name="wire_subscription_type",
            maximum_length=128,
        )
        if type(self.wire_parameters) is not tuple:
            raise TypeError("wire_parameters must be a built-in tuple.")
        require_collection_size(
            self.wire_parameters,
            field_name="wire_parameters",
            maximum_items=MAX_WIRE_PARAMETERS,
            minimum_items=1,
        )
        validated: list[tuple[str, CanonicalValue]] = []
        for parameter in self.wire_parameters:
            if type(parameter) is not PublicSubscriptionParameter:
                raise TypeError("wire_parameters must contain PublicSubscriptionParameter values.")
            validated.append(parameter.canonical_wire_row())
        names = tuple(name for name, _ in validated)
        if len(set(names)) != len(names):
            raise ValueError("wire parameter names must be unique.")
        expected = tuple(sorted(validated, key=lambda item: item[0]))
        if tuple(parameter.canonical_wire_row() for parameter in self.wire_parameters) != expected:
            raise ValueError("wire_parameters must be sorted by exact public wire name.")
        _validate_public_subscription_rows(
            feed_product_id=self.feed_product_id,
            wire_method=method,
            wire_subscription_type=subscription_type,
            wire_parameter_rows=expected,
        )
        content = canonical_json_array(
            (
                "subscription-spec-content-v1",
                self.feed_product_id,
                method,
                subscription_type,
                tuple(validated),
            ),
            maximum_length=MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
        )
        content_sha256 = sha256_hex(
            content.encode("utf-8"),
            field_name="subscription_spec_content",
        )
        object.__setattr__(self, "subscription_spec_canonical_content", content)
        object.__setattr__(self, "subscription_spec_content_sha256", content_sha256)
        object.__setattr__(
            self,
            "subscription_spec_id",
            subscription_spec_id_from_canonical_content(content),
        )


def _subscription_spec_parameter_rows(
    subscription_spec_canonical_content: str,
) -> tuple[tuple[str, CanonicalValue], ...]:
    components = parse_canonical_json_array(
        subscription_spec_canonical_content,
        field_name="subscription_spec_canonical_content",
        maximum_length=MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
    )
    rows = _component_tuple(components[4], field_name="wire_parameters")
    return tuple(
        (
            _component_text(
                _component_tuple(row, field_name="wire_parameter")[0],
                field_name="wire_parameter_name",
            ),
            _component_tuple(row, field_name="wire_parameter")[1],
        )
        for row in rows
    )


def _validate_unique_binance_request_ids(
    *,
    feed_product_id: FeedProductId,
    subscription_spec_contents: tuple[str, ...],
) -> None:
    if feed_product_id != BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id:
        return
    request_ids = tuple(
        dict(_subscription_spec_parameter_rows(content))["id"]
        for content in subscription_spec_contents
    )
    if len(set(request_ids)) != len(request_ids):
        raise ValueError("Binance subscription request IDs must be unique within one plan.")


def _validate_instrument_source_selector(
    *,
    instrument: Instrument,
    source_selector: PublicSourceSelector,
    subscription_spec_id: SubscriptionSpecId,
    subscription_spec_canonical_content: str,
    adapter_profile: str,
) -> None:
    """Validate the exact adapter/feed/instrument/wire-selector relationship."""

    reconstructed = _instrument_from_canonical_component(
        instrument.canonical_instrument_id,
        field_name="canonical_instrument_id",
        native_symbol=instrument.native_symbol,
    )
    if (
        reconstructed.venue is not instrument.venue
        or reconstructed.instrument_type is not instrument.instrument_type
        or reconstructed.base_asset != instrument.base_asset
        or reconstructed.quote_asset != instrument.quote_asset
        or reconstructed.venue_market_id != instrument.venue_market_id
        or reconstructed.native_symbol != instrument.native_symbol
        or reconstructed.contract_expiry != instrument.contract_expiry
    ):
        raise ValueError("instrument fields must exactly match canonical instrument identity.")
    derived_spec_id = subscription_spec_id_from_canonical_content(
        subscription_spec_canonical_content
    )
    if derived_spec_id != subscription_spec_id:
        raise ValueError("subscription spec content must match its content-addressed identity.")
    components = parse_canonical_json_array(
        subscription_spec_canonical_content,
        field_name="subscription_spec_canonical_content",
        maximum_length=MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
    )
    feed_product_id = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
    rows = dict(_subscription_spec_parameter_rows(subscription_spec_canonical_content))

    if feed_product_id == HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id:
        if adapter_profile != "hyperliquid-trades-v1":
            raise ValueError("Hyperliquid selector binding requires the public trades adapter.")
        if instrument.venue is not Venue.HYPERLIQUID:
            raise ValueError("Hyperliquid selector binding requires a Hyperliquid instrument.")
        if source_selector.kind is not PublicSourceSelectorKind.HYPERLIQUID_COIN:
            raise ValueError("Hyperliquid selector binding requires a coin selector.")
        if (
            source_selector.value != instrument.native_symbol
            or rows.get("coin") != source_selector.value
        ):
            raise ValueError("Hyperliquid coin, native symbol and source selector must match.")
        return

    if feed_product_id == BINANCE_MAINNET_SPOT_JSON_STREAMS.feed_product_id:
        if adapter_profile != "binance-spot-json-trade-v1":
            raise ValueError("Binance selector binding requires the Spot trade adapter.")
        if (
            instrument.venue is not Venue.BINANCE
            or instrument.instrument_type is not InstrumentType.SPOT
        ):
            raise ValueError("Binance Spot selector binding requires a Binance Spot instrument.")
        if source_selector.kind is not PublicSourceSelectorKind.BINANCE_SPOT_TRADE_STREAM:
            raise ValueError("Binance Spot selector binding requires an @trade stream selector.")
        expected_stream = f"{instrument.native_symbol.lower()}@trade"
        streams = rows.get("params")
        if (
            source_selector.value != expected_stream
            or type(streams) is not tuple
            or streams.count(source_selector.value) != 1
        ):
            raise ValueError(
                "Binance native symbol, trade stream and source selector must match exactly."
            )
        return

    raise ValueError("feed product has no supported instrument selector binding.")


@dataclass(frozen=True, slots=True)
class InstrumentSubscriptionBinding:
    """Exact Instrument-to-public-selector-to-wire-spec binding."""

    instrument: Instrument = field(repr=False)
    source_selector: PublicSourceSelector = field(repr=False)
    subscription_spec: SubscriptionSpecIdentity = field(repr=False)
    adapter_profile: str
    canonical_instrument_id: str = field(init=False, repr=False)
    instrument_native_symbol: str = field(init=False, repr=False)
    subscription_spec_id: SubscriptionSpecId = field(init=False)
    feed_product_id: FeedProductId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.instrument) is not Instrument:
            raise TypeError("instrument must be an Instrument.")
        if type(self.source_selector) is not PublicSourceSelector:
            raise TypeError("source_selector must be a PublicSourceSelector.")
        if type(self.subscription_spec) is not SubscriptionSpecIdentity:
            raise TypeError("subscription_spec must be a SubscriptionSpecIdentity.")
        adapter_profile = require_code(self.adapter_profile, field_name="adapter_profile")
        _validate_instrument_source_selector(
            instrument=self.instrument,
            source_selector=self.source_selector,
            subscription_spec_id=self.subscription_spec.subscription_spec_id,
            subscription_spec_canonical_content=(
                self.subscription_spec.subscription_spec_canonical_content
            ),
            adapter_profile=adapter_profile,
        )
        object.__setattr__(self, "canonical_instrument_id", self.instrument.canonical_instrument_id)
        object.__setattr__(self, "instrument_native_symbol", self.instrument.native_symbol)
        object.__setattr__(
            self,
            "subscription_spec_id",
            self.subscription_spec.subscription_spec_id,
        )
        object.__setattr__(self, "feed_product_id", self.subscription_spec.feed_product_id)

    def canonical_components(self) -> tuple[object, ...]:
        """Return the exact plan-content row, including the operational alias."""

        return (
            self.canonical_instrument_id,
            self.instrument_native_symbol,
            self.source_selector.canonical_components(),
            self.subscription_spec_id.value,
        )


@dataclass(frozen=True, slots=True)
class NormalizationBinding:
    """Plan-level semantic binding kept separate from wire-spec identity."""

    subscription_spec_id: SubscriptionSpecId
    adapter_profile: str
    event_family: str
    event_family_schema_version: int
    payload_type: str

    def __post_init__(self) -> None:
        if type(self.subscription_spec_id) is not SubscriptionSpecId:
            raise TypeError("subscription_spec_id must be a SubscriptionSpecId.")
        require_code(self.adapter_profile, field_name="adapter_profile")
        require_code(self.event_family, field_name="event_family")
        version = require_nonnegative_int(
            self.event_family_schema_version,
            field_name="event_family_schema_version",
        )
        if version == 0:
            raise ValueError("event_family_schema_version must be positive.")
        require_code(self.payload_type, field_name="payload_type")


def validate_subscription_plan_canonical_content(value: object) -> str:
    """Validate and return the complete canonical ``subscription-plan-content-v1``.

    The independently testable content may be large; the external
    :class:`SubscriptionPlanId` is a bounded SHA-256 content address.
    """

    components = parse_canonical_json_array(
        value,
        field_name="subscription plan canonical content",
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    assert type(value) is str
    _require_component_count(
        components,
        7,
        identifier_name="subscription plan canonical content",
    )
    if components[0] != "subscription-plan-content-v1":
        raise ValueError("subscription plan canonical content has an unsupported version tag.")

    feed = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
    feed_venue = Venue(json.loads(feed.value)[1])
    adapter = AdapterFeedBindingId(
        _component_text(components[2], field_name="adapter_feed_binding_id")
    )
    adapter_components = json.loads(adapter.value)
    if adapter_components[2] != feed.value:
        raise ValueError("subscription plan adapter binding must match feed product.")
    adapter_profile = adapter_components[1]

    spec_rows = _component_tuple(components[3], field_name="subscription_specs")
    require_collection_size(
        spec_rows,
        field_name="subscription_specs",
        maximum_items=MAX_SUBSCRIPTION_SPECS,
        minimum_items=1,
    )
    validated_specs: list[tuple[SubscriptionSpecId, str]] = []
    for spec_row_value in spec_rows:
        spec_row = _component_tuple(spec_row_value, field_name="subscription_spec")
        if len(spec_row) != 2:
            raise ValueError("subscription spec row must contain identity and canonical content.")
        spec_id = SubscriptionSpecId(
            _component_text(spec_row[0], field_name="subscription_spec_id")
        )
        spec_content = validate_subscription_spec_canonical_content(
            _component_text(
                spec_row[1],
                field_name="subscription_spec_canonical_content",
            )
        )
        if subscription_spec_id_from_canonical_content(spec_content) != spec_id:
            raise ValueError("subscription spec content must match its content-addressed identity.")
        validated_specs.append((spec_id, spec_content))
    specs = tuple(spec_id for spec_id, _ in validated_specs)
    expected_specs = tuple(sorted(set(specs), key=lambda item: item.value))
    if not specs or specs != expected_specs:
        raise ValueError("subscription plan specs must be non-empty, sorted and unique.")
    for spec_id, spec_content in validated_specs:
        spec_content_components = parse_canonical_json_array(
            spec_content,
            field_name="subscription_spec_canonical_content",
            maximum_length=MAX_SUBSCRIPTION_SPEC_CONTENT_LENGTH,
        )
        if spec_content_components[1] != feed.value:
            raise ValueError("subscription plan spec must match feed product.")
        del spec_id
    _validate_unique_binance_request_ids(
        feed_product_id=feed,
        subscription_spec_contents=tuple(content for _, content in validated_specs),
    )
    spec_values = {item.value for item in specs}
    spec_content_by_id = {spec_id.value: spec_content for spec_id, spec_content in validated_specs}

    instrument_rows = _component_tuple(components[4], field_name="instrument_bindings")
    require_collection_size(
        instrument_rows,
        field_name="instrument_bindings",
        maximum_items=MAX_INSTRUMENT_BINDINGS,
        minimum_items=1,
    )
    validated_instruments: list[tuple[str, str, tuple[str, str, str], str]] = []
    for row_value in instrument_rows:
        row = _component_tuple(row_value, field_name="instrument_binding")
        if len(row) != 4:
            raise ValueError("instrument binding must contain exactly four components.")
        native_symbol = require_text(
            _component_text(row[1], field_name="instrument_native_symbol"),
            field_name="instrument_native_symbol",
            maximum_length=1024,
        )
        instrument = _instrument_from_canonical_component(
            row[0],
            field_name="canonical_instrument_id",
            native_symbol=native_symbol,
        )
        if instrument.venue is not feed_venue:
            raise ValueError("subscription plan instrument must match feed-product venue.")
        selector_row = _component_tuple(row[2], field_name="public_source_selector")
        if len(selector_row) != 3 or selector_row[0] != "public-source-selector-v1":
            raise ValueError("instrument binding requires a versioned public source selector.")
        selector_kind: PublicSourceSelectorKind | None = None
        try:
            selector_kind = PublicSourceSelectorKind(
                _component_text(selector_row[1], field_name="source_selector_kind")
            )
        except ValueError:
            pass
        if selector_kind is None:
            raise ValueError("instrument binding contains an unsupported selector kind.")
        selector = PublicSourceSelector(
            selector_kind,
            _component_text(selector_row[2], field_name="source_selector_value"),
        )
        spec_id = SubscriptionSpecId(_component_text(row[3], field_name="subscription_spec_id"))
        if spec_id.value not in spec_values:
            raise ValueError("instrument binding spec must belong to plan.")
        _validate_instrument_source_selector(
            instrument=instrument,
            source_selector=selector,
            subscription_spec_id=spec_id,
            subscription_spec_canonical_content=spec_content_by_id[spec_id.value],
            adapter_profile=adapter_profile,
        )
        validated_instruments.append(
            (
                instrument.canonical_instrument_id,
                native_symbol,
                selector.canonical_components(),
                spec_id.value,
            )
        )
    if tuple(validated_instruments) != tuple(sorted(set(validated_instruments))):
        raise ValueError("instrument bindings must be sorted and unique.")
    selector_markets: dict[tuple[str, str], str] = {}
    instrument_selectors: dict[str, tuple[str, str]] = {}
    bound_selectors_by_spec: dict[str, list[str]] = {}
    for canonical_id, _native_symbol, selector_row, spec_value in validated_instruments:
        selector_key = (selector_row[1], selector_row[2])
        existing_market = selector_markets.get(selector_key)
        if existing_market is not None:
            raise ValueError("one public source selector must occur in exactly one plan binding.")
        selector_markets[selector_key] = canonical_id
        existing_selector = instrument_selectors.get(canonical_id)
        if existing_selector is not None and existing_selector != selector_key:
            raise ValueError("one canonical instrument must have exactly one source selector.")
        instrument_selectors[canonical_id] = selector_key
        bound_selectors_by_spec.setdefault(spec_value, []).append(selector_row[2])
    for spec in specs:
        parameters = dict(_subscription_spec_parameter_rows(spec_content_by_id[spec.value]))
        expected_selectors = (
            (parameters["coin"],)
            if feed == HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id
            else parameters["params"]
        )
        if type(expected_selectors) is not tuple:
            raise ValueError("subscription spec selectors must form an exact tuple.")
        expected_selector_texts = tuple(
            _component_text(item, field_name="public_source_selector")
            for item in expected_selectors
        )
        actual_selectors = tuple(sorted(bound_selectors_by_spec.get(spec.value, [])))
        if actual_selectors != tuple(sorted(expected_selector_texts)):
            raise ValueError("every public wire selector must have exactly one instrument binding.")

    normalization_rows = _component_tuple(components[5], field_name="normalization_bindings")
    require_collection_size(
        normalization_rows,
        field_name="normalization_bindings",
        maximum_items=MAX_NORMALIZATION_BINDINGS,
        minimum_items=1,
    )
    validated_normalization: list[tuple[str, str, str, int, str]] = []
    for row_value in normalization_rows:
        row = _component_tuple(row_value, field_name="normalization_binding")
        if len(row) != 5:
            raise ValueError("normalization binding must contain exactly five components.")
        spec_id = SubscriptionSpecId(_component_text(row[0], field_name="subscription_spec_id"))
        if spec_id.value not in spec_values:
            raise ValueError("normalization binding spec must belong to plan.")
        normalization_adapter_profile = require_code(
            _component_text(row[1], field_name="adapter_profile"),
            field_name="adapter_profile",
        )
        if normalization_adapter_profile != adapter_profile:
            raise ValueError("normalization adapter profile must match the plan adapter binding.")
        event_family = require_code(
            _component_text(row[2], field_name="event_family"),
            field_name="event_family",
        )
        family_version = _component_nonnegative_int(
            row[3], field_name="event_family_schema_version"
        )
        if family_version == 0:
            raise ValueError("event family schema version must be positive.")
        payload_type = require_code(
            _component_text(row[4], field_name="payload_type"),
            field_name="payload_type",
        )
        validated_normalization.append(
            (
                spec_id.value,
                normalization_adapter_profile,
                event_family,
                family_version,
                payload_type,
            )
        )
    if tuple(validated_normalization) != tuple(sorted(set(validated_normalization))):
        raise ValueError("normalization bindings must be sorted and unique.")

    option_values = _component_tuple(components[6], field_name="connection_wire_options")
    require_collection_size(
        option_values,
        field_name="connection_wire_options",
        maximum_items=MAX_CONNECTION_WIRE_OPTIONS,
        minimum_items=1,
    )
    validated_options: list[tuple[str, str]] = []
    for row_value in option_values:
        row = _component_tuple(row_value, field_name="connection_wire_option")
        if len(row) != 2:
            raise ValueError("connection wire option must contain exactly two components.")
        validated_options.append(
            (
                _component_text(row[0], field_name="connection_option_name"),
                _component_text(row[1], field_name="connection_option_value"),
            )
        )
    options = tuple(validated_options)
    if options != tuple(sorted(set(options))):
        raise ValueError("connection wire options must be sorted and unique.")
    _validate_public_connection_option_rows(feed_product_id=feed, option_rows=options)
    return value


def subscription_plan_id_from_canonical_content(value: object) -> SubscriptionPlanId:
    """Derive the bounded plan ID from independently validated canonical content."""

    canonical = validate_subscription_plan_canonical_content(value)
    loaded = json.loads(canonical)
    feed = FeedProductId(loaded[1])
    digest = sha256_hex(canonical.encode("utf-8"), field_name="subscription plan content")
    adapter = AdapterFeedBindingId(loaded[2])
    return SubscriptionPlanId(canonical_json_array(("subscription-plan-v1", feed, adapter, digest)))


@dataclass(frozen=True, slots=True)
class SubscriptionPlanIdentity:
    """Complete desired subscription configuration.

    Independently validated content preimage::

        ["subscription-plan-content-v1", feed_product_id, adapter_feed_binding_id,
         [[sorted spec ID, complete validated spec content], ...],
         [[canonical instrument ID, native symbol, source selector, spec ID], ...],
         [[spec ID, adapter profile, family, family version, payload type], ...],
         [[wire option name, wire option value], ...]]

    Bounded ID preimage::

        ["subscription-plan-v1", feed_product_id, adapter_feed_binding_id,
         content_sha256]
    """

    feed_product_id: FeedProductId
    adapter_feed_binding_id: AdapterFeedBindingId
    subscription_specs: tuple[SubscriptionSpecIdentity, ...]
    instrument_bindings: tuple[InstrumentSubscriptionBinding, ...]
    normalization_bindings: tuple[NormalizationBinding, ...]
    connection_wire_options: tuple[PublicConnectionOption, ...]
    subscription_plan_canonical_content: str = field(init=False, repr=False)
    subscription_plan_content_sha256: str = field(init=False)
    subscription_plan_id: SubscriptionPlanId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.adapter_feed_binding_id) is not AdapterFeedBindingId:
            raise TypeError("adapter_feed_binding_id must be an AdapterFeedBindingId.")
        adapter_profile = json.loads(self.adapter_feed_binding_id.value)[1]
        if type(self.subscription_specs) is not tuple or not self.subscription_specs:
            raise ValueError("subscription_specs must be a non-empty built-in tuple.")
        require_collection_size(
            self.subscription_specs,
            field_name="subscription_specs",
            maximum_items=MAX_SUBSCRIPTION_SPECS,
            minimum_items=1,
        )
        if any(type(spec) is not SubscriptionSpecIdentity for spec in self.subscription_specs):
            raise TypeError("subscription_specs must contain SubscriptionSpecIdentity values.")
        spec_ids = tuple(spec.subscription_spec_id for spec in self.subscription_specs)
        expected_spec_ids = tuple(sorted(set(spec_ids), key=lambda item: item.value))
        if spec_ids != expected_spec_ids:
            raise ValueError("subscription_specs must be sorted and unique by canonical ID.")
        if any(spec.feed_product_id != self.feed_product_id for spec in self.subscription_specs):
            raise ValueError("every subscription spec must belong to the plan feed product.")
        _validate_unique_binance_request_ids(
            feed_product_id=self.feed_product_id,
            subscription_spec_contents=tuple(
                spec.subscription_spec_canonical_content for spec in self.subscription_specs
            ),
        )
        spec_id_set = set(spec_ids)

        if type(self.instrument_bindings) is not tuple:
            raise TypeError("instrument_bindings must be a built-in tuple.")
        require_collection_size(
            self.instrument_bindings,
            field_name="instrument_bindings",
            maximum_items=MAX_INSTRUMENT_BINDINGS,
            minimum_items=1,
        )
        if any(
            type(binding) is not InstrumentSubscriptionBinding
            for binding in self.instrument_bindings
        ):
            raise TypeError("instrument_bindings contain an invalid value.")
        if any(
            binding.subscription_spec_id not in spec_id_set for binding in self.instrument_bindings
        ):
            raise ValueError("instrument binding references a spec outside the plan.")
        if any(
            binding.feed_product_id != self.feed_product_id for binding in self.instrument_bindings
        ):
            raise ValueError("instrument binding feed must match the subscription plan.")
        if any(binding.adapter_profile != adapter_profile for binding in self.instrument_bindings):
            raise ValueError("instrument selector adapter profile must match the plan binding.")
        instrument_rows = tuple(
            binding.canonical_components() for binding in self.instrument_bindings
        )
        if instrument_rows != tuple(sorted(set(instrument_rows))):
            raise ValueError("instrument_bindings must be sorted and unique.")
        selector_markets: dict[tuple[str, str], str] = {}
        instrument_selectors: dict[str, tuple[str, str]] = {}
        bound_selectors_by_spec: dict[SubscriptionSpecId, list[str]] = {}
        for binding in self.instrument_bindings:
            selector_key = (binding.source_selector.kind.value, binding.source_selector.value)
            existing_market = selector_markets.get(selector_key)
            if existing_market is not None:
                raise ValueError(
                    "one public source selector must occur in exactly one plan binding."
                )
            selector_markets[selector_key] = binding.canonical_instrument_id
            existing_selector = instrument_selectors.get(binding.canonical_instrument_id)
            if existing_selector is not None and existing_selector != selector_key:
                raise ValueError("one canonical instrument must have exactly one source selector.")
            instrument_selectors[binding.canonical_instrument_id] = selector_key
            bound_selectors_by_spec.setdefault(binding.subscription_spec_id, []).append(
                binding.source_selector.value
            )
        for spec in self.subscription_specs:
            parameters = dict(
                _subscription_spec_parameter_rows(spec.subscription_spec_canonical_content)
            )
            expected_selectors = (
                (parameters["coin"],)
                if self.feed_product_id == HYPERLIQUID_MAINNET_PUBLIC_TRADES.feed_product_id
                else parameters["params"]
            )
            if type(expected_selectors) is not tuple:
                raise ValueError("subscription spec selectors must form an exact tuple.")
            expected_selector_texts = tuple(
                _component_text(item, field_name="public_source_selector")
                for item in expected_selectors
            )
            actual_selectors = tuple(
                sorted(bound_selectors_by_spec.get(spec.subscription_spec_id, []))
            )
            if actual_selectors != tuple(sorted(expected_selector_texts)):
                raise ValueError(
                    "every public wire selector must have exactly one instrument binding."
                )

        if type(self.normalization_bindings) is not tuple:
            raise TypeError("normalization_bindings must be a built-in tuple.")
        require_collection_size(
            self.normalization_bindings,
            field_name="normalization_bindings",
            maximum_items=MAX_NORMALIZATION_BINDINGS,
            minimum_items=1,
        )
        if any(
            type(binding) is not NormalizationBinding for binding in self.normalization_bindings
        ):
            raise TypeError("normalization_bindings contain an invalid value.")
        if any(
            binding.subscription_spec_id not in spec_id_set
            for binding in self.normalization_bindings
        ):
            raise ValueError("normalization binding references a spec outside the plan.")
        if any(
            binding.adapter_profile != adapter_profile for binding in self.normalization_bindings
        ):
            raise ValueError("normalization adapter profile must match the plan adapter binding.")
        normalization_rows = tuple(
            (
                binding.subscription_spec_id.value,
                binding.adapter_profile,
                binding.event_family,
                binding.event_family_schema_version,
                binding.payload_type,
            )
            for binding in self.normalization_bindings
        )
        if normalization_rows != tuple(sorted(set(normalization_rows))):
            raise ValueError("normalization_bindings must be sorted and unique.")

        if type(self.connection_wire_options) is not tuple:
            raise TypeError("connection_wire_options must be a built-in tuple.")
        require_collection_size(
            self.connection_wire_options,
            field_name="connection_wire_options",
            maximum_items=MAX_CONNECTION_WIRE_OPTIONS,
            minimum_items=1,
        )
        option_rows: list[tuple[str, str]] = []
        for option in self.connection_wire_options:
            if type(option) is not PublicConnectionOption:
                raise TypeError(
                    "connection_wire_options must contain PublicConnectionOption values."
                )
            option_rows.append(option.canonical_wire_row())
        options = tuple(option_rows)
        if options != tuple(sorted(set(options))):
            raise ValueError("connection_wire_options must be sorted and unique.")
        _validate_public_connection_option_rows(
            feed_product_id=self.feed_product_id,
            option_rows=options,
        )

        content = canonical_json_array(
            (
                "subscription-plan-content-v1",
                self.feed_product_id,
                self.adapter_feed_binding_id,
                tuple(
                    (
                        spec.subscription_spec_id.value,
                        spec.subscription_spec_canonical_content,
                    )
                    for spec in self.subscription_specs
                ),
                instrument_rows,
                normalization_rows,
                options,
            ),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )
        plan_id = subscription_plan_id_from_canonical_content(content)
        object.__setattr__(self, "subscription_plan_canonical_content", content)
        object.__setattr__(
            self,
            "subscription_plan_content_sha256",
            sha256_hex(content.encode("utf-8"), field_name="subscription plan content"),
        )
        object.__setattr__(self, "subscription_plan_id", plan_id)


@dataclass(frozen=True, slots=True)
class SubscriptionAttemptIdentity:
    """One concrete subscription send in one session.

    ID preimage: ``["subscription-attempt-v1", connection_session_id,
    subscription_spec_id, attempt_ordinal]``.
    """

    connection_session: ConnectionSessionIdentity
    subscription_spec: SubscriptionSpecIdentity
    attempt_ordinal: int
    subscription_attempt_id: SubscriptionAttemptId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")
        if type(self.subscription_spec) is not SubscriptionSpecIdentity:
            raise TypeError("subscription_spec must be a SubscriptionSpecIdentity.")
        ordinal = require_nonnegative_int(self.attempt_ordinal, field_name="attempt_ordinal")
        object.__setattr__(
            self,
            "subscription_attempt_id",
            SubscriptionAttemptId(
                canonical_json_array(
                    (
                        "subscription-attempt-v1",
                        self.connection_session.connection_session_id,
                        self.subscription_spec.subscription_spec_id,
                        ordinal,
                    )
                )
            ),
        )


class SubscriptionAttemptStatus(StrEnum):
    """Status known for one subscription send attempt."""

    PENDING = "pending"
    SEND_STARTED = "send-started"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True, slots=True)
class SubscriptionAttemptSnapshot:
    """Immutable attempt status as known when a raw record is constructed.

    A future raw ACK record contains the pre-parse snapshot, normally ``SENT``
    or possibly ``SEND_STARTED``.  The later ACK transition is a separate
    immutable record and never mutates Bronze.
    """

    subscription_attempt: SubscriptionAttemptIdentity
    attempt_status: SubscriptionAttemptStatus

    def __post_init__(self) -> None:
        if type(self.subscription_attempt) is not SubscriptionAttemptIdentity:
            raise TypeError("subscription_attempt must be a SubscriptionAttemptIdentity.")
        if type(self.attempt_status) is not SubscriptionAttemptStatus:
            raise TypeError("attempt_status must be a SubscriptionAttemptStatus.")

    @property
    def subscription_spec(self) -> SubscriptionSpecIdentity:
        """Return the structured parent wire spec."""

        return self.subscription_attempt.subscription_spec


@dataclass(frozen=True, slots=True)
class SubscriptionAttemptTransition:
    """Pure immutable transition; runtime append-only storage belongs to 3B1B."""

    subscription_attempt: SubscriptionAttemptIdentity
    previous_status: SubscriptionAttemptStatus
    new_status: SubscriptionAttemptStatus

    def __post_init__(self) -> None:
        if type(self.subscription_attempt) is not SubscriptionAttemptIdentity:
            raise TypeError("subscription_attempt must be a SubscriptionAttemptIdentity.")
        if type(self.previous_status) is not SubscriptionAttemptStatus:
            raise TypeError("previous_status must be a SubscriptionAttemptStatus.")
        if type(self.new_status) is not SubscriptionAttemptStatus:
            raise TypeError("new_status must be a SubscriptionAttemptStatus.")
        if (self.previous_status, self.new_status) not in _ATTEMPT_TRANSITIONS:
            raise ValueError("subscription attempt transition is not allowed.")


_ATTEMPT_TRANSITIONS: Final = frozenset(
    {
        (SubscriptionAttemptStatus.PENDING, SubscriptionAttemptStatus.SEND_STARTED),
        (SubscriptionAttemptStatus.SEND_STARTED, SubscriptionAttemptStatus.SENT),
        (SubscriptionAttemptStatus.SEND_STARTED, SubscriptionAttemptStatus.ACKNOWLEDGED),
        (SubscriptionAttemptStatus.SENT, SubscriptionAttemptStatus.ACKNOWLEDGED),
        (SubscriptionAttemptStatus.ACKNOWLEDGED, SubscriptionAttemptStatus.ACKNOWLEDGED),
    }
)


def reduce_subscription_attempt_status(
    snapshot: SubscriptionAttemptSnapshot,
    requested_status: SubscriptionAttemptStatus,
) -> tuple[SubscriptionAttemptTransition, SubscriptionAttemptSnapshot]:
    """Apply the ACK-race-compatible transition matrix without mutable state."""

    if type(snapshot) is not SubscriptionAttemptSnapshot:
        raise TypeError("snapshot must be a SubscriptionAttemptSnapshot.")
    if type(requested_status) is not SubscriptionAttemptStatus:
        raise TypeError("requested_status must be a SubscriptionAttemptStatus.")
    transition_key = (snapshot.attempt_status, requested_status)
    if transition_key not in _ATTEMPT_TRANSITIONS:
        raise ValueError("requested subscription attempt transition is not allowed.")
    transition = SubscriptionAttemptTransition(
        subscription_attempt=snapshot.subscription_attempt,
        previous_status=snapshot.attempt_status,
        new_status=requested_status,
    )
    return transition, SubscriptionAttemptSnapshot(snapshot.subscription_attempt, requested_status)


def validate_subscription_attempt_sequence(
    attempts: Sequence[SubscriptionAttemptIdentity],
) -> tuple[SubscriptionAttemptIdentity, ...]:
    """Validate zero-based contiguous ordinals per explicitly supplied session/spec group."""

    if type(attempts) not in (list, tuple):
        raise TypeError("attempts must be an exact finite list or tuple.")
    require_collection_size(
        attempts,
        field_name="attempts",
        maximum_items=MAX_SUBSCRIPTION_ATTEMPTS,
    )
    validated = tuple(attempts)
    grouped_ordinals: dict[tuple[ConnectionSessionId, SubscriptionSpecId], list[int]] = {}
    seen_ids: set[SubscriptionAttemptId] = set()
    for attempt in validated:
        if type(attempt) is not SubscriptionAttemptIdentity:
            raise TypeError("attempts must contain only SubscriptionAttemptIdentity values.")
        if attempt.subscription_attempt_id in seen_ids:
            raise ValueError("subscription attempt sequence contains a duplicate identity.")
        seen_ids.add(attempt.subscription_attempt_id)
        key = (
            attempt.connection_session.connection_session_id,
            attempt.subscription_spec.subscription_spec_id,
        )
        grouped_ordinals.setdefault(key, []).append(attempt.attempt_ordinal)
    for ordinals in grouped_ordinals.values():
        if sorted(ordinals) != list(range(len(ordinals))):
            raise ValueError(
                "subscription attempt ordinals must be contiguous from zero per session and spec."
            )
    return validated


class FrameKind(StrEnum):
    """Original WebSocket application-message kind returned by ordinary recv()."""

    TEXT = "text"
    BINARY = "binary"


def _attempt_snapshot_rows(
    snapshots: tuple[SubscriptionAttemptSnapshot, ...],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            snapshot.subscription_spec.subscription_spec_id.value,
            snapshot.subscription_attempt.subscription_attempt_id.value,
            snapshot.attempt_status.value,
        )
        for snapshot in snapshots
    )


@dataclass(frozen=True, slots=True)
class RawMarketDataRecord:
    """Dormant immutable Bronze application-message contract.

    ``raw_record_id`` binds locator identity and payload integrity only::

        ["raw-record-v1", feed_product_id, collector_run_id,
         connection_session_id, ingress_ordinal, frame_kind, payload_sha256]

    ``full_record_integrity_sha256`` hashes this full metadata preimage::

        ["raw-record-content-v1", raw_record_schema_version, raw_record_id,
         subscription_plan_id,
         [[subscription_spec_id, subscription_attempt_id, attempt_status], ...],
         received_time, received_monotonic_ns, collector_version,
         collector_commit, payload_length, payload_sha256]

    This constructor does not prove runtime capture order, sink acceptance,
    persistence, coverage transitions, run completeness or tail integrity.
    """

    feed_product: FeedProductIdentity
    collector_run_id: CollectorRunId
    connection_session: ConnectionSessionIdentity
    subscription_plan: SubscriptionPlanIdentity
    subscription_attempt_snapshots: tuple[SubscriptionAttemptSnapshot, ...]
    ingress_ordinal: int
    frame_kind: FrameKind
    application_message_bytes: bytes = field(repr=False)
    received_time: datetime
    received_monotonic_ns: int
    collector_version: str
    collector_commit: str
    raw_record_schema_version: int = 1
    payload_length: int = field(init=False)
    payload_sha256: str = field(init=False)
    raw_record_id: RawRecordId = field(init=False)
    full_record_integrity_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.feed_product) is not FeedProductIdentity:
            raise TypeError("feed_product must be a FeedProductIdentity.")
        if type(self.collector_run_id) is not CollectorRunId:
            raise TypeError("collector_run_id must be a CollectorRunId.")
        if type(self.connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")
        if self.connection_session.collector_run_id != self.collector_run_id:
            raise ValueError("connection session must belong to collector_run_id.")
        if type(self.subscription_plan) is not SubscriptionPlanIdentity:
            raise TypeError("subscription_plan must be a SubscriptionPlanIdentity.")
        if self.subscription_plan.feed_product_id != self.feed_product.feed_product_id:
            raise ValueError("subscription plan must belong to the raw record feed product.")
        if type(self.subscription_attempt_snapshots) is not tuple:
            raise TypeError("subscription_attempt_snapshots must be a built-in tuple.")
        require_collection_size(
            self.subscription_attempt_snapshots,
            field_name="subscription_attempt_snapshots",
            maximum_items=MAX_SUBSCRIPTION_ATTEMPT_SNAPSHOTS,
        )
        if any(
            type(snapshot) is not SubscriptionAttemptSnapshot
            for snapshot in self.subscription_attempt_snapshots
        ):
            raise TypeError("subscription_attempt_snapshots contain an invalid value.")

        spec_ids = {spec.subscription_spec_id for spec in self.subscription_plan.subscription_specs}
        for snapshot in self.subscription_attempt_snapshots:
            attempt = snapshot.subscription_attempt
            if attempt.connection_session != self.connection_session:
                raise ValueError("every attempt snapshot must belong to the raw record session.")
            if attempt.subscription_spec.feed_product_id != self.feed_product.feed_product_id:
                raise ValueError("every attempt snapshot must belong to the raw record feed.")
            if attempt.subscription_spec.subscription_spec_id not in spec_ids:
                raise ValueError(
                    "every attempt snapshot spec must belong to the subscription plan."
                )
        snapshot_rows = _attempt_snapshot_rows(self.subscription_attempt_snapshots)
        snapshot_keys = tuple((row[0], row[1]) for row in snapshot_rows)
        if len(set(snapshot_keys)) != len(snapshot_keys):
            raise ValueError(
                "subscription_attempt_snapshots must be unique by spec and attempt identity."
            )
        expected_rows = tuple(sorted(snapshot_rows, key=lambda item: (item[0], item[1])))
        if snapshot_rows != expected_rows:
            raise ValueError("subscription_attempt_snapshots must be sorted and unique.")

        ordinal = require_nonnegative_int(self.ingress_ordinal, field_name="ingress_ordinal")
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
        received = canonical_utc_datetime(self.received_time, field_name="received_time")
        object.__setattr__(
            self,
            "received_time",
            datetime.fromisoformat(received.replace("Z", "+00:00")),
        )
        monotonic = require_nonnegative_int(
            self.received_monotonic_ns, field_name="received_monotonic_ns"
        )
        collector_version = require_text(self.collector_version, field_name="collector_version")
        collector_commit = require_text(self.collector_commit, field_name="collector_commit")
        if type(self.raw_record_schema_version) is not int:
            raise TypeError("raw_record_schema_version must be a built-in integer.")
        if self.raw_record_schema_version != 1:
            raise ValueError("raw_record_schema_version must be 1.")

        payload_length = len(self.application_message_bytes)
        payload_sha = sha256_hex(
            self.application_message_bytes, field_name="application_message_bytes"
        )
        raw_id = RawRecordId(
            canonical_json_array(
                (
                    "raw-record-v1",
                    self.feed_product.feed_product_id,
                    self.collector_run_id,
                    self.connection_session.connection_session_id,
                    ordinal,
                    self.frame_kind.value,
                    payload_sha,
                )
            )
        )
        full_preimage = canonical_json_array(
            (
                "raw-record-content-v1",
                self.raw_record_schema_version,
                raw_id,
                self.subscription_plan.subscription_plan_id,
                snapshot_rows,
                received,
                monotonic,
                collector_version,
                collector_commit,
                payload_length,
                payload_sha,
            )
        ).encode("utf-8")
        object.__setattr__(self, "payload_length", payload_length)
        object.__setattr__(self, "payload_sha256", payload_sha)
        object.__setattr__(self, "raw_record_id", raw_id)
        object.__setattr__(self, "full_record_integrity_sha256", sha256_hex(full_preimage))

    @classmethod
    def from_stored(
        cls,
        *,
        feed_product: FeedProductIdentity,
        collector_run_id: CollectorRunId,
        connection_session: ConnectionSessionIdentity,
        subscription_plan: SubscriptionPlanIdentity,
        subscription_attempt_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
        ingress_ordinal: int,
        frame_kind: FrameKind,
        application_message_bytes: bytes,
        received_time: datetime,
        received_monotonic_ns: int,
        collector_version: str,
        collector_commit: str,
        raw_record_schema_version: int,
        expected_payload_length: int,
        expected_payload_sha256: str,
        expected_raw_record_id: RawRecordId,
        expected_full_record_integrity_sha256: str,
    ) -> Self:
        """Reconstruct a stored record and fail closed on every derived mismatch."""

        require_nonnegative_int(expected_payload_length, field_name="expected_payload_length")
        require_sha256(expected_payload_sha256, field_name="expected_payload_sha256")
        if type(expected_raw_record_id) is not RawRecordId:
            raise TypeError("expected_raw_record_id must be a RawRecordId.")
        require_sha256(
            expected_full_record_integrity_sha256,
            field_name="expected_full_record_integrity_sha256",
        )
        record = cls(
            feed_product=feed_product,
            collector_run_id=collector_run_id,
            connection_session=connection_session,
            subscription_plan=subscription_plan,
            subscription_attempt_snapshots=subscription_attempt_snapshots,
            ingress_ordinal=ingress_ordinal,
            frame_kind=frame_kind,
            application_message_bytes=application_message_bytes,
            received_time=received_time,
            received_monotonic_ns=received_monotonic_ns,
            collector_version=collector_version,
            collector_commit=collector_commit,
            raw_record_schema_version=raw_record_schema_version,
        )
        if record.payload_length != expected_payload_length:
            raise ValueError("stored payload_length does not match its recomputed value.")
        if record.payload_sha256 != expected_payload_sha256:
            raise ValueError("stored payload_sha256 does not match its recomputed value.")
        if record.raw_record_id != expected_raw_record_id:
            raise ValueError("stored raw_record_id does not match its recomputed value.")
        if record.full_record_integrity_sha256 != expected_full_record_integrity_sha256:
            raise ValueError(
                "stored full_record_integrity_sha256 does not match its recomputed value."
            )
        return record


def validate_raw_record_sequence(
    records: Sequence[RawMarketDataRecord],
) -> tuple[RawMarketDataRecord, ...]:
    """Validate a complete explicitly supplied finite sequence from ordinal zero.

    Even a valid sequence cannot prove that its final tail record wasn't omitted;
    sealed-run manifests remain a Phase 1A-3B2 responsibility.
    """

    if type(records) not in (list, tuple):
        raise TypeError("records must be an exact finite list or tuple.")
    require_collection_size(
        records,
        field_name="records",
        maximum_items=MAX_RAW_RECORD_SEQUENCE,
    )
    validated = tuple(records)
    for record in validated:
        if type(record) is not RawMarketDataRecord:
            raise TypeError("records must contain only RawMarketDataRecord values.")
    if not validated:
        return validated
    run_id = validated[0].collector_run_id
    for expected_ordinal, record in enumerate(validated):
        if record.collector_run_id != run_id:
            raise ValueError("all records must belong to one collector run.")
        if record.ingress_ordinal != expected_ordinal:
            raise ValueError("records must have contiguous ordinals starting at zero.")
    return validated


class SourceTimeRole(StrEnum):
    """Bounded semantics of an exact source timestamp."""

    SOURCE_EVENT_TIME = "source-event-time"
    EXCHANGE_EVENT_TIME = "exchange-event-time"
    TRADE_EXECUTION_TIME = "trade-execution-time"


class SourceTimeUnit(StrEnum):
    """Exact unit of a raw integral source timestamp."""

    EPOCH_MILLISECONDS = "epoch-milliseconds"
    EPOCH_MICROSECONDS = "epoch-microseconds"


@dataclass(frozen=True, slots=True)
class SourceTimeFact:
    """Exact source time with deterministic UTC conversion."""

    role: SourceTimeRole
    raw_value: int
    unit: SourceTimeUnit
    utc_value: datetime = field(init=False)

    def __post_init__(self) -> None:
        if type(self.role) is not SourceTimeRole:
            raise TypeError("role must be a SourceTimeRole.")
        raw = require_nonnegative_int(self.raw_value, field_name="raw_value")
        if type(self.unit) is not SourceTimeUnit:
            raise TypeError("unit must be a SourceTimeUnit.")
        microseconds = raw * (1_000 if self.unit is SourceTimeUnit.EPOCH_MILLISECONDS else 1)
        converted: datetime | None = None
        try:
            converted = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=microseconds)
        except (OverflowError, ValueError):
            pass
        if converted is None:
            raise ValueError("raw source time is outside the supported UTC datetime range.")
        object.__setattr__(self, "utc_value", converted)


class SourceSequenceRole(StrEnum):
    """Bounded semantics for a true ordered source sequence range."""

    BOOK_UPDATE = "book-update"
    EVENT_SEQUENCE = "event-sequence"
    MESSAGE_SEQUENCE = "message-sequence"


@dataclass(frozen=True, slots=True)
class SourceSequenceRange:
    """Inclusive sequence range valid only within its explicit namespace."""

    role: SourceSequenceRole
    namespace: str
    first: int
    last: int

    def __post_init__(self) -> None:
        if type(self.role) is not SourceSequenceRole:
            raise TypeError("role must be a SourceSequenceRole.")
        require_code(self.namespace, field_name="namespace")
        first = require_nonnegative_int(self.first, field_name="first")
        last = require_nonnegative_int(self.last, field_name="last")
        if last < first:
            raise ValueError("last must be greater than or equal to first.")


def _subscription_spec_member_leaf(subscription_spec_id: SubscriptionSpecId) -> str:
    return sha256_hex(
        canonical_json_array(("coverage-scope-spec-member-leaf-v1", subscription_spec_id)).encode(
            "utf-8"
        ),
        field_name="subscription_spec_member_leaf",
    )


def _subscription_spec_padding_leaf(position: int) -> str:
    return sha256_hex(
        canonical_json_array(("coverage-scope-spec-padding-leaf-v1", position)).encode("utf-8"),
        field_name="subscription_spec_padding_leaf",
    )


def _subscription_spec_member_node(left: str, right: str) -> str:
    return sha256_hex(
        canonical_json_array(
            (
                "coverage-scope-spec-member-node-v1",
                require_sha256(left, field_name="left_member_sha256"),
                require_sha256(right, field_name="right_member_sha256"),
            )
        ).encode("utf-8"),
        field_name="subscription_spec_member_node",
    )


def _subscription_spec_member_tree(
    subscription_spec_ids: tuple[SubscriptionSpecId, ...],
) -> tuple[tuple[str, ...], ...]:
    require_collection_size(
        subscription_spec_ids,
        field_name="subscription_spec_ids",
        maximum_items=MAX_COVERAGE_SCOPE_MEMBERS,
        minimum_items=1,
    )
    if any(type(item) is not SubscriptionSpecId for item in subscription_spec_ids):
        raise TypeError("subscription_spec_ids must contain SubscriptionSpecId values.")
    if subscription_spec_ids != tuple(
        sorted(set(subscription_spec_ids), key=lambda item: item.value)
    ):
        raise ValueError("subscription_spec_ids must be sorted and unique.")
    padded_count = 1 << (len(subscription_spec_ids) - 1).bit_length()
    leaves = tuple(_subscription_spec_member_leaf(item) for item in subscription_spec_ids) + tuple(
        _subscription_spec_padding_leaf(position)
        for position in range(len(subscription_spec_ids), padded_count)
    )
    levels: list[tuple[str, ...]] = [leaves]
    current = leaves
    while len(current) > 1:
        current = tuple(
            _subscription_spec_member_node(current[index], current[index + 1])
            for index in range(0, len(current), 2)
        )
        levels.append(current)
    return tuple(levels)


def _subscription_spec_members_root(
    subscription_spec_ids: tuple[SubscriptionSpecId, ...],
) -> str:
    return _subscription_spec_member_tree(subscription_spec_ids)[-1][0]


@dataclass(frozen=True, slots=True)
class SubscriptionSpecMembershipProof:
    """Bounded Merkle proof that one wire spec belongs to one coverage scope.

    Canonical components are ``["subscription-spec-membership-proof-v1",
    coverage_scope_id, subscription_spec_id, member_index, member_count,
    [sibling_sha256, ...]]``. At most ten siblings are possible for the
    1,024-member scope bound.
    """

    coverage_scope_id: CoverageScopeId
    subscription_spec_id: SubscriptionSpecId
    member_index: int
    member_count: int
    sibling_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.coverage_scope_id) is not CoverageScopeId:
            raise TypeError("coverage_scope_id must be a CoverageScopeId.")
        if type(self.subscription_spec_id) is not SubscriptionSpecId:
            raise TypeError("subscription_spec_id must be a SubscriptionSpecId.")
        index = require_nonnegative_int(self.member_index, field_name="member_index")
        count = require_nonnegative_int(self.member_count, field_name="member_count")
        if count == 0 or count > MAX_COVERAGE_SCOPE_MEMBERS or index >= count:
            raise ValueError("subscription spec membership position is outside its scope bound.")
        if type(self.sibling_sha256s) is not tuple:
            raise TypeError("sibling_sha256s must be a built-in tuple.")
        require_collection_size(
            self.sibling_sha256s,
            field_name="sibling_sha256s",
            maximum_items=MAX_COVERAGE_SCOPE_MERKLE_SIBLINGS,
        )
        expected_siblings = (count - 1).bit_length()
        if len(self.sibling_sha256s) != expected_siblings:
            raise ValueError("subscription spec membership proof has the wrong path length.")
        siblings = tuple(
            require_sha256(item, field_name="membership_sibling_sha256")
            for item in self.sibling_sha256s
        )
        scope_components = parse_canonical_json_array(
            self.coverage_scope_id.value,
            field_name="coverage_scope_id",
        )
        if scope_components[6] != count:
            raise ValueError("subscription spec membership count must match coverage scope.")
        spec_components = parse_canonical_json_array(
            self.subscription_spec_id.value,
            field_name="subscription_spec_id",
        )
        if spec_components[1] != scope_components[2]:
            raise ValueError("subscription spec membership feed must match coverage scope.")
        computed = _subscription_spec_member_leaf(self.subscription_spec_id)
        tree_index = index
        for sibling in siblings:
            computed = (
                _subscription_spec_member_node(computed, sibling)
                if tree_index % 2 == 0
                else _subscription_spec_member_node(sibling, computed)
            )
            tree_index //= 2
        if computed != scope_components[8]:
            raise ValueError("subscription spec membership proof does not match coverage scope.")

    def canonical_components(self) -> tuple[object, ...]:
        return (
            "subscription-spec-membership-proof-v1",
            self.coverage_scope_id.value,
            self.subscription_spec_id.value,
            self.member_index,
            self.member_count,
            self.sibling_sha256s,
        )


class CoverageDomain(StrEnum):
    """Independent quality boundary; one status cannot represent all three."""

    BRONZE_INGRESS = "bronze-ingress"
    SILVER_NORMALIZATION = "silver-normalization"
    SILVER_DELIVERY = "silver-delivery"


class CoverageStatus(StrEnum):
    """Evidence-backed state for one exact coverage scope and epoch."""

    COMPLETE = "complete"
    UNCERTAIN = "uncertain"
    CONFIRMED_INCOMPLETE = "confirmed-incomplete"


class CoverageReason(StrEnum):
    """Bounded reason codes without raw data or exception text."""

    INITIAL_SCOPE = "initial-scope"
    TRANSPORT_AMBIGUITY = "transport-ambiguity"
    RAW_ACCEPTANCE_UNCERTAIN = "raw-acceptance-uncertain"
    RAW_DEFINITE_REJECTION = "raw-definite-rejection"
    UPSTREAM_COVERAGE_DEGRADED = "upstream-coverage-degraded"
    IN_SCOPE_NORMALIZATION_FAILURE = "in-scope-normalization-failure"
    NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN = "normalization-outcome-acceptance-uncertain"
    NORMALIZATION_OUTCOME_DEFINITE_REJECTION = "normalization-outcome-definite-rejection"
    SOURCE_SEQUENCE_BREAK = "source-sequence-break"
    EXPLICIT_RECOVERY_PROOF = "explicit-recovery-proof"
    AUTHORITATIVE_STATE_BOUNDARY = "authoritative-state-boundary"
    SOURCE_EVENT_CONFLICT = "source-event-conflict"


class InitialCoverageReason(StrEnum):
    """Closed ordinal-zero reasons, distinct from later transition reasons."""

    INITIAL_ACTIVATION = "initial-activation"
    TRANSPORT_AMBIGUITY = "transport-ambiguity"
    RAW_ACCEPTANCE_UNCERTAIN = "raw-acceptance-uncertain"
    RAW_DEFINITE_REJECTION = "raw-definite-rejection"
    UPSTREAM_COVERAGE_DEGRADED = "upstream-coverage-degraded"
    IN_SCOPE_NORMALIZATION_FAILURE = "in-scope-normalization-failure"
    NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN = "normalization-outcome-acceptance-uncertain"
    NORMALIZATION_OUTCOME_DEFINITE_REJECTION = "normalization-outcome-definite-rejection"
    SOURCE_SEQUENCE_BREAK = "source-sequence-break"
    SOURCE_EVENT_CONFLICT = "source-event-conflict"


class CoverageEvidenceKind(StrEnum):
    """Typed evidence; ACK and reconnect evidence can never restore coverage."""

    INITIAL_ACTIVATION = "initial-activation"
    TRANSPORT_FAILURE = "transport-failure"
    RAW_SINK_ACCEPTANCE_AMBIGUITY = "raw-sink-acceptance-ambiguity"
    RAW_RECORD_REJECTION = "raw-record-rejection"
    UPSTREAM_COVERAGE_TRANSITION = "upstream-coverage-transition"
    UPSTREAM_COVERAGE_STATE = "upstream-coverage-state"
    NORMALIZATION_FAILURE = "normalization-failure"
    NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY = (
        "normalization-outcome-sink-acceptance-ambiguity"
    )
    NORMALIZATION_OUTCOME_REJECTION = "normalization-outcome-rejection"
    SOURCE_SEQUENCE = "source-sequence"
    EXPLICIT_BACKFILL_PROOF = "explicit-backfill-proof"
    AUTHORITATIVE_STATE_SNAPSHOT = "authoritative-state-snapshot"
    ACKNOWLEDGEMENT = "acknowledgement"
    RECONNECT = "reconnect"
    SOURCE_EVENT_CONFLICT = "source-event-conflict"


@dataclass(frozen=True, slots=True)
class InitialActivationEvidenceSource:
    """Exact acknowledged attempts that activate one complete coverage scope."""

    connection_session: ConnectionSessionIdentity
    acknowledged_attempts: tuple[SubscriptionAttemptSnapshot, ...] = field(repr=False)
    acknowledged_attempts_canonical_content: str = field(init=False, repr=False)
    acknowledged_attempts_content_sha256: str = field(init=False)
    acknowledged_spec_members_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")
        if type(self.acknowledged_attempts) is not tuple:
            raise TypeError("acknowledged_attempts must be a built-in tuple.")
        require_collection_size(
            self.acknowledged_attempts,
            field_name="acknowledged_attempts",
            maximum_items=MAX_COVERAGE_SCOPE_MEMBERS,
            minimum_items=1,
        )
        for snapshot in self.acknowledged_attempts:
            if type(snapshot) is not SubscriptionAttemptSnapshot:
                raise TypeError("acknowledged_attempts contain an invalid value.")
            attempt = snapshot.subscription_attempt
            if attempt.connection_session != self.connection_session:
                raise ValueError("activation attempt must belong to its connection session.")
            if snapshot.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED:
                raise ValueError("initial activation requires acknowledged attempts.")
        attempt_ids = tuple(
            snapshot.subscription_attempt.subscription_attempt_id.value
            for snapshot in self.acknowledged_attempts
        )
        if attempt_ids != tuple(sorted(set(attempt_ids))):
            raise ValueError("acknowledged_attempts must be sorted and unique.")
        attempt_rows = tuple(
            (
                snapshot.subscription_attempt.subscription_attempt_id.value,
                snapshot.attempt_status.value,
            )
            for snapshot in self.acknowledged_attempts
        )
        attempt_content = canonical_json_array(
            ("initial-activation-attempts-v1", attempt_rows),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )
        object.__setattr__(
            self,
            "acknowledged_attempts_canonical_content",
            attempt_content,
        )
        object.__setattr__(
            self,
            "acknowledged_attempts_content_sha256",
            sha256_hex(attempt_content.encode("utf-8"), field_name="activation_attempts"),
        )
        object.__setattr__(
            self,
            "acknowledged_spec_members_sha256",
            _subscription_spec_members_root(
                tuple(
                    sorted(
                        (
                            snapshot.subscription_attempt.subscription_spec.subscription_spec_id
                            for snapshot in self.acknowledged_attempts
                        ),
                        key=lambda item: item.value,
                    )
                )
            ),
        )

    def canonical_components(self) -> tuple[object, ...]:
        return (
            "initial-activation-evidence-v1",
            self.connection_session.connection_session_id.value,
            len(self.acknowledged_attempts),
            self.acknowledged_spec_members_sha256,
            self.acknowledged_attempts_content_sha256,
        )


@dataclass(frozen=True, slots=True)
class TransportAmbiguityEvidenceSource:
    """Transport ambiguity in one session, optionally after one send attempt."""

    feed_product_id: FeedProductId
    connection_session: ConnectionSessionIdentity
    subscription_attempt: SubscriptionAttemptIdentity | None = field(default=None, repr=False)
    subscription_spec_membership: SubscriptionSpecMembershipProof | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")
        if self.subscription_attempt is not None:
            if type(self.subscription_attempt) is not SubscriptionAttemptIdentity:
                raise TypeError("subscription_attempt must be a SubscriptionAttemptIdentity.")
            if self.subscription_attempt.connection_session != self.connection_session:
                raise ValueError("transport attempt must belong to its connection session.")
            if type(self.subscription_spec_membership) is not SubscriptionSpecMembershipProof:
                raise TypeError("transport attempt requires a subscription membership proof.")
            if (
                self.subscription_spec_membership.subscription_spec_id
                != self.subscription_attempt.subscription_spec.subscription_spec_id
            ):
                raise ValueError("transport membership proof must bind its attempted spec.")
        elif self.subscription_spec_membership is not None:
            raise ValueError("transport membership proof requires a subscription attempt.")

    def canonical_components(self) -> tuple[object, ...]:
        return (
            "transport-ambiguity-evidence-v1",
            self.feed_product_id.value,
            self.connection_session.connection_session_id.value,
            (
                self.subscription_attempt.subscription_attempt_id.value
                if self.subscription_attempt is not None
                else None
            ),
            (
                self.subscription_spec_membership.canonical_components()
                if self.subscription_spec_membership is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RawRecordEvidenceSource:
    """Typed raw-record reference for acceptance ambiguity or definite rejection."""

    raw_record_id: RawRecordId = field(repr=False)
    identified_coverage_scope_id: CoverageScopeId

    def __post_init__(self) -> None:
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        if type(self.identified_coverage_scope_id) is not CoverageScopeId:
            raise TypeError("identified_coverage_scope_id must be a CoverageScopeId.")

    def canonical_components(self) -> tuple[str, str, str]:
        return (
            "raw-record-evidence-v1",
            self.raw_record_id.value,
            self.identified_coverage_scope_id.value,
        )


@dataclass(frozen=True, slots=True)
class UpstreamCoverageTransitionEvidenceSource:
    """Typed reference to a committed upstream coverage transition."""

    committed_state: "CommittedCoverageState" = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.committed_state) is not CommittedCoverageState:
            raise TypeError("committed_state must be a CommittedCoverageState.")
        if self.committed_state.state_reference.latest_transition is None:
            raise ValueError("upstream transition evidence requires a transitioned state.")

    @property
    def coverage_transition_id(self) -> CoverageTransitionId:
        transition = self.committed_state.state_reference.latest_transition
        if transition is None:  # pragma: no cover - closed by construction
            raise AssertionError("transitioned committed state lost its transition")
        return transition.coverage_transition_id

    def canonical_components(self) -> tuple[str, str]:
        return (
            "upstream-coverage-transition-evidence-v1",
            self.committed_state.committed_coverage_state_id.value,
        )


@dataclass(frozen=True, slots=True)
class UpstreamCoverageStateEvidenceSource:
    """Typed reference to a committed initial or transitioned upstream state.

    This source exists so Silver can cite an initially degraded Bronze state
    without fabricating a Bronze transition.  Its canonical components are::

        ["upstream-coverage-state-evidence-v1", committed_coverage_state_id]
    """

    committed_state: "CommittedCoverageState" = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.committed_state) is not CommittedCoverageState:
            raise TypeError("committed_state must be a CommittedCoverageState.")
        _upstream_coverage_state_details(
            self.committed_state.state_reference.coverage_state_reference_id
        )

    @property
    def coverage_state_reference_id(self) -> "CoverageStateReferenceId":
        return self.committed_state.state_reference.coverage_state_reference_id

    def canonical_components(self) -> tuple[str, str]:
        return (
            "upstream-coverage-state-evidence-v1",
            self.committed_state.committed_coverage_state_id.value,
        )


class NormalizationFailureCategory(StrEnum):
    """Bounded normalization-failure evidence categories."""

    PROTOCOL_REJECTION = "protocol-rejection"
    DECODER_REJECTION = "decoder-rejection"
    UNKNOWN_INSTRUMENT = "unknown-instrument"
    METADATA_UNAVAILABLE = "metadata-unavailable"
    PROVENANCE_MISMATCH = "provenance-mismatch"
    LOCAL_CONTRACT_FAILURE = "local-contract-failure"


@dataclass(frozen=True, slots=True)
class NormalizationFailureEvidenceSource:
    """Lower-layer normalization failure identity without a v3 import cycle.

    Exact ID preimage::

        ["normalization-failure-evidence-v1", raw_record_id,
         normalization_run_id, raw_event_index-or-null,
         source_event_id-when-established-or-null, failure_category,
         identified_coverage_scope_id]
    """

    raw_record_id: RawRecordId = field(repr=False)
    normalization_run_id: NormalizationRunId
    raw_event_index: int | None
    source_event_id: SourceEventId | None = field(repr=False)
    category: NormalizationFailureCategory
    identified_coverage_scope_id: CoverageScopeId
    normalization_failure_evidence_id: NormalizationFailureEvidenceId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        if type(self.normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        if self.raw_event_index is not None:
            require_nonnegative_int(self.raw_event_index, field_name="raw_event_index")
            if self.source_event_id is not None and type(self.source_event_id) is not SourceEventId:
                raise TypeError("source_event_id must be a SourceEventId or None.")
        elif self.source_event_id is not None:
            raise ValueError("pre-index normalization failure cannot name a source event.")
        if type(self.category) is not NormalizationFailureCategory:
            raise TypeError("category must be a NormalizationFailureCategory.")
        if type(self.identified_coverage_scope_id) is not CoverageScopeId:
            raise TypeError("identified_coverage_scope_id must be a CoverageScopeId.")
        object.__setattr__(
            self,
            "normalization_failure_evidence_id",
            NormalizationFailureEvidenceId(
                canonical_json_array(
                    (
                        "normalization-failure-evidence-v1",
                        self.raw_record_id,
                        self.normalization_run_id,
                        self.raw_event_index,
                        self.source_event_id,
                        self.category.value,
                        self.identified_coverage_scope_id,
                    )
                )
            ),
        )

    def canonical_components(self) -> tuple[str, str]:
        return (
            "normalization-failure-evidence-reference-v1",
            self.normalization_failure_evidence_id.value,
        )


@dataclass(frozen=True, slots=True)
class NormalizationOutcomeEvidenceSource:
    """Exact attempted outcome and Silver scope for post-outcome sink failure.

    Canonical source row::

        ["normalization-outcome-evidence-v1", normalization_outcome_id,
         identified_coverage_scope_id]

    The outer ``CoverageEvidenceKind`` distinguishes explicit rejection from
    acceptance ambiguity.  This evidence is created only after the referenced
    outcome exists and therefore can never be part of that same outcome's
    prepared normalization lineage.
    """

    normalization_outcome_id: NormalizationOutcomeId = field(repr=False)
    identified_coverage_scope_id: CoverageScopeId

    def __post_init__(self) -> None:
        if type(self.normalization_outcome_id) is not NormalizationOutcomeId:
            raise TypeError("normalization_outcome_id must be a NormalizationOutcomeId.")
        if type(self.identified_coverage_scope_id) is not CoverageScopeId:
            raise TypeError("identified_coverage_scope_id must be a CoverageScopeId.")

    @property
    def raw_record_id(self) -> RawRecordId:
        return _normalization_outcome_id_details(self.normalization_outcome_id)[1]

    @property
    def frame_status(self) -> str:
        return _normalization_outcome_id_details(self.normalization_outcome_id)[2]

    def canonical_components(self) -> tuple[str, str, str]:
        return (
            "normalization-outcome-evidence-v1",
            self.normalization_outcome_id.value,
            self.identified_coverage_scope_id.value,
        )


@dataclass(frozen=True, slots=True)
class SourceSequenceBreakEvidenceSource:
    """Exact feed-scoped source sequence range establishing a break."""

    feed_product_id: FeedProductId
    sequence_range: SourceSequenceRange
    identified_coverage_scope_id: CoverageScopeId

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.sequence_range) is not SourceSequenceRange:
            raise TypeError("sequence_range must be a SourceSequenceRange.")
        if type(self.identified_coverage_scope_id) is not CoverageScopeId:
            raise TypeError("identified_coverage_scope_id must be a CoverageScopeId.")

    def canonical_components(self) -> tuple[object, ...]:
        return (
            "source-sequence-break-evidence-v1",
            self.feed_product_id.value,
            self.sequence_range.role.value,
            self.sequence_range.namespace,
            self.sequence_range.first,
            self.sequence_range.last,
            self.identified_coverage_scope_id.value,
        )


@dataclass(frozen=True, slots=True)
class SourceEventConflictEvidenceSource:
    """Typed feed/source/raw lineage for a semantic source-event conflict."""

    feed_product_id: FeedProductId
    source_event_id: SourceEventId = field(repr=False)
    raw_record_id: RawRecordId = field(repr=False)
    raw_event_index: int
    identified_coverage_scope_id: CoverageScopeId

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.source_event_id) is not SourceEventId:
            raise TypeError("source_event_id must be a SourceEventId.")
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")
        require_nonnegative_int(self.raw_event_index, field_name="raw_event_index")
        if type(self.identified_coverage_scope_id) is not CoverageScopeId:
            raise TypeError("identified_coverage_scope_id must be a CoverageScopeId.")

    def canonical_components(self) -> tuple[object, ...]:
        return (
            "source-event-conflict-evidence-v1",
            self.feed_product_id.value,
            self.source_event_id.value,
            self.raw_record_id.value,
            self.raw_event_index,
            self.identified_coverage_scope_id.value,
        )


@dataclass(frozen=True, slots=True)
class AcknowledgementEvidenceSource:
    """Typed ACK evidence that is never sufficient to improve coverage."""

    acknowledged_attempt: SubscriptionAttemptSnapshot = field(repr=False)
    subscription_spec_membership: SubscriptionSpecMembershipProof = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.acknowledged_attempt) is not SubscriptionAttemptSnapshot:
            raise TypeError("acknowledged_attempt must be a SubscriptionAttemptSnapshot.")
        if self.acknowledged_attempt.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED:
            raise ValueError("acknowledgement evidence requires ACKNOWLEDGED status.")
        if type(self.subscription_spec_membership) is not SubscriptionSpecMembershipProof:
            raise TypeError("subscription_spec_membership must be a membership proof.")
        if (
            self.subscription_spec_membership.subscription_spec_id
            != self.acknowledged_attempt.subscription_attempt.subscription_spec.subscription_spec_id
        ):
            raise ValueError("acknowledgement membership proof must bind its attempted spec.")

    def canonical_components(self) -> tuple[object, ...]:
        return (
            "acknowledgement-evidence-v1",
            self.acknowledged_attempt.subscription_attempt.subscription_attempt_id.value,
            self.acknowledged_attempt.attempt_status.value,
            self.subscription_spec_membership.canonical_components(),
        )


@dataclass(frozen=True, slots=True)
class ReconnectEvidenceSource:
    """Typed reconnect session reference that cannot restore coverage."""

    feed_product_id: FeedProductId
    connection_session: ConnectionSessionIdentity

    def __post_init__(self) -> None:
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")

    def canonical_components(self) -> tuple[str, str, str]:
        return (
            "reconnect-evidence-v1",
            self.feed_product_id.value,
            self.connection_session.connection_session_id.value,
        )


@dataclass(frozen=True, slots=True)
class AuthoritativeStateSnapshotEvidenceSource:
    """Typed state snapshot reference that cannot repair historical trade coverage."""

    raw_record_id: RawRecordId = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.raw_record_id) is not RawRecordId:
            raise TypeError("raw_record_id must be a RawRecordId.")

    def canonical_components(self) -> tuple[str, str]:
        return ("authoritative-state-snapshot-evidence-v1", self.raw_record_id.value)


type CoverageEvidenceSource = (
    InitialActivationEvidenceSource
    | TransportAmbiguityEvidenceSource
    | RawRecordEvidenceSource
    | UpstreamCoverageTransitionEvidenceSource
    | UpstreamCoverageStateEvidenceSource
    | NormalizationFailureEvidenceSource
    | NormalizationOutcomeEvidenceSource
    | SourceSequenceBreakEvidenceSource
    | SourceEventConflictEvidenceSource
    | AcknowledgementEvidenceSource
    | ReconnectEvidenceSource
    | AuthoritativeStateSnapshotEvidenceSource
)


def validate_coverage_scope_canonical_content(value: object) -> str:
    """Validate complete bounded event-scope content retained beside its digest ID.

    Content preimage::

        ["coverage-scope-content-v1", feed_product_id,
         [subscription_spec_ids], [canonical_instrument_ids],
         event_family, event_family_schema_version, payload_type]
    """

    components = parse_canonical_json_array(
        value,
        field_name="coverage_scope_canonical_content",
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    assert type(value) is str
    _require_component_count(
        components,
        7,
        identifier_name="coverage scope canonical content",
    )
    if components[0] != "coverage-scope-content-v1":
        raise ValueError("coverage scope content has an unsupported version tag.")
    feed = FeedProductId(_component_text(components[1], field_name="feed_product_id"))
    feed_components = parse_canonical_json_array(feed.value, field_name="feed_product_id")
    feed_venue = Venue(_component_text(feed_components[1], field_name="venue"))
    spec_values = _component_tuple(components[2], field_name="subscription_spec_ids")
    require_collection_size(
        spec_values,
        field_name="subscription_spec_ids",
        maximum_items=MAX_COVERAGE_SCOPE_MEMBERS,
        minimum_items=1,
    )
    specs = tuple(
        SubscriptionSpecId(_component_text(item, field_name="subscription_spec_id"))
        for item in spec_values
    )
    if specs != tuple(sorted(set(specs), key=lambda item: item.value)):
        raise ValueError("coverage subscription specs must be sorted and unique.")
    for spec in specs:
        spec_components = parse_canonical_json_array(
            spec.value,
            field_name="subscription_spec_id",
        )
        if spec_components[1] != feed.value:
            raise ValueError("coverage subscription spec must match feed product.")
    instrument_values = _component_tuple(
        components[3],
        field_name="canonical_instrument_ids",
    )
    require_collection_size(
        instrument_values,
        field_name="canonical_instrument_ids",
        maximum_items=MAX_COVERAGE_SCOPE_MEMBERS,
        minimum_items=1,
    )
    instruments = tuple(
        _instrument_from_canonical_component(item, field_name="canonical_instrument_id")
        for item in instrument_values
    )
    if any(instrument.venue is not feed_venue for instrument in instruments):
        raise ValueError("coverage instruments must match feed-product venue.")
    canonical_instruments = tuple(item.canonical_instrument_id for item in instruments)
    if canonical_instruments != tuple(sorted(set(canonical_instruments))):
        raise ValueError("coverage instrument IDs must be sorted and unique.")
    require_code(
        _component_text(components[4], field_name="event_family"),
        field_name="event_family",
    )
    family_version = _component_nonnegative_int(
        components[5],
        field_name="event_family_schema_version",
    )
    if family_version == 0:
        raise ValueError("event_family_schema_version must be positive.")
    require_code(
        _component_text(components[6], field_name="payload_type"),
        field_name="payload_type",
    )
    return value


def coverage_scope_id_from_canonical_content(
    *,
    domain: CoverageDomain,
    canonical_content: object,
) -> CoverageScopeId:
    """Derive a bounded scope ID from independently validated complete content."""

    if type(domain) is not CoverageDomain:
        raise TypeError("domain must be a CoverageDomain.")
    content = validate_coverage_scope_canonical_content(canonical_content)
    components = parse_canonical_json_array(
        content,
        field_name="coverage_scope_canonical_content",
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    spec_members = _component_tuple(components[2], field_name="subscription_spec_ids")
    instrument_members = _component_tuple(
        components[3],
        field_name="canonical_instrument_ids",
    )
    spec_members_sha256 = _subscription_spec_members_root(
        tuple(
            SubscriptionSpecId(_component_text(item, field_name="subscription_spec_id"))
            for item in spec_members
        )
    )
    instrument_members_sha256 = sha256_hex(
        canonical_json_array(
            ("coverage-scope-instrument-members-v1", instrument_members),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        ).encode("utf-8"),
        field_name="instrument_members",
    )
    return CoverageScopeId(
        canonical_json_array(
            (
                "coverage-scope-v1",
                domain.value,
                _component_text(components[1], field_name="feed_product_id"),
                _component_text(components[4], field_name="event_family"),
                _component_nonnegative_int(
                    components[5],
                    field_name="event_family_schema_version",
                ),
                _component_text(components[6], field_name="payload_type"),
                len(spec_members),
                len(instrument_members),
                spec_members_sha256,
                instrument_members_sha256,
            )
        )
    )


@dataclass(frozen=True, slots=True)
class CoverageScope:
    """Exact subscription/instrument/event boundary for one coverage domain.

    Complete content is retained as ``coverage-scope-content-v1``. The bounded
    ID preimage is ``["coverage-scope-v1", domain, feed_product_id,
    event_family, event_family_schema_version, payload_type, spec_count,
    instrument_count, spec-members Merkle root, instrument-members SHA-256]``.
    """

    domain: CoverageDomain
    feed_product_id: FeedProductId
    subscription_spec_ids: tuple[SubscriptionSpecId, ...]
    canonical_instrument_ids: tuple[str, ...]
    event_family: str
    event_family_schema_version: int
    payload_type: str
    coverage_scope_canonical_content: str = field(init=False, repr=False)
    coverage_scope_content_sha256: str = field(init=False)
    subscription_spec_members_sha256: str = field(init=False)
    instrument_members_sha256: str = field(init=False)
    coverage_scope_id: CoverageScopeId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.domain) is not CoverageDomain:
            raise TypeError("domain must be a CoverageDomain.")
        if type(self.feed_product_id) is not FeedProductId:
            raise TypeError("feed_product_id must be a FeedProductId.")
        if type(self.subscription_spec_ids) is not tuple:
            raise TypeError("subscription_spec_ids must be a built-in tuple.")
        require_collection_size(
            self.subscription_spec_ids,
            field_name="subscription_spec_ids",
            maximum_items=MAX_COVERAGE_SCOPE_MEMBERS,
            minimum_items=1,
        )
        if any(type(item) is not SubscriptionSpecId for item in self.subscription_spec_ids):
            raise TypeError("subscription_spec_ids contain an invalid value.")
        expected_specs = tuple(sorted(set(self.subscription_spec_ids), key=lambda item: item.value))
        if not self.subscription_spec_ids or self.subscription_spec_ids != expected_specs:
            raise ValueError("subscription_spec_ids must be non-empty, sorted and unique.")
        if type(self.canonical_instrument_ids) is not tuple:
            raise TypeError("canonical_instrument_ids must be a built-in tuple.")
        require_collection_size(
            self.canonical_instrument_ids,
            field_name="canonical_instrument_ids",
            maximum_items=MAX_COVERAGE_SCOPE_MEMBERS,
            minimum_items=1,
        )
        canonical_instruments = tuple(
            validate_canonical_instrument_id(instrument_id)
            for instrument_id in self.canonical_instrument_ids
        )
        if canonical_instruments != self.canonical_instrument_ids:
            raise ValueError("canonical_instrument_ids must retain exact canonical text.")
        if not self.canonical_instrument_ids or self.canonical_instrument_ids != tuple(
            sorted(set(self.canonical_instrument_ids))
        ):
            raise ValueError("canonical_instrument_ids must be non-empty, sorted and unique.")
        family = require_code(self.event_family, field_name="event_family")
        family_version = require_nonnegative_int(
            self.event_family_schema_version,
            field_name="event_family_schema_version",
        )
        if family_version == 0:
            raise ValueError("event_family_schema_version must be positive.")
        payload = require_code(self.payload_type, field_name="payload_type")
        content = canonical_json_array(
            (
                "coverage-scope-content-v1",
                self.feed_product_id,
                tuple(item.value for item in self.subscription_spec_ids),
                self.canonical_instrument_ids,
                family,
                family_version,
                payload,
            ),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )
        content_sha256 = sha256_hex(
            content.encode("utf-8"),
            field_name="coverage_scope_content",
        )
        object.__setattr__(self, "coverage_scope_canonical_content", content)
        object.__setattr__(self, "coverage_scope_content_sha256", content_sha256)
        scope_id = coverage_scope_id_from_canonical_content(
            domain=self.domain,
            canonical_content=content,
        )
        scope_components = parse_canonical_json_array(
            scope_id.value,
            field_name="coverage_scope_id",
        )
        object.__setattr__(self, "subscription_spec_members_sha256", scope_components[8])
        object.__setattr__(self, "instrument_members_sha256", scope_components[9])
        object.__setattr__(self, "coverage_scope_id", scope_id)


def subscription_spec_membership_proof(
    scope: CoverageScope,
    subscription_spec_id: SubscriptionSpecId,
) -> SubscriptionSpecMembershipProof:
    """Derive one bounded membership path from an explicit validated scope."""

    if type(scope) is not CoverageScope:
        raise TypeError("scope must be a CoverageScope.")
    if type(subscription_spec_id) is not SubscriptionSpecId:
        raise TypeError("subscription_spec_id must be a SubscriptionSpecId.")
    try:
        member_index = scope.subscription_spec_ids.index(subscription_spec_id)
    except ValueError:
        member_index = -1
    if member_index < 0:
        raise ValueError("subscription spec does not belong to coverage scope.")
    levels = _subscription_spec_member_tree(scope.subscription_spec_ids)
    siblings: list[str] = []
    tree_index = member_index
    for level in levels[:-1]:
        siblings.append(level[tree_index ^ 1])
        tree_index //= 2
    return SubscriptionSpecMembershipProof(
        coverage_scope_id=scope.coverage_scope_id,
        subscription_spec_id=subscription_spec_id,
        member_index=member_index,
        member_count=len(scope.subscription_spec_ids),
        sibling_sha256s=tuple(siblings),
    )


@dataclass(frozen=True, slots=True)
class CoverageEpochIdentity:
    """One caller-delimited epoch for a scope.

    ID preimage: ``["coverage-epoch-v1", coverage_scope_id,
    collector_run_id, epoch_ordinal, activation_time,
    activation_monotonic_ns]``.
    """

    scope: CoverageScope
    collector_run_id: CollectorRunId
    epoch_ordinal: int
    activation_time: datetime
    activation_monotonic_ns: int
    coverage_epoch_id: CoverageEpochId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.scope) is not CoverageScope:
            raise TypeError("scope must be a CoverageScope.")
        if type(self.collector_run_id) is not CollectorRunId:
            raise TypeError("collector_run_id must be a CollectorRunId.")
        ordinal = require_nonnegative_int(self.epoch_ordinal, field_name="epoch_ordinal")
        activation = canonical_utc_datetime(
            self.activation_time,
            field_name="activation_time",
        )
        activation_monotonic = require_nonnegative_int(
            self.activation_monotonic_ns,
            field_name="activation_monotonic_ns",
        )
        object.__setattr__(
            self,
            "activation_time",
            datetime.fromisoformat(activation.replace("Z", "+00:00")),
        )
        object.__setattr__(
            self,
            "coverage_epoch_id",
            CoverageEpochId(
                canonical_json_array(
                    (
                        "coverage-epoch-v1",
                        self.scope.coverage_scope_id,
                        self.collector_run_id,
                        ordinal,
                        activation,
                        activation_monotonic,
                    )
                )
            ),
        )


def _coverage_source_components(source: CoverageEvidenceSource) -> tuple[object, ...]:
    return source.canonical_components()


def _raw_record_feed_run_session(
    raw_record_id: RawRecordId,
) -> tuple[FeedProductId, CollectorRunId, ConnectionSessionId]:
    components = parse_canonical_json_array(raw_record_id.value, field_name="raw_record_id")
    return (
        FeedProductId(_component_text(components[1], field_name="feed_product_id")),
        CollectorRunId(_component_text(components[2], field_name="collector_run_id")),
        ConnectionSessionId(_component_text(components[3], field_name="connection_session_id")),
    )


def _raw_record_feed_run(raw_record_id: RawRecordId) -> tuple[FeedProductId, CollectorRunId]:
    feed, run, _session = _raw_record_feed_run_session(raw_record_id)
    return feed, run


def _same_event_scope(left: CoverageScopeId, right: CoverageScopeId) -> bool:
    left_components = parse_canonical_json_array(left.value, field_name="coverage_scope_id")
    right_components = parse_canonical_json_array(right.value, field_name="coverage_scope_id")
    return left_components[2:] == right_components[2:]


def _validate_coverage_evidence_source(
    *,
    kind: CoverageEvidenceKind,
    source: CoverageEvidenceSource,
    scope: CoverageScope,
    epoch: CoverageEpochIdentity,
) -> None:
    expected_type: type[object] | None = {
        CoverageEvidenceKind.INITIAL_ACTIVATION: InitialActivationEvidenceSource,
        CoverageEvidenceKind.TRANSPORT_FAILURE: TransportAmbiguityEvidenceSource,
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY: RawRecordEvidenceSource,
        CoverageEvidenceKind.RAW_RECORD_REJECTION: RawRecordEvidenceSource,
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION: (
            UpstreamCoverageTransitionEvidenceSource
        ),
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE: UpstreamCoverageStateEvidenceSource,
        CoverageEvidenceKind.NORMALIZATION_FAILURE: NormalizationFailureEvidenceSource,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY: (
            NormalizationOutcomeEvidenceSource
        ),
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION: (NormalizationOutcomeEvidenceSource),
        CoverageEvidenceKind.SOURCE_SEQUENCE: SourceSequenceBreakEvidenceSource,
        CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT: (
            AuthoritativeStateSnapshotEvidenceSource
        ),
        CoverageEvidenceKind.ACKNOWLEDGEMENT: AcknowledgementEvidenceSource,
        CoverageEvidenceKind.RECONNECT: ReconnectEvidenceSource,
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT: SourceEventConflictEvidenceSource,
    }.get(kind)
    if expected_type is None or type(source) is not expected_type:
        raise ValueError("coverage evidence kind requires its exact closed source type.")

    allowed_domains = {
        CoverageEvidenceKind.INITIAL_ACTIVATION: {
            CoverageDomain.BRONZE_INGRESS,
            CoverageDomain.SILVER_NORMALIZATION,
        },
        CoverageEvidenceKind.TRANSPORT_FAILURE: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.RAW_RECORD_REJECTION: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.NORMALIZATION_FAILURE: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY: {
            CoverageDomain.SILVER_NORMALIZATION
        },
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.SOURCE_SEQUENCE: {
            CoverageDomain.BRONZE_INGRESS,
            CoverageDomain.SILVER_NORMALIZATION,
        },
        CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.ACKNOWLEDGEMENT: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.RECONNECT: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT: {CoverageDomain.SILVER_NORMALIZATION},
    }[kind]
    if scope.domain not in allowed_domains:
        raise ValueError("coverage evidence kind is incompatible with the coverage domain.")

    run = epoch.collector_run_id
    feed = scope.feed_product_id
    if type(source) is InitialActivationEvidenceSource:
        if source.connection_session.collector_run_id != run:
            raise ValueError("activation evidence must belong to the coverage collector run.")
        attempt_specs = tuple(
            sorted(
                (
                    snapshot.subscription_attempt.subscription_spec.subscription_spec_id
                    for snapshot in source.acknowledged_attempts
                ),
                key=lambda item: item.value,
            )
        )
        if attempt_specs != scope.subscription_spec_ids:
            raise ValueError("activation evidence must acknowledge every scope subscription spec.")
        if any(
            snapshot.subscription_attempt.subscription_spec.feed_product_id != feed
            for snapshot in source.acknowledged_attempts
        ):
            raise ValueError("activation evidence subscription feed must match coverage scope.")
        return
    if type(source) is TransportAmbiguityEvidenceSource:
        if source.feed_product_id != feed or source.connection_session.collector_run_id != run:
            raise ValueError("transport evidence must belong to the coverage feed and run.")
        if source.subscription_attempt is not None:
            if source.subscription_attempt.subscription_spec.subscription_spec_id not in (
                scope.subscription_spec_ids
            ):
                raise ValueError("transport attempt must belong to the coverage scope.")
            if source.subscription_attempt.subscription_spec.feed_product_id != feed:
                raise ValueError("transport attempt feed must match the coverage scope.")
            if (
                source.subscription_spec_membership is None
                or source.subscription_spec_membership.coverage_scope_id != scope.coverage_scope_id
            ):
                raise ValueError("transport membership proof must match coverage scope.")
        return
    if type(source) is RawRecordEvidenceSource:
        raw_feed, raw_run = _raw_record_feed_run(source.raw_record_id)
        if (
            raw_feed != feed
            or raw_run != run
            or source.identified_coverage_scope_id != scope.coverage_scope_id
        ):
            raise ValueError("raw evidence must belong to the exact coverage scope and run.")
        return
    if type(source) is AuthoritativeStateSnapshotEvidenceSource:
        raw_feed, raw_run = _raw_record_feed_run(source.raw_record_id)
        if raw_feed != feed or raw_run != run:
            raise ValueError("raw evidence must belong to the coverage feed and collector run.")
        return
    if type(source) is UpstreamCoverageTransitionEvidenceSource:
        upstream_state = source.committed_state.state_reference
        upstream_scope_id = upstream_state.reference.scope.coverage_scope_id
        upstream_scope_components = parse_canonical_json_array(
            upstream_scope_id.value,
            field_name="upstream_coverage_scope_id",
        )
        if (
            scope.domain is not CoverageDomain.SILVER_NORMALIZATION
            or upstream_scope_components[1] != CoverageDomain.BRONZE_INGRESS.value
            or not _same_event_scope(upstream_scope_id, scope.coverage_scope_id)
            or upstream_state.reference.epoch.collector_run_id != run
            or upstream_state.reference.status is CoverageStatus.COMPLETE
        ):
            raise ValueError("upstream transition must match the Bronze event scope and run.")
        return
    if type(source) is UpstreamCoverageStateEvidenceSource:
        upstream_scope_id, upstream_run_id, upstream_status = _upstream_coverage_state_details(
            source.coverage_state_reference_id
        )
        upstream_scope_components = parse_canonical_json_array(
            upstream_scope_id.value,
            field_name="upstream_coverage_scope_id",
        )
        if (
            scope.domain is not CoverageDomain.SILVER_NORMALIZATION
            or upstream_scope_components[1] != CoverageDomain.BRONZE_INGRESS.value
            or not _same_event_scope(upstream_scope_id, scope.coverage_scope_id)
            or upstream_run_id != run
            or upstream_status is CoverageStatus.COMPLETE
        ):
            raise ValueError("upstream state must be degraded matching Bronze coverage.")
        return
    if type(source) is NormalizationFailureEvidenceSource:
        raw_feed, raw_run = _raw_record_feed_run(source.raw_record_id)
        if (
            scope.domain is not CoverageDomain.SILVER_NORMALIZATION
            or raw_feed != feed
            or raw_run != run
            or source.identified_coverage_scope_id != scope.coverage_scope_id
        ):
            raise ValueError("normalization evidence must match the exact Silver scope and run.")
        return
    if type(source) is NormalizationOutcomeEvidenceSource:
        _normalization_run, raw_record_id, _frame_status = _normalization_outcome_id_details(
            source.normalization_outcome_id
        )
        raw_feed, raw_run = _raw_record_feed_run(raw_record_id)
        if (
            scope.domain is not CoverageDomain.SILVER_NORMALIZATION
            or raw_feed != feed
            or raw_run != run
            or source.identified_coverage_scope_id != scope.coverage_scope_id
        ):
            raise ValueError(
                "normalization outcome evidence must match the exact Silver scope and run."
            )
        return
    if type(source) is SourceSequenceBreakEvidenceSource:
        if (
            source.feed_product_id != feed
            or source.identified_coverage_scope_id != scope.coverage_scope_id
        ):
            raise ValueError("source-sequence evidence must match the exact coverage scope.")
        return
    if type(source) is SourceEventConflictEvidenceSource:
        raw_feed, raw_run = _raw_record_feed_run(source.raw_record_id)
        if (
            scope.domain is not CoverageDomain.SILVER_NORMALIZATION
            or source.feed_product_id != feed
            or raw_feed != feed
            or raw_run != run
            or source.identified_coverage_scope_id != scope.coverage_scope_id
        ):
            raise ValueError("source-conflict evidence must match the exact Silver scope and run.")
        return
    if type(source) is AcknowledgementEvidenceSource:
        attempt = source.acknowledged_attempt.subscription_attempt
        if (
            attempt.connection_session.collector_run_id != run
            or attempt.subscription_spec.feed_product_id != feed
            or attempt.subscription_spec.subscription_spec_id not in scope.subscription_spec_ids
            or source.subscription_spec_membership.coverage_scope_id != scope.coverage_scope_id
        ):
            raise ValueError("acknowledgement evidence must match coverage scope and run.")
        return
    if type(source) is ReconnectEvidenceSource:
        if source.feed_product_id != feed or source.connection_session.collector_run_id != run:
            raise ValueError("reconnect evidence must belong to the coverage feed and run.")
        return
    raise AssertionError("unhandled coverage evidence source")


def _validate_coverage_evidence_id_components(
    components: tuple[CanonicalValue, ...],
) -> None:
    _require_component_count(components, 8, identifier_name="CoverageEvidenceId")
    scope = CoverageScopeId(_component_text(components[1], field_name="coverage_scope_id"))
    epoch = CoverageEpochId(_component_text(components[2], field_name="coverage_epoch_id"))
    run = CollectorRunId(_component_text(components[3], field_name="collector_run_id"))
    epoch_components = parse_canonical_json_array(epoch.value, field_name="coverage_epoch_id")
    if epoch_components[1] != scope.value or epoch_components[2] != run.value:
        raise ValueError("coverage evidence scope, epoch and collector run must agree.")
    kind = CoverageEvidenceKind(_component_text(components[4], field_name="evidence_kind"))
    source = _component_tuple(components[5], field_name="typed_source_reference")
    expected_tag = {
        CoverageEvidenceKind.INITIAL_ACTIVATION: "initial-activation-evidence-v1",
        CoverageEvidenceKind.TRANSPORT_FAILURE: "transport-ambiguity-evidence-v1",
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY: "raw-record-evidence-v1",
        CoverageEvidenceKind.RAW_RECORD_REJECTION: "raw-record-evidence-v1",
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION: (
            "upstream-coverage-transition-evidence-v1"
        ),
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE: "upstream-coverage-state-evidence-v1",
        CoverageEvidenceKind.NORMALIZATION_FAILURE: ("normalization-failure-evidence-reference-v1"),
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY: (
            "normalization-outcome-evidence-v1"
        ),
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION: ("normalization-outcome-evidence-v1"),
        CoverageEvidenceKind.SOURCE_SEQUENCE: "source-sequence-break-evidence-v1",
        CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT: (
            "authoritative-state-snapshot-evidence-v1"
        ),
        CoverageEvidenceKind.ACKNOWLEDGEMENT: "acknowledgement-evidence-v1",
        CoverageEvidenceKind.RECONNECT: "reconnect-evidence-v1",
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT: "source-event-conflict-evidence-v1",
    }.get(kind)
    if expected_tag is None or not source or source[0] != expected_tag:
        raise ValueError("coverage evidence contains an incompatible typed source reference.")
    scope_components = parse_canonical_json_array(scope.value, field_name="coverage_scope_id")
    scope_domain = CoverageDomain(
        _component_text(scope_components[1], field_name="coverage_domain")
    )
    scope_feed = FeedProductId(_component_text(scope_components[2], field_name="feed_product_id"))
    scope_spec_count = _component_nonnegative_int(
        scope_components[6],
        field_name="subscription_spec_count",
    )
    allowed_domains = {
        CoverageEvidenceKind.INITIAL_ACTIVATION: {
            CoverageDomain.BRONZE_INGRESS,
            CoverageDomain.SILVER_NORMALIZATION,
        },
        CoverageEvidenceKind.TRANSPORT_FAILURE: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.RAW_RECORD_REJECTION: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.NORMALIZATION_FAILURE: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY: {
            CoverageDomain.SILVER_NORMALIZATION
        },
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.SOURCE_SEQUENCE: {
            CoverageDomain.BRONZE_INGRESS,
            CoverageDomain.SILVER_NORMALIZATION,
        },
        CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT: {CoverageDomain.SILVER_NORMALIZATION},
        CoverageEvidenceKind.ACKNOWLEDGEMENT: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.RECONNECT: {CoverageDomain.BRONZE_INGRESS},
        CoverageEvidenceKind.SOURCE_EVENT_CONFLICT: {CoverageDomain.SILVER_NORMALIZATION},
    }[kind]
    if scope_domain not in allowed_domains:
        raise ValueError("coverage evidence kind is incompatible with the coverage domain.")

    def validate_session(value: CanonicalValue) -> ConnectionSessionId:
        session = ConnectionSessionId(_component_text(value, field_name="connection_session_id"))
        session_components = parse_canonical_json_array(
            session.value,
            field_name="connection_session_id",
        )
        if session_components[1] != run.value:
            raise ValueError("coverage evidence session must belong to its collector run.")
        return session

    def validate_attempt(
        value: CanonicalValue,
        session: ConnectionSessionId | None,
    ) -> SubscriptionSpecId:
        attempt = SubscriptionAttemptId(
            _component_text(value, field_name="subscription_attempt_id")
        )
        attempt_components = parse_canonical_json_array(
            attempt.value,
            field_name="subscription_attempt_id",
        )
        attempt_session = ConnectionSessionId(
            _component_text(attempt_components[1], field_name="connection_session_id")
        )
        attempt_session_components = parse_canonical_json_array(
            attempt_session.value,
            field_name="connection_session_id",
        )
        if attempt_session_components[1] != run.value:
            raise ValueError("coverage evidence attempt must belong to its collector run.")
        if session is not None and attempt_session != session:
            raise ValueError("coverage evidence attempt must belong to its session.")
        spec = SubscriptionSpecId(
            _component_text(attempt_components[2], field_name="subscription_spec_id")
        )
        spec_components = parse_canonical_json_array(
            spec.value,
            field_name="subscription_spec_id",
        )
        if spec_components[1] != scope_feed.value:
            raise ValueError("coverage evidence attempt must belong to its scope feed.")
        return spec

    def validate_membership(
        value: CanonicalValue,
        subscription_spec_id: SubscriptionSpecId,
    ) -> None:
        proof_components = _component_tuple(value, field_name="subscription_spec_membership")
        _require_component_count(
            proof_components,
            6,
            identifier_name="subscription spec membership proof",
        )
        if proof_components[0] != "subscription-spec-membership-proof-v1":
            raise ValueError("subscription spec membership proof has an unsupported version.")
        sibling_values = _component_tuple(
            proof_components[5],
            field_name="membership_sibling_sha256s",
        )
        proof = SubscriptionSpecMembershipProof(
            coverage_scope_id=CoverageScopeId(
                _component_text(proof_components[1], field_name="coverage_scope_id")
            ),
            subscription_spec_id=SubscriptionSpecId(
                _component_text(proof_components[2], field_name="subscription_spec_id")
            ),
            member_index=_component_nonnegative_int(
                proof_components[3],
                field_name="member_index",
            ),
            member_count=_component_nonnegative_int(
                proof_components[4],
                field_name="member_count",
            ),
            sibling_sha256s=tuple(
                _component_text(item, field_name="membership_sibling_sha256")
                for item in sibling_values
            ),
        )
        if proof.coverage_scope_id != scope or proof.subscription_spec_id != subscription_spec_id:
            raise ValueError(
                "subscription spec membership proof must match evidence scope and spec."
            )

    def validate_raw(value: CanonicalValue) -> RawRecordId:
        raw = RawRecordId(_component_text(value, field_name="raw_record_id"))
        raw_feed, raw_run = _raw_record_feed_run(raw)
        if raw_feed != scope_feed or raw_run != run:
            raise ValueError("coverage raw evidence must match its feed and collector run.")
        return raw

    if kind is CoverageEvidenceKind.INITIAL_ACTIVATION:
        _require_component_count(source, 5, identifier_name="initial activation source")
        validate_session(source[1])
        attempt_count = _component_nonnegative_int(
            source[2],
            field_name="acknowledged_attempt_count",
        )
        if attempt_count != scope_spec_count:
            raise ValueError("initial activation count must match the committed scope spec count.")
        spec_members_sha256 = require_sha256(
            _component_text(source[3], field_name="acknowledged_spec_members_sha256"),
            field_name="acknowledged_spec_members_sha256",
        )
        if spec_members_sha256 != scope_components[8]:
            raise ValueError("initial activation specs must match the committed coverage scope.")
        require_sha256(
            _component_text(source[4], field_name="acknowledged_attempts_content_sha256"),
            field_name="acknowledged_attempts_content_sha256",
        )
    elif kind is CoverageEvidenceKind.TRANSPORT_FAILURE:
        _require_component_count(source, 5, identifier_name="transport ambiguity source")
        if source[1] != scope_feed.value:
            raise ValueError("transport evidence feed must match coverage scope.")
        session = validate_session(source[2])
        if source[3] is not None:
            attempted_spec = validate_attempt(source[3], session)
            if source[4] is None:
                raise ValueError("transport attempt requires a membership proof.")
            validate_membership(source[4], attempted_spec)
        elif source[4] is not None:
            raise ValueError("transport membership proof requires an attempt.")
    elif kind in {
        CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.RAW_RECORD_REJECTION,
        CoverageEvidenceKind.AUTHORITATIVE_STATE_SNAPSHOT,
    }:
        expected_count = (
            3
            if kind
            in {
                CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
                CoverageEvidenceKind.RAW_RECORD_REJECTION,
            }
            else 2
        )
        _require_component_count(source, expected_count, identifier_name="raw evidence source")
        validate_raw(source[1])
        if expected_count == 3 and source[2] != scope.value:
            raise ValueError("raw evidence must bind the exact coverage scope.")
    elif kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION:
        _require_component_count(source, 2, identifier_name="upstream transition source")
        committed = CommittedCoverageStateId(
            _component_text(source[1], field_name="committed_coverage_state_id")
        )
        committed_details = _committed_coverage_state_id_details(committed)
        state_components = parse_canonical_json_array(
            committed_details[0].value,
            field_name="coverage_state_reference_id",
        )
        if state_components[1] != "transition":
            raise ValueError("upstream transition evidence requires a transitioned state.")
        upstream_scope = committed_details[1]
        upstream_scope_components = parse_canonical_json_array(
            upstream_scope.value,
            field_name="upstream_scope_id",
        )
        if (
            scope_components[1] != CoverageDomain.SILVER_NORMALIZATION.value
            or upstream_scope_components[1] != CoverageDomain.BRONZE_INGRESS.value
            or not _same_event_scope(upstream_scope, scope)
            or committed_details[2] != run
            or committed_details[3] is CoverageStatus.COMPLETE
        ):
            raise ValueError("upstream coverage evidence must match Bronze scope and run.")
    elif kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE:
        _require_component_count(source, 2, identifier_name="upstream state source")
        committed = CommittedCoverageStateId(
            _component_text(source[1], field_name="committed_coverage_state_id")
        )
        committed_details = _committed_coverage_state_id_details(committed)
        upstream_scope = committed_details[1]
        upstream_run = committed_details[2]
        upstream_status = committed_details[3]
        upstream_scope_components = parse_canonical_json_array(
            upstream_scope.value,
            field_name="upstream_scope_id",
        )
        if (
            scope_components[1] != CoverageDomain.SILVER_NORMALIZATION.value
            or upstream_scope_components[1] != CoverageDomain.BRONZE_INGRESS.value
            or not _same_event_scope(upstream_scope, scope)
            or upstream_run != run
            or upstream_status is CoverageStatus.COMPLETE
        ):
            raise ValueError("upstream state evidence must match degraded Bronze scope and run.")
    elif kind is CoverageEvidenceKind.NORMALIZATION_FAILURE:
        _require_component_count(source, 2, identifier_name="normalization evidence source")
        failure = NormalizationFailureEvidenceId(
            _component_text(source[1], field_name="normalization_failure_evidence_id")
        )
        failure_components = parse_canonical_json_array(
            failure.value,
            field_name="normalization_failure_evidence_id",
        )
        validate_raw(failure_components[1])
        if (
            scope_components[1] != CoverageDomain.SILVER_NORMALIZATION.value
            or failure_components[6] != scope.value
        ):
            raise ValueError("normalization evidence must bind exact Silver-normalization scope.")
    elif kind in {
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
    }:
        _require_component_count(source, 3, identifier_name="normalization outcome evidence")
        outcome_id = NormalizationOutcomeId(
            _component_text(source[1], field_name="normalization_outcome_id")
        )
        _normalization_run, raw_record_id, _frame_status = _normalization_outcome_id_details(
            outcome_id
        )
        validate_raw(raw_record_id.value)
        if (
            scope_components[1] != CoverageDomain.SILVER_NORMALIZATION.value
            or source[2] != scope.value
        ):
            raise ValueError(
                "normalization outcome evidence must bind exact Silver-normalization scope."
            )
    elif kind is CoverageEvidenceKind.SOURCE_SEQUENCE:
        _require_component_count(source, 7, identifier_name="source sequence evidence")
        if source[1] != scope_feed.value:
            raise ValueError("source sequence evidence feed must match coverage scope.")
        SourceSequenceRole(_component_text(source[2], field_name="source_sequence_role"))
        require_code(
            _component_text(source[3], field_name="sequence_namespace"),
            field_name="sequence_namespace",
        )
        first = _component_nonnegative_int(source[4], field_name="sequence_first")
        last = _component_nonnegative_int(source[5], field_name="sequence_last")
        if last < first:
            raise ValueError("source sequence evidence range is invalid.")
        if source[6] != scope.value:
            raise ValueError("source sequence evidence must bind the exact coverage scope.")
    elif kind is CoverageEvidenceKind.SOURCE_EVENT_CONFLICT:
        _require_component_count(source, 6, identifier_name="source conflict evidence")
        if source[1] != scope_feed.value:
            raise ValueError("source conflict feed must match coverage scope.")
        SourceEventId(_component_text(source[2], field_name="source_event_id"))
        validate_raw(source[3])
        _component_nonnegative_int(source[4], field_name="raw_event_index")
        if (
            scope_components[1] != CoverageDomain.SILVER_NORMALIZATION.value
            or source[5] != scope.value
        ):
            raise ValueError("source conflict evidence must bind exact Silver-normalization scope.")
    elif kind is CoverageEvidenceKind.ACKNOWLEDGEMENT:
        _require_component_count(source, 4, identifier_name="acknowledgement evidence")
        if source[2] != SubscriptionAttemptStatus.ACKNOWLEDGED.value:
            raise ValueError("acknowledgement evidence requires acknowledged status.")
        acknowledged_spec = validate_attempt(source[1], None)
        validate_membership(source[3], acknowledged_spec)
    elif kind is CoverageEvidenceKind.RECONNECT:
        _require_component_count(source, 3, identifier_name="reconnect evidence")
        if source[1] != scope_feed.value:
            raise ValueError("reconnect evidence feed must match coverage scope.")
        validate_session(source[2])
    else:
        raise ValueError("coverage evidence kind has no safe persisted source contract.")
    _canonical_utc_component(components[6], field_name="observed_at")
    observed_monotonic_ns = _component_nonnegative_int(
        components[7],
        field_name="observed_monotonic_ns",
    )
    epoch_activation_monotonic_ns = _component_nonnegative_int(
        epoch_components[5],
        field_name="activation_monotonic_ns",
    )
    if observed_monotonic_ns < epoch_activation_monotonic_ns:
        raise ValueError("coverage evidence cannot precede epoch activation.")
    if kind in {
        CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
        CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
    }:
        committed = CommittedCoverageStateId(
            _component_text(source[1], field_name="committed_coverage_state_id")
        )
        upstream_details = _committed_coverage_state_id_details(committed)
        if observed_monotonic_ns < upstream_details[6]:
            raise ValueError("downstream evidence cannot precede upstream monotonic evidence.")


@dataclass(frozen=True, slots=True)
class CoverageEvidence:
    """Closed typed evidence bound to one exact scope, epoch and collector run."""

    kind: CoverageEvidenceKind
    source: CoverageEvidenceSource = field(repr=False)
    scope: CoverageScope
    epoch: CoverageEpochIdentity
    observed_at: datetime
    observed_monotonic_ns: int
    coverage_evidence_id: CoverageEvidenceId = field(init=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not CoverageEvidenceKind:
            raise TypeError("kind must be a CoverageEvidenceKind.")
        if type(self.scope) is not CoverageScope:
            raise TypeError("scope must be a CoverageScope.")
        if type(self.epoch) is not CoverageEpochIdentity:
            raise TypeError("epoch must be a CoverageEpochIdentity.")
        if self.epoch.scope != self.scope:
            raise ValueError("coverage evidence epoch must belong to its scope.")
        observed = canonical_utc_datetime(self.observed_at, field_name="observed_at")
        monotonic = require_nonnegative_int(
            self.observed_monotonic_ns,
            field_name="observed_monotonic_ns",
        )
        if monotonic < self.epoch.activation_monotonic_ns:
            raise ValueError("coverage evidence cannot precede epoch activation.")
        _validate_coverage_evidence_source(
            kind=self.kind,
            source=self.source,
            scope=self.scope,
            epoch=self.epoch,
        )
        if isinstance(
            self.source,
            (UpstreamCoverageTransitionEvidenceSource, UpstreamCoverageStateEvidenceSource),
        ):
            upstream_state = self.source.committed_state.state_reference
            upstream_details = _coverage_state_reference_details(
                upstream_state.coverage_state_reference_id
            )
            if monotonic < upstream_details[6]:
                raise ValueError("downstream evidence cannot precede upstream monotonic evidence.")
        object.__setattr__(
            self,
            "observed_at",
            datetime.fromisoformat(observed.replace("Z", "+00:00")),
        )
        object.__setattr__(
            self,
            "coverage_evidence_id",
            CoverageEvidenceId(
                canonical_json_array(
                    (
                        "coverage-evidence-v1",
                        self.scope.coverage_scope_id,
                        self.epoch.coverage_epoch_id,
                        self.epoch.collector_run_id,
                        self.kind.value,
                        _coverage_source_components(self.source),
                        observed,
                        monotonic,
                    )
                )
            ),
        )

    @property
    def coverage_scope_id(self) -> CoverageScopeId:
        return self.scope.coverage_scope_id

    @property
    def coverage_epoch_id(self) -> CoverageEpochId:
        return self.epoch.coverage_epoch_id

    @property
    def collector_run_id(self) -> CollectorRunId:
        return self.epoch.collector_run_id


_INITIAL_COVERAGE_MATRIX: Final = frozenset(
    {
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
    }
)


@dataclass(frozen=True, slots=True, init=False)
class CoverageReference:
    """Current immutable coverage state created initially or by the reducer only."""

    scope: CoverageScope
    epoch: CoverageEpochIdentity
    transition_ordinal: int
    status: CoverageStatus
    initial_reason: InitialCoverageReason
    initial_evidence: CoverageEvidence
    transition_id: CoverageTransitionId | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use CoverageReference.initial() or reduce_coverage().")

    @classmethod
    def initial(
        cls,
        *,
        scope: CoverageScope,
        epoch: CoverageEpochIdentity,
        status: CoverageStatus,
        initial_reason: InitialCoverageReason,
        initial_evidence: CoverageEvidence,
    ) -> "CoverageReference":
        """Construct exactly ordinal-zero coverage through the closed initial matrix."""

        if cls is not CoverageReference:
            raise TypeError("coverage references do not support subclass construction.")
        return _construct_coverage_reference(
            scope=scope,
            epoch=epoch,
            transition_ordinal=0,
            status=status,
            initial_reason=initial_reason,
            initial_evidence=initial_evidence,
            transition_id=None,
        )

    def _validate(self) -> None:
        if type(self.scope) is not CoverageScope:
            raise TypeError("scope must be a CoverageScope.")
        if type(self.epoch) is not CoverageEpochIdentity:
            raise TypeError("epoch must be a CoverageEpochIdentity.")
        if self.epoch.scope != self.scope:
            raise ValueError("coverage epoch must belong to the reference scope.")
        ordinal = require_nonnegative_int(self.transition_ordinal, field_name="transition_ordinal")
        if type(self.status) is not CoverageStatus:
            raise TypeError("status must be a CoverageStatus.")
        if type(self.initial_reason) is not InitialCoverageReason:
            raise TypeError("initial_reason must be an InitialCoverageReason.")
        if type(self.initial_evidence) is not CoverageEvidence:
            raise TypeError("initial_evidence must be CoverageEvidence.")
        if self.initial_evidence.coverage_scope_id != self.scope.coverage_scope_id:
            raise ValueError("initial evidence must belong to the reference scope.")
        if self.initial_evidence.coverage_epoch_id != self.epoch.coverage_epoch_id:
            raise ValueError("initial evidence must belong to the reference epoch.")
        if self.initial_evidence.collector_run_id != self.epoch.collector_run_id:
            raise ValueError("initial evidence must belong to the reference collector run.")
        if (
            self.initial_evidence.observed_at != self.epoch.activation_time
            or self.initial_evidence.observed_monotonic_ns != self.epoch.activation_monotonic_ns
        ):
            raise ValueError("initial evidence must bind the epoch activation boundary.")
        if self.transition_id is not None and type(self.transition_id) is not CoverageTransitionId:
            raise TypeError("transition_id must be a CoverageTransitionId or None.")
        if ordinal == 0 and self.transition_id is not None:
            raise ValueError("the initial coverage reference cannot name a transition.")
        if ordinal > 0 and self.transition_id is None:
            raise ValueError("a non-initial coverage reference requires a transition_id.")
        initial_matches = tuple(
            row
            for row in _INITIAL_COVERAGE_MATRIX
            if row[0] is self.scope.domain
            and row[2] is self.initial_reason
            and row[3] is self.initial_evidence.kind
        )
        if type(self.initial_evidence.source) is UpstreamCoverageStateEvidenceSource:
            upstream_details = _coverage_state_reference_details(
                self.initial_evidence.source.coverage_state_reference_id
            )
            if (
                self.scope.domain,
                upstream_details[3],
                self.initial_reason,
                self.initial_evidence.kind,
            ) not in _INITIAL_COVERAGE_MATRIX or (
                ordinal == 0 and upstream_details[3] is not self.status
            ):
                raise ValueError(
                    "initial Silver status must exactly match its degraded Bronze state."
                )
            if ordinal == 0 and (
                canonical_utc_datetime(
                    self.initial_evidence.observed_at,
                    field_name="initial_evidence.observed_at",
                )
                != upstream_details[5]
                or self.initial_evidence.observed_monotonic_ns != upstream_details[6]
            ):
                raise ValueError(
                    "initial Silver boundary must exactly copy its upstream Bronze boundary."
                )
        elif (
            ordinal == 0
            and self.initial_evidence.kind is CoverageEvidenceKind.TRANSPORT_FAILURE
            and type(self.initial_evidence.source) is TransportAmbiguityEvidenceSource
            and self.initial_evidence.source.subscription_attempt is None
        ):
            raise ValueError("transport failure before any send cannot initialize coverage.")
        elif len(initial_matches) != 1 or (
            ordinal == 0 and initial_matches[0][1] is not self.status
        ):
            raise ValueError("initial coverage status, reason and evidence are incompatible.")
        if self.transition_id is not None:
            transition_components = json.loads(self.transition_id.value)
            if (
                transition_components[1] != self.scope.coverage_scope_id.value
                or transition_components[2] != self.epoch.coverage_epoch_id.value
                or transition_components[3] != ordinal
                or transition_components[5] != self.status.value
            ):
                raise ValueError(
                    "transition_id must bind the reference scope, epoch, ordinal and status."
                )
            _validate_coverage_transition_semantics(
                domain=self.scope.domain,
                previous_status=CoverageStatus(transition_components[4]),
                new_status=CoverageStatus(transition_components[5]),
                reason=CoverageReason(transition_components[6]),
                evidence_kind=CoverageEvidenceKind(
                    _component_text(
                        parse_canonical_json_array(
                            transition_components[7],
                            field_name="coverage_evidence_id",
                        )[4],
                        field_name="coverage_evidence_id",
                    )
                ),
            )


def _construct_coverage_reference(
    *,
    scope: CoverageScope,
    epoch: CoverageEpochIdentity,
    transition_ordinal: int,
    status: CoverageStatus,
    initial_reason: InitialCoverageReason,
    initial_evidence: CoverageEvidence,
    transition_id: CoverageTransitionId | None,
) -> CoverageReference:
    reference = object.__new__(CoverageReference)
    object.__setattr__(reference, "scope", scope)
    object.__setattr__(reference, "epoch", epoch)
    object.__setattr__(reference, "transition_ordinal", transition_ordinal)
    object.__setattr__(reference, "status", status)
    object.__setattr__(reference, "initial_reason", initial_reason)
    object.__setattr__(reference, "initial_evidence", initial_evidence)
    object.__setattr__(reference, "transition_id", transition_id)
    reference._validate()
    return reference


@dataclass(frozen=True, slots=True)
class RequestedCoverageTransition:
    """Caller-requested bounded state transition, pending evidence validation."""

    scope: CoverageScope
    epoch: CoverageEpochIdentity
    previous_status: CoverageStatus
    next_transition_ordinal: int
    requested_status: CoverageStatus
    reason: CoverageReason

    def __post_init__(self) -> None:
        if type(self.scope) is not CoverageScope:
            raise TypeError("scope must be a CoverageScope.")
        if type(self.epoch) is not CoverageEpochIdentity:
            raise TypeError("epoch must be a CoverageEpochIdentity.")
        if self.epoch.scope != self.scope:
            raise ValueError("coverage epoch must belong to the requested scope.")
        if type(self.previous_status) is not CoverageStatus:
            raise TypeError("previous_status must be a CoverageStatus.")
        require_nonnegative_int(
            self.next_transition_ordinal,
            field_name="next_transition_ordinal",
        )
        if type(self.requested_status) is not CoverageStatus:
            raise TypeError("requested_status must be a CoverageStatus.")
        if type(self.reason) is not CoverageReason:
            raise TypeError("reason must be a CoverageReason.")


_UNCERTAIN_TRANSITION_EVIDENCE: Final = frozenset(
    {
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageReason.TRANSPORT_AMBIGUITY,
            CoverageEvidenceKind.TRANSPORT_FAILURE,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageReason.TRANSPORT_AMBIGUITY,
            CoverageEvidenceKind.RECONNECT,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageReason.RAW_ACCEPTANCE_UNCERTAIN,
            CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
        ),
    }
)

_CONFIRMED_INCOMPLETE_TRANSITION_EVIDENCE: Final = frozenset(
    {
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageReason.RAW_DEFINITE_REJECTION,
            CoverageEvidenceKind.RAW_RECORD_REJECTION,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
            CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
            CoverageEvidenceKind.NORMALIZATION_FAILURE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
            CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.SOURCE_EVENT_CONFLICT,
            CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
        ),
        (
            CoverageDomain.BRONZE_INGRESS,
            CoverageReason.SOURCE_SEQUENCE_BREAK,
            CoverageEvidenceKind.SOURCE_SEQUENCE,
        ),
        (
            CoverageDomain.SILVER_NORMALIZATION,
            CoverageReason.SOURCE_SEQUENCE_BREAK,
            CoverageEvidenceKind.SOURCE_SEQUENCE,
        ),
    }
)


def _validate_coverage_transition_semantics(
    *,
    domain: CoverageDomain,
    previous_status: CoverageStatus,
    new_status: CoverageStatus,
    reason: CoverageReason,
    evidence_kind: CoverageEvidenceKind,
) -> None:
    """Validate the closed evidence matrix for one requested status change."""

    if domain is CoverageDomain.SILVER_DELIVERY:
        raise ValueError("silver delivery is represented by separate delivery contracts.")

    severity = {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.UNCERTAIN: 1,
        CoverageStatus.CONFIRMED_INCOMPLETE: 2,
    }
    if previous_status is new_status:
        raise ValueError("coverage transitions must change status.")

    pair = (reason, evidence_kind)
    if severity[new_status] < severity[previous_status]:
        raise ValueError(
            "coverage recovery is unsupported without typed interval-bound sequence/backfill proof."
        )
    if new_status is CoverageStatus.UNCERTAIN:
        if (domain, *pair) not in _UNCERTAIN_TRANSITION_EVIDENCE:
            raise ValueError("uncertain coverage requires matching ambiguity evidence.")
        return
    if new_status is CoverageStatus.CONFIRMED_INCOMPLETE:
        if (domain, *pair) not in _CONFIRMED_INCOMPLETE_TRANSITION_EVIDENCE:
            raise ValueError(
                "confirmed-incomplete coverage requires positively identified in-scope evidence."
            )
        return
    raise ValueError("the requested coverage transition is not allowed.")


def _validate_upstream_transition_status(
    *,
    requested_status: CoverageStatus,
    evidence: CoverageEvidence,
) -> None:
    """Require exact status propagation and same-run monotonic causality."""

    if evidence.kind is not CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION:
        return
    if type(evidence.source) is not UpstreamCoverageTransitionEvidenceSource:
        raise ValueError("upstream transition evidence requires its committed source.")
    upstream = evidence.source.committed_state.state_reference
    if requested_status is not upstream.reference.status:
        raise ValueError("downstream coverage status must exactly match upstream status.")
    upstream_details = _coverage_state_reference_details(upstream.coverage_state_reference_id)
    if evidence.observed_monotonic_ns < upstream_details[6]:
        raise ValueError("downstream coverage evidence cannot precede upstream evidence.")


@dataclass(frozen=True, slots=True)
class CoverageTransition:
    """Validated immutable coverage transition.

    ID preimage: ``["coverage-transition-v1", scope_id, epoch_id, ordinal,
    previous_status, new_status, reason, coverage_evidence_id]``.
    """

    scope: CoverageScope
    epoch: CoverageEpochIdentity
    transition_ordinal: int
    previous_status: CoverageStatus
    new_status: CoverageStatus
    reason: CoverageReason
    evidence: CoverageEvidence
    coverage_transition_id: CoverageTransitionId

    def __post_init__(self) -> None:
        if type(self.scope) is not CoverageScope:
            raise TypeError("scope must be a CoverageScope.")
        if type(self.epoch) is not CoverageEpochIdentity:
            raise TypeError("epoch must be a CoverageEpochIdentity.")
        if self.epoch.scope != self.scope:
            raise ValueError("coverage epoch must belong to transition scope.")
        ordinal = require_nonnegative_int(self.transition_ordinal, field_name="transition_ordinal")
        if ordinal == 0:
            raise ValueError("coverage transition ordinal must be positive.")
        if type(self.previous_status) is not CoverageStatus:
            raise TypeError("previous_status must be a CoverageStatus.")
        if type(self.new_status) is not CoverageStatus:
            raise TypeError("new_status must be a CoverageStatus.")
        if self.previous_status is self.new_status:
            raise ValueError("coverage transition must change status.")
        if type(self.reason) is not CoverageReason:
            raise TypeError("reason must be a CoverageReason.")
        if type(self.evidence) is not CoverageEvidence:
            raise TypeError("evidence must be CoverageEvidence.")
        if self.evidence.coverage_scope_id != self.scope.coverage_scope_id:
            raise ValueError("coverage evidence must belong to transition scope.")
        if self.evidence.coverage_epoch_id != self.epoch.coverage_epoch_id:
            raise ValueError("coverage evidence must belong to transition epoch.")
        if self.evidence.collector_run_id != self.epoch.collector_run_id:
            raise ValueError("coverage evidence must belong to transition collector run.")
        if self.evidence.observed_monotonic_ns < self.epoch.activation_monotonic_ns:
            raise ValueError("coverage evidence cannot precede the epoch activation boundary.")
        if type(self.coverage_transition_id) is not CoverageTransitionId:
            raise TypeError("coverage_transition_id must be a CoverageTransitionId.")
        expected_id = CoverageTransitionId(
            canonical_json_array(
                (
                    "coverage-transition-v1",
                    self.scope.coverage_scope_id,
                    self.epoch.coverage_epoch_id,
                    ordinal,
                    self.previous_status.value,
                    self.new_status.value,
                    self.reason.value,
                    self.evidence.coverage_evidence_id,
                )
            )
        )
        if self.coverage_transition_id != expected_id:
            raise ValueError("coverage_transition_id does not match transition content.")
        _validate_coverage_transition_semantics(
            domain=self.scope.domain,
            previous_status=self.previous_status,
            new_status=self.new_status,
            reason=self.reason,
            evidence_kind=self.evidence.kind,
        )
        _validate_upstream_transition_status(
            requested_status=self.new_status,
            evidence=self.evidence,
        )


def reduce_coverage(
    current: CoverageReference,
    requested: RequestedCoverageTransition,
    evidence: CoverageEvidence,
) -> tuple[CoverageTransition, CoverageReference]:
    """Validate continuity and return a transition plus its new reference."""

    if type(current) is not CoverageReference:
        raise TypeError("current must be a CoverageReference.")
    if type(requested) is not RequestedCoverageTransition:
        raise TypeError("requested must be a RequestedCoverageTransition.")
    if type(evidence) is not CoverageEvidence:
        raise TypeError("evidence must be CoverageEvidence.")
    if requested.scope != current.scope or requested.epoch != current.epoch:
        raise ValueError("coverage transition scope and epoch must match current coverage.")
    if requested.previous_status is not current.status:
        raise ValueError("requested previous status does not match current coverage.")
    if requested.next_transition_ordinal != current.transition_ordinal + 1:
        raise ValueError("coverage transition ordinal must be exactly the next ordinal.")
    if evidence.coverage_scope_id != current.scope.coverage_scope_id:
        raise ValueError("coverage evidence must belong to current coverage scope.")
    if evidence.coverage_epoch_id != current.epoch.coverage_epoch_id:
        raise ValueError("coverage evidence must belong to current coverage epoch.")
    if evidence.collector_run_id != current.epoch.collector_run_id:
        raise ValueError("coverage evidence must belong to current collector run.")
    previous_monotonic_ns = current.epoch.activation_monotonic_ns
    if current.transition_id is not None:
        transition_components = json.loads(current.transition_id.value)
        evidence_components = parse_canonical_json_array(
            transition_components[7],
            field_name="coverage_evidence_id",
        )
        previous_monotonic_ns = _component_nonnegative_int(
            evidence_components[7],
            field_name="previous_evidence_observed_monotonic_ns",
        )
    if evidence.observed_monotonic_ns < previous_monotonic_ns:
        raise ValueError("coverage evidence must not move backwards in monotonic time.")
    _validate_coverage_transition_semantics(
        domain=current.scope.domain,
        previous_status=current.status,
        new_status=requested.requested_status,
        reason=requested.reason,
        evidence_kind=evidence.kind,
    )
    _validate_upstream_transition_status(
        requested_status=requested.requested_status,
        evidence=evidence,
    )

    transition_id = CoverageTransitionId(
        canonical_json_array(
            (
                "coverage-transition-v1",
                current.scope.coverage_scope_id,
                current.epoch.coverage_epoch_id,
                requested.next_transition_ordinal,
                current.status.value,
                requested.requested_status.value,
                requested.reason.value,
                evidence.coverage_evidence_id,
            )
        )
    )
    transition = CoverageTransition(
        scope=current.scope,
        epoch=current.epoch,
        transition_ordinal=requested.next_transition_ordinal,
        previous_status=current.status,
        new_status=requested.requested_status,
        reason=requested.reason,
        evidence=evidence,
        coverage_transition_id=transition_id,
    )
    return transition, _construct_coverage_reference(
        scope=current.scope,
        epoch=current.epoch,
        transition_ordinal=requested.next_transition_ordinal,
        status=requested.requested_status,
        initial_reason=current.initial_reason,
        initial_evidence=current.initial_evidence,
        transition_id=transition_id,
    )


@dataclass(frozen=True, slots=True)
class CoverageInitializationId(_CanonicalIdentifier):
    """Canonical ``coverage-initialization-v1`` identity text."""

    VERSION_TAG: ClassVar = "coverage-initialization-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 11, identifier_name=type(self).__name__)
        run = CollectorRunId(_component_text(components[1], field_name="collector_run_id"))
        scope = CoverageScopeId(_component_text(components[2], field_name="coverage_scope_id"))
        epoch = CoverageEpochId(_component_text(components[3], field_name="coverage_epoch_id"))
        epoch_components = parse_canonical_json_array(epoch.value, field_name="coverage_epoch_id")
        if epoch_components[1] != scope.value or epoch_components[2] != run.value:
            raise ValueError("coverage initialization scope, epoch and collector run disagree.")
        try:
            status = CoverageStatus(_component_text(components[4], field_name="coverage_status"))
            reason = InitialCoverageReason(
                _component_text(components[5], field_name="initial_coverage_reason")
            )
        except ValueError:
            raise ValueError(
                "coverage initialization contains an unsupported enum value."
            ) from None
        activation = _canonical_utc_component(components[6], field_name="activation_time")
        activation_monotonic = _component_nonnegative_int(
            components[7], field_name="activation_monotonic_ns"
        )
        evidence = CoverageEvidenceId(
            _component_text(components[8], field_name="coverage_evidence_id")
        )
        evidence_components = parse_canonical_json_array(
            evidence.value, field_name="coverage_evidence_id"
        )
        scope_components = parse_canonical_json_array(scope.value, field_name="coverage_scope_id")
        try:
            domain = CoverageDomain(
                _component_text(scope_components[1], field_name="coverage_domain")
            )
            evidence_kind = CoverageEvidenceKind(
                _component_text(evidence_components[4], field_name="coverage_evidence_kind")
            )
        except ValueError:
            raise ValueError(
                "coverage initialization contains an unsupported coverage binding."
            ) from None
        if (domain, status, reason, evidence_kind) not in _INITIAL_COVERAGE_MATRIX:
            raise ValueError("coverage initialization violates the closed initial matrix.")
        observed = _canonical_utc_component(components[9], field_name="evidence_observed_at")
        observed_monotonic = _component_nonnegative_int(
            components[10], field_name="evidence_observed_monotonic_ns"
        )
        if (
            epoch_components[4] != activation
            or epoch_components[5] != activation_monotonic
            or evidence_components[1] != scope.value
            or evidence_components[2] != epoch.value
            or evidence_components[3] != run.value
            or evidence_components[6] != observed
            or evidence_components[7] != observed_monotonic
            or observed != activation
            or observed_monotonic != activation_monotonic
        ):
            raise ValueError("coverage initialization does not bind one exact activation boundary.")
        source = _component_tuple(evidence_components[5], field_name="coverage_evidence_source")
        if evidence_kind is CoverageEvidenceKind.TRANSPORT_FAILURE:
            _require_component_count(source, 5, identifier_name="transport ambiguity source")
            if source[3] is None:
                raise ValueError("transport failure before any send cannot initialize coverage.")
        if evidence_kind is CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE:
            _require_component_count(source, 2, identifier_name="upstream state source")
            committed = CommittedCoverageStateId(
                _component_text(source[1], field_name="committed_coverage_state_id")
            )
            upstream_details = _committed_coverage_state_id_details(committed)
            if (
                status is not upstream_details[3]
                or observed != upstream_details[5]
                or observed_monotonic != upstream_details[6]
            ):
                raise ValueError(
                    "initial downstream coverage must copy exact upstream status and boundary."
                )


@dataclass(frozen=True, slots=True)
class CoverageStateReferenceId(_CanonicalIdentifier):
    """Canonical candidate-state token retaining its complete predecessor chain."""

    VERSION_TAG: ClassVar = "coverage-state-reference-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        if len(components) not in {3, 5}:
            raise ValueError("CoverageStateReferenceId has an invalid component count.")
        tag = _component_text(components[1], field_name="coverage_state_reference_kind")
        initialization = CoverageInitializationId(
            _component_text(components[2], field_name="coverage_initialization_id")
        )
        if tag == "initialization":
            if len(components) != 3:
                raise ValueError("initial coverage state reference has invalid components.")
            return
        if tag != "transition" or len(components) != 5:
            raise ValueError("coverage state reference has an unsupported kind.")
        predecessor = CoverageStateReferenceId(
            _component_text(components[3], field_name="previous_state_reference_id")
        )
        transition = CoverageTransitionId(
            _component_text(components[4], field_name="coverage_transition_id")
        )
        initialization_components = parse_canonical_json_array(
            initialization.value, field_name="coverage_initialization_id"
        )
        transition_components = parse_canonical_json_array(
            transition.value, field_name="coverage_transition_id"
        )
        predecessor_details = _coverage_state_reference_details(predecessor)
        if (
            predecessor_details[0] != initialization
            or predecessor_details[1]
            != CoverageScopeId(
                _component_text(initialization_components[2], field_name="coverage_scope_id")
            )
            or transition_components[1] != initialization_components[2]
            or transition_components[2] != initialization_components[3]
            or transition_components[3] != predecessor_details[4] + 1
            or transition_components[4] != predecessor_details[3].value
        ):
            raise ValueError("coverage state transition must retain its exact predecessor history.")
        evidence_components = parse_canonical_json_array(
            _component_text(transition_components[7], field_name="coverage_evidence_id"),
            field_name="coverage_evidence_id",
        )
        transition_monotonic = _component_nonnegative_int(
            evidence_components[7],
            field_name="transition_observed_monotonic_ns",
        )
        if transition_monotonic < predecessor_details[6]:
            raise ValueError("coverage state history cannot move backwards in monotonic time.")


def _coverage_state_reference_details(
    state_reference_id: CoverageStateReferenceId,
) -> tuple[
    CoverageInitializationId,
    CoverageScopeId,
    CollectorRunId,
    CoverageStatus,
    int,
    str,
    int,
]:
    """Return validated initialization, scope, run, status, ordinal and boundary."""

    if type(state_reference_id) is not CoverageStateReferenceId:
        raise TypeError("state_reference_id must be a CoverageStateReferenceId.")
    state_components = parse_canonical_json_array(
        state_reference_id.value,
        field_name="coverage_state_reference_id",
    )
    initialization = CoverageInitializationId(
        _component_text(state_components[2], field_name="coverage_initialization_id")
    )
    initialization_components = parse_canonical_json_array(
        initialization.value,
        field_name="coverage_initialization_id",
    )
    scope = CoverageScopeId(
        _component_text(initialization_components[2], field_name="coverage_scope_id")
    )
    run = CollectorRunId(
        _component_text(initialization_components[1], field_name="collector_run_id")
    )
    if state_components[1] == "initialization":
        return (
            initialization,
            scope,
            run,
            CoverageStatus(
                _component_text(initialization_components[4], field_name="coverage_status")
            ),
            0,
            _canonical_utc_component(
                initialization_components[9], field_name="evidence_observed_at"
            ),
            _component_nonnegative_int(
                initialization_components[10],
                field_name="evidence_observed_monotonic_ns",
            ),
        )
    transition = CoverageTransitionId(
        _component_text(state_components[4], field_name="coverage_transition_id")
    )
    transition_components = parse_canonical_json_array(
        transition.value,
        field_name="coverage_transition_id",
    )
    evidence_components = parse_canonical_json_array(
        _component_text(transition_components[7], field_name="coverage_evidence_id"),
        field_name="coverage_evidence_id",
    )
    return (
        initialization,
        scope,
        run,
        CoverageStatus(_component_text(transition_components[5], field_name="coverage_status")),
        _component_nonnegative_int(transition_components[3], field_name="transition_ordinal"),
        _canonical_utc_component(evidence_components[6], field_name="evidence_observed_at"),
        _component_nonnegative_int(
            evidence_components[7], field_name="evidence_observed_monotonic_ns"
        ),
    )


def _upstream_coverage_state_details(
    state_reference_id: CoverageStateReferenceId,
) -> tuple[CoverageScopeId, CollectorRunId, CoverageStatus]:
    """Return the exact upstream scope, run and status represented by a state token."""

    details = _coverage_state_reference_details(state_reference_id)
    return details[1], details[2], details[3]


def _committed_coverage_state_id_details(
    committed_state_id: "CommittedCoverageStateId",
) -> tuple[
    CoverageStateReferenceId,
    CoverageScopeId,
    CollectorRunId,
    CoverageStatus,
    int,
    str,
    int,
]:
    """Return the state facts cryptographically bound by a committed-state ID."""

    if type(committed_state_id) is not CommittedCoverageStateId:
        raise TypeError("committed_state_id must be a CommittedCoverageStateId.")
    components = parse_canonical_json_array(
        committed_state_id.value,
        field_name="committed_coverage_state_id",
    )
    state_id = CoverageStateReferenceId(
        _component_text(components[1], field_name="coverage_state_reference_id")
    )
    CoverageCommitAcceptanceId(
        _component_text(components[2], field_name="coverage_commit_acceptance_id")
    )
    state_details = _coverage_state_reference_details(state_id)
    return (
        state_id,
        state_details[1],
        state_details[2],
        state_details[3],
        state_details[4],
        state_details[5],
        state_details[6],
    )


@dataclass(frozen=True, slots=True)
class CoverageTargetCatalogId(_CanonicalIdentifier):
    """Bounded content-addressed identity for one plan-derived leaf-scope catalogue."""

    VERSION_TAG: ClassVar = "coverage-target-catalog-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        SubscriptionPlanId(_component_text(components[1], field_name="subscription_plan_id"))
        count = _component_nonnegative_int(components[2], field_name="target_count")
        if count == 0 or count > MAX_COVERAGE_MUTATION_TARGETS:
            raise ValueError("coverage target count is outside its finite bound.")
        require_sha256(
            _component_text(components[3], field_name="coverage_target_catalog_sha256"),
            field_name="coverage_target_catalog_sha256",
        )


@dataclass(frozen=True, slots=True)
class CoverageFanoutProofId(_CanonicalIdentifier):
    """Bounded content-addressed identity for one cause-specific target proof.

    Existing fan-out kinds retain ``coverage-fanout-proof-v1`` byte-for-byte.
    Exact identified-rejection fan-outs use ``coverage-fanout-proof-v2`` so the
    capture-time attempt status and route discriminator are version-bound.
    """

    VERSION_TAG: ClassVar = "coverage-fanout-proof-v1"

    def __post_init__(self) -> None:
        components = parse_canonical_json_array(
            self.value,
            field_name="canonical identifier",
            maximum_length=MAX_CANONICAL_IDENTIFIER_LENGTH,
        )
        if not components or components[0] not in {
            self.VERSION_TAG,
            _IDENTIFIED_REJECTION_FANOUT_ID_VERSION,
        }:
            raise ValueError("canonical identifier has an unexpected version tag.")
        invalid_components = False
        try:
            self._validate_components(components)
        except (TypeError, ValueError):
            invalid_components = True
        if invalid_components:
            raise ValueError("canonical identifier contains invalid canonical components.")

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 6, identifier_name=type(self).__name__)
        try:
            kind = CoverageFanoutKind(
                _component_text(components[1], field_name="coverage_fanout_kind")
            )
        except ValueError:
            raise ValueError("coverage fanout proof has an unsupported kind.") from None
        identified_rejection_kinds = {
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
        }
        version = _component_text(components[0], field_name="coverage_fanout_version")
        if (version == _IDENTIFIED_REJECTION_FANOUT_ID_VERSION) is (
            kind not in identified_rejection_kinds
        ):
            raise ValueError("coverage fanout kind requires its exact identity version.")
        plan = SubscriptionPlanId(_component_text(components[2], field_name="subscription_plan_id"))
        catalog = CoverageTargetCatalogId(
            _component_text(components[3], field_name="coverage_target_catalog_id")
        )
        catalog_components = parse_canonical_json_array(
            catalog.value, field_name="coverage_target_catalog_id"
        )
        if catalog_components[1] != plan.value:
            raise ValueError("coverage fanout plan and target catalogue disagree.")
        count = _component_nonnegative_int(components[4], field_name="target_count")
        if count > MAX_COVERAGE_MUTATION_TARGETS:
            raise ValueError("coverage fanout target count is outside its finite bound.")
        if (kind is CoverageFanoutKind.HANDSHAKE_BEFORE_SEND) is (count != 0):
            raise ValueError("only handshake-before-send fanout may have zero targets.")
        if kind is CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION and count != 1:
            raise ValueError("singular identified-rejection fanout must have one target.")
        if kind is CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS and count < 2:
            raise ValueError("plural identified-rejection fanout must have multiple targets.")
        require_sha256(
            _component_text(components[5], field_name="coverage_fanout_content_sha256"),
            field_name="coverage_fanout_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class RawCoverageFanoutBindingId(_CanonicalIdentifier):
    """Canonical binding of one fanout proof to one exact raw snapshot."""

    VERSION_TAG: ClassVar = _RAW_COVERAGE_FANOUT_BINDING_ID_VERSION

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 5, identifier_name=type(self).__name__)
        RawRecordId(_component_text(components[1], field_name="raw_record_id"))
        CoverageFanoutProofId(_component_text(components[2], field_name="coverage_fanout_proof_id"))
        require_sha256(
            _component_text(
                components[3],
                field_name="subscription_snapshot_content_sha256",
            ),
            field_name="subscription_snapshot_content_sha256",
        )
        require_sha256(
            _component_text(
                components[4],
                field_name="raw_coverage_fanout_binding_content_sha256",
            ),
            field_name="raw_coverage_fanout_binding_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class CoverageMutationBatchId(_CanonicalIdentifier):
    """Bounded content-addressed identity for one prepared all-target CAS batch."""

    VERSION_TAG: ClassVar = "coverage-mutation-batch-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        fanout = CoverageFanoutProofId(
            _component_text(components[1], field_name="coverage_fanout_proof_id")
        )
        count = _component_nonnegative_int(components[2], field_name="target_count")
        if count > MAX_COVERAGE_MUTATION_TARGETS:
            raise ValueError("coverage mutation target count is outside its finite bound.")
        fanout_components = parse_canonical_json_array(
            fanout.value, field_name="coverage_fanout_proof_id"
        )
        if fanout_components[4] != count:
            raise ValueError("coverage mutation and fanout target counts disagree.")
        require_sha256(
            _component_text(components[3], field_name="coverage_mutation_content_sha256"),
            field_name="coverage_mutation_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class CoverageCommitAcceptanceId(_CanonicalIdentifier):
    """Bounded content-addressed proof identity created only after a successful CAS.

    Exact preimage::

        ["coverage-commit-acceptance-v1", coverage_mutation_batch_id,
         resulting_state_count, SHA256(canonical_acceptance_content)]
    """

    VERSION_TAG: ClassVar = "coverage-commit-acceptance-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 4, identifier_name=type(self).__name__)
        batch = CoverageMutationBatchId(
            _component_text(components[1], field_name="coverage_mutation_batch_id")
        )
        count = _component_nonnegative_int(components[2], field_name="resulting_state_count")
        if count > MAX_COVERAGE_MUTATION_TARGETS:
            raise ValueError("coverage acceptance result count is outside its finite bound.")
        batch_components = parse_canonical_json_array(
            batch.value, field_name="coverage_mutation_batch_id"
        )
        if batch_components[2] != count:
            raise ValueError("coverage acceptance count must match its mutation batch.")
        require_sha256(
            _component_text(components[3], field_name="coverage_acceptance_content_sha256"),
            field_name="coverage_acceptance_content_sha256",
        )


@dataclass(frozen=True, slots=True)
class CommittedCoverageStateId(_CanonicalIdentifier):
    """Canonical binding of one candidate state token to its CAS acceptance."""

    VERSION_TAG: ClassVar = "committed-coverage-state-v1"

    def _validate_components(self, components: tuple[CanonicalValue, ...]) -> None:
        _require_component_count(components, 3, identifier_name=type(self).__name__)
        CoverageStateReferenceId(
            _component_text(components[1], field_name="coverage_state_reference_id")
        )
        CoverageCommitAcceptanceId(
            _component_text(components[2], field_name="coverage_commit_acceptance_id")
        )


@dataclass(frozen=True, slots=True)
class CoverageInitialization:
    """Immutable ordinal-zero state decision with an independently verifiable ID.

    ID preimage, in exact order::

        ["coverage-initialization-v1", collector_run_id, coverage_scope_id,
         coverage_epoch_id, initial_status, initial_reason, activation_utc,
         activation_monotonic_ns, evidence_id, evidence_utc,
         evidence_monotonic_ns]
    """

    scope: CoverageScope
    epoch: CoverageEpochIdentity
    status: CoverageStatus
    initial_reason: InitialCoverageReason
    initial_evidence: CoverageEvidence
    reference: CoverageReference = field(init=False, repr=False)
    coverage_initialization_id: CoverageInitializationId = field(init=False)

    def __post_init__(self) -> None:
        reference = CoverageReference.initial(
            scope=self.scope,
            epoch=self.epoch,
            status=self.status,
            initial_reason=self.initial_reason,
            initial_evidence=self.initial_evidence,
        )
        initialization_id = CoverageInitializationId(
            canonical_json_array(
                (
                    "coverage-initialization-v1",
                    self.epoch.collector_run_id,
                    self.scope.coverage_scope_id,
                    self.epoch.coverage_epoch_id,
                    self.status.value,
                    self.initial_reason.value,
                    canonical_utc_datetime(
                        self.epoch.activation_time,
                        field_name="activation_time",
                    ),
                    self.epoch.activation_monotonic_ns,
                    self.initial_evidence.coverage_evidence_id,
                    canonical_utc_datetime(
                        self.initial_evidence.observed_at,
                        field_name="evidence_observed_at",
                    ),
                    self.initial_evidence.observed_monotonic_ns,
                )
            )
        )
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "coverage_initialization_id", initialization_id)

    @classmethod
    def from_stored(
        cls,
        *,
        scope: CoverageScope,
        epoch: CoverageEpochIdentity,
        status: CoverageStatus,
        initial_reason: InitialCoverageReason,
        initial_evidence: CoverageEvidence,
        expected_initialization_id: CoverageInitializationId,
    ) -> Self:
        """Recompute a stored initialization and reject an ID mismatch."""

        if type(expected_initialization_id) is not CoverageInitializationId:
            raise TypeError("expected_initialization_id must be a CoverageInitializationId.")
        value = cls(scope, epoch, status, initial_reason, initial_evidence)
        if value.coverage_initialization_id != expected_initialization_id:
            raise ValueError("stored coverage initialization ID does not match its content.")
        return value


@dataclass(frozen=True, slots=True, init=False)
class CoverageStateReference:
    """CAS token created only from initialization or a reducer-produced transition.

    Initial ID preimage::

        ["coverage-state-reference-v1", "initialization",
         coverage_initialization_id]

    Transitioned ID preimage::

        ["coverage-state-reference-v1", "transition",
         coverage_initialization_id, previous_coverage_state_reference_id,
         latest_coverage_transition_id]

    A transitioned reference is a prepared candidate CAS token.  Only a
    matching :class:`CoverageCommitAcceptance` establishes runtime commit.
    """

    initialization: CoverageInitialization = field(repr=False)
    reference: CoverageReference = field(repr=False)
    previous_state_reference_id: CoverageStateReferenceId | None = field(repr=False)
    latest_transition: CoverageTransition | None = field(repr=False)
    coverage_state_reference_id: CoverageStateReferenceId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use CoverageStateReference.from_initialization() or .from_transition().")

    @classmethod
    def from_initialization(
        cls, initialization: CoverageInitialization
    ) -> "CoverageStateReference":
        if cls is not CoverageStateReference:
            raise TypeError("coverage state references do not support subclass construction.")
        if type(initialization) is not CoverageInitialization:
            raise TypeError("initialization must be a CoverageInitialization.")
        return _construct_coverage_state_reference(
            initialization,
            initialization.reference,
            None,
            None,
        )

    @classmethod
    def from_transition(
        cls,
        *,
        previous: Self,
        transition: CoverageTransition,
        resulting_reference: CoverageReference,
    ) -> "CoverageStateReference":
        if cls is not CoverageStateReference:
            raise TypeError("coverage state references do not support subclass construction.")
        if type(previous) is not CoverageStateReference:
            raise TypeError("previous must be a CoverageStateReference.")
        if type(transition) is not CoverageTransition:
            raise TypeError("transition must be a CoverageTransition.")
        if type(resulting_reference) is not CoverageReference:
            raise TypeError("resulting_reference must be a CoverageReference.")
        if (
            transition.scope != previous.reference.scope
            or transition.epoch != previous.reference.epoch
        ):
            raise ValueError("coverage transition must belong to the previous state.")
        if transition.previous_status is not previous.reference.status:
            raise ValueError("coverage transition must start at the previous state status.")
        if transition.transition_ordinal != previous.reference.transition_ordinal + 1:
            raise ValueError("coverage transition must be exactly the next state ordinal.")
        if (
            resulting_reference.scope != transition.scope
            or resulting_reference.epoch != transition.epoch
            or resulting_reference.transition_ordinal != transition.transition_ordinal
            or resulting_reference.status is not transition.new_status
            or resulting_reference.transition_id != transition.coverage_transition_id
        ):
            raise ValueError("resulting coverage reference must match the prepared transition.")
        return _construct_coverage_state_reference(
            previous.initialization,
            resulting_reference,
            previous.coverage_state_reference_id,
            transition,
        )

    @classmethod
    def from_stored(
        cls,
        *,
        initialization: CoverageInitialization,
        reference: CoverageReference,
        latest_transition: CoverageTransition | None,
        previous_state: Self | None = None,
        expected_state_reference_id: CoverageStateReferenceId,
    ) -> "CoverageStateReference":
        """Recompute a stored tagged reference and fail closed on mismatch."""

        if type(expected_state_reference_id) is not CoverageStateReferenceId:
            raise TypeError("expected_state_reference_id must be a CoverageStateReferenceId.")
        if latest_transition is None:
            if previous_state is not None:
                raise ValueError("stored initial state reference cannot have a previous state.")
            value = cls.from_initialization(initialization)
            if reference != initialization.reference:
                raise ValueError("stored initial state reference does not match initialization.")
        else:
            if type(previous_state) is not CoverageStateReference:
                raise TypeError(
                    "previous_state must be a CoverageStateReference for stored transitions."
                )
            if previous_state.initialization != initialization:
                raise ValueError("stored previous state must retain the same initialization.")
            value = cls.from_transition(
                previous=previous_state,
                transition=latest_transition,
                resulting_reference=reference,
            )
        if value.coverage_state_reference_id != expected_state_reference_id:
            raise ValueError("stored coverage state reference ID does not match its content.")
        return value


def _construct_coverage_state_reference(
    initialization: CoverageInitialization,
    reference: CoverageReference,
    previous_state_reference_id: CoverageStateReferenceId | None,
    transition: CoverageTransition | None,
) -> CoverageStateReference:
    if reference.scope != initialization.scope or reference.epoch != initialization.epoch:
        raise ValueError("coverage state reference must retain its initialization scope and epoch.")
    if transition is None:
        if previous_state_reference_id is not None:
            raise ValueError("initial state cannot retain a predecessor state.")
        state_id = CoverageStateReferenceId(
            canonical_json_array(
                (
                    "coverage-state-reference-v1",
                    "initialization",
                    initialization.coverage_initialization_id,
                )
            )
        )
    else:
        if type(previous_state_reference_id) is not CoverageStateReferenceId:
            raise TypeError("transitioned state requires a previous state reference ID.")
        state_id = CoverageStateReferenceId(
            canonical_json_array(
                (
                    "coverage-state-reference-v1",
                    "transition",
                    initialization.coverage_initialization_id,
                    previous_state_reference_id,
                    transition.coverage_transition_id,
                )
            )
        )
    value = object.__new__(CoverageStateReference)
    object.__setattr__(value, "initialization", initialization)
    object.__setattr__(value, "reference", reference)
    object.__setattr__(value, "previous_state_reference_id", previous_state_reference_id)
    object.__setattr__(value, "latest_transition", transition)
    object.__setattr__(value, "coverage_state_reference_id", state_id)
    return value


@dataclass(frozen=True, slots=True, init=False)
class CoverageTargetCatalog:
    """Complete immutable leaf-scope catalogue derived from one subscription plan.

    Content preimage::

        ["coverage-target-catalog-content-v1", subscription_plan_id,
         sorted_coverage_scope_ids]

    Bounded ID preimage::

        ["coverage-target-catalog-v1", subscription_plan_id, target_count,
         SHA256(canonical_content)]
    """

    subscription_plan: SubscriptionPlanIdentity = field(repr=False)
    scopes: tuple[CoverageScope, ...] = field(repr=False)
    canonical_content: str = field(repr=False)
    content_sha256: str
    coverage_target_catalog_id: CoverageTargetCatalogId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use CoverageTargetCatalog.from_subscription_plan().")

    @classmethod
    def from_subscription_plan(cls, plan: SubscriptionPlanIdentity) -> Self:
        if cls is not CoverageTargetCatalog:
            raise TypeError("coverage target catalogues do not support subclass construction.")
        if type(plan) is not SubscriptionPlanIdentity:
            raise TypeError("plan must be a SubscriptionPlanIdentity.")
        scopes: list[CoverageScope] = []
        for normalization in plan.normalization_bindings:
            for instrument_binding in plan.instrument_bindings:
                if instrument_binding.subscription_spec_id != normalization.subscription_spec_id:
                    continue
                for domain in (
                    CoverageDomain.BRONZE_INGRESS,
                    CoverageDomain.SILVER_NORMALIZATION,
                ):
                    scopes.append(
                        CoverageScope(
                            domain=domain,
                            feed_product_id=plan.feed_product_id,
                            subscription_spec_ids=(normalization.subscription_spec_id,),
                            canonical_instrument_ids=(instrument_binding.canonical_instrument_id,),
                            event_family=normalization.event_family,
                            event_family_schema_version=normalization.event_family_schema_version,
                            payload_type=normalization.payload_type,
                        )
                    )
                    if len(scopes) > MAX_COVERAGE_MUTATION_TARGETS:
                        raise ValueError("plan-derived coverage targets exceed their finite bound.")
        ordered = tuple(sorted(scopes, key=lambda item: item.coverage_scope_id.value))
        if not ordered or len({item.coverage_scope_id for item in ordered}) != len(ordered):
            raise ValueError("plan-derived coverage targets must be non-empty and unique.")
        content = canonical_json_array(
            (
                "coverage-target-catalog-content-v1",
                plan.subscription_plan_id,
                tuple(scope.coverage_scope_id.value for scope in ordered),
            ),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )
        digest = sha256_hex(content.encode("utf-8"), field_name="coverage target catalog")
        catalog_id = CoverageTargetCatalogId(
            canonical_json_array(
                ("coverage-target-catalog-v1", plan.subscription_plan_id, len(ordered), digest)
            )
        )
        value = object.__new__(cls)
        object.__setattr__(value, "subscription_plan", plan)
        object.__setattr__(value, "scopes", ordered)
        object.__setattr__(value, "canonical_content", content)
        object.__setattr__(value, "content_sha256", digest)
        object.__setattr__(value, "coverage_target_catalog_id", catalog_id)
        return value

    @classmethod
    def from_stored(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        expected_canonical_content: str,
        expected_catalog_id: CoverageTargetCatalogId,
    ) -> "CoverageTargetCatalog":
        value = cls.from_subscription_plan(plan)
        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        if type(expected_catalog_id) is not CoverageTargetCatalogId:
            raise TypeError("expected_catalog_id must be a CoverageTargetCatalogId.")
        if (
            value.canonical_content != expected_canonical_content
            or value.coverage_target_catalog_id != expected_catalog_id
        ):
            raise ValueError("stored coverage target catalogue does not match its plan.")
        return value


class CoverageFanoutKind(StrEnum):
    """Closed route proofs for one coverage cause."""

    HANDSHAKE_BEFORE_SEND = "handshake-before-send"
    ONE_POSSIBLY_DELIVERED_SPEC = "one-possibly-delivered-spec"
    POSSIBLY_DELIVERED_SPECS = "possibly-delivered-specs"
    ACKNOWLEDGED_ACTIVE = "acknowledged-active"
    EXACT_ROUTED_EVENT = "exact-routed-event"
    EXACT_ROUTED_EVENTS = "exact-routed-events"
    EXACT_IDENTIFIED_REJECTION = "exact-identified-rejection"
    EXACT_IDENTIFIED_REJECTIONS = "exact-identified-rejections"
    ALL_POSSIBLY_ACTIVE = "all-possibly-active"


def _validate_fanout_parent(
    plan: SubscriptionPlanIdentity,
    catalog: CoverageTargetCatalog,
) -> None:
    if type(plan) is not SubscriptionPlanIdentity:
        raise TypeError("plan must be a SubscriptionPlanIdentity.")
    if type(catalog) is not CoverageTargetCatalog:
        raise TypeError("catalog must be a CoverageTargetCatalog.")
    if catalog.subscription_plan.subscription_plan_id != plan.subscription_plan_id:
        raise ValueError("coverage target catalogue must belong to the supplied plan.")


def _validate_complete_attempt_snapshot_set(
    plan: SubscriptionPlanIdentity,
    snapshots: tuple[SubscriptionAttemptSnapshot, ...],
) -> ConnectionSessionIdentity:
    if type(snapshots) is not tuple:
        raise TypeError("snapshots must be a built-in tuple.")
    require_collection_size(
        snapshots,
        field_name="snapshots",
        maximum_items=MAX_SUBSCRIPTION_SPECS,
        minimum_items=1,
    )
    if any(type(item) is not SubscriptionAttemptSnapshot for item in snapshots):
        raise TypeError("snapshots contain an invalid value.")
    sessions = {item.subscription_attempt.connection_session for item in snapshots}
    if len(sessions) != 1:
        raise ValueError("coverage fanout snapshots must belong to one connection session.")
    session = next(iter(sessions))
    expected_specs = tuple(spec.subscription_spec_id for spec in plan.subscription_specs)
    actual_specs = tuple(item.subscription_spec.subscription_spec_id for item in snapshots)
    if actual_specs != expected_specs:
        raise ValueError("coverage fanout requires the complete sorted plan attempt set.")
    for item in snapshots:
        if item.subscription_attempt.connection_session != session:
            raise ValueError("coverage fanout snapshots must belong to one connection session.")
        if item.subscription_spec.feed_product_id != plan.feed_product_id:
            raise ValueError("coverage fanout snapshots must belong to the plan feed.")
    return session


@dataclass(frozen=True, slots=True)
class RoutedCoverageTarget:
    """One exact acknowledged wire route and its normalized event binding."""

    acknowledged_snapshot: SubscriptionAttemptSnapshot = field(repr=False)
    canonical_instrument_id: str = field(repr=False)
    event_family: str
    event_family_schema_version: int
    payload_type: str

    def __post_init__(self) -> None:
        if type(self.acknowledged_snapshot) is not SubscriptionAttemptSnapshot:
            raise TypeError("acknowledged_snapshot must be a SubscriptionAttemptSnapshot.")
        if self.acknowledged_snapshot.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED:
            raise ValueError("routed market data requires an acknowledged subscription attempt.")
        object.__setattr__(
            self,
            "canonical_instrument_id",
            validate_canonical_instrument_id(self.canonical_instrument_id),
        )
        object.__setattr__(
            self,
            "event_family",
            require_code(self.event_family, field_name="event_family"),
        )
        family_version = require_nonnegative_int(
            self.event_family_schema_version,
            field_name="event_family_schema_version",
        )
        if family_version == 0:
            raise ValueError("event_family_schema_version must be positive.")
        object.__setattr__(
            self,
            "payload_type",
            require_code(self.payload_type, field_name="payload_type"),
        )


_IDENTIFIED_REJECTION_ATTEMPT_STATUSES: Final = frozenset(
    {
        SubscriptionAttemptStatus.PENDING,
        SubscriptionAttemptStatus.SEND_STARTED,
        SubscriptionAttemptStatus.SENT,
    }
)


@dataclass(frozen=True, slots=True)
class ExactIdentifiedRejectionTarget:
    """One exact non-ACK public route used only for an indexed rejection.

    The snapshot is the immutable state captured in Bronze. This target never
    asserts that the subscription was active; it proves only the exact public
    route of a reliably indexed item that was intentionally not materialized.
    Plan, selector, adapter, instrument and family consistency are verified by
    the cause-specific :class:`CoverageFanoutProof` factory.
    """

    attempt_snapshot: SubscriptionAttemptSnapshot = field(repr=False)
    source_selector: PublicSourceSelector = field(repr=False)
    canonical_instrument_id: str = field(repr=False)
    adapter_profile: str
    event_family: str
    event_family_schema_version: int
    payload_type: str

    def __post_init__(self) -> None:
        if type(self.attempt_snapshot) is not SubscriptionAttemptSnapshot:
            raise TypeError("attempt_snapshot must be a SubscriptionAttemptSnapshot.")
        if self.attempt_snapshot.attempt_status not in _IDENTIFIED_REJECTION_ATTEMPT_STATUSES:
            raise ValueError("identified rejection requires a non-acknowledged attempt snapshot.")
        if type(self.source_selector) is not PublicSourceSelector:
            raise TypeError("source_selector must be a PublicSourceSelector.")
        object.__setattr__(
            self,
            "canonical_instrument_id",
            validate_canonical_instrument_id(self.canonical_instrument_id),
        )
        object.__setattr__(
            self,
            "adapter_profile",
            require_code(self.adapter_profile, field_name="adapter_profile"),
        )
        object.__setattr__(
            self,
            "event_family",
            require_code(self.event_family, field_name="event_family"),
        )
        version = require_nonnegative_int(
            self.event_family_schema_version,
            field_name="event_family_schema_version",
        )
        if version == 0:
            raise ValueError("event_family_schema_version must be positive.")
        object.__setattr__(
            self,
            "payload_type",
            require_code(self.payload_type, field_name="payload_type"),
        )


@dataclass(frozen=True, slots=True, init=False)
class CoverageFanoutProof:
    """Factory-derived proof of the complete cause-specific leaf-scope slice.

    Existing content and ID preimages remain::

        ["coverage-fanout-proof-content-v1", fanout_kind,
         subscription_plan_id, target_catalog_id, connection_session_id,
         kind_specific_source_row, sorted_target_scope_ids]

    Bounded ID preimage::

        ["coverage-fanout-proof-v1", fanout_kind, subscription_plan_id,
         target_catalog_id, target_count, SHA256(canonical_content)]

    Identified-rejection proofs use the same ordered components with the
    ``coverage-fanout-proof-content-v2`` and ``coverage-fanout-proof-v2`` tags.
    Their kind-specific source row is documented by the corresponding factory.
    """

    kind: CoverageFanoutKind
    subscription_plan_id: SubscriptionPlanId
    coverage_target_catalog_id: CoverageTargetCatalogId
    connection_session_id: ConnectionSessionId
    source_canonical_row: tuple[object, ...] = field(repr=False)
    source_attempt_snapshots: tuple[SubscriptionAttemptSnapshot, ...] = field(repr=False)
    selected_attempt_ids: tuple[SubscriptionAttemptId, ...] = field(repr=False)
    target_scopes: tuple[CoverageScope, ...] = field(repr=False)
    identified_rejection_targets: tuple[ExactIdentifiedRejectionTarget, ...] = field(repr=False)
    identified_rejection_scope_ids: tuple[CoverageScopeId, ...] = field(repr=False)
    acknowledged_routed_targets: tuple[RoutedCoverageTarget, ...] = field(repr=False)
    acknowledged_routed_scope_ids: tuple[CoverageScopeId, ...] = field(repr=False)
    canonical_content: str = field(repr=False)
    content_sha256: str
    coverage_fanout_proof_id: CoverageFanoutProofId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use a CoverageFanoutProof cause-specific factory.")

    @classmethod
    def handshake_before_send(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        connection_session: ConnectionSessionIdentity,
    ) -> "CoverageFanoutProof":
        _validate_fanout_parent(plan, catalog)
        if type(connection_session) is not ConnectionSessionIdentity:
            raise TypeError("connection_session must be a ConnectionSessionIdentity.")
        return _construct_coverage_fanout(
            kind=CoverageFanoutKind.HANDSHAKE_BEFORE_SEND,
            plan=plan,
            catalog=catalog,
            connection_session=connection_session,
            source_row=("handshake-before-send-v1", connection_session.connection_session_id),
            source_attempt_snapshots=(),
            selected_attempt_ids=(),
            target_scopes=(),
        )

    @classmethod
    def one_possibly_delivered_spec(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        snapshot: SubscriptionAttemptSnapshot,
    ) -> "CoverageFanoutProof":
        _validate_fanout_parent(plan, catalog)
        if type(snapshot) is not SubscriptionAttemptSnapshot:
            raise TypeError("snapshot must be a SubscriptionAttemptSnapshot.")
        if len(plan.subscription_specs) != 1:
            raise ValueError("single-spec ambiguity requires a complete one-spec plan snapshot.")
        proof = cls.possibly_delivered_specs(
            plan=plan,
            catalog=catalog,
            complete_snapshots=(snapshot,),
            selected_attempt_ids=(snapshot.subscription_attempt.subscription_attempt_id,),
        )
        return _construct_coverage_fanout(
            kind=CoverageFanoutKind.ONE_POSSIBLY_DELIVERED_SPEC,
            plan=plan,
            catalog=catalog,
            connection_session=snapshot.subscription_attempt.connection_session,
            source_row=("one-possibly-delivered-spec-v1", proof.source_canonical_row),
            source_attempt_snapshots=(snapshot,),
            selected_attempt_ids=(snapshot.subscription_attempt.subscription_attempt_id,),
            target_scopes=proof.target_scopes,
        )

    @classmethod
    def possibly_delivered_specs(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        complete_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
        selected_attempt_ids: tuple[SubscriptionAttemptId, ...],
    ) -> "CoverageFanoutProof":
        """Prove the exact SEND_STARTED/SENT subset from a complete plan snapshot."""

        _validate_fanout_parent(plan, catalog)
        session = _validate_complete_attempt_snapshot_set(plan, complete_snapshots)
        if type(selected_attempt_ids) is not tuple or any(
            type(item) is not SubscriptionAttemptId for item in selected_attempt_ids
        ):
            raise TypeError("selected_attempt_ids must be a tuple of SubscriptionAttemptId.")
        require_collection_size(
            selected_attempt_ids,
            field_name="selected_attempt_ids",
            maximum_items=MAX_SUBSCRIPTION_SPECS,
            minimum_items=1,
        )
        expected_selected = tuple(sorted(set(selected_attempt_ids), key=lambda item: item.value))
        if selected_attempt_ids != expected_selected:
            raise ValueError("selected attempt IDs must be sorted and unique.")
        snapshots_by_attempt = {
            item.subscription_attempt.subscription_attempt_id: item for item in complete_snapshots
        }
        if any(item not in snapshots_by_attempt for item in selected_attempt_ids):
            raise ValueError("selected attempt must belong to the complete plan snapshot.")
        selected = tuple(snapshots_by_attempt[item] for item in selected_attempt_ids)
        if any(
            item.attempt_status
            not in {SubscriptionAttemptStatus.SEND_STARTED, SubscriptionAttemptStatus.SENT}
            for item in selected
        ):
            raise ValueError("possibly delivered subset permits only SEND_STARTED or SENT.")
        spec_ids = {
            item.subscription_attempt.subscription_spec.subscription_spec_id for item in selected
        }
        targets = tuple(
            scope
            for scope in catalog.scopes
            if scope.domain is CoverageDomain.BRONZE_INGRESS
            and scope.subscription_spec_ids[0] in spec_ids
        )
        snapshot_rows = tuple(
            (
                item.subscription_attempt.subscription_attempt_id.value,
                item.attempt_status.value,
            )
            for item in complete_snapshots
        )
        return _construct_coverage_fanout(
            kind=CoverageFanoutKind.POSSIBLY_DELIVERED_SPECS,
            plan=plan,
            catalog=catalog,
            connection_session=session,
            source_row=(
                "possibly-delivered-specs-v1",
                snapshot_rows,
                tuple(item.value for item in selected_attempt_ids),
            ),
            source_attempt_snapshots=complete_snapshots,
            selected_attempt_ids=selected_attempt_ids,
            target_scopes=targets,
        )

    @classmethod
    def acknowledged_active(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        complete_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
        selected_attempt_ids: tuple[SubscriptionAttemptId, ...],
        domain: CoverageDomain,
    ) -> "CoverageFanoutProof":
        """Select current ACKs intended to initialize leaves from a complete snapshot."""

        _validate_fanout_parent(plan, catalog)
        if type(domain) is not CoverageDomain or domain is CoverageDomain.SILVER_DELIVERY:
            raise ValueError("activation fanout requires an ingress or normalization domain.")
        session = _validate_complete_attempt_snapshot_set(plan, complete_snapshots)
        if type(selected_attempt_ids) is not tuple or any(
            type(item) is not SubscriptionAttemptId for item in selected_attempt_ids
        ):
            raise TypeError("selected_attempt_ids must be a tuple of SubscriptionAttemptId.")
        require_collection_size(
            selected_attempt_ids,
            field_name="selected_attempt_ids",
            maximum_items=MAX_SUBSCRIPTION_SPECS,
            minimum_items=1,
        )
        expected_selected = tuple(sorted(set(selected_attempt_ids), key=lambda item: item.value))
        if selected_attempt_ids != expected_selected:
            raise ValueError("selected attempt IDs must be sorted and unique.")
        snapshots_by_attempt = {
            item.subscription_attempt.subscription_attempt_id: item for item in complete_snapshots
        }
        if any(item not in snapshots_by_attempt for item in selected_attempt_ids):
            raise ValueError("selected attempt must belong to the complete plan snapshot.")
        selected = tuple(snapshots_by_attempt[item] for item in selected_attempt_ids)
        if any(
            item.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED for item in selected
        ):
            raise ValueError("activation selection permits only ACKNOWLEDGED attempts.")
        spec_ids = {item.subscription_spec.subscription_spec_id for item in selected}
        targets = tuple(
            scope
            for scope in catalog.scopes
            if scope.domain is domain and scope.subscription_spec_ids[0] in spec_ids
        )
        return _construct_coverage_fanout(
            kind=CoverageFanoutKind.ACKNOWLEDGED_ACTIVE,
            plan=plan,
            catalog=catalog,
            connection_session=session,
            source_row=(
                "acknowledged-active-v1",
                domain.value,
                tuple(
                    (
                        item.subscription_attempt.subscription_attempt_id.value,
                        item.attempt_status.value,
                    )
                    for item in complete_snapshots
                ),
                tuple(item.value for item in selected_attempt_ids),
            ),
            source_attempt_snapshots=complete_snapshots,
            selected_attempt_ids=selected_attempt_ids,
            target_scopes=targets,
        )

    @classmethod
    def exact_routed_event(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        acknowledged_snapshot: SubscriptionAttemptSnapshot,
        canonical_instrument_id: str,
        event_family: str,
        event_family_schema_version: int,
        payload_type: str,
    ) -> "CoverageFanoutProof":
        return cls.exact_routed_events(
            plan=plan,
            catalog=catalog,
            routed_targets=(
                RoutedCoverageTarget(
                    acknowledged_snapshot,
                    canonical_instrument_id,
                    event_family,
                    event_family_schema_version,
                    payload_type,
                ),
            ),
        )

    @classmethod
    def exact_routed_events(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        routed_targets: tuple[RoutedCoverageTarget, ...],
    ) -> "CoverageFanoutProof":
        """Prove the exact unique Silver leaf-scope union for indexed frame items."""

        _validate_fanout_parent(plan, catalog)
        if type(routed_targets) is not tuple or any(
            type(item) is not RoutedCoverageTarget for item in routed_targets
        ):
            raise TypeError("routed_targets must be a tuple of RoutedCoverageTarget values.")
        require_collection_size(
            routed_targets,
            field_name="routed_targets",
            maximum_items=MAX_COVERAGE_MUTATION_TARGETS,
            minimum_items=1,
        )
        resolved: list[tuple[CoverageScope, RoutedCoverageTarget]] = []
        for routed in routed_targets:
            spec_id = routed.acknowledged_snapshot.subscription_spec.subscription_spec_id
            matches = tuple(
                scope
                for scope in catalog.scopes
                if scope.domain is CoverageDomain.SILVER_NORMALIZATION
                and scope.subscription_spec_ids == (spec_id,)
                and scope.canonical_instrument_ids == (routed.canonical_instrument_id,)
                and scope.event_family == routed.event_family
                and scope.event_family_schema_version == routed.event_family_schema_version
                and scope.payload_type == routed.payload_type
            )
            if len(matches) != 1:
                raise ValueError(
                    "every routed event must identify exactly one plan-derived Silver scope."
                )
            resolved.append((matches[0], routed))
        ordered = tuple(sorted(resolved, key=lambda item: item[0].coverage_scope_id.value))
        scopes = tuple(item[0] for item in ordered)
        if len({scope.coverage_scope_id for scope in scopes}) != len(scopes):
            raise ValueError("routed event coverage targets must be unique by exact scope.")
        sessions = {
            item[1].acknowledged_snapshot.subscription_attempt.connection_session
            for item in ordered
        }
        if len(sessions) != 1:
            raise ValueError("routed frame targets must belong to one connection session.")
        source_rows = tuple(
            (
                routed.acknowledged_snapshot.subscription_attempt.subscription_attempt_id.value,
                routed.acknowledged_snapshot.subscription_spec.subscription_spec_id.value,
                routed.canonical_instrument_id,
                routed.event_family,
                routed.event_family_schema_version,
                routed.payload_type,
            )
            for _scope, routed in ordered
        )
        snapshots_by_attempt = {
            routed.acknowledged_snapshot.subscription_attempt.subscription_attempt_id: (
                routed.acknowledged_snapshot
            )
            for _scope, routed in ordered
        }
        source_snapshots = tuple(
            snapshots_by_attempt[item]
            for item in sorted(snapshots_by_attempt, key=lambda attempt_id: attempt_id.value)
        )
        selected_attempt_ids = tuple(
            item.subscription_attempt.subscription_attempt_id for item in source_snapshots
        )
        return _construct_coverage_fanout(
            kind=(
                CoverageFanoutKind.EXACT_ROUTED_EVENT
                if len(scopes) == 1
                else CoverageFanoutKind.EXACT_ROUTED_EVENTS
            ),
            plan=plan,
            catalog=catalog,
            connection_session=next(iter(sessions)),
            source_row=("exact-routed-events-v1", source_rows),
            source_attempt_snapshots=source_snapshots,
            selected_attempt_ids=selected_attempt_ids,
            target_scopes=scopes,
            acknowledged_routed_targets=tuple(item[1] for item in ordered),
            acknowledged_routed_scope_ids=tuple(item[0].coverage_scope_id for item in ordered),
        )

    @classmethod
    def exact_identified_rejection(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        attempt_snapshot: SubscriptionAttemptSnapshot,
        source_selector: PublicSourceSelector,
        canonical_instrument_id: str,
        adapter_profile: str,
        event_family: str,
        event_family_schema_version: int,
        payload_type: str,
    ) -> "CoverageFanoutProof":
        """Prove one exact indexed non-ACK rejection route."""

        return cls.exact_identified_rejections(
            plan=plan,
            catalog=catalog,
            rejection_targets=(
                ExactIdentifiedRejectionTarget(
                    attempt_snapshot=attempt_snapshot,
                    source_selector=source_selector,
                    canonical_instrument_id=canonical_instrument_id,
                    adapter_profile=adapter_profile,
                    event_family=event_family,
                    event_family_schema_version=event_family_schema_version,
                    payload_type=payload_type,
                ),
            ),
        )

    @classmethod
    def exact_identified_rejections(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        rejection_targets: tuple[ExactIdentifiedRejectionTarget, ...],
        acknowledged_targets: tuple[RoutedCoverageTarget, ...] = (),
    ) -> "CoverageFanoutProof":
        """Prove exact indexed rejected routes and an optional ACK route union.

        The non-empty rejection partition is used directly for pre-ACK
        normalization failure. The optional acknowledged partition exists so a
        later outcome-sink failure can prove the complete indexed frame union
        without weakening :class:`RoutedCoverageTarget`.

        Kind-specific source preimage::

            ["exact-identified-rejections-v1",
             sorted exact-identified-rejection-target-v1 rows,
             sorted acknowledged-routed-target-v1 rows]
        """

        _validate_fanout_parent(plan, catalog)
        if type(rejection_targets) is not tuple or any(
            type(item) is not ExactIdentifiedRejectionTarget for item in rejection_targets
        ):
            raise TypeError(
                "rejection_targets must be a tuple of ExactIdentifiedRejectionTarget values."
            )
        require_collection_size(
            rejection_targets,
            field_name="rejection_targets",
            maximum_items=MAX_COVERAGE_MUTATION_TARGETS,
            minimum_items=1,
        )
        if type(acknowledged_targets) is not tuple or any(
            type(item) is not RoutedCoverageTarget for item in acknowledged_targets
        ):
            raise TypeError("acknowledged_targets must be a tuple of RoutedCoverageTarget values.")
        require_collection_size(
            acknowledged_targets,
            field_name="acknowledged_targets",
            maximum_items=MAX_COVERAGE_MUTATION_TARGETS,
        )
        if len(rejection_targets) + len(acknowledged_targets) > MAX_COVERAGE_MUTATION_TARGETS:
            raise ValueError("identified rejection routes exceed their finite bound.")

        rejected_resolved = tuple(
            _resolve_identified_rejection_target(
                plan=plan,
                catalog=catalog,
                target=target,
            )
            for target in rejection_targets
        )
        acknowledged_resolved = tuple(
            _resolve_acknowledged_route_for_identified_rejection(
                plan=plan,
                catalog=catalog,
                target=target,
            )
            for target in acknowledged_targets
        )
        ordered_rejected = tuple(
            sorted(rejected_resolved, key=lambda item: item[0].coverage_scope_id.value)
        )
        ordered_acknowledged = tuple(
            sorted(acknowledged_resolved, key=lambda item: item[0].coverage_scope_id.value)
        )
        combined = tuple(
            sorted(
                (*ordered_rejected, *ordered_acknowledged),
                key=lambda item: item[0].coverage_scope_id.value,
            )
        )
        scopes = tuple(item[0] for item in combined)
        if len({scope.coverage_scope_id for scope in scopes}) != len(scopes):
            raise ValueError("identified rejection routes must be unique by exact Silver scope.")
        sessions = {
            item[1].attempt_snapshot.subscription_attempt.connection_session
            for item in ordered_rejected
        } | {
            item[1].acknowledged_snapshot.subscription_attempt.connection_session
            for item in ordered_acknowledged
        }
        if len(sessions) != 1:
            raise ValueError("identified rejection routes must belong to one connection session.")

        rejected_rows = tuple(item[2] for item in ordered_rejected)
        acknowledged_rows = tuple(item[2] for item in ordered_acknowledged)
        all_snapshots = tuple(item[1].attempt_snapshot for item in ordered_rejected) + tuple(
            item[1].acknowledged_snapshot for item in ordered_acknowledged
        )
        snapshots_by_attempt: dict[SubscriptionAttemptId, SubscriptionAttemptSnapshot] = {}
        for snapshot in all_snapshots:
            attempt_id = snapshot.subscription_attempt.subscription_attempt_id
            previous = snapshots_by_attempt.get(attempt_id)
            if previous is not None and previous != snapshot:
                raise ValueError("one identified route attempt cannot have conflicting snapshots.")
            snapshots_by_attempt[attempt_id] = snapshot
        source_snapshots = tuple(
            snapshots_by_attempt[attempt_id]
            for attempt_id in sorted(snapshots_by_attempt, key=lambda item: item.value)
        )
        selected_attempt_ids = tuple(
            snapshot.subscription_attempt.subscription_attempt_id for snapshot in source_snapshots
        )
        return _construct_coverage_fanout(
            kind=(
                CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION
                if len(scopes) == 1
                else CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS
            ),
            plan=plan,
            catalog=catalog,
            connection_session=next(iter(sessions)),
            source_row=(
                _IDENTIFIED_REJECTION_SOURCE_VERSION,
                rejected_rows,
                acknowledged_rows,
            ),
            source_attempt_snapshots=source_snapshots,
            selected_attempt_ids=selected_attempt_ids,
            target_scopes=scopes,
            identified_rejection_targets=tuple(item[1] for item in ordered_rejected),
            identified_rejection_scope_ids=tuple(
                item[0].coverage_scope_id for item in ordered_rejected
            ),
            acknowledged_routed_targets=tuple(item[1] for item in ordered_acknowledged),
            acknowledged_routed_scope_ids=tuple(
                item[0].coverage_scope_id for item in ordered_acknowledged
            ),
        )

    @classmethod
    def all_possibly_active(
        cls,
        *,
        plan: SubscriptionPlanIdentity,
        catalog: CoverageTargetCatalog,
        complete_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
        domain: CoverageDomain,
        event_family: str | None = None,
        event_family_schema_version: int | None = None,
        payload_type: str | None = None,
    ) -> "CoverageFanoutProof":
        _validate_fanout_parent(plan, catalog)
        if type(domain) is not CoverageDomain or domain is CoverageDomain.SILVER_DELIVERY:
            raise ValueError("possibly-active fanout requires an ingress or normalization domain.")
        session = _validate_complete_attempt_snapshot_set(plan, complete_snapshots)
        possible = tuple(
            item
            for item in complete_snapshots
            if item.attempt_status is not SubscriptionAttemptStatus.PENDING
        )
        if not possible:
            raise ValueError(
                "all-possibly-active fanout requires at least one possibly active spec."
            )
        spec_ids = {item.subscription_spec.subscription_spec_id for item in possible}
        if domain is CoverageDomain.SILVER_NORMALIZATION:
            if type(event_family) is not str or type(payload_type) is not str:
                raise TypeError("Silver possibly-active fanout requires family and payload.")
            event_family = require_code(event_family, field_name="event_family")
            payload_type = require_code(payload_type, field_name="payload_type")
            if type(event_family_schema_version) is not int:
                raise TypeError("event_family_schema_version must be a built-in integer.")
            if event_family_schema_version <= 0:
                raise ValueError("event_family_schema_version must be positive.")
        elif any(
            value is not None for value in (event_family, event_family_schema_version, payload_type)
        ):
            raise ValueError("Bronze possibly-active fanout has no normalized binding filter.")
        targets = tuple(
            scope
            for scope in catalog.scopes
            if scope.domain is domain
            and scope.subscription_spec_ids[0] in spec_ids
            and (
                domain is CoverageDomain.BRONZE_INGRESS
                or (
                    scope.event_family == event_family
                    and scope.event_family_schema_version == event_family_schema_version
                    and scope.payload_type == payload_type
                )
            )
        )
        if not targets:
            raise ValueError("possibly-active binding selects no plan-derived coverage scopes.")
        return _construct_coverage_fanout(
            kind=CoverageFanoutKind.ALL_POSSIBLY_ACTIVE,
            plan=plan,
            catalog=catalog,
            connection_session=session,
            source_row=(
                "all-possibly-active-v1",
                domain.value,
                event_family,
                event_family_schema_version,
                payload_type,
                tuple(
                    (
                        item.subscription_attempt.subscription_attempt_id.value,
                        item.attempt_status.value,
                    )
                    for item in complete_snapshots
                ),
            ),
            source_attempt_snapshots=complete_snapshots,
            selected_attempt_ids=tuple(
                item.subscription_attempt.subscription_attempt_id for item in possible
            ),
            target_scopes=targets,
        )

    def verify_stored(
        self,
        *,
        expected_canonical_content: str,
        expected_proof_id: CoverageFanoutProofId,
    ) -> None:
        """Reject any persisted fanout content or bounded-ID mismatch."""

        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        if type(expected_proof_id) is not CoverageFanoutProofId:
            raise TypeError("expected_proof_id must be a CoverageFanoutProofId.")
        if (
            self.canonical_content != expected_canonical_content
            or self.coverage_fanout_proof_id != expected_proof_id
        ):
            raise ValueError("stored coverage fanout proof does not match its content.")


def _exact_plan_route_scope(
    *,
    plan: SubscriptionPlanIdentity,
    catalog: CoverageTargetCatalog,
    snapshot: SubscriptionAttemptSnapshot,
    source_selector: PublicSourceSelector,
    canonical_instrument_id: str,
    adapter_profile: str,
    event_family: str,
    event_family_schema_version: int,
    payload_type: str,
) -> CoverageScope:
    """Resolve one typed public route to exactly one plan-derived Silver leaf."""

    spec = snapshot.subscription_spec
    plan_specs = tuple(
        candidate
        for candidate in plan.subscription_specs
        if candidate.subscription_spec_id == spec.subscription_spec_id
    )
    if len(plan_specs) != 1 or plan_specs[0] != spec:
        raise ValueError("identified route subscription spec must belong exactly to the plan.")
    if spec.feed_product_id != plan.feed_product_id:
        raise ValueError("identified route feed must match the subscription plan.")
    instrument_bindings = tuple(
        binding
        for binding in plan.instrument_bindings
        if binding.subscription_spec_id == spec.subscription_spec_id
        and binding.source_selector == source_selector
        and binding.canonical_instrument_id == canonical_instrument_id
        and binding.adapter_profile == adapter_profile
    )
    if len(instrument_bindings) != 1:
        raise ValueError("identified route must match one exact plan selector and instrument.")
    normalization_bindings = tuple(
        binding
        for binding in plan.normalization_bindings
        if binding.subscription_spec_id == spec.subscription_spec_id
        and binding.adapter_profile == adapter_profile
        and binding.event_family == event_family
        and binding.event_family_schema_version == event_family_schema_version
        and binding.payload_type == payload_type
    )
    if len(normalization_bindings) != 1:
        raise ValueError("identified route must match one exact plan normalization binding.")
    scopes = tuple(
        scope
        for scope in catalog.scopes
        if scope.domain is CoverageDomain.SILVER_NORMALIZATION
        and scope.feed_product_id == plan.feed_product_id
        and scope.subscription_spec_ids == (spec.subscription_spec_id,)
        and scope.canonical_instrument_ids == (canonical_instrument_id,)
        and scope.event_family == event_family
        and scope.event_family_schema_version == event_family_schema_version
        and scope.payload_type == payload_type
    )
    if len(scopes) != 1:
        raise ValueError("identified route must resolve to one exact plan-derived Silver scope.")
    return scopes[0]


def _identified_route_source_row(
    *,
    version_tag: str,
    snapshot: SubscriptionAttemptSnapshot,
    source_selector: PublicSourceSelector,
    canonical_instrument_id: str,
    adapter_profile: str,
    event_family: str,
    event_family_schema_version: int,
    payload_type: str,
    scope: CoverageScope,
) -> tuple[object, ...]:
    attempt = snapshot.subscription_attempt
    return (
        version_tag,
        snapshot.subscription_spec.feed_product_id.value,
        attempt.connection_session.connection_session_id.value,
        snapshot.subscription_spec.subscription_spec_id.value,
        attempt.subscription_attempt_id.value,
        snapshot.attempt_status.value,
        source_selector.canonical_components(),
        canonical_instrument_id,
        adapter_profile,
        event_family,
        event_family_schema_version,
        payload_type,
        scope.coverage_scope_id.value,
    )


def _resolve_identified_rejection_target(
    *,
    plan: SubscriptionPlanIdentity,
    catalog: CoverageTargetCatalog,
    target: ExactIdentifiedRejectionTarget,
) -> tuple[CoverageScope, ExactIdentifiedRejectionTarget, tuple[object, ...]]:
    scope = _exact_plan_route_scope(
        plan=plan,
        catalog=catalog,
        snapshot=target.attempt_snapshot,
        source_selector=target.source_selector,
        canonical_instrument_id=target.canonical_instrument_id,
        adapter_profile=target.adapter_profile,
        event_family=target.event_family,
        event_family_schema_version=target.event_family_schema_version,
        payload_type=target.payload_type,
    )
    return (
        scope,
        target,
        _identified_route_source_row(
            version_tag=_IDENTIFIED_REJECTION_TARGET_VERSION,
            snapshot=target.attempt_snapshot,
            source_selector=target.source_selector,
            canonical_instrument_id=target.canonical_instrument_id,
            adapter_profile=target.adapter_profile,
            event_family=target.event_family,
            event_family_schema_version=target.event_family_schema_version,
            payload_type=target.payload_type,
            scope=scope,
        ),
    )


def _resolve_acknowledged_route_for_identified_rejection(
    *,
    plan: SubscriptionPlanIdentity,
    catalog: CoverageTargetCatalog,
    target: RoutedCoverageTarget,
) -> tuple[CoverageScope, RoutedCoverageTarget, tuple[object, ...]]:
    snapshot = target.acknowledged_snapshot
    instrument_bindings = tuple(
        binding
        for binding in plan.instrument_bindings
        if binding.subscription_spec_id == snapshot.subscription_spec.subscription_spec_id
        and binding.canonical_instrument_id == target.canonical_instrument_id
    )
    if len(instrument_bindings) != 1:
        raise ValueError("acknowledged route must match one exact plan instrument binding.")
    instrument_binding = instrument_bindings[0]
    scope = _exact_plan_route_scope(
        plan=plan,
        catalog=catalog,
        snapshot=snapshot,
        source_selector=instrument_binding.source_selector,
        canonical_instrument_id=target.canonical_instrument_id,
        adapter_profile=instrument_binding.adapter_profile,
        event_family=target.event_family,
        event_family_schema_version=target.event_family_schema_version,
        payload_type=target.payload_type,
    )
    return (
        scope,
        target,
        _identified_route_source_row(
            version_tag=_IDENTIFIED_REJECTION_ACK_TARGET_VERSION,
            snapshot=snapshot,
            source_selector=instrument_binding.source_selector,
            canonical_instrument_id=target.canonical_instrument_id,
            adapter_profile=instrument_binding.adapter_profile,
            event_family=target.event_family,
            event_family_schema_version=target.event_family_schema_version,
            payload_type=target.payload_type,
            scope=scope,
        ),
    )


def _construct_coverage_fanout(
    *,
    kind: CoverageFanoutKind,
    plan: SubscriptionPlanIdentity,
    catalog: CoverageTargetCatalog,
    connection_session: ConnectionSessionIdentity,
    source_row: tuple[object, ...],
    source_attempt_snapshots: tuple[SubscriptionAttemptSnapshot, ...],
    selected_attempt_ids: tuple[SubscriptionAttemptId, ...],
    target_scopes: tuple[CoverageScope, ...],
    identified_rejection_targets: tuple[ExactIdentifiedRejectionTarget, ...] = (),
    identified_rejection_scope_ids: tuple[CoverageScopeId, ...] = (),
    acknowledged_routed_targets: tuple[RoutedCoverageTarget, ...] = (),
    acknowledged_routed_scope_ids: tuple[CoverageScopeId, ...] = (),
) -> CoverageFanoutProof:
    if connection_session.collector_run_id.value == "":
        raise ValueError("coverage fanout connection session must have a collector run.")
    ordered = tuple(sorted(target_scopes, key=lambda item: item.coverage_scope_id.value))
    if ordered != target_scopes or len({item.coverage_scope_id for item in ordered}) != len(
        ordered
    ):
        raise ValueError("coverage fanout targets must be sorted and unique.")
    if ordered and len({item.domain for item in ordered}) != 1:
        raise ValueError("one coverage fanout proof must target exactly one coverage domain.")
    require_collection_size(
        ordered,
        field_name="target_scopes",
        maximum_items=MAX_COVERAGE_MUTATION_TARGETS,
    )
    catalog_ids = {item.coverage_scope_id for item in catalog.scopes}
    if any(item.coverage_scope_id not in catalog_ids for item in ordered):
        raise ValueError("coverage fanout target must belong to its immutable catalogue.")
    if type(source_attempt_snapshots) is not tuple or any(
        type(item) is not SubscriptionAttemptSnapshot for item in source_attempt_snapshots
    ):
        raise TypeError("source_attempt_snapshots must contain exact snapshot values.")
    source_attempt_ids = tuple(
        item.subscription_attempt.subscription_attempt_id for item in source_attempt_snapshots
    )
    if source_attempt_ids != tuple(sorted(set(source_attempt_ids), key=lambda item: item.value)):
        raise ValueError("source attempt snapshots must be sorted and unique by attempt ID.")
    if any(
        item.subscription_attempt.connection_session != connection_session
        for item in source_attempt_snapshots
    ):
        raise ValueError("source attempt snapshots must belong to the fanout session.")
    if type(selected_attempt_ids) is not tuple or any(
        type(item) is not SubscriptionAttemptId for item in selected_attempt_ids
    ):
        raise TypeError("selected_attempt_ids must contain exact attempt IDs.")
    if selected_attempt_ids != tuple(
        sorted(set(selected_attempt_ids), key=lambda item: item.value)
    ):
        raise ValueError("selected fanout attempt IDs must be sorted and unique.")
    if any(item not in source_attempt_ids for item in selected_attempt_ids):
        raise ValueError("selected fanout attempts must belong to its source snapshot.")
    if type(identified_rejection_targets) is not tuple or any(
        type(item) is not ExactIdentifiedRejectionTarget for item in identified_rejection_targets
    ):
        raise TypeError("identified_rejection_targets contain an invalid value.")
    if type(acknowledged_routed_targets) is not tuple or any(
        type(item) is not RoutedCoverageTarget for item in acknowledged_routed_targets
    ):
        raise TypeError("acknowledged_routed_targets contain an invalid value.")
    if type(identified_rejection_scope_ids) is not tuple or any(
        type(item) is not CoverageScopeId for item in identified_rejection_scope_ids
    ):
        raise TypeError("identified_rejection_scope_ids contain an invalid value.")
    if type(acknowledged_routed_scope_ids) is not tuple or any(
        type(item) is not CoverageScopeId for item in acknowledged_routed_scope_ids
    ):
        raise TypeError("acknowledged_routed_scope_ids contain an invalid value.")
    if len(identified_rejection_targets) != len(identified_rejection_scope_ids):
        raise ValueError("identified rejection targets and scopes must have equal cardinality.")
    if len(acknowledged_routed_targets) != len(acknowledged_routed_scope_ids):
        raise ValueError("acknowledged routed targets and scopes must have equal cardinality.")
    if identified_rejection_scope_ids != tuple(
        sorted(set(identified_rejection_scope_ids), key=lambda item: item.value)
    ):
        raise ValueError("identified rejection scope IDs must be sorted and unique.")
    if acknowledged_routed_scope_ids != tuple(
        sorted(set(acknowledged_routed_scope_ids), key=lambda item: item.value)
    ):
        raise ValueError("acknowledged routed scope IDs must be sorted and unique.")
    routed_scope_ids = tuple(
        sorted(
            (*identified_rejection_scope_ids, *acknowledged_routed_scope_ids),
            key=lambda item: item.value,
        )
    )
    if routed_scope_ids and routed_scope_ids != tuple(scope.coverage_scope_id for scope in ordered):
        raise ValueError("typed route partitions must equal the exact fanout scope union.")
    identified_kind = kind in {
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
    }
    if identified_kind is (not identified_rejection_targets):
        raise ValueError("identified rejection fanout requires its typed rejection targets.")
    if not identified_kind and identified_rejection_targets:
        raise ValueError("other fanout kinds cannot retain identified rejection targets.")
    if acknowledged_routed_targets and kind not in {
        CoverageFanoutKind.EXACT_ROUTED_EVENT,
        CoverageFanoutKind.EXACT_ROUTED_EVENTS,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
    }:
        raise ValueError("fanout kind cannot retain acknowledged routed targets.")
    content_version = (
        _IDENTIFIED_REJECTION_FANOUT_CONTENT_VERSION
        if identified_kind
        else "coverage-fanout-proof-content-v1"
    )
    id_version = (
        _IDENTIFIED_REJECTION_FANOUT_ID_VERSION if identified_kind else "coverage-fanout-proof-v1"
    )
    content = canonical_json_array(
        (
            content_version,
            kind.value,
            plan.subscription_plan_id,
            catalog.coverage_target_catalog_id,
            connection_session.connection_session_id,
            source_row,
            tuple(item.coverage_scope_id.value for item in ordered),
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    digest = sha256_hex(content.encode("utf-8"), field_name="coverage fanout proof")
    proof_id = CoverageFanoutProofId(
        canonical_json_array(
            (
                id_version,
                kind.value,
                plan.subscription_plan_id,
                catalog.coverage_target_catalog_id,
                len(ordered),
                digest,
            )
        )
    )
    value = object.__new__(CoverageFanoutProof)
    object.__setattr__(value, "kind", kind)
    object.__setattr__(value, "subscription_plan_id", plan.subscription_plan_id)
    object.__setattr__(value, "coverage_target_catalog_id", catalog.coverage_target_catalog_id)
    object.__setattr__(value, "connection_session_id", connection_session.connection_session_id)
    object.__setattr__(value, "source_canonical_row", source_row)
    object.__setattr__(value, "source_attempt_snapshots", source_attempt_snapshots)
    object.__setattr__(value, "selected_attempt_ids", selected_attempt_ids)
    object.__setattr__(value, "target_scopes", ordered)
    object.__setattr__(value, "identified_rejection_targets", identified_rejection_targets)
    object.__setattr__(value, "identified_rejection_scope_ids", identified_rejection_scope_ids)
    object.__setattr__(value, "acknowledged_routed_targets", acknowledged_routed_targets)
    object.__setattr__(value, "acknowledged_routed_scope_ids", acknowledged_routed_scope_ids)
    object.__setattr__(value, "canonical_content", content)
    object.__setattr__(value, "content_sha256", digest)
    object.__setattr__(value, "coverage_fanout_proof_id", proof_id)
    return value


def _rederive_raw_coverage_fanout(
    raw_record: RawMarketDataRecord,
    fanout_proof: CoverageFanoutProof,
) -> CoverageFanoutProof:
    """Rebuild a strict frame fanout from the raw record's complete snapshot."""

    plan = raw_record.subscription_plan
    catalog = CoverageTargetCatalog.from_subscription_plan(plan)
    snapshots = raw_record.subscription_attempt_snapshots
    if fanout_proof.kind in {
        CoverageFanoutKind.EXACT_ROUTED_EVENT,
        CoverageFanoutKind.EXACT_ROUTED_EVENTS,
    }:
        source_row = fanout_proof.source_canonical_row
        if (
            len(source_row) != 2
            or source_row[0] != "exact-routed-events-v1"
            or type(source_row[1]) is not tuple
        ):
            raise ValueError("exact-routed fanout has an invalid source snapshot.")
        snapshots_by_attempt = {
            item.subscription_attempt.subscription_attempt_id: item for item in snapshots
        }
        routed: list[RoutedCoverageTarget] = []
        for row in source_row[1]:
            if type(row) is not tuple or len(row) != 6:
                raise ValueError("exact-routed fanout has an invalid routed row.")
            attempt_id = SubscriptionAttemptId(row[0])
            spec_id = SubscriptionSpecId(row[1])
            snapshot = snapshots_by_attempt.get(attempt_id)
            if (
                snapshot is None
                or snapshot.subscription_attempt.subscription_spec.subscription_spec_id != spec_id
                or snapshot.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED
            ):
                raise ValueError("exact-routed fanout isn't present in the raw snapshot.")
            routed.append(
                RoutedCoverageTarget(
                    acknowledged_snapshot=snapshot,
                    canonical_instrument_id=row[2],
                    event_family=row[3],
                    event_family_schema_version=row[4],
                    payload_type=row[5],
                )
            )
        return CoverageFanoutProof.exact_routed_events(
            plan=plan,
            catalog=catalog,
            routed_targets=tuple(routed),
        )
    if fanout_proof.kind in {
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
    }:
        source_row = fanout_proof.source_canonical_row
        if (
            len(source_row) != 3
            or source_row[0] != _IDENTIFIED_REJECTION_SOURCE_VERSION
            or type(source_row[1]) is not tuple
            or type(source_row[2]) is not tuple
        ):
            raise ValueError("identified rejection fanout has an invalid source snapshot.")
        snapshots_by_attempt = {
            item.subscription_attempt.subscription_attempt_id: item for item in snapshots
        }

        def parse_route_row(
            row: object,
            *,
            expected_tag: str,
        ) -> tuple[
            SubscriptionAttemptSnapshot,
            PublicSourceSelector,
            str,
            str,
            str,
            int,
            str,
        ]:
            if type(row) is not tuple or len(row) != 13 or row[0] != expected_tag:
                raise ValueError("identified rejection fanout has an invalid routed row.")
            feed_id = FeedProductId(row[1])
            session_id = ConnectionSessionId(row[2])
            spec_id = SubscriptionSpecId(row[3])
            attempt_id = SubscriptionAttemptId(row[4])
            status: SubscriptionAttemptStatus | None = None
            try:
                status = SubscriptionAttemptStatus(row[5])
            except (TypeError, ValueError):
                pass
            if status is None:
                raise ValueError("identified rejection row has an invalid attempt status.")
            selector_row = row[6]
            if (
                type(selector_row) is not tuple
                or len(selector_row) != 3
                or selector_row[0] != "public-source-selector-v1"
            ):
                raise ValueError("identified rejection row has an invalid public selector.")
            selector_kind: PublicSourceSelectorKind | None = None
            try:
                selector_kind = PublicSourceSelectorKind(selector_row[1])
            except (TypeError, ValueError):
                pass
            if selector_kind is None:
                raise ValueError("identified rejection row has an invalid public selector kind.")
            selector = PublicSourceSelector(selector_kind, selector_row[2])
            snapshot = snapshots_by_attempt.get(attempt_id)
            if (
                snapshot is None
                or snapshot.subscription_spec.feed_product_id != feed_id
                or snapshot.subscription_attempt.connection_session.connection_session_id
                != session_id
                or snapshot.subscription_spec.subscription_spec_id != spec_id
                or snapshot.attempt_status is not status
            ):
                raise ValueError("identified rejection route isn't present in the raw snapshot.")
            canonical_instrument_id = validate_canonical_instrument_id(row[7])
            adapter_profile = require_code(row[8], field_name="adapter_profile")
            event_family = require_code(row[9], field_name="event_family")
            family_version = require_nonnegative_int(
                row[10],
                field_name="event_family_schema_version",
            )
            if family_version == 0:
                raise ValueError("event_family_schema_version must be positive.")
            payload_type = require_code(row[11], field_name="payload_type")
            CoverageScopeId(row[12])
            return (
                snapshot,
                selector,
                canonical_instrument_id,
                adapter_profile,
                event_family,
                family_version,
                payload_type,
            )

        rejected: list[ExactIdentifiedRejectionTarget] = []
        for row in source_row[1]:
            (
                snapshot,
                selector,
                canonical_instrument_id,
                adapter_profile,
                event_family,
                family_version,
                payload_type,
            ) = parse_route_row(row, expected_tag=_IDENTIFIED_REJECTION_TARGET_VERSION)
            rejected.append(
                ExactIdentifiedRejectionTarget(
                    attempt_snapshot=snapshot,
                    source_selector=selector,
                    canonical_instrument_id=canonical_instrument_id,
                    adapter_profile=adapter_profile,
                    event_family=event_family,
                    event_family_schema_version=family_version,
                    payload_type=payload_type,
                )
            )
        acknowledged: list[RoutedCoverageTarget] = []
        for row in source_row[2]:
            (
                snapshot,
                selector,
                canonical_instrument_id,
                adapter_profile,
                event_family,
                family_version,
                payload_type,
            ) = parse_route_row(row, expected_tag=_IDENTIFIED_REJECTION_ACK_TARGET_VERSION)
            if snapshot.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED:
                raise ValueError("acknowledged route row requires its captured ACK state.")
            matching_plan_bindings = tuple(
                binding
                for binding in plan.instrument_bindings
                if binding.subscription_spec_id == snapshot.subscription_spec.subscription_spec_id
                and binding.source_selector == selector
                and binding.canonical_instrument_id == canonical_instrument_id
                and binding.adapter_profile == adapter_profile
            )
            if len(matching_plan_bindings) != 1:
                raise ValueError("acknowledged route row doesn't match the raw subscription plan.")
            acknowledged.append(
                RoutedCoverageTarget(
                    acknowledged_snapshot=snapshot,
                    canonical_instrument_id=canonical_instrument_id,
                    event_family=event_family,
                    event_family_schema_version=family_version,
                    payload_type=payload_type,
                )
            )
        return CoverageFanoutProof.exact_identified_rejections(
            plan=plan,
            catalog=catalog,
            rejection_targets=tuple(rejected),
            acknowledged_targets=tuple(acknowledged),
        )
    if fanout_proof.kind is CoverageFanoutKind.ALL_POSSIBLY_ACTIVE:
        if not fanout_proof.target_scopes:
            raise ValueError("possibly-active frame fanout requires target scopes.")
        first_scope = fanout_proof.target_scopes[0]
        if first_scope.domain is CoverageDomain.SILVER_NORMALIZATION:
            return CoverageFanoutProof.all_possibly_active(
                plan=plan,
                catalog=catalog,
                complete_snapshots=snapshots,
                domain=first_scope.domain,
                event_family=first_scope.event_family,
                event_family_schema_version=first_scope.event_family_schema_version,
                payload_type=first_scope.payload_type,
            )
        return CoverageFanoutProof.all_possibly_active(
            plan=plan,
            catalog=catalog,
            complete_snapshots=snapshots,
            domain=first_scope.domain,
        )
    raise ValueError("raw frame binding rejects lifecycle-only coverage fanout.")


@dataclass(frozen=True, slots=True, init=False)
class RawCoverageFanoutBinding:
    """Bind a strict fanout proof to one raw record's complete attempt snapshot.

    Snapshot digest preimage::

        ["raw-coverage-fanout-snapshot-content-v1",
         [[subscription_spec_id, subscription_attempt_id, attempt_status], ...]]

    Content preimage::

        ["raw-coverage-fanout-binding-content-v1", raw_record_id,
         full_record_integrity_sha256, subscription_plan_id,
         connection_session_id, coverage_fanout_proof_id,
         subscription_snapshot_content_sha256]

    ID preimage::

        ["raw-coverage-fanout-binding-v1", raw_record_id,
         coverage_fanout_proof_id, subscription_snapshot_content_sha256,
         SHA256(canonical_content)]

    No raw record, application bytes, or snapshot object is retained.
    """

    raw_record_id: RawRecordId
    full_record_integrity_sha256: str
    subscription_plan_id: SubscriptionPlanId
    connection_session_id: ConnectionSessionId
    coverage_fanout_proof_id: CoverageFanoutProofId
    subscription_snapshot_content_sha256: str
    canonical_content: str = field(repr=False)
    content_sha256: str
    raw_coverage_fanout_binding_id: RawCoverageFanoutBindingId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use RawCoverageFanoutBinding.from_raw_record() or .from_stored().")

    @classmethod
    def from_raw_record(
        cls,
        *,
        raw_record: RawMarketDataRecord,
        coverage_fanout_proof: CoverageFanoutProof,
    ) -> Self:
        if cls is not RawCoverageFanoutBinding:
            raise TypeError("raw coverage fanout bindings don't support subclass factories.")
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        if type(coverage_fanout_proof) is not CoverageFanoutProof:
            raise TypeError("coverage_fanout_proof must be a CoverageFanoutProof.")
        if (
            coverage_fanout_proof.subscription_plan_id
            != raw_record.subscription_plan.subscription_plan_id
            or coverage_fanout_proof.connection_session_id
            != raw_record.connection_session.connection_session_id
        ):
            raise ValueError("coverage fanout must belong to the raw plan and session.")
        rederived = _rederive_raw_coverage_fanout(raw_record, coverage_fanout_proof)
        if rederived != coverage_fanout_proof:
            raise ValueError("coverage fanout isn't exactly reproducible from the raw snapshot.")
        snapshot_rows = _attempt_snapshot_rows(raw_record.subscription_attempt_snapshots)
        snapshot_content = canonical_json_array(
            (_RAW_COVERAGE_SNAPSHOT_CONTENT_VERSION, snapshot_rows),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )
        snapshot_digest = sha256_hex(
            snapshot_content.encode("utf-8"),
            field_name="subscription_snapshot_content",
        )
        content = canonical_json_array(
            (
                _RAW_COVERAGE_FANOUT_BINDING_CONTENT_VERSION,
                raw_record.raw_record_id.value,
                raw_record.full_record_integrity_sha256,
                raw_record.subscription_plan.subscription_plan_id.value,
                raw_record.connection_session.connection_session_id.value,
                coverage_fanout_proof.coverage_fanout_proof_id.value,
                snapshot_digest,
            )
        )
        content_digest = sha256_hex(
            content.encode("utf-8"),
            field_name="raw_coverage_fanout_binding_content",
        )
        identifier = RawCoverageFanoutBindingId(
            canonical_json_array(
                (
                    _RAW_COVERAGE_FANOUT_BINDING_ID_VERSION,
                    raw_record.raw_record_id.value,
                    coverage_fanout_proof.coverage_fanout_proof_id.value,
                    snapshot_digest,
                    content_digest,
                )
            )
        )
        value = object.__new__(cls)
        object.__setattr__(value, "raw_record_id", raw_record.raw_record_id)
        object.__setattr__(
            value,
            "full_record_integrity_sha256",
            raw_record.full_record_integrity_sha256,
        )
        object.__setattr__(
            value,
            "subscription_plan_id",
            raw_record.subscription_plan.subscription_plan_id,
        )
        object.__setattr__(
            value,
            "connection_session_id",
            raw_record.connection_session.connection_session_id,
        )
        object.__setattr__(
            value,
            "coverage_fanout_proof_id",
            coverage_fanout_proof.coverage_fanout_proof_id,
        )
        object.__setattr__(value, "subscription_snapshot_content_sha256", snapshot_digest)
        object.__setattr__(value, "canonical_content", content)
        object.__setattr__(value, "content_sha256", content_digest)
        object.__setattr__(value, "raw_coverage_fanout_binding_id", identifier)
        return value

    @classmethod
    def from_stored(
        cls,
        *,
        raw_record: RawMarketDataRecord,
        coverage_fanout_proof: CoverageFanoutProof,
        expected_subscription_snapshot_content_sha256: str,
        expected_canonical_content: str,
        expected_content_sha256: str,
        expected_binding_id: RawCoverageFanoutBindingId,
    ) -> Self:
        require_sha256(
            expected_subscription_snapshot_content_sha256,
            field_name="expected_subscription_snapshot_content_sha256",
        )
        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        require_sha256(expected_content_sha256, field_name="expected_content_sha256")
        if type(expected_binding_id) is not RawCoverageFanoutBindingId:
            raise TypeError("expected_binding_id must be a RawCoverageFanoutBindingId.")
        value = cls.from_raw_record(
            raw_record=raw_record,
            coverage_fanout_proof=coverage_fanout_proof,
        )
        if (
            value.subscription_snapshot_content_sha256
            != expected_subscription_snapshot_content_sha256
            or value.canonical_content != expected_canonical_content
            or value.content_sha256 != expected_content_sha256
            or value.raw_coverage_fanout_binding_id != expected_binding_id
        ):
            raise ValueError("stored raw coverage fanout binding doesn't match its source.")
        return value


@dataclass(frozen=True, slots=True)
class RequestedCoverageMutation:
    """One target's requested evidence-backed status within an atomic CAS batch."""

    scope: CoverageScope
    epoch: CoverageEpochIdentity
    requested_status: CoverageStatus
    initial_reason: InitialCoverageReason
    transition_reason: CoverageReason
    evidence: CoverageEvidence

    def __post_init__(self) -> None:
        if type(self.scope) is not CoverageScope:
            raise TypeError("scope must be a CoverageScope.")
        if type(self.epoch) is not CoverageEpochIdentity or self.epoch.scope != self.scope:
            raise ValueError("epoch must belong to the requested mutation scope.")
        if type(self.requested_status) is not CoverageStatus:
            raise TypeError("requested_status must be a CoverageStatus.")
        if type(self.initial_reason) is not InitialCoverageReason:
            raise TypeError("initial_reason must be an InitialCoverageReason.")
        if type(self.transition_reason) is not CoverageReason:
            raise TypeError("transition_reason must be a CoverageReason.")
        if type(self.evidence) is not CoverageEvidence:
            raise TypeError("evidence must be a CoverageEvidence.")
        if self.evidence.scope != self.scope or self.evidence.epoch != self.epoch:
            raise ValueError("mutation evidence must belong to its exact scope and epoch.")


@dataclass(frozen=True, slots=True)
class CoverageMutationNoOp:
    """Audit lineage for repeated degradation without state or ordinal change.

    Canonical row::

        ["coverage-mutation-no-op-v1", scope_id, current_state_reference_id,
         requested_status, initial_reason, transition_reason, evidence_id,
         "already-at-or-beyond-requested-severity"]
    """

    current_state: CoverageStateReference = field(repr=False)
    request: RequestedCoverageMutation = field(repr=False)
    canonical_row: tuple[object, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.current_state) is not CoverageStateReference:
            raise TypeError("current_state must be a CoverageStateReference.")
        if type(self.request) is not RequestedCoverageMutation:
            raise TypeError("request must be a RequestedCoverageMutation.")
        if self.request.scope != self.current_state.reference.scope:
            raise ValueError("coverage no-op request must match its current scope.")
        if self.request.epoch != self.current_state.reference.epoch:
            raise ValueError("coverage no-op request must match its current epoch.")
        current_boundary = (
            self.current_state.latest_transition.evidence.observed_monotonic_ns
            if self.current_state.latest_transition is not None
            else self.current_state.reference.initial_evidence.observed_monotonic_ns
        )
        if self.request.evidence.observed_monotonic_ns < current_boundary:
            raise ValueError("coverage no-op evidence cannot precede the current state boundary.")
        current_severity = _coverage_status_severity(self.current_state.reference.status)
        requested_severity = _coverage_status_severity(self.request.requested_status)
        if requested_severity > current_severity:
            raise ValueError("a stricter requested status requires a coverage transition.")
        if (
            self.request.requested_status is CoverageStatus.COMPLETE
            and self.current_state.reference.status is not CoverageStatus.COMPLETE
        ):
            raise ValueError("coverage recovery cannot be represented as a no-op.")
        _validate_mutation_request_semantics(self.request)
        object.__setattr__(
            self,
            "canonical_row",
            (
                "coverage-mutation-no-op-v1",
                self.request.scope.coverage_scope_id.value,
                self.current_state.coverage_state_reference_id.value,
                self.request.requested_status.value,
                self.request.initial_reason.value,
                self.request.transition_reason.value,
                self.request.evidence.coverage_evidence_id.value,
                "already-at-or-beyond-requested-severity",
            ),
        )


def _coverage_status_severity(status: CoverageStatus) -> int:
    return {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.UNCERTAIN: 1,
        CoverageStatus.CONFIRMED_INCOMPLETE: 2,
    }[status]


def _validate_mutation_request_semantics(request: RequestedCoverageMutation) -> None:
    expected_initial_reason = {
        CoverageReason.INITIAL_SCOPE: InitialCoverageReason.INITIAL_ACTIVATION,
        CoverageReason.TRANSPORT_AMBIGUITY: InitialCoverageReason.TRANSPORT_AMBIGUITY,
        CoverageReason.RAW_ACCEPTANCE_UNCERTAIN: (InitialCoverageReason.RAW_ACCEPTANCE_UNCERTAIN),
        CoverageReason.RAW_DEFINITE_REJECTION: InitialCoverageReason.RAW_DEFINITE_REJECTION,
        CoverageReason.UPSTREAM_COVERAGE_DEGRADED: (
            InitialCoverageReason.UPSTREAM_COVERAGE_DEGRADED
        ),
        CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE: (
            InitialCoverageReason.IN_SCOPE_NORMALIZATION_FAILURE
        ),
        CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN: (
            InitialCoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN
        ),
        CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION: (
            InitialCoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION
        ),
        CoverageReason.SOURCE_SEQUENCE_BREAK: InitialCoverageReason.SOURCE_SEQUENCE_BREAK,
        CoverageReason.SOURCE_EVENT_CONFLICT: InitialCoverageReason.SOURCE_EVENT_CONFLICT,
    }.get(request.transition_reason)
    if expected_initial_reason is None or request.initial_reason is not expected_initial_reason:
        raise ValueError("coverage mutation initial and transition reasons are incompatible.")
    initial_row = (
        request.scope.domain,
        request.requested_status,
        request.initial_reason,
        request.evidence.kind,
    )
    if request.requested_status is CoverageStatus.COMPLETE:
        if initial_row not in _INITIAL_COVERAGE_MATRIX:
            raise ValueError("complete coverage mutation requires exact activation evidence.")
        return
    if request.requested_status is CoverageStatus.UNCERTAIN:
        allowed = _UNCERTAIN_TRANSITION_EVIDENCE
    else:
        allowed = _CONFIRMED_INCOMPLETE_TRANSITION_EVIDENCE
    transition_row = (
        request.scope.domain,
        request.transition_reason,
        request.evidence.kind,
    )
    if initial_row not in _INITIAL_COVERAGE_MATRIX and transition_row not in allowed:
        raise ValueError("coverage mutation status, reason and evidence are incompatible.")
    _validate_upstream_transition_status(
        requested_status=request.requested_status,
        evidence=request.evidence,
    )


def _fanout_attempt_ids_for_scope(
    fanout: CoverageFanoutProof,
    scope: CoverageScope,
) -> tuple[SubscriptionAttemptId, ...]:
    snapshots_by_attempt = {
        item.subscription_attempt.subscription_attempt_id: item
        for item in fanout.source_attempt_snapshots
    }
    return tuple(
        attempt_id
        for attempt_id in fanout.selected_attempt_ids
        if snapshots_by_attempt[attempt_id].subscription_spec.subscription_spec_id
        in scope.subscription_spec_ids
    )


def _validate_fanout_evidence_lineage(
    fanout: CoverageFanoutProof,
    requests: tuple[RequestedCoverageMutation, ...],
) -> None:
    """Require every cause-specific evidence source to match the fanout session."""

    for request in requests:
        source = request.evidence.source
        expected_attempt_ids = _fanout_attempt_ids_for_scope(fanout, request.scope)
        if type(source) is InitialActivationEvidenceSource:
            if source.connection_session.connection_session_id != fanout.connection_session_id:
                raise ValueError("activation evidence must belong to the exact fanout session.")
            evidence_attempt_ids = tuple(
                item.subscription_attempt.subscription_attempt_id
                for item in source.acknowledged_attempts
            )
            if evidence_attempt_ids != expected_attempt_ids:
                raise ValueError("activation evidence must match exact fanout attempts.")
            continue
        if type(source) is TransportAmbiguityEvidenceSource:
            if source.connection_session.connection_session_id != fanout.connection_session_id:
                raise ValueError("transport evidence must belong to the exact fanout session.")
            if fanout.kind in {
                CoverageFanoutKind.ONE_POSSIBLY_DELIVERED_SPEC,
                CoverageFanoutKind.POSSIBLY_DELIVERED_SPECS,
                CoverageFanoutKind.ALL_POSSIBLY_ACTIVE,
            }:
                if (
                    source.subscription_attempt is None
                    or (source.subscription_attempt.subscription_attempt_id,)
                    != expected_attempt_ids
                ):
                    raise ValueError("transport evidence must bind the exact fanout attempt.")
            continue
        raw_record_id: RawRecordId | None = None
        if isinstance(
            source,
            (
                RawRecordEvidenceSource,
                NormalizationFailureEvidenceSource,
                NormalizationOutcomeEvidenceSource,
                SourceEventConflictEvidenceSource,
                AuthoritativeStateSnapshotEvidenceSource,
            ),
        ):
            raw_record_id = source.raw_record_id
        if raw_record_id is not None:
            _feed, _run, raw_session_id = _raw_record_feed_run_session(raw_record_id)
            if raw_session_id != fanout.connection_session_id:
                raise ValueError("raw evidence must belong to the exact fanout session.")


def _raw_record_ids_for_requests(
    requests: tuple[RequestedCoverageMutation, ...],
) -> tuple[RawRecordId, ...]:
    """Return the canonical unique raw-record IDs cited by mutation evidence."""

    raw_record_ids: list[RawRecordId] = []
    for request in requests:
        source = request.evidence.source
        raw_record_id: RawRecordId | None = None
        if type(source) is RawRecordEvidenceSource:
            raw_record_id = source.raw_record_id
        elif type(source) is NormalizationFailureEvidenceSource:
            raw_record_id = source.raw_record_id
        elif type(source) is NormalizationOutcomeEvidenceSource:
            raw_record_id = source.raw_record_id
        elif type(source) is SourceEventConflictEvidenceSource:
            raw_record_id = source.raw_record_id
        elif type(source) is AuthoritativeStateSnapshotEvidenceSource:
            raw_record_id = source.raw_record_id
        if raw_record_id is not None:
            raw_record_ids.append(raw_record_id)
    return tuple(sorted(set(raw_record_ids), key=lambda item: item.value))


def _validate_raw_fanout_binding(
    *,
    fanout: CoverageFanoutProof,
    requests: tuple[RequestedCoverageMutation, ...],
    raw_fanout_binding: RawCoverageFanoutBinding | None,
) -> None:
    """Bind raw-backed evidence to the exact full attempt snapshot used by fanout."""

    if raw_fanout_binding is not None and type(raw_fanout_binding) is not RawCoverageFanoutBinding:
        raise TypeError("raw_fanout_binding must be a RawCoverageFanoutBinding or None.")
    raw_record_ids = _raw_record_ids_for_requests(requests)
    if raw_record_ids and raw_fanout_binding is None:
        raise ValueError("raw-backed coverage evidence requires an exact raw fanout binding.")
    if raw_fanout_binding is None:
        return
    if (
        raw_fanout_binding.coverage_fanout_proof_id != fanout.coverage_fanout_proof_id
        or raw_fanout_binding.subscription_plan_id != fanout.subscription_plan_id
        or raw_fanout_binding.connection_session_id != fanout.connection_session_id
    ):
        raise ValueError("raw fanout binding must match the prepared coverage fanout.")
    if raw_record_ids and raw_record_ids != (raw_fanout_binding.raw_record_id,):
        raise ValueError("all raw-backed coverage evidence must cite the bound raw record.")


def _validate_fanout_request_semantics(
    fanout: CoverageFanoutProof,
    requests: tuple[RequestedCoverageMutation, ...],
) -> None:
    """Close cause-to-status semantics without deriving runtime evidence."""

    outcome_sink_requests = tuple(
        request
        for request in requests
        if type(request.evidence.source) is NormalizationOutcomeEvidenceSource
    )
    if outcome_sink_requests:
        if len(outcome_sink_requests) != len(requests):
            raise ValueError("one fanout cannot mix outcome-sink failure with other causes.")
        outcome_keys = {
            (
                request.evidence.source.normalization_outcome_id,
                request.evidence.kind,
            )
            for request in outcome_sink_requests
            if type(request.evidence.source) is NormalizationOutcomeEvidenceSource
        }
        if len(outcome_keys) != 1:
            raise ValueError(
                "outcome-sink failure fanout requires one exact outcome and failure knowledge."
            )
        outcome_source = outcome_sink_requests[0].evidence.source
        assert type(outcome_source) is NormalizationOutcomeEvidenceSource
        outcome_status = outcome_source.frame_status
        if fanout.kind in {
            CoverageFanoutKind.EXACT_ROUTED_EVENT,
            CoverageFanoutKind.EXACT_ROUTED_EVENTS,
        }:
            if outcome_status not in _EXACT_ROUTED_NORMALIZATION_OUTCOME_FRAME_STATUS_CODES:
                raise ValueError(
                    "outcome-sink failure status requires its exact indexed fanout kind."
                )
        elif fanout.kind in {
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
            CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
        }:
            if outcome_status != "rejected_after_indexing":
                raise ValueError(
                    "identified-rejection outcome-sink fanout requires an indexed rejection."
                )
        elif fanout.kind is CoverageFanoutKind.ALL_POSSIBLY_ACTIVE:
            if outcome_status not in _PLAN_SLICE_NORMALIZATION_OUTCOME_FRAME_STATUS_CODES:
                raise ValueError(
                    "outcome-sink failure status requires its complete pre-index plan slice."
                )
        else:
            raise ValueError("outcome-sink failure evidence has no compatible fanout kind.")

    if fanout.kind is CoverageFanoutKind.HANDSHAKE_BEFORE_SEND:
        if requests:
            raise ValueError("handshake-before-send cannot mutate coverage.")
        return
    if fanout.kind is CoverageFanoutKind.ACKNOWLEDGED_ACTIVE:
        if any(
            request.requested_status is not CoverageStatus.COMPLETE
            or request.initial_reason is not InitialCoverageReason.INITIAL_ACTIVATION
            or request.evidence.kind is not CoverageEvidenceKind.INITIAL_ACTIVATION
            for request in requests
        ):
            raise ValueError("acknowledged-active fanout permits only exact initial activation.")
        return
    if fanout.kind in {
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTION,
        CoverageFanoutKind.EXACT_IDENTIFIED_REJECTIONS,
    }:
        outcome_rows = {
            (
                CoverageStatus.UNCERTAIN,
                CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
                CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
            ),
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
                CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            ),
        }
        acknowledged_rows = {
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageEvidenceKind.NORMALIZATION_FAILURE,
            ),
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.SOURCE_EVENT_CONFLICT,
                CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
            ),
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.SOURCE_SEQUENCE_BREAK,
                CoverageEvidenceKind.SOURCE_SEQUENCE,
            ),
        }
        rejected_scope_ids = set(fanout.identified_rejection_scope_ids)
        acknowledged_scope_ids = set(fanout.acknowledged_routed_scope_ids)
        for request in requests:
            row = (
                request.requested_status,
                request.transition_reason,
                request.evidence.kind,
            )
            if row in outcome_rows:
                continue
            if request.scope.coverage_scope_id in acknowledged_scope_ids:
                if row not in acknowledged_rows:
                    raise ValueError(
                        "acknowledged partition requires exact indexed failure evidence."
                    )
                continue
            source = request.evidence.source
            if (
                request.scope.coverage_scope_id not in rejected_scope_ids
                or row
                != (
                    CoverageStatus.CONFIRMED_INCOMPLETE,
                    CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                    CoverageEvidenceKind.NORMALIZATION_FAILURE,
                )
                or type(source) is not NormalizationFailureEvidenceSource
                or source.raw_event_index is None
                or source.category is not NormalizationFailureCategory.PROVENANCE_MISMATCH
            ):
                raise ValueError(
                    "identified rejection requires exact provenance-mismatch failure evidence."
                )
        return
    if fanout.kind in {
        CoverageFanoutKind.EXACT_ROUTED_EVENT,
        CoverageFanoutKind.EXACT_ROUTED_EVENTS,
    }:
        exact_routed_allowed_rows = {
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageEvidenceKind.NORMALIZATION_FAILURE,
            ),
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.SOURCE_EVENT_CONFLICT,
                CoverageEvidenceKind.SOURCE_EVENT_CONFLICT,
            ),
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.SOURCE_SEQUENCE_BREAK,
                CoverageEvidenceKind.SOURCE_SEQUENCE,
            ),
            (
                CoverageStatus.UNCERTAIN,
                CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
                CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
            ),
            (
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
                CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            ),
        }
        if any(
            (
                request.requested_status,
                request.transition_reason,
                request.evidence.kind,
            )
            not in exact_routed_allowed_rows
            for request in requests
        ):
            raise ValueError("exact-routed-event fanout requires exact in-scope failure evidence.")
        return
    if fanout.kind in {
        CoverageFanoutKind.ONE_POSSIBLY_DELIVERED_SPEC,
        CoverageFanoutKind.POSSIBLY_DELIVERED_SPECS,
    }:
        if any(
            request.scope.domain is not CoverageDomain.BRONZE_INGRESS
            or request.requested_status is not CoverageStatus.UNCERTAIN
            or request.transition_reason is not CoverageReason.TRANSPORT_AMBIGUITY
            or request.evidence.kind is not CoverageEvidenceKind.TRANSPORT_FAILURE
            for request in requests
        ):
            raise ValueError("possibly-delivered fanout permits only Bronze transport uncertainty.")
        return
    if fanout.kind is CoverageFanoutKind.ALL_POSSIBLY_ACTIVE:
        possibly_active_allowed_rows = {
            (
                CoverageDomain.BRONZE_INGRESS,
                CoverageStatus.UNCERTAIN,
                CoverageReason.TRANSPORT_AMBIGUITY,
                CoverageEvidenceKind.TRANSPORT_FAILURE,
            ),
            (
                CoverageDomain.BRONZE_INGRESS,
                CoverageStatus.UNCERTAIN,
                CoverageReason.RAW_ACCEPTANCE_UNCERTAIN,
                CoverageEvidenceKind.RAW_SINK_ACCEPTANCE_AMBIGUITY,
            ),
            (
                CoverageDomain.BRONZE_INGRESS,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.RAW_DEFINITE_REJECTION,
                CoverageEvidenceKind.RAW_RECORD_REJECTION,
            ),
            (
                CoverageDomain.BRONZE_INGRESS,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.SOURCE_SEQUENCE_BREAK,
                CoverageEvidenceKind.SOURCE_SEQUENCE,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.IN_SCOPE_NORMALIZATION_FAILURE,
                CoverageEvidenceKind.NORMALIZATION_FAILURE,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.UNCERTAIN,
                CoverageReason.NORMALIZATION_OUTCOME_ACCEPTANCE_UNCERTAIN,
                CoverageEvidenceKind.NORMALIZATION_OUTCOME_SINK_ACCEPTANCE_AMBIGUITY,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.NORMALIZATION_OUTCOME_DEFINITE_REJECTION,
                CoverageEvidenceKind.NORMALIZATION_OUTCOME_REJECTION,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.SOURCE_SEQUENCE_BREAK,
                CoverageEvidenceKind.SOURCE_SEQUENCE,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.UNCERTAIN,
                CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
                CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
                CoverageEvidenceKind.UPSTREAM_COVERAGE_TRANSITION,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.UNCERTAIN,
                CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
                CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
            ),
            (
                CoverageDomain.SILVER_NORMALIZATION,
                CoverageStatus.CONFIRMED_INCOMPLETE,
                CoverageReason.UPSTREAM_COVERAGE_DEGRADED,
                CoverageEvidenceKind.UPSTREAM_COVERAGE_STATE,
            ),
        }
        if any(
            (
                request.scope.domain,
                request.requested_status,
                request.transition_reason,
                request.evidence.kind,
            )
            not in possibly_active_allowed_rows
            for request in requests
        ):
            raise ValueError("possibly-active fanout has an incompatible cause/status matrix.")
        return
    raise ValueError("coverage fanout kind has no closed mutation semantics.")


@dataclass(frozen=True, slots=True, init=False)
class CoverageMutationBatch:
    """Prepared all-target compare-and-swap mutation with no partial representation.

    Content preimage::

        ["coverage-mutation-batch-content-v1", fanout_proof_id,
         raw-coverage-fanout-binding-id-or-null,
         sorted_expected_pre-state_rows, sorted_initialization_ids,
         sorted_transition_ids, sorted_no-op_rows,
         sorted_resulting_state-reference_ids]

    Bounded ID preimage::

        ["coverage-mutation-batch-v1", fanout_proof_id, target_count,
         SHA256(canonical_content)]
    """

    fanout_proof: CoverageFanoutProof = field(repr=False)
    raw_fanout_binding: RawCoverageFanoutBinding | None = field(repr=False)
    expected_pre_state_rows: tuple[tuple[str, str | None], ...] = field(repr=False)
    initializations: tuple[CoverageInitialization, ...] = field(repr=False)
    transitions: tuple[CoverageTransition, ...] = field(repr=False)
    no_ops: tuple[CoverageMutationNoOp, ...] = field(repr=False)
    resulting_state_references: tuple[CoverageStateReference, ...] = field(repr=False)
    canonical_content: str = field(repr=False)
    content_sha256: str
    coverage_mutation_batch_id: CoverageMutationBatchId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use prepare_coverage_mutation_batch().")

    def verify_compare_and_swap(
        self,
        current_state_references: tuple[CoverageStateReference, ...],
    ) -> tuple[CoverageStateReference, ...]:
        """Return prepared results only when every current CAS token still matches."""

        rows = _coverage_pre_state_rows(self.fanout_proof, current_state_references)
        if rows != self.expected_pre_state_rows:
            raise ValueError(
                "coverage compare-and-swap pre-state does not match the prepared batch."
            )
        return self.resulting_state_references

    def verify_stored(
        self,
        *,
        expected_canonical_content: str,
        expected_batch_id: CoverageMutationBatchId,
    ) -> None:
        """Reject any persisted prepared-batch content or bounded-ID mismatch."""

        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        if type(expected_batch_id) is not CoverageMutationBatchId:
            raise TypeError("expected_batch_id must be a CoverageMutationBatchId.")
        if (
            self.canonical_content != expected_canonical_content
            or self.coverage_mutation_batch_id != expected_batch_id
        ):
            raise ValueError("stored coverage mutation batch does not match its content.")


def _coverage_pre_state_rows(
    fanout: CoverageFanoutProof,
    states: tuple[CoverageStateReference, ...],
) -> tuple[tuple[str, str | None], ...]:
    if type(states) is not tuple:
        raise TypeError("current_state_references must be a built-in tuple.")
    if any(type(item) is not CoverageStateReference for item in states):
        raise TypeError("current_state_references contain an invalid value.")
    by_scope = {item.reference.scope.coverage_scope_id: item for item in states}
    if len(by_scope) != len(states):
        raise ValueError("current coverage state references must be unique by scope.")
    target_ids = {item.coverage_scope_id for item in fanout.target_scopes}
    if any(item not in target_ids for item in by_scope):
        raise ValueError("current coverage state contains a scope outside the fanout proof.")
    return tuple(
        (
            scope.coverage_scope_id.value,
            (
                by_scope[scope.coverage_scope_id].coverage_state_reference_id.value
                if scope.coverage_scope_id in by_scope
                else None
            ),
        )
        for scope in fanout.target_scopes
    )


def prepare_coverage_mutation_batch(
    *,
    fanout_proof: CoverageFanoutProof,
    current_state_references: tuple[CoverageStateReference, ...],
    requests: tuple[RequestedCoverageMutation, ...],
    raw_fanout_binding: RawCoverageFanoutBinding | None = None,
) -> CoverageMutationBatch:
    """Prepare one complete deterministic CAS batch for a cause-derived target slice."""

    if type(fanout_proof) is not CoverageFanoutProof:
        raise TypeError("fanout_proof must be a CoverageFanoutProof.")
    if type(requests) is not tuple:
        raise TypeError("requests must be a built-in tuple.")
    if any(type(item) is not RequestedCoverageMutation for item in requests):
        raise TypeError("requests contain an invalid value.")
    require_collection_size(
        requests,
        field_name="requests",
        maximum_items=MAX_COVERAGE_MUTATION_TARGETS,
    )
    expected_scope_ids = tuple(item.coverage_scope_id for item in fanout_proof.target_scopes)
    request_scope_ids = tuple(item.scope.coverage_scope_id for item in requests)
    if request_scope_ids != expected_scope_ids:
        raise ValueError(
            "coverage mutation requests must cover every fanout target in exact order."
        )
    session_components = parse_canonical_json_array(
        fanout_proof.connection_session_id.value,
        field_name="connection_session_id",
    )
    fanout_run_id = CollectorRunId(
        _component_text(session_components[1], field_name="collector_run_id")
    )
    if any(request.epoch.collector_run_id != fanout_run_id for request in requests):
        raise ValueError("coverage mutation epochs must belong to the fanout collector run.")
    for request in requests:
        _validate_mutation_request_semantics(request)
    _validate_fanout_request_semantics(fanout_proof, requests)
    _validate_fanout_evidence_lineage(fanout_proof, requests)
    _validate_raw_fanout_binding(
        fanout=fanout_proof,
        requests=requests,
        raw_fanout_binding=raw_fanout_binding,
    )
    pre_rows = _coverage_pre_state_rows(fanout_proof, current_state_references)
    states_by_scope = {
        item.reference.scope.coverage_scope_id: item for item in current_state_references
    }
    initializations: list[CoverageInitialization] = []
    transitions: list[CoverageTransition] = []
    no_ops: list[CoverageMutationNoOp] = []
    resulting: list[CoverageStateReference] = []
    for request in requests:
        current = states_by_scope.get(request.scope.coverage_scope_id)
        if current is None:
            initialization = CoverageInitialization(
                request.scope,
                request.epoch,
                request.requested_status,
                request.initial_reason,
                request.evidence,
            )
            initializations.append(initialization)
            resulting.append(CoverageStateReference.from_initialization(initialization))
            continue
        if current.reference.epoch != request.epoch:
            raise ValueError("coverage mutation cannot cross a state epoch.")
        current_severity = _coverage_status_severity(current.reference.status)
        requested_severity = _coverage_status_severity(request.requested_status)
        if requested_severity <= current_severity:
            no_op = CoverageMutationNoOp(current, request)
            no_ops.append(no_op)
            resulting.append(current)
            continue
        transition, updated = reduce_coverage(
            current.reference,
            RequestedCoverageTransition(
                scope=request.scope,
                epoch=request.epoch,
                previous_status=current.reference.status,
                next_transition_ordinal=current.reference.transition_ordinal + 1,
                requested_status=request.requested_status,
                reason=request.transition_reason,
            ),
            request.evidence,
        )
        transitions.append(transition)
        resulting.append(
            CoverageStateReference.from_transition(
                previous=current,
                transition=transition,
                resulting_reference=updated,
            )
        )
    ordered_initializations = tuple(
        sorted(initializations, key=lambda item: item.scope.coverage_scope_id.value)
    )
    ordered_transitions = tuple(
        sorted(transitions, key=lambda item: item.scope.coverage_scope_id.value)
    )
    ordered_no_ops = tuple(
        sorted(no_ops, key=lambda item: item.request.scope.coverage_scope_id.value)
    )
    ordered_results = tuple(
        sorted(resulting, key=lambda item: item.reference.scope.coverage_scope_id.value)
    )
    content = canonical_json_array(
        (
            "coverage-mutation-batch-content-v1",
            fanout_proof.coverage_fanout_proof_id,
            (
                raw_fanout_binding.raw_coverage_fanout_binding_id
                if raw_fanout_binding is not None
                else None
            ),
            pre_rows,
            tuple(item.coverage_initialization_id.value for item in ordered_initializations),
            tuple(item.coverage_transition_id.value for item in ordered_transitions),
            tuple(item.canonical_row for item in ordered_no_ops),
            tuple(item.coverage_state_reference_id.value for item in ordered_results),
        ),
        maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
    )
    digest = sha256_hex(content.encode("utf-8"), field_name="coverage mutation batch")
    batch_id = CoverageMutationBatchId(
        canonical_json_array(
            (
                "coverage-mutation-batch-v1",
                fanout_proof.coverage_fanout_proof_id,
                len(fanout_proof.target_scopes),
                digest,
            )
        )
    )
    batch = object.__new__(CoverageMutationBatch)
    object.__setattr__(batch, "fanout_proof", fanout_proof)
    object.__setattr__(batch, "raw_fanout_binding", raw_fanout_binding)
    object.__setattr__(batch, "expected_pre_state_rows", pre_rows)
    object.__setattr__(batch, "initializations", ordered_initializations)
    object.__setattr__(batch, "transitions", ordered_transitions)
    object.__setattr__(batch, "no_ops", ordered_no_ops)
    object.__setattr__(batch, "resulting_state_references", ordered_results)
    object.__setattr__(batch, "canonical_content", content)
    object.__setattr__(batch, "content_sha256", digest)
    object.__setattr__(batch, "coverage_mutation_batch_id", batch_id)
    return batch


@dataclass(frozen=True, slots=True, init=False)
class CoverageCommitAcceptance:
    """Content-addressed proof that one complete prepared CAS was committed.

    Canonical content::

        ["coverage-commit-acceptance-content-v1", mutation_batch_id,
         sorted_resulting_state_reference_ids]

    The factory name is intentionally post-CAS: a prepared batch or candidate
    state reference alone is never proof of runtime commit.
    """

    coverage_mutation_batch_id: CoverageMutationBatchId
    resulting_state_reference_ids: tuple[CoverageStateReferenceId, ...]
    canonical_content: str = field(repr=False)
    content_sha256: str
    coverage_commit_acceptance_id: CoverageCommitAcceptanceId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use CoverageCommitAcceptance.after_compare_and_swap() or .from_stored().")

    @classmethod
    def _verified_value(
        cls,
        coverage_mutation_batch_id: CoverageMutationBatchId,
        resulting_state_reference_ids: tuple[CoverageStateReferenceId, ...],
    ) -> Self:
        if type(coverage_mutation_batch_id) is not CoverageMutationBatchId:
            raise TypeError("coverage_mutation_batch_id must be a CoverageMutationBatchId.")
        if type(resulting_state_reference_ids) is not tuple or any(
            type(item) is not CoverageStateReferenceId for item in resulting_state_reference_ids
        ):
            raise TypeError("resulting_state_reference_ids contain an invalid value.")
        require_collection_size(
            resulting_state_reference_ids,
            field_name="resulting_state_reference_ids",
            maximum_items=MAX_COVERAGE_MUTATION_TARGETS,
        )
        expected = tuple(sorted(set(resulting_state_reference_ids), key=lambda item: item.value))
        if resulting_state_reference_ids != expected:
            raise ValueError("resulting state reference IDs must be sorted and unique.")
        content = canonical_json_array(
            (
                "coverage-commit-acceptance-content-v1",
                coverage_mutation_batch_id,
                tuple(item.value for item in resulting_state_reference_ids),
            ),
            maximum_length=MAX_SUBSCRIPTION_PLAN_CONTENT_LENGTH,
        )
        digest = sha256_hex(content.encode("utf-8"), field_name="coverage commit acceptance")
        acceptance_id = CoverageCommitAcceptanceId(
            canonical_json_array(
                (
                    "coverage-commit-acceptance-v1",
                    coverage_mutation_batch_id,
                    len(resulting_state_reference_ids),
                    digest,
                )
            )
        )
        value = object.__new__(cls)
        object.__setattr__(value, "coverage_mutation_batch_id", coverage_mutation_batch_id)
        object.__setattr__(value, "resulting_state_reference_ids", resulting_state_reference_ids)
        object.__setattr__(value, "canonical_content", content)
        object.__setattr__(value, "content_sha256", digest)
        object.__setattr__(value, "coverage_commit_acceptance_id", acceptance_id)
        return value

    @classmethod
    def after_compare_and_swap(
        cls,
        *,
        batch: CoverageMutationBatch,
        committed_state_references: tuple[CoverageStateReference, ...],
    ) -> Self:
        """Create commit proof only after CAS returned the exact prepared results."""

        if type(batch) is not CoverageMutationBatch:
            raise TypeError("batch must be a CoverageMutationBatch.")
        if type(committed_state_references) is not tuple or any(
            type(item) is not CoverageStateReference for item in committed_state_references
        ):
            raise TypeError("committed_state_references contain an invalid value.")
        if committed_state_references != batch.resulting_state_references:
            raise ValueError("committed states must exactly match the prepared CAS results.")
        return cls._verified_value(
            batch.coverage_mutation_batch_id,
            tuple(
                sorted(
                    (item.coverage_state_reference_id for item in committed_state_references),
                    key=lambda item: item.value,
                )
            ),
        )

    @classmethod
    def from_stored(
        cls,
        *,
        batch: CoverageMutationBatch,
        coverage_mutation_batch_id: CoverageMutationBatchId,
        resulting_state_reference_ids: tuple[CoverageStateReferenceId, ...],
        expected_canonical_content: str,
        expected_acceptance_id: CoverageCommitAcceptanceId,
    ) -> Self:
        """Verify a persisted acceptance against the complete prepared result set."""

        value = cls._verified_value(
            coverage_mutation_batch_id,
            resulting_state_reference_ids,
        )
        value.verify_for(batch)
        if type(expected_canonical_content) is not str:
            raise TypeError("expected_canonical_content must be a built-in string.")
        if type(expected_acceptance_id) is not CoverageCommitAcceptanceId:
            raise TypeError("expected_acceptance_id must be a CoverageCommitAcceptanceId.")
        if (
            value.canonical_content != expected_canonical_content
            or value.coverage_commit_acceptance_id != expected_acceptance_id
        ):
            raise ValueError("stored coverage commit acceptance does not match its content.")
        return value

    def verify_for(self, batch: CoverageMutationBatch) -> None:
        if type(batch) is not CoverageMutationBatch:
            raise TypeError("batch must be a CoverageMutationBatch.")
        expected_ids = tuple(
            sorted(
                (item.coverage_state_reference_id for item in batch.resulting_state_references),
                key=lambda item: item.value,
            )
        )
        if (
            self.coverage_mutation_batch_id != batch.coverage_mutation_batch_id
            or self.resulting_state_reference_ids != expected_ids
        ):
            raise ValueError("coverage mutation acceptance does not echo the exact batch results.")


@dataclass(frozen=True, slots=True, init=False)
class CommittedCoverageState:
    """One exact state reference proven present in a CAS commit acceptance."""

    state_reference: CoverageStateReference = field(repr=False)
    commit_acceptance: CoverageCommitAcceptance = field(repr=False)
    committed_coverage_state_id: CommittedCoverageStateId

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("use CommittedCoverageState.from_commit() or .from_stored().")

    @classmethod
    def from_commit(
        cls,
        *,
        state_reference: CoverageStateReference,
        commit_acceptance: CoverageCommitAcceptance,
    ) -> Self:
        if type(state_reference) is not CoverageStateReference:
            raise TypeError("state_reference must be a CoverageStateReference.")
        if type(commit_acceptance) is not CoverageCommitAcceptance:
            raise TypeError("commit_acceptance must be a CoverageCommitAcceptance.")
        if (
            state_reference.coverage_state_reference_id
            not in commit_acceptance.resulting_state_reference_ids
        ):
            raise ValueError("coverage state was not committed by the supplied acceptance.")
        committed_id = CommittedCoverageStateId(
            canonical_json_array(
                (
                    "committed-coverage-state-v1",
                    state_reference.coverage_state_reference_id,
                    commit_acceptance.coverage_commit_acceptance_id,
                )
            )
        )
        value = object.__new__(cls)
        object.__setattr__(value, "state_reference", state_reference)
        object.__setattr__(value, "commit_acceptance", commit_acceptance)
        object.__setattr__(value, "committed_coverage_state_id", committed_id)
        return value

    @classmethod
    def from_stored(
        cls,
        *,
        state_reference: CoverageStateReference,
        commit_acceptance: CoverageCommitAcceptance,
        expected_committed_state_id: CommittedCoverageStateId,
    ) -> Self:
        value = cls.from_commit(
            state_reference=state_reference,
            commit_acceptance=commit_acceptance,
        )
        if type(expected_committed_state_id) is not CommittedCoverageStateId:
            raise TypeError("expected_committed_state_id must be a CommittedCoverageStateId.")
        if value.committed_coverage_state_id != expected_committed_state_id:
            raise ValueError("stored committed coverage state does not match its commit proof.")
        return value


# Backward-compatible draft name retained for collaborating modules written in parallel.
CoverageMutationCommitAcceptance = CoverageCommitAcceptance


@dataclass(frozen=True, slots=True)
class EventCoverage:
    """Exactly ingress and normalization coverage known at materialization."""

    bronze_ingress: CommittedCoverageState
    silver_normalization: CommittedCoverageState

    def __post_init__(self) -> None:
        if type(self.bronze_ingress) is not CommittedCoverageState:
            raise TypeError("bronze_ingress must be a CommittedCoverageState.")
        if type(self.silver_normalization) is not CommittedCoverageState:
            raise TypeError("silver_normalization must be a CommittedCoverageState.")
        ingress_reference = self.bronze_ingress.state_reference.reference
        normalization_reference = self.silver_normalization.state_reference.reference
        if ingress_reference.scope.domain is not CoverageDomain.BRONZE_INGRESS:
            raise ValueError("bronze_ingress must reference BRONZE_INGRESS coverage.")
        if normalization_reference.scope.domain is not CoverageDomain.SILVER_NORMALIZATION:
            raise ValueError("silver_normalization must reference SILVER_NORMALIZATION coverage.")
        ingress_scope = ingress_reference.scope
        normalization_scope = normalization_reference.scope
        if (
            ingress_reference.epoch.collector_run_id
            != normalization_reference.epoch.collector_run_id
        ):
            raise ValueError("event coverage references must belong to one collector run.")
        if (
            ingress_scope.feed_product_id,
            ingress_scope.subscription_spec_ids,
            ingress_scope.canonical_instrument_ids,
            ingress_scope.event_family,
            ingress_scope.event_family_schema_version,
            ingress_scope.payload_type,
        ) != (
            normalization_scope.feed_product_id,
            normalization_scope.subscription_spec_ids,
            normalization_scope.canonical_instrument_ids,
            normalization_scope.event_family,
            normalization_scope.event_family_schema_version,
            normalization_scope.payload_type,
        ):
            raise ValueError("event coverage references must describe the same event scope.")
        if _coverage_status_severity(normalization_reference.status) < _coverage_status_severity(
            ingress_reference.status
        ):
            raise ValueError("Silver event coverage cannot be better than its Bronze ingress.")
        if (
            normalization_reference.transition_ordinal == 0
            and type(normalization_reference.initial_evidence.source)
            is UpstreamCoverageStateEvidenceSource
            and normalization_reference.initial_evidence.source.committed_state
            != self.bronze_ingress
        ):
            raise ValueError(
                "initial Silver degradation must cite the exact committed Bronze ingress."
            )
        latest_normalization_transition = (
            self.silver_normalization.state_reference.latest_transition
        )
        if (
            latest_normalization_transition is not None
            and type(latest_normalization_transition.evidence.source)
            is UpstreamCoverageTransitionEvidenceSource
            and latest_normalization_transition.evidence.source.committed_state
            != self.bronze_ingress
        ):
            raise ValueError(
                "Silver upstream transition must cite the exact committed Bronze ingress."
            )


@dataclass(frozen=True, slots=True)
class DeliveryAttemptIdentity:
    """Legacy v1 locators for one proposed delivery attempt and ordered key batch.

    These byte-stable locators prove neither queue acceptance nor a delivery
    outcome.  The closed commit/failure contracts live in ``market_event_v3``.

    Batch-content preimage: ``["delivery-batch-content-v1", [materialization texts]]``.
    Bounded batch ID preimage: ``["delivery-batch-v1", item_count, content_sha256]``.
    Attempt ID preimage: ``["delivery-attempt-v1", destination_id,
    delivery_batch_id, attempt_ordinal]``.
    """

    destination_id: str
    materialization_key_canonical_texts: tuple[str, ...]
    attempt_ordinal: int
    delivery_batch_content_sha256: str = field(init=False)
    delivery_batch_id: DeliveryBatchId = field(init=False)
    delivery_attempt_id: DeliveryAttemptId = field(init=False)

    def __post_init__(self) -> None:
        destination = require_code(self.destination_id, field_name="destination_id")
        if type(self.materialization_key_canonical_texts) is not tuple:
            raise TypeError("materialization_key_canonical_texts must be a built-in tuple.")
        if not self.materialization_key_canonical_texts:
            raise ValueError("a delivery batch must contain at least one materialization key.")
        require_collection_size(
            self.materialization_key_canonical_texts,
            field_name="materialization_key_canonical_texts",
            maximum_items=MAX_DELIVERY_BATCH_ITEMS,
            minimum_items=1,
        )
        for materialization in self.materialization_key_canonical_texts:
            _canonical_materialization_key_component(
                materialization,
                field_name="materialization_key_canonical_text",
            )
        ordinal = require_nonnegative_int(self.attempt_ordinal, field_name="attempt_ordinal")
        batch_content = canonical_json_array(
            ("delivery-batch-content-v1", self.materialization_key_canonical_texts)
        ).encode("utf-8")
        batch_content_sha256 = sha256_hex(
            batch_content,
            field_name="delivery_batch_content",
        )
        batch_id = DeliveryBatchId(
            canonical_json_array(
                (
                    "delivery-batch-v1",
                    len(self.materialization_key_canonical_texts),
                    batch_content_sha256,
                )
            )
        )
        object.__setattr__(self, "delivery_batch_content_sha256", batch_content_sha256)
        object.__setattr__(self, "delivery_batch_id", batch_id)
        object.__setattr__(
            self,
            "delivery_attempt_id",
            DeliveryAttemptId(
                canonical_json_array(("delivery-attempt-v1", destination, batch_id, ordinal))
            ),
        )
