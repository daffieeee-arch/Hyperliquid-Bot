"""Bounded public BTC capture through the existing venue-neutral v2 collector."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path

from fit_gates.d41_nautilus.fit_gate import (
    STALE_AFTER_SECONDS,
    assert_paper_boundary,
    captured_public_records_to_dataset,
    envelope_to_trade_tick,
    existing_btc_contract,
    prepare_new_output_directory,
    write_json,
)
from hyperliquid_bot.contracts import MARKET_EVENT_SCHEMA_VERSION, MarketEventEnvelope
from hyperliquid_bot.data_provenance import (
    CollectorRunId,
    NormalizationRunId,
    RawMarketDataRecord,
)
from hyperliquid_bot.hyperliquid_ws_client import (
    HyperliquidTradesCollector,
    HyperliquidTradesCollectorConfig,
)
from hyperliquid_bot.market_data_sinks import (
    NormalizationOutcomeAcceptance,
    RawRecordAcceptance,
    SinkDestinationId,
)
from hyperliquid_bot.market_event_v3 import NormalizationOutcome

COMMIT = "be51fba535af2956613f238bcdb0e213053952e9"


class VolatileRawSink:
    """Acknowledge Bronze ownership without claiming durable persistence."""

    def __init__(self) -> None:
        self._destination_id = SinkDestinationId("course1-fit-volatile-raw")
        self.accepted = 0
        self.closed = False

    @property
    def destination_id(self) -> SinkDestinationId:
        return self._destination_id

    async def accept(self, record: RawMarketDataRecord) -> RawRecordAcceptance:
        self.accepted += 1
        return RawRecordAcceptance(
            raw_record_id=record.raw_record_id,
            full_record_integrity_sha256=record.full_record_integrity_sha256,
            destination_id=self.destination_id,
        )

    async def aclose(self) -> None:
        self.closed = True


class VolatileOutcomeSink:
    """Acknowledge normalization without claiming durable persistence."""

    def __init__(self) -> None:
        self._destination_id = SinkDestinationId("course1-fit-volatile-outcome")
        self.accepted = 0
        self.closed = False

    @property
    def destination_id(self) -> SinkDestinationId:
        return self._destination_id

    async def accept(
        self,
        outcome: NormalizationOutcome,
    ) -> NormalizationOutcomeAcceptance:
        self.accepted += 1
        return NormalizationOutcomeAcceptance(
            normalization_outcome_id=outcome.normalization_outcome_id,
            destination_id=self.destination_id,
        )

    async def aclose(self) -> None:
        self.closed = True


async def capture(seconds: int, output_dir: Path) -> dict[str, object]:
    assert_paper_boundary(seconds=seconds)
    prepare_new_output_directory(output_dir)
    instrument = existing_btc_contract()
    raw_sink = VolatileRawSink()
    outcome_sink = VolatileOutcomeSink()
    collector = HyperliquidTradesCollector(
        HyperliquidTradesCollectorConfig(
            instruments=(instrument,),
            collector_version="course1-nautilus-fit-gate",
            collector_commit=COMMIT,
        ),
        collector_run_id=CollectorRunId("course1-nautilus-fit-public-btc-001"),
        normalization_run_id=NormalizationRunId("course1-nautilus-fit-normalization-001"),
        normalizer_version="hyperliquid-v2-fit-gate",
        normalizer_commit=COMMIT,
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
    )
    producer = asyncio.create_task(
        collector.run(),
        name="course1-hyperliquid-public-btc-collector",
    )
    events: list[MarketEventEnvelope] = []
    try:
        first_batch = await asyncio.wait_for(collector.receive_batch(), timeout=30.0)
        events.extend(first_batch)
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                events.extend(
                    await asyncio.wait_for(
                        collector.receive_batch(),
                        timeout=remaining,
                    )
                )
            except TimeoutError:
                break
    finally:
        if not producer.done():
            producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass

    if not events:
        raise RuntimeError("No public BTC perpetual trades were captured.")
    if not raw_sink.closed or not outcome_sink.closed:
        raise RuntimeError("Existing collector did not close both volatile sinks.")
    if any(event.schema_version != MARKET_EVENT_SCHEMA_VERSION for event in events):
        raise RuntimeError("Existing collector emitted a non-v2 event.")
    if any(event.instrument != instrument for event in events):
        raise RuntimeError("Existing collector escaped the one-instrument boundary.")

    accepted_events: list[MarketEventEnvelope] = []
    stale_events_rejected = 0
    invalid_precision_events_rejected = 0
    prior_ts_init: int | None = None
    for event in events:
        if event.is_gap:
            raise RuntimeError("Existing collector marked the bounded capture as gap-tainted.")
        age = Decimal(str((event.received_time - event.event_time).total_seconds()))
        if age < 0 or age > STALE_AFTER_SECONDS:
            stale_events_rejected += 1
            continue
        try:
            tick = envelope_to_trade_tick(event, prior_ts_init=prior_ts_init)
        except ValueError as error:
            if "increment" not in str(error):
                raise
            invalid_precision_events_rejected += 1
            continue
        accepted_events.append(event)
        prior_ts_init = tick.ts_init
    if len(accepted_events) < 12:
        raise RuntimeError(
            "Collector produced fewer than twelve events accepted by the fail-closed boundary."
        )

    dataset = captured_public_records_to_dataset(
        accepted_events,
        capture_metadata={
            "collector_route": (
                "HyperliquidTradesCollector -> MarketEventEnvelope-v2 -> envelope_to_trade_tick"
            ),
            "collector_commit": COMMIT,
            "capture_seconds_after_first_batch": seconds,
            "events_received_total": len(events),
            "stale_or_future_events_rejected": stale_events_rejected,
            "invalid_precision_events_rejected": invalid_precision_events_rejected,
            "accepted_event_count": len(accepted_events),
            "raw_records_accepted": raw_sink.accepted,
            "normalization_outcomes_accepted": outcome_sink.accepted,
            "volatile_sinks": True,
            "first_event_time": accepted_events[0].event_time.isoformat(),
            "last_event_time": accepted_events[-1].event_time.isoformat(),
            "collector_state_after_shutdown": collector.health.session_state.value,
            "sticky_gap": collector.health.sticky_gap,
        },
    )
    write_json(output_dir / "dataset.json", dataset)
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dataset = asyncio.run(capture(args.seconds, args.output_dir))
    print(
        {
            "event_count": dataset["event_count"],
            "events_sha256": dataset["events_sha256"],
            "capture_metadata": dataset["capture_metadata"],
        }
    )


if __name__ == "__main__":
    main()
