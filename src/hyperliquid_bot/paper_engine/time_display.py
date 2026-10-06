"""Human-facing timestamps in Europe/Amsterdam.

Machine fields stay UTC. Display labels use the civil abbreviation for the
offset in force at that instant: CEST (UTC+2) or CET (UTC+1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

AMSTERDAM_TZ = ZoneInfo("Europe/Amsterdam")
TIMEZONE_NAME = "Europe/Amsterdam"


def require_utc(value: datetime, *, field_name: str) -> datetime:
    """Return an aware UTC datetime. Naive values are rejected."""

    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def datetime_to_utc_ns(value: datetime, *, field_name: str) -> int:
    """Convert an aware datetime to integer UTC nanoseconds without float rounding."""

    utc = require_utc(value, field_name=field_name)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = utc - epoch
    return (
        delta.days * 86_400_000_000_000 + delta.seconds * 1_000_000_000 + delta.microseconds * 1_000
    )


def format_utc(value: datetime, *, field_name: str) -> str:
    """Serialize UTC with microsecond precision and a Z suffix."""

    utc = require_utc(value, field_name=field_name)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def amsterdam_zone_label(value: datetime, *, field_name: str) -> str:
    """Return CEST or CET for the Europe/Amsterdam offset at ``value``."""

    local = require_utc(value, field_name=field_name).astimezone(AMSTERDAM_TZ)
    offset = local.utcoffset()
    if offset == timedelta(hours=2):
        return "CEST"
    if offset == timedelta(hours=1):
        return "CET"
    raise ValueError(f"{field_name} does not map to CET or CEST in Europe/Amsterdam.")


def format_amsterdam(value: datetime, *, field_name: str) -> str:
    """Format ``YYYY-MM-DD HH:MM:SS CEST`` or ``CET`` in Europe/Amsterdam."""

    local = require_utc(value, field_name=field_name).astimezone(AMSTERDAM_TZ)
    label = amsterdam_zone_label(value, field_name=field_name)
    return local.strftime("%Y-%m-%d %H:%M:%S ") + label
