import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeEach, describe, expect, it } from "vitest";

import {
  applyTapeRow,
  binanceTimestampUnit,
  classifyProduct,
  computeSpread,
  createVenueTapeState,
  epochToIso,
  snapshotInstruments,
} from "./market-tape-decode";
import {
  loadMarketTapeStrip,
  loadVenueMarketTape,
  marketTapeCacheSize,
  refreshRunTape,
  resetMarketTapeCache,
} from "./market-tape";
import { VENUE_CAPTURE_CONTRACTS } from "./paths";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixtureRoot = join(repoRoot, "tests", "fixtures", "market_tape", "artifact-root");
const HL_RUN = "20260905t180000z-live-retained";
const BN_RUN = "20260905t180100z-live-retained";
const BV_STD_RUN = "20260905t180130z-live-retained";
const BV_RUN = "20260905t180200z-live-retained";
const KR_RUN = "20260905t180300z-live-retained";
const observedAt = "2026-09-05T18:01:00.000Z";

const env = {
  TRADING_MODE: "PAPER",
  ARTIFACT_ROOT: fixtureRoot,
  COCKPIT_DATA1A_RUN_ID: HL_RUN,
  COCKPIT_DATA1F_RUN_ID: BN_RUN,
  COCKPIT_DATA1D_RUN_ID: BV_STD_RUN,
  COCKPIT_DATA1E_RUN_ID: BV_RUN,
  COCKPIT_DATA1B_RUN_ID: KR_RUN,
};

function row(
  venue: string,
  product: string,
  channel: string,
  payload: unknown,
  ordinal: number,
  local = false,
) {
  return {
    venue,
    product,
    channel,
    direction: local ? "local" : "inbound",
    frame_type: local ? "marker" : "text",
    received_utc_ns: 1_788_631_200_000_000_000n + BigInt(ordinal) * 1_000_000_000n,
    payload: JSON.stringify(payload),
  };
}

beforeEach(() => {
  resetMarketTapeCache();
});

describe("market tape decoding", () => {
  it("keeps spot, perpetual and quote currency apart by the captured product string", () => {
    expect(classifyProduct("hyperliquid", "BTC-PERP")).toEqual({
      kind: "perpetual",
      base: "BTC",
      quote: "USDC",
    });
    expect(classifyProduct("binance", "BTCUSDT-SPOT")).toEqual({
      kind: "spot",
      base: "BTC",
      quote: "USDT",
    });
    expect(classifyProduct("binance", "BTCUSDT-USDS-M-PERPETUAL")).toEqual({
      kind: "perpetual",
      base: "BTC",
      quote: "USDT",
    });
    expect(classifyProduct("bitvavo", "BTC-EUR")).toEqual({
      kind: "spot",
      base: "BTC",
      quote: "EUR",
    });
    expect(classifyProduct("kraken", "BTC/USD")).toEqual({
      kind: "spot",
      base: "BTC",
      quote: "USD",
    });
  });

  it("computes spread in quote units and basis points without float drift", () => {
    expect(computeSpread("109498.0", "109501.0")).toEqual({ spread: "3.0", spreadBps: "0.27" });
    expect(computeSpread("93800.1", "93811.0")).toEqual({ spread: "10.9", spreadBps: "1.16" });
    expect(computeSpread("x", "1")).toEqual({ spread: "n/a", spreadBps: "n/a" });
  });

  it("decodes Hyperliquid trades and bbo frames", () => {
    const state = createVenueTapeState();
    applyTapeRow(
      state,
      row(
        "hyperliquid",
        "BTC-PERP",
        "trades",
        {
          data: [
            { px: "100.0", sz: "0.5", side: "B", time: 1_788_631_201_000 },
            { px: "99.5", sz: "0.1", side: "A", time: 1_788_631_201_500 },
          ],
        },
        1,
      ),
    );
    applyTapeRow(
      state,
      row(
        "hyperliquid",
        "BTC-PERP",
        "bbo",
        {
          data: {
            time: 1_788_631_202_000,
            bbo: [
              { px: "99.0", sz: "2" },
              { px: "101.0", sz: "1" },
            ],
          },
        },
        2,
      ),
    );
    const [perp] = snapshotInstruments(state);
    expect(perp?.kind).toBe("perpetual");
    expect(perp?.tradeCount).toBe(2);
    expect(perp?.lastTrade).toMatchObject({ price: "99.5", size: "0.1", side: "sell" });
    expect(perp?.recentTrades[0]?.price).toBe("99.5");
    expect(perp?.lastBbo).toMatchObject({ bid: "99.0", ask: "101.0", spread: "2.0" });
    expect(perp?.lastBbo?.at).toBe("2026-09-05T18:00:02.000Z");
  });

  it("never merges Binance spot with the USDS-M perpetual context", () => {
    const state = createVenueTapeState();
    applyTapeRow(
      state,
      row(
        "binance",
        "BTCUSDT-SPOT",
        "normalized_spot_trade",
        {
          price: "100",
          quantity: "1",
          trade_time: "1788688801000",
          timestamp_unit: "ms",
          aggressor_side: "buy",
        },
        1,
        true,
      ),
    );
    applyTapeRow(
      state,
      row(
        "binance",
        "BTCUSDT-USDS-M-PERPETUAL",
        "normalized_usdm_context",
        { mark_price: "101", index_price: "100.5", funding_rate: "0.0001" },
        2,
        true,
      ),
    );
    const instruments = snapshotInstruments(state);
    expect(instruments.map((item) => `${item.product}:${item.kind}`)).toEqual([
      "BTCUSDT-SPOT:spot",
      "BTCUSDT-USDS-M-PERPETUAL:perpetual",
    ]);
    expect(instruments[0]?.lastTrade?.side).toBe("buy");
    expect(instruments[0]?.lastContext).toBeUndefined();
    expect(instruments[1]?.lastContext?.markPrice).toBe("101");
    expect(instruments[1]?.lastTrade).toBeUndefined();
  });

  it("reads Binance spot trade_time in the unit the collector recorded (MICROSECONDS)", () => {
    // Binance streams are ms by default and µs only with `timeUnit=MICROSECOND`
    // (official web-socket-streams doc); DATA-1F opens the socket that way and
    // the Python marker stores the enum value "MICROSECONDS", not "us".
    const state = createVenueTapeState();
    applyTapeRow(
      state,
      row(
        "binance",
        "BTCUSDT-SPOT",
        "normalized_spot_trade",
        {
          price: "109480.10",
          quantity: "0.00200",
          trade_time: "1788631201123456",
          timestamp_unit: "MICROSECONDS",
          aggressor_side: "buy",
        },
        1,
        true,
      ),
    );
    const [spot] = snapshotInstruments(state);
    expect(spot?.lastTrade?.at).toBe("2026-09-05T18:00:01.123Z");
    expect(spot?.lastTrade?.at.startsWith("+")).toBe(false);
  });

  it("never renders a five-digit year: implausible epochs fall back to the receive time", () => {
    const receiveAt = "2026-09-05T18:00:07.000Z";
    expect(epochToIso("1788631201123456", "ms", receiveAt)).toBe(receiveAt);
    expect(epochToIso("1788631201123456", "us", receiveAt)).toBe("2026-09-05T18:00:01.123Z");
    expect(epochToIso("1788631201123", "ms", receiveAt)).toBe("2026-09-05T18:00:01.123Z");
    expect(epochToIso(1_788_631_201_123, "auto", receiveAt)).toBe("2026-09-05T18:00:01.123Z");
    expect(epochToIso("1788631201123456", "auto", receiveAt)).toBe("2026-09-05T18:00:01.123Z");
    expect(epochToIso("1788631201123456789", "auto", receiveAt)).toBe("2026-09-05T18:00:01.123Z");
    expect(epochToIso("1788631201", "auto", receiveAt)).toBe("2026-09-05T18:00:01.000Z");
    expect(epochToIso(1_788_631_201_123_456_789n, "ns", receiveAt)).toBe(
      "2026-09-05T18:00:01.123Z",
    );
    expect(epochToIso("0", "ms", receiveAt)).toBe(receiveAt);
    expect(epochToIso("not-a-number", "ms", receiveAt)).toBe(receiveAt);
    expect(epochToIso(undefined, "us", receiveAt)).toBe(receiveAt);
    expect(binanceTimestampUnit("MICROSECONDS")).toBe("us");
    expect(binanceTimestampUnit("ms")).toBe("ms");
    expect(binanceTimestampUnit("MILLISECONDS")).toBe("ms");
    expect(binanceTimestampUnit(undefined)).toBe("auto");
  });

  it("decodes the Bitvavo Market Data Pro channels the DATA-1E collector actually writes", () => {
    const state = createVenueTapeState();
    applyTapeRow(
      state,
      row(
        "bitvavo",
        "BTC-EUR",
        "normalized_mdpro_book",
        {
          event: "normalized_mdpro_book_frame",
          source_channel: "mdpro_book_snapshot",
          message_type: "snapshot",
          venue_timestamp_ns: "1788631201000000000",
          events: [
            { side: "bid", action: "snapshot", price: "93800.1", quantity: "0.5" },
            { side: "bid", action: "snapshot", price: "93799.0", quantity: "1.0" },
            { side: "ask", action: "snapshot", price: "93812.4", quantity: "0.25" },
            { side: "ask", action: "snapshot", price: "93813.0", quantity: "0.7" },
          ],
        },
        1,
        true,
      ),
    );
    applyTapeRow(
      state,
      row(
        "bitvavo",
        "BTC-EUR",
        "normalized_mdpro_book",
        {
          event: "normalized_mdpro_book_frame",
          source_channel: "mdpro_book",
          message_type: "update",
          venue_timestamp_ns: "1788631202000000000",
          events: [{ side: "ask", action: "delete", price: "93812.4", quantity: "0" }],
        },
        2,
        true,
      ),
    );
    applyTapeRow(
      state,
      row(
        "bitvavo",
        "BTC-EUR",
        "normalized_mdpro_trades",
        {
          event: "normalized_mdpro_trade_frame",
          source_channel: "mdpro_trades",
          events: [
            {
              event_index: 0,
              market: "BTC-EUR",
              trade_id: "t-1",
              price: "93810.5",
              quantity: "0.01000000",
              taker_side: "sell",
              event_time_ms: "1788631203000",
              event_time_ns: "1788631203000000000",
            },
          ],
        },
        3,
        true,
      ),
    );
    applyTapeRow(
      state,
      row(
        "bitvavo",
        "BTC-EUR",
        "normalized_mdpro_ticker",
        {
          event: "normalized_mdpro_ticker_frame",
          source_channel: "mdpro_ticker",
          bid_price: "93801.0",
          bid_quantity: "0.4",
          ask_price: "93813.0",
          ask_quantity: "0.7",
          last_price: null,
        },
        4,
        true,
      ),
    );
    const [spot] = snapshotInstruments(state);
    expect(spot).toMatchObject({ product: "BTC-EUR", kind: "spot", quote: "EUR" });
    expect(spot?.tradeCount).toBe(1);
    expect(spot?.lastTrade).toMatchObject({
      price: "93810.5",
      size: "0.01000000",
      side: "sell",
      at: "2026-09-05T18:00:03.000Z",
    });
    // Snapshot best ask 93812.4 was deleted by the update; the book falls to 93813.0.
    expect(spot?.bboCount).toBe(3);
    expect(spot?.lastBbo).toMatchObject({ bid: "93801.0", ask: "93813.0", spread: "12.0" });
    expect(spot?.channelsSeen).toEqual([
      "normalized_mdpro_book",
      "normalized_mdpro_ticker",
      "normalized_mdpro_trades",
    ]);
  });

  it("reconstructs Bitvavo top-of-book from the book frames when no ticker was subscribed", () => {
    const state = createVenueTapeState();
    applyTapeRow(
      state,
      row(
        "bitvavo",
        "BTC-EUR",
        "normalized_mdpro_book",
        {
          message_type: "snapshot",
          venue_timestamp_ns: "1788631201000000000",
          events: [
            { side: "bid", action: "snapshot", price: "93800.1", quantity: "0.5" },
            { side: "ask", action: "snapshot", price: "93812.4", quantity: "0.25" },
          ],
        },
        1,
        true,
      ),
    );
    const [spot] = snapshotInstruments(state);
    expect(spot?.lastBbo).toMatchObject({
      at: "2026-09-05T18:00:01.000Z",
      bid: "93800.1",
      bidSize: "0.5",
      ask: "93812.4",
      askSize: "0.25",
    });
  });

  it("ignores inbound frames for marker-based venues and unknown channels without failing", () => {
    const state = createVenueTapeState();
    applyTapeRow(state, row("binance", "BTCUSDT-SPOT", "btcusdt@trade", { p: "1" }, 1));
    applyTapeRow(
      state,
      row("binance", "BTCUSDT-SPOT", "normalized_future_thing", { x: 1 }, 2, true),
    );
    applyTapeRow(state, {
      ...row("kraken", "BTC/USD", "normalized_trade", {}, 3, true),
      payload: "{not json",
    });
    const instruments = snapshotInstruments(state);
    const spot = instruments.find((item) => item.product === "BTCUSDT-SPOT");
    const kraken = instruments.find((item) => item.product === "BTC/USD");
    expect(spot?.tradeCount).toBe(0);
    expect(spot?.channelsSeen).toEqual(["btcusdt@trade", "normalized_future_thing"]);
    expect(kraken?.tradeCount).toBe(0);
    expect(kraken?.channelsSeen).toEqual(["normalized_trade"]);
    expect(state.rows).toBe(3);
  });
});

describe("market tape reader", () => {
  it("reads published DuckDB/ZSTD parts for every venue from the fixture root", async () => {
    const strip = await loadMarketTapeStrip(env, repoRoot, {}, () => observedAt);
    expect(strip.venues.map((venue) => venue.status)).toEqual(["ok", "ok", "ok", "ok", "ok"]);
    expect(strip.cache.partsParsedThisCall).toBe(6);

    const hl = strip.venues[0];
    expect(hl?.runId).toBe(HL_RUN);
    expect(hl?.parts).toMatchObject({ published: 2, processed: 2, skippedOnColdStart: 0 });
    const perp = hl?.instruments[0];
    expect(perp).toMatchObject({
      product: "BTC-PERP",
      kind: "perpetual",
      quote: "USDC",
      tradeCount: 3,
      bboCount: 2,
    });
    expect(perp?.lastTrade).toMatchObject({ price: "109510.0", size: "0.0050", side: "buy" });
    expect(perp?.lastBbo).toMatchObject({ bid: "109509.0", ask: "109511.0", spread: "2.0" });
    expect(perp?.lastContext?.fundingRate).toBe("0.0000125");
    expect(hl?.lastEventUtc).toBe("2026-09-05T18:00:05.000Z");
    expect(hl?.lastEventAgeS).toBe(55);

    const bn = strip.venues[1];
    expect(bn?.instruments.map((item) => item.product)).toEqual([
      "BTCUSDT-SPOT",
      "BTCUSDT-USDS-M-PERPETUAL",
    ]);
    expect(bn?.instruments[0]?.lastBbo).toMatchObject({
      bid: "109479.90",
      ask: "109480.20",
      spread: "0.30",
    });
    expect(bn?.instruments[0]?.lastTrade).toMatchObject({ price: "109480.10", side: "buy" });
    expect(bn?.instruments[1]?.lastContext).toMatchObject({
      markPrice: "109520.00",
      fundingRate: "0.00010000",
    });

    const bvStd = strip.venues[2];
    expect(bvStd?.id).toBe("bitvavo-std");
    expect(bvStd?.series).toBe("DATA-1D");
    expect(bvStd?.instruments[0]).toMatchObject({
      product: "BTC-EUR",
      quote: "EUR",
      kind: "spot",
    });
    expect(bvStd?.instruments[0]?.lastTrade).toMatchObject({ price: "93750.0", side: "buy" });
    expect(bvStd?.instruments[0]?.lastBbo).toMatchObject({
      bid: "93740.0",
      ask: "93755.0",
    });

    const bv = strip.venues[3];
    expect(bv?.id).toBe("bitvavo");
    expect(bv?.series).toBe("DATA-1E");
    expect(bv?.instruments[0]).toMatchObject({ product: "BTC-EUR", quote: "EUR", kind: "spot" });
    // Partial ticker update replaced only the ask; the bid carried over.
    expect(bv?.instruments[0]?.lastBbo).toMatchObject({
      bid: "93800.1",
      ask: "93811.0",
      askSize: "0.30",
    });
    expect(bv?.instruments[0]?.lastTrade).toMatchObject({ price: "93810.5", side: "sell" });

    const kr = strip.venues[4];
    expect(kr?.instruments[0]).toMatchObject({ product: "BTC/USD", quote: "USD", kind: "spot" });
    // Snapshot best ask 109452 was deleted by the update; next best is 109453; bid 109451 arrived.
    expect(kr?.instruments[0]?.lastBbo).toMatchObject({
      bid: "109451.0",
      ask: "109453.0",
      spread: "2.0",
    });
    expect(kr?.instruments[0]?.lastTrade).toMatchObject({
      price: "109452.0",
      size: "0.02",
      side: "buy",
    });
    expect(kr?.instruments[0]?.lastTrade?.at).toBe("2026-09-05T18:00:03.000Z");
  });

  it("parses each published part once and picks up a new part incrementally", async () => {
    const root = mkdtempSync(join(tmpdir(), "market-tape-incremental-"));
    const runDir = join(root, "data-1a", "hyperliquid", "BTC-PERP", HL_RUN);
    const fixtureRun = join(fixtureRoot, "data-1a", "hyperliquid", "BTC-PERP", HL_RUN);
    mkdirSync(join(runDir, "raw"), { recursive: true });
    copyFileSync(join(fixtureRun, "capture-claim.json"), join(runDir, "capture-claim.json"));
    const part1 = "part-000001-000000000001-000000000003-fixture00001.parquet";
    const part2 = "part-000002-000000000004-000000000005-fixture00001.parquet";
    copyFileSync(join(fixtureRun, "raw", part1), join(runDir, "raw", part1));
    // A hidden partial file must never be read.
    writeFileSync(join(runDir, "raw", ".part-000003-partial.partial"), "garbage");

    const first = await refreshRunTape(runDir);
    expect(first.parsed).toBe(1);
    expect(snapshotInstruments(first.cache.state)[0]?.tradeCount).toBe(2);

    const second = await refreshRunTape(runDir);
    expect(second.parsed).toBe(0);

    copyFileSync(join(fixtureRun, "raw", part2), join(runDir, "raw", part2));
    const third = await refreshRunTape(runDir);
    expect(third.parsed).toBe(1);
    expect(third.parts.map((part) => part.name)).toEqual([part1, part2]);
    expect(snapshotInstruments(third.cache.state)[0]?.tradeCount).toBe(3);
    expect(snapshotInstruments(third.cache.state)[0]?.lastTrade?.price).toBe("109510.0");
    expect(marketTapeCacheSize()).toBe(1);
  });

  it("never folds a part in twice when refreshes overlap (StrictMode, two tabs, two clocks)", async () => {
    const runDir = join(fixtureRoot, "data-1a", "hyperliquid", "BTC-PERP", HL_RUN);

    const results = await Promise.all([
      refreshRunTape(runDir),
      refreshRunTape(runDir),
      refreshRunTape(runDir),
    ]);

    const parsed = results.map((result) => result.parsed);
    expect(parsed.reduce((sum, count) => sum + count, 0)).toBe(2);
    const cache = results[0]?.cache;
    expect(cache?.processed.size).toBe(2);
    const instrument = snapshotInstruments(cache?.state ?? createVenueTapeState())[0];
    expect(instrument?.tradeCount).toBe(3);
    expect(instrument?.bboCount).toBe(2);
    expect(instrument?.recentTrades.length).toBe(3);
    expect(marketTapeCacheSize()).toBe(1);
  });

  it("skips older parts on a cold start instead of re-reading the whole retain", async () => {
    const root = mkdtempSync(join(tmpdir(), "market-tape-cold-"));
    const runDir = join(root, "data-1a", "hyperliquid", "BTC-PERP", HL_RUN);
    const fixtureRun = join(fixtureRoot, "data-1a", "hyperliquid", "BTC-PERP", HL_RUN);
    mkdirSync(join(runDir, "raw"), { recursive: true });
    copyFileSync(join(fixtureRun, "capture-claim.json"), join(runDir, "capture-claim.json"));
    const part1 = "part-000001-000000000001-000000000003-fixture00001.parquet";
    const part2 = "part-000002-000000000004-000000000005-fixture00001.parquet";
    copyFileSync(join(fixtureRun, "raw", part1), join(runDir, "raw", part1));
    copyFileSync(join(fixtureRun, "raw", part2), join(runDir, "raw", part2));

    const { tape, parsed } = await loadVenueMarketTape(
      VENUE_CAPTURE_CONTRACTS.hl,
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: root, COCKPIT_DATA1A_RUN_ID: HL_RUN },
      repoRoot,
      {},
      observedAt,
      180,
      1,
    );
    expect(parsed).toBe(1);
    expect(tape.parts).toMatchObject({ published: 2, processed: 1, skippedOnColdStart: 1 });
    expect(tape.parts.bytes).toBeGreaterThan(0);
    // Only the newest part was decoded; the older trades are counted as volume, not invented.
    expect(tape.instruments[0]?.tradeCount).toBe(1);
    expect(tape.lastEventUtc).toBe("2026-09-05T18:00:05.000Z");
  });

  it("fails closed per venue when a run is missing and never invents a quote", async () => {
    const { tape } = await loadVenueMarketTape(
      VENUE_CAPTURE_CONTRACTS.binance,
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: fixtureRoot, COCKPIT_DATA1F_RUN_ID: "not-there" },
      repoRoot,
      {},
      observedAt,
    );
    expect(tape.status).toBe("missing");
    expect(tape.instruments).toEqual([]);
    expect(tape.error).toMatch(/missing/);
    expect(tape.runId).toBe("not-there");
  });

  it("refuses non-PAPER trading modes", async () => {
    await expect(
      loadMarketTapeStrip({ TRADING_MODE: "LIVE", ARTIFACT_ROOT: fixtureRoot }, repoRoot),
    ).rejects.toThrow(/PAPER/);
  });
});
