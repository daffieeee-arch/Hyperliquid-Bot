"""Native fast-L2 subscription evidence and bounded snapshot shape checks.

Official subscriptions documentation (reviewed 2026-11-13) defines ``fast`` as
five levels rather than twenty slow levels. Data messages do not echo this
flag: a matching subscription acknowledgement is mandatory before treating a
fast book as market evidence. Neither depth nor cadence certifies order fills.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import cast

MAX_L2_MODE_PAYLOAD_BYTES = 64 * 1024


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate native L2 object field")
        result[name] = value
    return result


def _native_object(payload: bytes) -> dict[str, object] | None:
    if not payload or len(payload) > MAX_L2_MODE_PAYLOAD_BYTES:
        return None
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return cast(dict[str, object], value) if type(value) is dict else None


def l2_ack_payload_matches(payload: bytes, *, fast: bool) -> bool:
    document = _native_object(payload)
    return document is not None and l2_ack_mode_matches(document, fast=fast)


def fast_book_payload_error(payload: bytes, coins: tuple[str, ...]) -> str | None:
    # The general collector classifier preserves numeric tokens as strings.
    # Validate native types separately without changing historical parsing.
    document = _native_object(payload)
    if document is None:
        return "fast L2 native JSON is ambiguous, invalid or exceeds its byte bound"
    return fast_book_error(document, coins)


def l2_ack_mode_matches(document: dict[str, object], *, fast: bool) -> bool:
    """Accept only this unaggregated L2 mode, including server null defaults."""
    if document.get("channel") != "subscriptionResponse":
        return False
    data = document.get("data")
    if type(data) is not dict or data.get("method") != "subscribe":
        return False
    subscription = data.get("subscription")
    if type(subscription) is not dict or subscription.get("type") != "l2Book":
        return False
    allowed = {"type", "coin", "fast", "nSigFigs", "mantissa"}
    if set(subscription) - allowed:
        return False
    if subscription.get("nSigFigs") is not None or subscription.get("mantissa") is not None:
        return False
    if "fast" not in subscription:
        return not fast
    return type(subscription["fast"]) is bool and subscription["fast"] is fast


def fast_book_error(document: dict[str, object], coins: tuple[str, ...]) -> str | None:
    """Reject mode/shape drift; sparse and empty native books remain observable."""
    if document.get("channel") != "l2Book":
        return "fast L2 channel identity is invalid"
    data = document.get("data")
    if type(data) is not dict or data.get("coin") not in coins:
        return "fast L2 instrument is not requested"
    if type(data.get("time")) is not int or data["time"] < 0:
        return "fast L2 native timestamp is invalid"
    sides = data.get("levels")
    if type(sides) is not list or len(sides) != 2:
        return "fast L2 requires exactly two native sides"
    best: list[Decimal | None] = []
    for side_index, side in enumerate(sides):
        if type(side) is not list or len(side) > 5:
            return "fast L2 side exceeds five levels or is not an array"
        prices = []
        for level in side:
            if type(level) is not dict or type(level.get("n")) is not int or level["n"] <= 0:
                return "fast L2 native level/order count is invalid"
            numbers = []
            for name in ("px", "sz"):
                value = level.get(name)
                if type(value) is not str or not 0 < len(value) <= 128:
                    return "fast L2 price/size must be bounded decimal strings"
                try:
                    number = Decimal(value)
                except InvalidOperation:
                    return "fast L2 price/size decimal is invalid"
                if not number.is_finite() or number <= 0:
                    return "fast L2 price/size must be finite and positive"
                numbers.append(number)
            prices.append(numbers[0])
        if any((a <= b if side_index == 0 else a >= b) for a, b in pairwise(prices)):
            return "fast L2 native price levels are not strictly ordered"
        best.append(prices[0] if prices else None)
    if best[0] is not None and best[1] is not None and best[0] > best[1]:
        return "fast L2 native best prices are crossed"
    return None
