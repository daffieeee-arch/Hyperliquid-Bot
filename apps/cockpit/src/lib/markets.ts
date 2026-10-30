import { presentCopiedText } from "./display";
import type { InstrumentTape, MarketTapeResponse, VenueMarketTape } from "./market-tape-types";
import { formatAgeSeconds, secondsBetween } from "./poll-state";
import type { PublicBtcPerpPrice } from "./public-price";
import type { PaperPnl, VenueCaptureChip, VenueCaptureChipStatus } from "./types";

export const MARKET_QUOTE_UNAVAILABLE = "UNAVAILABLE";
export const MARKET_BBO_NOT_IN_COCKPIT_APIS =
  "no stored trade or BBO for this run yet · prices are not invented";
export const MARKET_PUBLIC_MID_SOURCE = "hyperliquid-public-info-allMids";
export const MARKET_SOAK_MARK_SOURCE =
  "paper-pnl.json mark_price · soak mark, not live mid, not venue PnL";
export const MARKET_STORED_TAPE_SOURCE = "stored capture";

export type MarketQuoteKind = "stored-tape" | "unavailable" | "soak-mark";

/** Freshness of the quote itself, independent of the capture chip status. */
export type MarketQuoteState = "ok" | "stale" | "unavailable";

export type MarketQuoteRead = {
  at: string | undefined;
  age: string;
  state: MarketQuoteState;
  reason: string | undefined;
};

export type MarketRow = {
  id: VenueCaptureChip["id"] | "soak-mark";
  venue: string;
  /** Contract product from the capture strip (`BTCUSDT`, `BTC-EUR`, `BTC/USD`, `BTC-PERP`). */
  product: string;
  /** Instrument the quote was taken from (`BTCUSDT-SPOT`); the contract product when none. */
  instrument: string;
  last: string;
  mid: string;
  lastQuote: MarketQuoteRead;
  midQuote: MarketQuoteRead;
  quoteKind: MarketQuoteKind;
  quoteSource: string;
  /** Oldest available Last/Mid source time (UTC ISO); each field retains its own clock. */
  quoteAt: string | undefined;
  /** Age of `quoteAt` relative to the backend read, formatted; "n/a" when unknown. */
  quoteAge: string;
  quoteState: MarketQuoteState;
  /** Honest reason when a value is missing or stale. */
  quoteReason: string | undefined;
  captureStatus: VenueCaptureChipStatus | undefined;
  lastPartAge: string;
  runId: string;
  /** Raw binding identity; presentation escaping must not affect matching. */
  boundRunId: string | undefined;
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
    instrument: venue.product,
    last: MARKET_QUOTE_UNAVAILABLE,
    mid: MARKET_QUOTE_UNAVAILABLE,
    lastQuote: unavailableQuote("no stored trade decoded yet"),
    midQuote: unavailableQuote("no stored BBO decoded yet"),
    quoteKind: "unavailable",
    quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
    quoteAt: undefined,
    quoteAge: "n/a",
    quoteState: "unavailable",
    quoteReason:
      venue.status === "MISSING"
        ? `capture ${venue.status} · ${venue.status_detail}`
        : "no stored trade or BBO decoded yet",
    captureStatus: venue.status,
    lastPartAge: venue.last_part_age,
    runId: presentCopiedText(venue.run_id),
    boundRunId: venue.run_id,
    live: venue.live,
    tone: venue.tone,
  };
}

/**
 * One fail-closed row per bound venue.
 *
 * Prices are filled later from the stored capture. The public Hyperliquid
 * `/info` mid is context for the chart only and is never written onto a row.
 */
export function marketsRowsFromBoundVenues(venues: readonly VenueCaptureChip[]): MarketRow[] {
  return venues.map((venue) => boundInstrumentRow(venue));
}

type ParsedDecimal = { negative: boolean; digits: bigint; scale: number };

function parseDecimal(raw: string): ParsedDecimal | undefined {
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(raw.trim());
  if (match === null) {
    return undefined;
  }
  const fraction = match[3] ?? "";
  return {
    negative: match[1] === "-",
    digits: BigInt(`${match[2] ?? "0"}${fraction}`),
    scale: fraction.length,
  };
}

function signedScaled(value: ParsedDecimal, scale: number): bigint {
  const scaled = value.digits * 10n ** BigInt(scale - value.scale);
  return value.negative ? -scaled : scaled;
}

function formatScaled(value: bigint, scale: number): string {
  const negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const fraction = digits.slice(digits.length - scale);
  const body = scale === 0 ? whole : `${whole}.${fraction}`;
  return negative ? `-${body}` : body;
}

/**
 * (bid + ask) / 2 in exact decimal string arithmetic.
 *
 * Keeps the finer of the two scales and adds one digit only when the sum is
 * odd, so `93801.0` / `93813.0` → `93807.0` and `1.1` / `1.2` → `1.15`. No
 * float rounding, no invented precision.
 */
export function decimalMidpoint(bid: string, ask: string): string | undefined {
  const parsedBid = parseDecimal(bid);
  const parsedAsk = parseDecimal(ask);
  if (parsedBid === undefined || parsedAsk === undefined) {
    return undefined;
  }
  const scale = Math.max(parsedBid.scale, parsedAsk.scale);
  const sum = signedScaled(parsedBid, scale) + signedScaled(parsedAsk, scale);
  if (sum % 2n === 0n) {
    return formatScaled(sum / 2n, scale);
  }
  return formatScaled(sum * 5n, scale + 1);
}

/**
 * Choose the instrument that represents the bound contract product.
 *
 * Collectors key instruments by their own product strings (`BTCUSDT-SPOT`
 * for the Binance `BTCUSDT` contract, `BTC/USD`, `BTC-EUR`, `BTC-PERP`), so
 * only an exact match or the explicit `-SPOT` variant is accepted. A tick
 * from another instrument or quote currency cannot stand in for the contract.
 */
export function pickQuoteInstrument(
  venue: Pick<VenueMarketTape, "instruments" | "contractProduct">,
  contractProduct: string,
): InstrumentTape | undefined {
  const candidates = [contractProduct, venue.contractProduct];
  const byProduct = (predicate: (instrument: InstrumentTape) => boolean) =>
    venue.instruments.find(predicate);
  return (
    byProduct((item) => candidates.includes(item.product)) ??
    byProduct((item) => candidates.some((product) => item.product === `${product}-SPOT`))
  );
}

function oldestIso(...values: (string | undefined)[]): string | undefined {
  return values
    .filter((value): value is string => value !== undefined)
    .sort()
    .at(0);
}

function unavailableQuote(reason: string): MarketQuoteRead {
  return { at: undefined, age: "n/a", state: "unavailable", reason };
}

function quoteRead(
  label: string,
  value: string | undefined,
  at: string | undefined,
  observedAt: string,
  freshMaxS: number,
): MarketQuoteRead {
  if (value === undefined) return unavailableQuote(`no ${label} decoded yet`);
  const ageS = secondsBetween(at, observedAt);
  if (ageS === undefined) return unavailableQuote(`${label} source timestamp is unavailable`);
  const stale = ageS > freshMaxS;
  return {
    at,
    age: formatAgeSeconds(ageS),
    state: stale ? "stale" : "ok",
    reason: stale
      ? `${label} is ${formatAgeSeconds(ageS)} old (fresh ≤ ${String(freshMaxS)}s)`
      : undefined,
  };
}

function storedSource(venue: VenueMarketTape, instrument: InstrumentTape): string {
  const channels = instrument.channelsSeen.length > 0 ? instrument.channelsSeen.join("+") : "n/a";
  return `${MARKET_STORED_TAPE_SOURCE} · run ${presentCopiedText(venue.runId)} · ${channels}`;
}

function unavailableStoredRow(row: MarketRow, quoteReason: string): MarketRow {
  return {
    ...row,
    last: MARKET_QUOTE_UNAVAILABLE,
    mid: MARKET_QUOTE_UNAVAILABLE,
    lastQuote: unavailableQuote(quoteReason),
    midQuote: unavailableQuote(quoteReason),
    quoteKind: "unavailable",
    quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
    quoteAt: undefined,
    quoteAge: "n/a",
    quoteState: "unavailable",
    quoteReason,
  };
}

function applyStoredVenue(
  row: MarketRow,
  venue: VenueMarketTape,
  observedAt: string,
  freshMaxS: number,
): MarketRow {
  if (venue.status !== "ok") {
    return unavailableStoredRow(
      row,
      venue.status === "missing"
        ? `stored data missing · ${venue.error ?? venue.note}`
        : `stored data unreadable · ${venue.error ?? venue.note}`,
    );
  }
  if (row.boundRunId === undefined || venue.runId !== row.boundRunId) {
    return unavailableStoredRow(
      row,
      "stored tape run does not match the bound capture run; waiting for matching reads",
    );
  }
  if (venue.contractProduct !== row.product) {
    return unavailableStoredRow(
      row,
      "stored tape product does not match the bound capture product",
    );
  }
  const instrument = pickQuoteInstrument(venue, row.product);
  if (instrument === undefined) {
    return unavailableStoredRow(row, `no instrument decoded yet · ${venue.note}`);
  }
  const last = instrument.lastTrade?.price;
  const storedMid =
    instrument.lastBbo === undefined
      ? undefined
      : decimalMidpoint(instrument.lastBbo.bid, instrument.lastBbo.ask);
  if (last === undefined && storedMid === undefined) {
    return unavailableStoredRow(
      {
        ...row,
        instrument: instrument.product,
      },
      `no trade or BBO decoded yet in run ${presentCopiedText(venue.runId)} · channels ${instrument.channelsSeen.join(", ") || "none"}`,
    );
  }
  const lastQuote = quoteRead("trade", last, instrument.lastTrade?.at, observedAt, freshMaxS);
  const midQuote =
    storedMid === undefined && instrument.bboUnavailableReason !== undefined
      ? unavailableQuote(instrument.bboUnavailableReason)
      : quoteRead("BBO", storedMid, instrument.lastBbo?.at, observedAt, freshMaxS);
  const quoteAt = oldestIso(lastQuote.at, midQuote.at);
  const ageS = secondsBetween(quoteAt, observedAt);
  const stale = lastQuote.state === "stale" || midQuote.state === "stale";
  const available = lastQuote.state !== "unavailable" || midQuote.state !== "unavailable";
  return {
    ...row,
    instrument: instrument.product,
    last:
      lastQuote.state === "unavailable"
        ? MARKET_QUOTE_UNAVAILABLE
        : (last ?? MARKET_QUOTE_UNAVAILABLE),
    mid:
      midQuote.state === "unavailable"
        ? MARKET_QUOTE_UNAVAILABLE
        : (storedMid ?? MARKET_QUOTE_UNAVAILABLE),
    lastQuote,
    midQuote,
    quoteKind: "stored-tape",
    quoteSource: storedSource(venue, instrument),
    quoteAt,
    quoteAge: formatAgeSeconds(ageS),
    quoteState: stale ? "stale" : available ? "ok" : "unavailable",
    quoteReason: stale
      ? [lastQuote.reason, midQuote.reason].filter((reason) => reason !== undefined).join(" · ")
      : !available
        ? "stored quote source timestamps are unavailable"
        : last === undefined
          ? "no trade decoded yet · mid from stored BBO"
          : storedMid === undefined
            ? "no BBO decoded yet · last from stored trades"
            : undefined,
  };
}

/**
 * Fill Last / Mid from the stored market tape for every bound venue, including HL.
 *
 * Last is the newest stored trade. Mid is the stored BBO midpoint in exact
 * decimal arithmetic. A missing trade or BBO stays UNAVAILABLE — the last is
 * never copied into the mid, and the public `/info` mid is never used here.
 * One unreadable venue does not blank the others.
 */
export function applyStoredTapeQuote(
  rows: readonly MarketRow[],
  tape: MarketTapeResponse,
  freshMaxS: number,
): MarketRow[] {
  if (!tape.ok) {
    return rows.map((row) =>
      unavailableStoredRow(row, `stored market data unavailable · ${tape.error}`),
    );
  }
  return rows.map((row) => {
    const venue = tape.tape.venues.find((item) => item.id === row.id);
    if (venue === undefined) {
      return unavailableStoredRow(row, "stored venue is missing from this tape read");
    }
    try {
      return applyStoredVenue(row, venue, tape.tape.observed_at, freshMaxS);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "unreadable stored quote";
      return unavailableStoredRow(row, `stored quote unreadable · ${message}`);
    }
  });
}

export function soakMarkRow(pnl: PaperPnl | undefined): MarketRow | undefined {
  if (pnl === undefined) {
    return undefined;
  }
  return {
    id: "soak-mark",
    venue: "PAPER",
    product: pnl.instrument_id,
    instrument: pnl.instrument_id,
    last: MARKET_QUOTE_UNAVAILABLE,
    mid: pnl.mark_price,
    lastQuote: unavailableQuote("soak mark is not a trade"),
    midQuote: unavailableQuote("soak mark source timestamp is not recorded"),
    quoteKind: "soak-mark",
    quoteSource: MARKET_SOAK_MARK_SOURCE,
    quoteAt: undefined,
    quoteAge: "n/a",
    quoteState: "ok",
    quoteReason: undefined,
    captureStatus: undefined,
    lastPartAge: "n/a",
    runId: "n/a",
    boundRunId: undefined,
    live: false,
    tone: "warn",
  };
}
