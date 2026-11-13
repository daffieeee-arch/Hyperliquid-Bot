from __future__ import annotations

import copy
import random
import statistics
from decimal import Decimal
from typing import Any

import pytest

from research import micro_response as model
from research.micro_flow import SECOND, MicroEvent, Pressure, _book
from research.micro_response_io import Observation
from research.micro_response_stats import Summary

S = SECOND
UTC = 1_789_164_300_000_000_000  # Fixed synthetic receipt clock, five minutes after an hour.


def book(at: float, price: str = "10000", *, native_offset_ms: int = 0) -> Observation:
    mono = int(at * S)
    utc = UTC + mono
    value = Decimal(price)
    event = MicroEvent(
        mono,
        utc,
        utc // 1_000_000 + native_offset_ms,
        "book",
        "s",
        value,
        value + 1,
        Decimal(3),
        Decimal(1),
    )
    return Observation(1, mono, utc, "bbo", f"b-{mono}", "a" * 64, mono, event, level_counts=(1, 1))


def trade(at: float) -> Observation:
    mono = int(at * S)
    utc = UTC + mono
    event = MicroEvent(
        mono,
        utc,
        utc // 1_000_000,
        "trade",
        "s",
        trade_price=Decimal(10000),
        trade_quantity=Decimal(1),
        trade_side=1,
    )
    return Observation(1, mono, utc, "trades", f"t-{mono}", "b" * 64, mono, event)


def runner(end: int = 500) -> tuple[model.Window, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    metadata = {"id": 1, "start_mono_ns": 0, "end_mono_ns": end * S, "start_utc_ns": UTC}
    funding = (UTC // 1_000_000 - 3_600_000, UTC // 1_000_000 + 3_600_000)
    return model.Window(
        metadata, "synthetic", "development", funding, ((funding[0], funding[-1]),), rows.append
    ), rows


def armed(
    monkeypatch: pytest.MonkeyPatch, end: int = 500
) -> tuple[model.Window, list[dict[str, Any]]]:
    # Isolate observation scheduling from the existing separately tested alpha.
    monkeypatch.setattr(Pressure, "signal", lambda self, now, current, strength, benchmark: 1)
    window, rows = runner(end)
    window.receive(book(0))
    window.receive(trade(0))
    window.receive(book(130))
    return window, rows


def test_four_frozen_feature_policies_and_no_target_or_horizon_selector() -> None:
    assert [(span, strength) for _, span, strength in model.POLICIES] == [
        (30, 0.2),
        (30, 0.4),
        (120, 0.2),
        (120, 0.4),
    ]
    assert model.HORIZONS == (1, 5, 30, 120, 300)
    assert set(model.COSTS) == {"base", "stress"}
    assert model.ENTRY_WAIT == S and model.LABEL_WAIT == 15 * S


def test_charged_unit_opportunity_uses_ask_bid_fees_and_adverse_grid() -> None:
    opening, closing = _book(book(0).event), _book(book(1, "10020").event)  # type: ignore[arg-type]
    # Independently calculate Decimal prices at this grid; no quantity or margin.
    long_entry, long_exit = Decimal(10003), Decimal(10018)
    expected = long_exit - long_entry - Decimal("0.00045") * (long_entry + long_exit)
    assert model.charged_price_bps(opening, closing, 1, "base") == pytest.approx(
        float(expected / long_entry * 10000)
    )
    assert model.charged_price_bps(opening, closing, 1, "stress") < model.charged_price_bps(
        opening, closing, 1, "base"
    )
    assert model.charged_price_bps(opening, closing, -1, "base") < 0


def test_pre_delay_quote_never_becomes_entry_and_horizon_begins_at_actual_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, rows = armed(monkeypatch)
    window.receive(book(130.4))
    assert not [r for r in rows if r["kind"] == "entry" and r["status"] == "observed"]
    window.receive(book(130.6))
    window.receive(book(131.1))
    entries = [r for r in rows if r["kind"] == "entry" and r["status"] == "observed"]
    assert [r["entry"]["mono_ns"] for r in entries] == [int(130.6 * S), int(131.1 * S)]
    window.receive(book(131.5))
    assert not [r for r in rows if r["kind"] == "label" and r["status"] == "observed"]
    window.receive(book(131.9, "10020"))
    label = next(r for r in rows if r["kind"] == "label" and r["status"] == "observed")
    assert label["cost"] == "base" and label["horizon_seconds"] == 1
    assert label["label_due_ns"] == int(131.6 * S)
    assert label["actual_horizon_ns"] == int(1.3 * S)
    assert label["label_wait_ns"] == int(0.3 * S)
    assert label["entry"]["source_id"] != label["label"]["source_id"]


@pytest.mark.parametrize("cost,at", [("base", 131.5), ("stress", 132.0)])
def test_entry_wait_inclusive_boundary_accepts_new_observation(
    monkeypatch: pytest.MonkeyPatch,
    cost: str,
    at: float,
) -> None:
    window, rows = armed(monkeypatch)
    window.receive(book(at))
    row = next(r for r in rows if r["kind"] == "entry" and r["cost"] == cost)
    assert row["status"] == "observed"


def test_unavailable_entry_and_future_labels_are_preserved_in_denominators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, rows = armed(monkeypatch)
    window.receive(book(133))
    assert sum(r["kind"] == "entry" and r["status"] == "unavailable_quote" for r in rows) == 2
    window.finish()
    summary = Summary()
    for row in rows:
        summary.consume(row)
    cells = summary.result()
    assert cells and all(c["counts"]["scheduled"] == 50 for c in cells)
    assert all(c["metrics_bps"]["charged_price_bps"]["observations"] == 0 for c in cells)


@pytest.mark.parametrize("extra,status", [(0, "observed"), (1, "unavailable_quote")])
def test_future_label_wait_is_fifteen_seconds_not_entry_wait(
    monkeypatch: pytest.MonkeyPatch,
    extra: int,
    status: str,
) -> None:
    window, rows = armed(monkeypatch)
    window.receive(book(130.5))
    window.receive(book(146.5 + extra / S))
    row = next(
        r
        for r in rows
        if r["kind"] == "label" and r["cost"] == "base" and r["horizon_seconds"] == 1
    )
    assert row["status"] == status


def test_short_window_and_future_boundary_are_visible_not_flat_winners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, rows = armed(monkeypatch, 150)
    window.receive(book(130.5))
    purged = [r for r in rows if r["kind"] == "label" and r["status"] == "boundary_purged"]
    assert {r["horizon_seconds"] for r in purged} == {5, 30, 120, 300}
    window.finish()
    summary = Summary()
    for row in rows:
        summary.consume(row)
    assert all(c["counts"]["scheduled"] == 15 for c in summary.result())


def test_heartbeat_liveness_does_not_renew_quote_receipt_freshness() -> None:
    window, rows = runner(30)
    window.receive(book(0))
    window.receive(trade(0))
    window.receive(Observation(1, 20 * S, UTC + 20 * S, "pong", "pong", "f" * 64, 20 * S))
    row = next(r for r in rows if r["decision_mono_ns"] == 20 * S)
    assert set(row["reasons"].values()) == {"stale_quote"}
    assert row["last_inbound_age_ns"] == 0 and row["quote_receipt_age_ns"] == 20 * S


def test_future_observation_cannot_change_an_earlier_decision() -> None:
    a, rows_a = runner(50)
    b, rows_b = runner(50)
    for window in (a, b):
        window.receive(book(0))
        window.receive(trade(0))
        window.receive(book(30))
    b.receive(book(31, "11000"))
    assert [r for r in rows_a if r["kind"] == "decision"] == [
        r for r in rows_b if r["kind"] == "decision"
    ]


def test_older_native_book_cannot_replace_latest_book() -> None:
    window, _ = runner()
    window.receive(book(0))
    window.receive(book(1, "20000", native_offset_ms=-2000))
    assert window.old_native_books == 1
    assert window.observation is not None and window.observation.source_id == "b-0"


def test_funding_intersection_is_only_flagged_and_cash_stays_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, rows = armed(monkeypatch)
    window.funding = (*window.funding, (UTC + 131 * S) // 1_000_000)
    window.funding = tuple(sorted(window.funding))
    window.receive(book(130.5))
    window.receive(book(131.5))
    row = next(r for r in rows if r["kind"] == "label" and r["status"] == "observed")
    assert row["funding_event_within_padded_interval"] is True
    assert row["funding_cash"] is None and row["capacity_certified"] is False
    assert row["price_opportunity_only"] is True
    assert not any("margin" in key or "profit" in key for key in row)


def test_disjoint_funding_requests_do_not_certify_the_union_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _ = armed(monkeypatch)
    middle = UTC // 1_000_000
    window.funding_ranges = (
        (middle - 3_600_000, middle - 100_000),
        (middle + 1_000_000, middle + 3_600_000),
    )
    window.receive(book(130.5))
    with pytest.raises(Exception, match="one qualified native funding request range"):
        window.receive(book(131.5))


@pytest.mark.parametrize(
    "setting", ["MAX_RING_ITEMS", "MAX_RING_BYTES", "MAX_PENDING", "MAX_PENDING_BYTES"]
)
def test_memory_admission_bounds_fail_instead_of_evicting(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
) -> None:
    window, _ = armed(monkeypatch)
    monkeypatch.setattr(model, setting, 0)
    with pytest.raises(Exception, match="bound"):
        window.bound()


def sample_row(day: int, value: float, *, momentum: int = -1) -> list[dict[str, Any]]:
    directions = {p[0]: 1 for p in model.POLICIES}
    common = {
        "group": "development",
        "capture": f"cap-{day}",
        "directions": directions,
        "momentum_direction": momentum,
    }
    return [
        {**common, "kind": "decision", "reasons": {p: "long" for p in directions}},
        {**common, "kind": "entry", "status": "observed", "cost": "base"},
        *[
            {
                **common,
                "kind": "label",
                "status": "observed",
                "cost": "base",
                "horizon_seconds": h,
                "entry": {"utc_ns": day * 86_400_000_000_000},
                "gross_mid_bps": value,
                "charged_long_price_bps": value - 11,
                "charged_short_price_bps": -value - 11,
                "funding_event_within_padded_interval": False,
            }
            for h in model.HORIZONS
        ],
        {**common, "kind": "entry", "status": "unavailable_quote", "cost": "stress"},
    ]


@pytest.mark.parametrize("days,available", [(4, False), (5, True)])
def test_day_cluster_threshold_and_literal_shared_indices(days: int, available: bool) -> None:
    summary = Summary()
    for day in range(days):
        for row in sample_row(day, float(day * day)):
            summary.consume(row)
    cell = next(
        c
        for c in summary.result()
        if c["group"] == "development" and c["cost"] == "base" and c["horizon_seconds"] == 300
    )
    metric = cell["metrics_bps"]["charged_price_bps"]
    assert metric["day_cluster_interval"]["available"] is available
    if available:
        rng = random.Random(20261113)
        samples = sorted(
            statistics.mean((rng.randrange(days) ** 2 - 11) for _ in range(days))
            for _ in range(2000)
        )
        assert metric["day_cluster_interval"]["lower_95"] == samples[49]
        assert metric["day_cluster_interval"]["upper_95"] == samples[1949]
        assert cell["metrics_bps"]["vs_long_bps"]["day_cluster_interval"]["lower_95"] == 0


def test_missing_momentum_day_does_not_silently_select_other_cluster_cohort() -> None:
    summary = Summary()
    for day in range(5):
        for row in sample_row(day, float(day), momentum=0 if day == 0 else -1):
            summary.consume(row)
    cell = next(c for c in summary.result() if c["group"] == "development" and c["cost"] == "base")
    assert cell["metrics_bps"]["charged_price_bps"]["day_cluster_interval"]["available"]
    assert not cell["metrics_bps"]["vs_momentum_bps"]["day_cluster_interval"]["available"]
    assert cell["counts"]["momentum_unavailable"] == 1


def test_missing_label_cannot_be_counted_as_zero_return() -> None:
    summary = Summary()
    rows = sample_row(0, 100)
    for row in rows:
        row = copy.deepcopy(row)
        if row["kind"] == "label":
            row["status"] = "unavailable_quote"
        summary.consume(row)
    cell = next(c for c in summary.result() if c["group"] == "development" and c["cost"] == "base")
    assert cell["metrics_bps"]["charged_price_bps"]["event_weighted_mean"] is None
    assert cell["counts"]["label_unavailable_quote"] == 1


def test_incomplete_denominator_fails_closed() -> None:
    summary = Summary()
    summary.consume(sample_row(0, 1)[0])
    with pytest.raises(Exception, match="every signal"):
        summary.result()
