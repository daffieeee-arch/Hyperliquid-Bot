"""Projection counterexamples independent of financial strategy outcomes."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from research import regime_io as io
from research.harness.errors import IntegrityError
from research.repaired_accounting import Frame
from research.two_models_io import BTC, HOUR, MINUTE


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    start, end = 1000 * HOUR, 1002 * HOUR
    funding = start + 30 * MINUTE + 15
    frames = [
        Frame(t, {BTC: 100.0}, {BTC: 0.0}, {BTC: 99.0}) for t in range(start, end + 1, MINUTE)
    ]
    frames.append(Frame(funding, {BTC: 107.0}, {BTC: 0.0001}, {}))
    frames.sort(key=lambda f: f.timestamp_ms)
    split = start + HOUR
    hours = {t: 100.0 for t in range(start - 720 * HOUR, split + 1, HOUR)}
    bounds = {t: (99.0, 101.0) for t in range(start + MINUTE, end + 1, MINUTE)}
    parts: list[Any] = [
        (
            "first",
            [f for f in frames if f.timestamp_ms <= split],
            hours,
            {t: b for t, b in bounds.items() if t <= split},
            frozenset(),
        ),
        (
            "second",
            [
                replace(f, marks={}, execution_prices={}) if f.timestamp_ms == split else f
                for f in frames
                if f.timestamp_ms >= split
            ],
            {t: 100.0 for t in range(split - 80 * HOUR, end + 1, HOUR)},
            {t: b for t, b in bounds.items() if t > split},
            frozenset(),
        ),
    ]
    body = b'{"synthetic_source_projection":true}'
    path = tmp_path / "input.json"
    path.write_bytes(body)
    monkeypatch.setattr(io, "INPUT_SHA256", hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(io, "START", start)
    monkeypatch.setattr(io, "END", end)
    monkeypatch.setattr(io, "INTERVALS", ((start, end),))
    monkeypatch.setattr(io, "check_index", lambda *a, **kw: {})
    monkeypatch.setattr(io, "funding_censuses", lambda body: ({funding: (0.0001, 107)}, ()))
    monkeypatch.setattr(io, "daily_months", lambda body: iter(parts))
    return path, {"parts": parts, "start": start, "end": end, "funding": funding}


def test_hourly_projection_keeps_exact_settlement_and_complete_old_warmup(
    source: tuple[Path, dict[str, Any]],
) -> None:
    path, data = source
    result = io.load(path)
    segment = result.segments[0]
    assert len(result.hourly) == 723
    assert data["start"] - 720 * HOUR in result.hourly
    assert next(f for f in segment.frames if f.timestamp_ms == data["funding"]).marks == {BTC: 107}
    boundary = next(f for f in segment.frames if f.timestamp_ms == data["start"] + HOUR)
    assert boundary.execution_prices == {BTC: 99}
    assert len(segment.minute_extremes) == 120
    assert segment.spec(123).initial_equity == 123


def test_changed_input_cannot_reuse_original_binding(source: tuple[Path, dict[str, Any]]) -> None:
    path, _ = source
    path.write_bytes(b"{}")
    with pytest.raises(IntegrityError, match="pinned input"):
        io.load(path)


def test_new_missing_mark_minute_is_not_repaired(source: tuple[Path, dict[str, Any]]) -> None:
    path, data = source
    del data["parts"][0][3][data["start"] + 2 * MINUTE]
    with pytest.raises(IntegrityError, match="minute extrema"):
        io.load(path)


def test_missing_hourly_valuation_is_not_filled(source: tuple[Path, dict[str, Any]]) -> None:
    path, data = source
    data["parts"][1][1].pop()
    with pytest.raises(IntegrityError, match="endpoints"):
        io.load(path)


def test_settlement_cannot_disappear_in_hourly_projection(
    source: tuple[Path, dict[str, Any]],
) -> None:
    path, data = source
    data["parts"][0][1][:] = [f for f in data["parts"][0][1] if f.timestamp_ms != data["funding"]]
    with pytest.raises(IntegrityError, match="exact settlement"):
        io.load(path)


def test_entry_execution_reference_cannot_be_replaced_by_mark(
    source: tuple[Path, dict[str, Any]],
) -> None:
    path, data = source
    # A complete hourly point also requires a native trade reference.
    part = data["parts"][1][1]
    part[-1] = replace(part[-1], execution_prices={})
    with pytest.raises(IntegrityError, match="execution references"):
        io.load(path)


def test_month_warmup_loss_is_not_silently_accepted(source: tuple[Path, dict[str, Any]]) -> None:
    path, data = source
    del data["parts"][0][2][data["start"] - 720 * HOUR]
    with pytest.raises(IntegrityError, match="30-day completed-hour"):
        io.load(path)


def test_conflicting_complete_boundary_is_rejected(source: tuple[Path, dict[str, Any]]) -> None:
    path, data = source
    part = data["parts"][1][1]
    part[0] = replace(part[0], marks={BTC: 120}, execution_prices={BTC: 119})
    with pytest.raises(IntegrityError, match="shared month boundary"):
        io.load(path)
