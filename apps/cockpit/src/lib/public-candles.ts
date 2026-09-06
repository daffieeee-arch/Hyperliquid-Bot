import { BTC_PERP_COIN, HYPERLIQUID_PUBLIC_INFO_URL } from "./public-price";

export const PUBLIC_CANDLE_SOURCE = "hyperliquid-public-info-candleSnapshot";
export const PUBLIC_CANDLE_INTERVAL = "15m";
export const PUBLIC_CANDLE_LOOKBACK_MS = 24 * 60 * 60 * 1000;

export type PublicBtcCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type PublicBtcCandleSnapshot = {
  coin: string;
  instrument: string;
  interval: typeof PUBLIC_CANDLE_INTERVAL;
  candles: PublicBtcCandle[];
  source: typeof PUBLIC_CANDLE_SOURCE;
  endpoint: string;
  signing: false;
  credentialless: true;
  fetched_at: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireCandleNumber(raw: unknown, field: string): number {
  if (typeof raw !== "string" || raw === "") {
    throw new Error(`Hyperliquid candleSnapshot is missing string field ${field}.`);
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`Hyperliquid candleSnapshot field ${field} is not a finite number.`);
  }
  return value;
}

export function parsePublicCandleRow(payload: unknown): PublicBtcCandle {
  if (!isRecord(payload)) {
    throw new Error("Hyperliquid candleSnapshot row must be a JSON object.");
  }
  if (payload.s !== BTC_PERP_COIN) {
    throw new Error("Hyperliquid candleSnapshot row is not a BTC candle.");
  }
  if (typeof payload.t !== "number" || !Number.isFinite(payload.t)) {
    throw new Error("Hyperliquid candleSnapshot row is missing numeric open time.");
  }
  return {
    time: Math.floor(payload.t / 1000),
    open: requireCandleNumber(payload.o, "o"),
    high: requireCandleNumber(payload.h, "h"),
    low: requireCandleNumber(payload.l, "l"),
    close: requireCandleNumber(payload.c, "c"),
  };
}

export function parsePublicCandleRows(payload: unknown): PublicBtcCandle[] {
  if (!Array.isArray(payload)) {
    throw new Error("Hyperliquid candleSnapshot must be a JSON array.");
  }
  return payload.map(parsePublicCandleRow);
}

function requireFiniteNumber(raw: unknown, field: string): number {
  if (typeof raw !== "number" || !Number.isFinite(raw)) {
    throw new Error(`Public candle field ${field} must be a finite number.`);
  }
  return raw;
}

export function parseNormalizedCandle(payload: unknown): PublicBtcCandle {
  if (!isRecord(payload)) {
    throw new Error("Public candle row must be a JSON object.");
  }
  return {
    time: requireFiniteNumber(payload.time, "time"),
    open: requireFiniteNumber(payload.open, "open"),
    high: requireFiniteNumber(payload.high, "high"),
    low: requireFiniteNumber(payload.low, "low"),
    close: requireFiniteNumber(payload.close, "close"),
  };
}

export function parsePublicCandleSnapshot(payload: unknown): PublicBtcCandleSnapshot {
  if (!isRecord(payload) || !Array.isArray(payload.candles)) {
    throw new Error("Public candle response did not include a candles array.");
  }
  if (
    payload.source !== PUBLIC_CANDLE_SOURCE ||
    payload.instrument !== "BTC-PERP" ||
    payload.interval !== PUBLIC_CANDLE_INTERVAL ||
    payload.signing !== false ||
    payload.credentialless !== true ||
    typeof payload.fetched_at !== "string" ||
    payload.fetched_at === "" ||
    typeof payload.endpoint !== "string" ||
    payload.endpoint === "" ||
    typeof payload.coin !== "string" ||
    payload.coin === ""
  ) {
    throw new Error("Public candle response is not a credentialless unsigned BTC snapshot.");
  }
  return {
    coin: payload.coin,
    instrument: "BTC-PERP",
    interval: PUBLIC_CANDLE_INTERVAL,
    candles: payload.candles.map(parseNormalizedCandle),
    source: PUBLIC_CANDLE_SOURCE,
    endpoint: payload.endpoint,
    signing: false,
    credentialless: true,
    fetched_at: payload.fetched_at,
  };
}

export async function fetchPublicBtcPerpCandles(
  fetchImpl: typeof fetch = fetch,
  now: () => number = () => Date.now(),
  nowIso: () => string = () => new Date().toISOString(),
): Promise<PublicBtcCandleSnapshot> {
  const endTime = now();
  const startTime = endTime - PUBLIC_CANDLE_LOOKBACK_MS;
  const response = await fetchImpl(HYPERLIQUID_PUBLIC_INFO_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      type: "candleSnapshot",
      req: {
        coin: BTC_PERP_COIN,
        interval: PUBLIC_CANDLE_INTERVAL,
        startTime,
        endTime,
      },
    }),
    cache: "no-store",
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) {
    throw new Error(`Hyperliquid public /info candles returned HTTP ${String(response.status)}.`);
  }
  const payload: unknown = await response.json();
  return {
    coin: BTC_PERP_COIN,
    instrument: "BTC-PERP",
    interval: PUBLIC_CANDLE_INTERVAL,
    candles: parsePublicCandleRows(payload),
    source: PUBLIC_CANDLE_SOURCE,
    endpoint: HYPERLIQUID_PUBLIC_INFO_URL,
    signing: false,
    credentialless: true,
    fetched_at: nowIso(),
  };
}
