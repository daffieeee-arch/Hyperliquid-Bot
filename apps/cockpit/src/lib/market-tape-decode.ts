import {
  MARKET_TAPE_RECENT_TRADES,
  type InstrumentKind,
  type InstrumentTape,
  type TapeBbo,
  type TapeContext,
  type TapeSide,
  type TapeTrade,
} from "./market-tape-types";

/**
 * One raw_segment row as published by the capture writers.
 *
 * Only the columns the tape needs are read. `payload` is the UTF-8 JSON the
 * collector stored verbatim (inbound frame) or produced locally (marker).
 */
export type RawTapeRow = {
  venue: string;
  product: string;
  channel: string;
  direction: string;
  frame_type: string;
  received_utc_ns: bigint;
  payload: string;
};

type BookSide = Map<string, string>;

type InstrumentState = {
  tape: InstrumentTape;
  /** Kraken L2 top-of-book is reconstructed from snapshot + updates. */
  book: { bids: BookSide; asks: BookSide } | undefined;
  /** Bitvavo ticker sends partial updates; keep the last complete values. */
  ticker: { bid?: string; bidSize?: string; ask?: string; askSize?: string } | undefined;
};

export type VenueTapeState = {
  instruments: Map<string, InstrumentState>;
  lastEventNs: bigint | undefined;
  rows: number;
};

export function createVenueTapeState(): VenueTapeState {
  return { instruments: new Map(), lastEventNs: undefined, rows: 0 };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim() !== "") {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
}

function nsToIso(ns: bigint): string {
  return new Date(Number(ns / 1_000_000n)).toISOString();
}

function msToIso(value: unknown, fallback: string): string {
  const raw = typeof value === "string" ? Number(value) : value;
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw <= 0) {
    return fallback;
  }
  return new Date(raw).toISOString();
}

function isoOrFallback(value: unknown, fallback: string): string {
  if (typeof value !== "string") {
    return fallback;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : fallback;
}

/**
 * Classify the captured product string. The contract only fixes the base
 * asset; kind and quote are read from the venue's own product naming so a
 * USDT spot pair and a USDS-M perpetual never collapse into one row.
 */
export function classifyProduct(
  venue: string,
  product: string,
): { kind: InstrumentKind; base: string; quote: string } {
  const upper = product.toUpperCase();
  const perpetual = /PERP|SWAP|FUTURE/.test(upper);
  const kind: InstrumentKind = perpetual ? "perpetual" : "spot";
  if (venue === "hyperliquid") {
    return { kind: "perpetual", base: upper.replace(/-PERP$/, ""), quote: "USDC" };
  }
  const match = /^([A-Z]+?)[-/]?(USDT|USDC|USD|EUR|BTC)(?:[-/]|$)/.exec(upper);
  if (match !== null) {
    return { kind, base: match[1] ?? upper, quote: match[2] ?? "?" };
  }
  return { kind: perpetual ? "perpetual" : "unknown", base: upper, quote: "?" };
}

function instrument(state: VenueTapeState, venue: string, product: string): InstrumentState {
  const existing = state.instruments.get(product);
  if (existing !== undefined) {
    return existing;
  }
  const classified = classifyProduct(venue, product);
  const created: InstrumentState = {
    tape: {
      product,
      kind: classified.kind,
      base: classified.base,
      quote: classified.quote,
      tradeCount: 0,
      bboCount: 0,
      contextCount: 0,
      lastTrade: undefined,
      lastBbo: undefined,
      lastContext: undefined,
      recentTrades: [],
      lastEventUtc: undefined,
      channelsSeen: [],
    },
    book: undefined,
    ticker: undefined,
  };
  state.instruments.set(product, created);
  return created;
}

function noteChannel(entry: InstrumentState, channel: string, at: string): void {
  if (!entry.tape.channelsSeen.includes(channel)) {
    entry.tape.channelsSeen.push(channel);
    entry.tape.channelsSeen.sort();
  }
  if (entry.tape.lastEventUtc === undefined || at > entry.tape.lastEventUtc) {
    entry.tape.lastEventUtc = at;
  }
}

function pushTrade(entry: InstrumentState, trade: TapeTrade): void {
  entry.tape.tradeCount += 1;
  entry.tape.lastTrade = trade;
  entry.tape.recentTrades.unshift(trade);
  if (entry.tape.recentTrades.length > MARKET_TAPE_RECENT_TRADES) {
    entry.tape.recentTrades.length = MARKET_TAPE_RECENT_TRADES;
  }
}

/** Decimal spread as text without floating-point drift for typical tick sizes. */
export function computeSpread(bid: string, ask: string): { spread: string; spreadBps: string } {
  const bidValue = Number(bid);
  const askValue = Number(ask);
  if (!Number.isFinite(bidValue) || !Number.isFinite(askValue)) {
    return { spread: "n/a", spreadBps: "n/a" };
  }
  const decimals = Math.max(decimalsOf(bid), decimalsOf(ask));
  const spread = (askValue - bidValue).toFixed(decimals);
  const mid = (askValue + bidValue) / 2;
  const bps = mid > 0 ? (((askValue - bidValue) / mid) * 10_000).toFixed(2) : "n/a";
  return { spread, spreadBps: bps };
}

function decimalsOf(value: string): number {
  const index = value.indexOf(".");
  return index === -1 ? 0 : value.length - index - 1;
}

function setBbo(
  entry: InstrumentState,
  at: string,
  bid: string,
  ask: string,
  bidSize: string | undefined,
  askSize: string | undefined,
): void {
  const { spread, spreadBps } = computeSpread(bid, ask);
  const bbo: TapeBbo = { at, bid, bidSize, ask, askSize, spread, spreadBps };
  entry.tape.bboCount += 1;
  entry.tape.lastBbo = bbo;
}

function side(
  value: unknown,
  buyTokens: readonly string[],
  sellTokens: readonly string[],
): TapeSide {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (buyTokens.includes(normalized)) {
    return "buy";
  }
  if (sellTokens.includes(normalized)) {
    return "sell";
  }
  return "unknown";
}

function applyHyperliquid(
  entry: InstrumentState,
  row: RawTapeRow,
  payload: unknown,
  at: string,
): void {
  if (!isRecord(payload) || row.direction !== "inbound") {
    return;
  }
  if (row.channel === "trades" && Array.isArray(payload.data)) {
    for (const item of payload.data) {
      if (!isRecord(item)) {
        continue;
      }
      const price = text(item.px);
      const size = text(item.sz);
      if (price === undefined || size === undefined) {
        continue;
      }
      pushTrade(entry, {
        at: msToIso(item.time, at),
        price,
        size,
        side: side(item.side, ["b", "buy"], ["a", "sell"]),
      });
    }
    return;
  }
  if (row.channel === "bbo" && isRecord(payload.data) && Array.isArray(payload.data.bbo)) {
    const [bidLevel, askLevel] = payload.data.bbo;
    const bid = isRecord(bidLevel) ? text(bidLevel.px) : undefined;
    const ask = isRecord(askLevel) ? text(askLevel.px) : undefined;
    if (bid !== undefined && ask !== undefined) {
      setBbo(
        entry,
        msToIso(payload.data.time, at),
        bid,
        ask,
        isRecord(bidLevel) ? text(bidLevel.sz) : undefined,
        isRecord(askLevel) ? text(askLevel.sz) : undefined,
      );
    }
    return;
  }
  if (row.channel === "activeAssetCtx" && isRecord(payload.data) && isRecord(payload.data.ctx)) {
    const ctx = payload.data.ctx;
    const context: TapeContext = { at };
    const mark = text(ctx.markPx);
    const oracle = text(ctx.oraclePx);
    const mid = text(ctx.midPx);
    const funding = text(ctx.funding);
    const oi = text(ctx.openInterest);
    if (mark !== undefined) context.markPrice = mark;
    if (oracle !== undefined) context.oraclePrice = oracle;
    if (mid !== undefined) context.midPrice = mid;
    if (funding !== undefined) context.fundingRate = funding;
    if (oi !== undefined) context.openInterest = oi;
    entry.tape.contextCount += 1;
    entry.tape.lastContext = context;
  }
}

function isMarker(row: RawTapeRow): boolean {
  return row.direction === "local" && row.frame_type === "marker";
}

function applyBinance(entry: InstrumentState, row: RawTapeRow, payload: unknown, at: string): void {
  if (!isRecord(payload) || !isMarker(row)) {
    return;
  }
  if (row.channel === "normalized_spot_trade") {
    const price = text(payload.price);
    const size = text(payload.quantity);
    if (price !== undefined && size !== undefined) {
      const unit = text(payload.timestamp_unit);
      const raw = text(payload.trade_time);
      const eventAt =
        raw === undefined ? at : msToIso(unit === "us" ? Number(raw) / 1000 : Number(raw), at);
      pushTrade(entry, {
        at: eventAt,
        price,
        size,
        side: side(payload.aggressor_side, ["buy"], ["sell"]),
      });
    }
    return;
  }
  if (row.channel === "normalized_spot_bbo") {
    const bid = text(payload.bid_price);
    const ask = text(payload.ask_price);
    if (bid !== undefined && ask !== undefined) {
      setBbo(entry, at, bid, ask, text(payload.bid_quantity), text(payload.ask_quantity));
    }
    return;
  }
  if (row.channel === "normalized_usdm_context") {
    const context: TapeContext = { at: msToIso(payload.event_time, at) };
    const mark = text(payload.mark_price);
    const index = text(payload.index_price);
    const funding = text(payload.funding_rate);
    if (mark !== undefined) context.markPrice = mark;
    if (index !== undefined) context.indexPrice = index;
    if (funding !== undefined) context.fundingRate = funding;
    entry.tape.contextCount += 1;
    entry.tape.lastContext = context;
  }
}

function applyBitvavo(entry: InstrumentState, row: RawTapeRow, payload: unknown, at: string): void {
  if (!isRecord(payload) || !isMarker(row)) {
    return;
  }
  if (row.channel === "normalized_trades" && Array.isArray(payload.events)) {
    for (const item of payload.events) {
      if (!isRecord(item)) {
        continue;
      }
      const price = text(item.price);
      const size = text(item.quantity);
      if (price === undefined || size === undefined) {
        continue;
      }
      pushTrade(entry, {
        at: msToIso(item.event_time_ms, at),
        price,
        size,
        side: side(item.taker_side, ["buy"], ["sell"]),
      });
    }
    return;
  }
  if (row.channel === "normalized_ticker") {
    const ticker = entry.ticker ?? {};
    const bid = text(payload.bid_price);
    const bidSize = text(payload.bid_quantity);
    const ask = text(payload.ask_price);
    const askSize = text(payload.ask_quantity);
    if (bid !== undefined) ticker.bid = bid;
    if (bidSize !== undefined) ticker.bidSize = bidSize;
    if (ask !== undefined) ticker.ask = ask;
    if (askSize !== undefined) ticker.askSize = askSize;
    entry.ticker = ticker;
    if (ticker.bid !== undefined && ticker.ask !== undefined) {
      setBbo(entry, at, ticker.bid, ticker.ask, ticker.bidSize, ticker.askSize);
    }
  }
}

function bestOf(sideMap: BookSide, pick: "max" | "min"): [string, string] | undefined {
  let best: [string, string] | undefined;
  let bestValue = pick === "max" ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY;
  for (const [price, qty] of sideMap) {
    const value = Number(price);
    if (!Number.isFinite(value)) {
      continue;
    }
    if ((pick === "max" && value > bestValue) || (pick === "min" && value < bestValue)) {
      bestValue = value;
      best = [price, qty];
    }
  }
  return best;
}

function applyKraken(entry: InstrumentState, row: RawTapeRow, payload: unknown, at: string): void {
  if (!isRecord(payload) || !isMarker(row)) {
    return;
  }
  if (row.channel === "normalized_trade" && Array.isArray(payload.events)) {
    for (const item of payload.events) {
      if (!isRecord(item)) {
        continue;
      }
      const price = text(item.price);
      const size = text(item.qty);
      if (price === undefined || size === undefined) {
        continue;
      }
      pushTrade(entry, {
        at: isoOrFallback(item.timestamp, at),
        price,
        size,
        side: side(item.side, ["buy"], ["sell"]),
      });
    }
    return;
  }
  if (row.channel === "normalized_book" && Array.isArray(payload.events)) {
    const book = entry.book ?? { bids: new Map<string, string>(), asks: new Map<string, string>() };
    if (payload.message_type === "snapshot") {
      book.bids.clear();
      book.asks.clear();
    }
    for (const item of payload.events) {
      if (!isRecord(item)) {
        continue;
      }
      const price = text(item.price);
      const qty = text(item.qty);
      if (price === undefined || qty === undefined) {
        continue;
      }
      const target = item.side === "bid" ? book.bids : item.side === "ask" ? book.asks : undefined;
      if (target === undefined) {
        continue;
      }
      if (Number(qty) === 0) {
        target.delete(price);
      } else {
        target.set(price, qty);
      }
    }
    entry.book = book;
    const bid = bestOf(book.bids, "max");
    const ask = bestOf(book.asks, "min");
    if (bid !== undefined && ask !== undefined) {
      setBbo(entry, isoOrFallback(payload.message_timestamp, at), bid[0], ask[0], bid[1], ask[1]);
    }
  }
}

/**
 * Fold one published row into the venue state.
 *
 * Unknown channels are recorded as seen but otherwise ignored, so a new
 * collector channel never breaks the tape; it simply is not decoded yet.
 */
export function applyTapeRow(state: VenueTapeState, row: RawTapeRow): void {
  state.rows += 1;
  if (state.lastEventNs === undefined || row.received_utc_ns > state.lastEventNs) {
    state.lastEventNs = row.received_utc_ns;
  }
  const at = nsToIso(row.received_utc_ns);
  const entry = instrument(state, row.venue, row.product);
  noteChannel(entry, row.channel, at);

  let payload: unknown;
  try {
    payload = JSON.parse(row.payload);
  } catch {
    return;
  }

  switch (row.venue) {
    case "hyperliquid":
      applyHyperliquid(entry, row, payload, at);
      return;
    case "binance":
      applyBinance(entry, row, payload, at);
      return;
    case "bitvavo":
      applyBitvavo(entry, row, payload, at);
      return;
    case "kraken":
      applyKraken(entry, row, payload, at);
      return;
    default:
      return;
  }
}

/** Serialisable snapshot of the state, instruments sorted for stable output. */
export function snapshotInstruments(state: VenueTapeState): InstrumentTape[] {
  return [...state.instruments.values()]
    .map((entry) => ({
      ...entry.tape,
      recentTrades: [...entry.tape.recentTrades],
      channelsSeen: [...entry.tape.channelsSeen],
    }))
    .sort((left, right) => left.product.localeCompare(right.product));
}

export function lastEventIso(state: VenueTapeState): string | undefined {
  return state.lastEventNs === undefined ? undefined : nsToIso(state.lastEventNs);
}
