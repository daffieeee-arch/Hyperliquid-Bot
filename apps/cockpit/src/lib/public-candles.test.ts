import { describe, expect, it } from "vitest";

import { HYPERLIQUID_PUBLIC_INFO_URL } from "./public-price";
import {
  PUBLIC_CANDLE_SOURCE,
  fetchPublicBtcPerpCandles,
  parsePublicCandleRow,
  parsePublicCandleSnapshot,
} from "./public-candles";

const rawCandle = {
  T: 1681924499999,
  c: "29258.0",
  h: "29309.0",
  i: "15m",
  l: "29250.0",
  n: 189,
  o: "29295.0",
  s: "BTC",
  t: 1681923600000,
  v: "0.98639",
};

describe("public BTC-PERP candles", () => {
  it("copies HL candleSnapshot strings and does not invent a last", () => {
    expect(parsePublicCandleRow(rawCandle)).toEqual({
      time: 1681923600,
      open: 29295,
      high: 29309,
      low: 29250,
      close: 29258,
    });
    expect(() => parsePublicCandleRow({ ...rawCandle, s: "ETH" })).toThrow(/BTC candle/);
    expect(() => parsePublicCandleRow({ ...rawCandle, c: 29258 })).toThrow(/string field c/);
  });

  it("labels the cockpit payload as credentialless unsigned candles", async () => {
    const fetchImpl: typeof fetch = async () =>
      new Response(JSON.stringify([rawCandle]), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const snapshot = await fetchPublicBtcPerpCandles(
      "15m",
      fetchImpl,
      () => 1_681_924_500_000,
      () => "2026-09-06T00:00:00.000Z",
    );
    expect(snapshot.source).toBe(PUBLIC_CANDLE_SOURCE);
    expect(snapshot.signing).toBe(false);
    expect(snapshot.credentialless).toBe(true);
    expect(snapshot.endpoint).toBe(HYPERLIQUID_PUBLIC_INFO_URL);
    expect(snapshot.candles).toHaveLength(1);
    expect(() =>
      parsePublicCandleSnapshot({
        ...snapshot,
        signing: true,
      }),
    ).toThrow(/credentialless unsigned/);
  });
});
