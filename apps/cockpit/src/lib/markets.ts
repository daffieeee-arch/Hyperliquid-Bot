import { presentCopiedText } from "./display";
import type { PublicBtcPerpPrice } from "./public-price";
import type { PaperPnl, VenueCaptureChip, VenueCaptureChipStatus } from "./types";

export const MARKET_QUOTE_UNAVAILABLE = "UNAVAILABLE";
export const MARKET_BBO_NOT_IN_COCKPIT_APIS =
  "BBO/last not in cockpit APIs · prices are not invented";
export const MARKET_PUBLIC_MID_SOURCE = "hyperliquid-public-info-allMids";
export const MARKET_SOAK_MARK_SOURCE =
  "paper-pnl.json mark_price · soak mark, not live mid, not venue PnL";

export type MarketQuoteKind = "public-mid" | "unavailable" | "soak-mark";

export type MarketRow = {
  id: VenueCaptureChip["id"] | "soak-mark";
  venue: string;
  product: string;
  quote: string;
  quoteKind: MarketQuoteKind;
  quoteSource: string;
  captureStatus: VenueCaptureChipStatus | undefined;
  lastPartAge: string;
  runId: string;
  live: boolean;
  tone: VenueCaptureChip["tone"] | "warn";
};

export type PublicMidState =
  | { status: "loading" }
  | { status: "ready"; price: PublicBtcPerpPrice }
  | { status: "error"; message: string };

function boundInstrumentRow(venue: VenueCaptureChip): MarketRow {
  return {
    id: venue.id,
    venue: venue.chip,
    product: venue.product,
    quote: MARKET_QUOTE_UNAVAILABLE,
    quoteKind: "unavailable",
    quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
    captureStatus: venue.status,
    lastPartAge: venue.last_part_age,
    runId: presentCopiedText(venue.run_id),
    live: venue.live,
    tone: venue.tone,
  };
}

export function applyPublicMid(row: MarketRow, mid: PublicMidState): MarketRow {
  if (row.id !== "hl") {
    return row;
  }
  switch (mid.status) {
    case "loading":
      return {
        ...row,
        quote: "—",
        quoteKind: "unavailable",
        quoteSource: "Fetching public /info…",
      };
    case "error":
      return {
        ...row,
        quote: MARKET_QUOTE_UNAVAILABLE,
        quoteKind: "unavailable",
        quoteSource: mid.message,
        tone: "warn",
      };
    case "ready":
      return {
        ...row,
        quote: mid.price.mid,
        quoteKind: "public-mid",
        quoteSource: mid.price.source,
      };
    default: {
      const exhaustive: never = mid;
      throw new Error(`Unhandled public mid state: ${String(exhaustive)}`);
    }
  }
}

export function marketsRowsFromBoundVenues(
  venues: readonly VenueCaptureChip[],
  mid: PublicMidState,
): MarketRow[] {
  return venues.map((venue) => applyPublicMid(boundInstrumentRow(venue), mid));
}

export function soakMarkRow(pnl: PaperPnl | undefined): MarketRow | undefined {
  if (pnl === undefined) {
    return undefined;
  }
  return {
    id: "soak-mark",
    venue: "PAPER",
    product: pnl.instrument_id,
    quote: pnl.mark_price,
    quoteKind: "soak-mark",
    quoteSource: MARKET_SOAK_MARK_SOURCE,
    captureStatus: undefined,
    lastPartAge: "n/a",
    runId: "n/a",
    live: false,
    tone: "warn",
  };
}
