import type { CaptureBindingSource, VenueCaptureChip } from "./types";

export type TapeSide = "buy" | "sell" | "unknown";

export type TapeTrade = {
  /** Event time when the venue reports one, otherwise the capture receive time (UTC ISO). */
  at: string;
  price: string;
  size: string;
  side: TapeSide;
};

export type TapeBbo = {
  at: string;
  bid: string;
  bidSize: string | undefined;
  ask: string;
  askSize: string | undefined;
  /** ask − bid in quote units, as a decimal string. */
  spread: string;
  /** spread / mid × 10 000, rounded to 2 decimals. */
  spreadBps: string;
};

export type TapeContext = {
  at: string;
  markPrice?: string;
  indexPrice?: string;
  oraclePrice?: string;
  midPrice?: string;
  fundingRate?: string;
  openInterest?: string;
};

export type InstrumentKind = "perpetual" | "spot" | "unknown";

/**
 * Everything the reader knows about one captured product.
 *
 * Instruments are keyed by the product string exactly as the collector wrote
 * it (`BTCUSDT-SPOT`, `BTCUSDT-USDS-M-PERPETUAL`, `BTC-PERP`, `BTC-EUR`,
 * `BTC/USD`), so spot, perpetual and different quote currencies never merge.
 */
export type InstrumentTape = {
  product: string;
  kind: InstrumentKind;
  base: string;
  quote: string;
  tradeCount: number;
  bboCount: number;
  contextCount: number;
  lastTrade: TapeTrade | undefined;
  lastBbo: TapeBbo | undefined;
  lastContext: TapeContext | undefined;
  /** Newest first, bounded. */
  recentTrades: TapeTrade[];
  lastEventUtc: string | undefined;
  channelsSeen: string[];
};

export type MarketTapeStatus = "ok" | "missing" | "error";

export type MarketTapeParts = {
  published: number;
  processed: number;
  /** Older parts deliberately not parsed on a cold start; counted, not read. */
  skippedOnColdStart: number;
  bytes: number;
  newestPartName: string | undefined;
  newestPartMtimeUtc: string | undefined;
};

export type VenueMarketTape = {
  id: VenueCaptureChip["id"];
  chip: VenueCaptureChip["chip"];
  series: VenueCaptureChip["series"];
  venue: string;
  contractProduct: string;
  runId: string | undefined;
  bindingSource: CaptureBindingSource;
  status: MarketTapeStatus;
  error: string | undefined;
  parts: MarketTapeParts;
  instruments: InstrumentTape[];
  /** Max receive time across processed parts. */
  lastEventUtc: string | undefined;
  /** observed_at − lastEventUtc, whole seconds. */
  lastEventAgeS: number | undefined;
  /** newest part mtime − last event inside it: how long the writer held data before publishing. */
  publicationLagS: number | undefined;
  note: string;
  observedAt: string;
};

export type MarketTapeStrip = {
  observed_at: string;
  venues: VenueMarketTape[];
  cache: {
    runsCached: number;
    partsParsedThisCall: number;
  };
};

export type MarketTapeResponse = { ok: true; tape: MarketTapeStrip } | { ok: false; error: string };

export const MARKET_TAPE_RECENT_TRADES = 12;

/** Writers publish a part after ≤60s or 5 000 records; lag under that is normal. */
export const MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S = 60;
