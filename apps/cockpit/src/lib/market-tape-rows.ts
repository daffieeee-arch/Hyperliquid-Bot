import { captureChipOrigin, marketTapeOrigin, type DataOrigin } from "./data-origin";
import type { DataState } from "./data-state";
import {
  MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S,
  type InstrumentTape,
  type MarketTapeResponse,
  type TapeSide,
  type VenueMarketTape,
} from "./market-tape-types";
import { clockLabel, formatAgeSeconds, secondsBetween } from "./poll-state";
import type { VenueCaptureChip, VenueCaptureStripResponse } from "./types";

export const TAPE_VALUE_UNAVAILABLE = "—";

/** One table row per captured instrument; spot and perpetual never share a row. */
export type InstrumentRow = {
  key: string;
  venue: VenueMarketTape["chip"];
  product: string;
  kind: InstrumentTape["kind"];
  quote: string;
  lastPrice: string;
  lastSize: string;
  lastSide: TapeSide | "—";
  lastTradeAt: string;
  bid: string;
  ask: string;
  spread: string;
  spreadBps: string;
  trades: number;
  lastEventAge: string;
};

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "n/a";
  }
  if (bytes < 1024) {
    return `${String(bytes)} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit] ?? "TB"}`;
}

export function instrumentRows(tape: VenueMarketTape, observedAt: string): InstrumentRow[] {
  return tape.instruments.map((instrument) => ({
    key: `${tape.id}:${instrument.product}`,
    venue: tape.chip,
    product: instrument.product,
    kind: instrument.kind,
    quote: instrument.quote,
    lastPrice: instrument.lastTrade?.price ?? TAPE_VALUE_UNAVAILABLE,
    lastSize: instrument.lastTrade?.size ?? TAPE_VALUE_UNAVAILABLE,
    lastSide: instrument.lastTrade?.side ?? "—",
    lastTradeAt: instrument.lastTrade === undefined ? "—" : clockLabel(instrument.lastTrade.at),
    bid: instrument.lastBbo?.bid ?? TAPE_VALUE_UNAVAILABLE,
    ask: instrument.lastBbo?.ask ?? TAPE_VALUE_UNAVAILABLE,
    spread: instrument.lastBbo?.spread ?? TAPE_VALUE_UNAVAILABLE,
    spreadBps: instrument.lastBbo?.spreadBps ?? TAPE_VALUE_UNAVAILABLE,
    trades: instrument.tradeCount,
    lastEventAge: formatAgeSeconds(secondsBetween(instrument.lastEventUtc, observedAt)),
  }));
}

export type VenueTapeSummary = {
  tape: VenueMarketTape;
  chip: VenueCaptureChip | undefined;
  origin: DataOrigin;
  /** Freshness of the stored data itself, independent of the read. */
  dataState: DataState;
  published: string;
  volume: string;
  newestPart: string;
  lastData: string;
  lastDataAge: string;
  publicationLag: string;
  publicationLagExceeded: boolean;
  processed: string;
};

/**
 * Per-venue card model: capture status from the strip, stored data from the
 * tape, both attributed to the same run id or flagged when they disagree.
 */
export function summariseVenueTape(
  tape: VenueMarketTape,
  strip: VenueCaptureStripResponse,
  freshMaxS: number,
): VenueTapeSummary {
  const chip = strip.ok ? strip.strip.venues.find((venue) => venue.id === tape.id) : undefined;
  const origin = marketTapeOrigin(tape, chip);
  const lagExceeded =
    tape.publicationLagS !== undefined &&
    tape.publicationLagS > MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S;
  let dataState: DataState;
  if (tape.status === "error") {
    dataState = "error";
  } else if (tape.status === "missing") {
    dataState = "missing";
  } else if (tape.lastEventAgeS === undefined) {
    dataState = "pending";
  } else if (tape.lastEventAgeS > freshMaxS && origin === "live") {
    dataState = "stale";
  } else {
    dataState = "ok";
  }
  return {
    tape,
    chip,
    origin,
    dataState,
    published: `${String(tape.parts.published)} part${tape.parts.published === 1 ? "" : "s"}`,
    volume: formatBytes(tape.parts.bytes),
    newestPart: clockLabel(tape.parts.newestPartMtimeUtc),
    lastData: clockLabel(tape.lastEventUtc),
    lastDataAge: formatAgeSeconds(tape.lastEventAgeS),
    publicationLag:
      tape.publicationLagS === undefined ? "n/a" : formatAgeSeconds(tape.publicationLagS),
    publicationLagExceeded: lagExceeded,
    processed:
      tape.parts.skippedOnColdStart > 0
        ? `${String(tape.parts.processed)} parsed · ${String(tape.parts.skippedOnColdStart)} older counted only`
        : `${String(tape.parts.processed)} parsed`,
  };
}

/** True when the strip and the tape bound different run ids for one venue. */
export function runIdDisagrees(summary: VenueTapeSummary): boolean {
  return (
    summary.chip?.run_id !== undefined &&
    summary.tape.runId !== undefined &&
    summary.chip.run_id !== summary.tape.runId
  );
}

export function venueTapeSummaries(
  tape: MarketTapeResponse,
  strip: VenueCaptureStripResponse,
): VenueTapeSummary[] {
  if (!tape.ok) {
    return [];
  }
  const freshMaxS = strip.ok ? strip.strip.fresh_max_s : 180;
  return tape.tape.venues.map((venue) => summariseVenueTape(venue, strip, freshMaxS));
}

export function newestTapeEvent(tape: MarketTapeResponse): string | undefined {
  if (!tape.ok) {
    return undefined;
  }
  return tape.tape.venues
    .map((venue) => venue.lastEventUtc)
    .filter((value): value is string => value !== undefined)
    .sort()
    .at(-1);
}

/** Screen-level origins for the capture strip, for the topbar and page heads. */
export function stripVenueOrigins(strip: VenueCaptureStripResponse): DataOrigin[] {
  return strip.ok ? strip.strip.venues.map(captureChipOrigin) : [];
}
