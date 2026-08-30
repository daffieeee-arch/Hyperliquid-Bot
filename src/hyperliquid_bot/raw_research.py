"""Small exact-payload boundary shared by the bounded research captures."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

RAW_RESEARCH_SCHEMA_VERSION: Final = 1


class MessageDirection(StrEnum):
    """Direction of an application payload or locally generated marker."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    LOCAL = "local"


class FrameType(StrEnum):
    """WebSocket application frame kind, with a separate local-marker value."""

    TEXT = "text"
    BINARY = "binary"
    MARKER = "marker"


class PayloadEncoding(StrEnum):
    """How payload bytes may be interpreted without changing those bytes."""

    UTF8 = "utf-8"
    BINARY = "binary"
    UTF8_JSON = "utf-8-json"


@dataclass(frozen=True, slots=True)
class CapturedApplicationPayload:
    """Application payload and clocks captured before any parsing."""

    received_utc_ns: int
    received_monotonic_ns: int
    frame_type: FrameType
    payload_encoding: PayloadEncoding
    payload_bytes: bytes


@dataclass(frozen=True, slots=True)
class RawResearchRecord:
    """Flat DATA-1A row; deliberately not a canonical/provenance contract."""

    schema_version: int
    venue: str
    product: str
    channel: str
    session_id: str
    message_ordinal: int
    received_utc_ns: int
    received_monotonic_ns: int
    direction: MessageDirection
    frame_type: FrameType
    payload_encoding: PayloadEncoding
    payload_bytes: bytes

    def __post_init__(self) -> None:
        if self.schema_version != RAW_RESEARCH_SCHEMA_VERSION:
            raise ValueError("Unsupported raw research schema version.")
        for field_name in ("venue", "product", "channel", "session_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"{field_name} must be non-empty text without outer whitespace.")
        if type(self.message_ordinal) is not int or self.message_ordinal <= 0:
            raise ValueError("message_ordinal must be a positive integer.")
        for field_name in ("received_utc_ns", "received_monotonic_ns"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer.")
        if type(self.direction) is not MessageDirection:
            raise TypeError("direction must be a MessageDirection.")
        if type(self.frame_type) is not FrameType:
            raise TypeError("frame_type must be a FrameType.")
        if type(self.payload_encoding) is not PayloadEncoding:
            raise TypeError("payload_encoding must be a PayloadEncoding.")
        if type(self.payload_bytes) is not bytes:
            raise TypeError("payload_bytes must be immutable bytes.")

    @property
    def payload_sha256(self) -> str:
        """Digest used to verify the exact BLOB after Parquet round trips."""

        return hashlib.sha256(self.payload_bytes).hexdigest()


class RawResearchSink(Protocol):
    """Only the persistence surface needed by DATA-1A and the next L3 slice."""

    async def append(self, record: RawResearchRecord) -> None: ...

    async def aclose(self) -> None: ...


NanosecondClock = Callable[[], int]


def capture_application_payload(
    frame: str | bytes,
    *,
    utc_ns: NanosecondClock = time.time_ns,
    monotonic_ns: NanosecondClock = time.monotonic_ns,
) -> CapturedApplicationPayload:
    """Capture callback-entry clocks and immutable bytes before parsing a frame."""

    captured_utc_ns = utc_ns()
    captured_monotonic_ns = monotonic_ns()
    if type(frame) is str:
        return CapturedApplicationPayload(
            received_utc_ns=captured_utc_ns,
            received_monotonic_ns=captured_monotonic_ns,
            frame_type=FrameType.TEXT,
            payload_encoding=PayloadEncoding.UTF8,
            payload_bytes=frame.encode("utf-8"),
        )
    if type(frame) is bytes:
        return CapturedApplicationPayload(
            received_utc_ns=captured_utc_ns,
            received_monotonic_ns=captured_monotonic_ns,
            frame_type=FrameType.BINARY,
            payload_encoding=PayloadEncoding.BINARY,
            payload_bytes=bytes(frame),
        )
    raise TypeError("WebSocket application frames must be built-in str or bytes values.")
