import { describe, expect, it } from "vitest";

import {
  MARKET_BBO_NOT_IN_COCKPIT_APIS,
  MARKET_QUOTE_UNAVAILABLE,
  MARKET_SOAK_MARK_SOURCE,
  applyStoredTapeQuote,
  decimalMidpoint,
  marketsRowsFromBoundVenues,
  pickQuoteInstrument,
  soakMarkRow,
} from "./markets";
import type { InstrumentTape, MarketTapeResponse, VenueMarketTape } from "./market-tape-types";
import {
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
} from "./terrapc-defaults";
import type { PaperPnl, VenueCaptureChip } from "./types";

function chip(
  partial: Partial<VenueCaptureChip> & Pick<VenueCaptureChip, "id" | "chip" | "product">,
): VenueCaptureChip {
  return {
    series: "DATA-1A",
    venue: "hyperliquid",
    path_contract: "data-1a-hyperliquid-btc-perp-v1",
    status: "RUNNING",
    status_detail: "RUNNING",
    tone: "ok",
    live: true,
    part_count: 2,
    last_part_age: "12s",
    last_part_mtime_utc: "2026-09-06T10:15:47.000Z",
    gaps: undefined,
    reconnects: undefined,
    run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
    binding_source: "env",
    observed_at: "2026-09-06T10:15:59.000Z",
    error: undefined,
    ...partial,
  };
}

const venues: VenueCaptureChip[] = [
  chip({ id: "hl", chip: "HL", product: "BTC-PERP" }),
  chip({
    id: "binance",
    chip: "BINANCE",
    series: "DATA-1F",
    product: "BTCUSDT",
    status: "STALE",
    live: false,
    tone: "warn",
    run_id: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    last_part_age: "4m",
  }),
  chip({
    id: "bitvavo",
    chip: "BITVAVO",
    product: "BTC-EUR",
    status: "MISSING",
    live: false,
    tone: "warn",
    run_id: undefined,
    last_part_age: "n/a",
  }),
];

function instrument(
  partial: Partial<InstrumentTape> & Pick<InstrumentTape, "product">,
): InstrumentTape {
  return {
    kind: "spot",
    base: "BTC",
    quote: "USDT",
    tradeCount: 0,
    bboCount: 0,
    contextCount: 0,
    lastTrade: undefined,
    lastBbo: undefined,
    lastContext: undefined,
    recentTrades: [],
    lastEventUtc: undefined,
    channelsSeen: [],
    ...partial,
  };
}

function tapeVenue(
  partial: Partial<VenueMarketTape> & Pick<VenueMarketTape, "id" | "chip" | "contractProduct">,
): VenueMarketTape {
  return {
    series: "DATA-1F",
    venue: "binance",
    runId: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    bindingSource: "env",
    status: "ok",
    error: undefined,
    parts: {
      published: 3,
      processed: 3,
      skippedOnColdStart: 0,
      bytes: 1024,
      newestPartName: "part-00003.parquet",
      newestPartMtimeUtc: "2026-09-06T10:15:47.000Z",
    },
    instruments: [],
    lastEventUtc: "2026-09-06T10:15:40.000Z",
    lastEventAgeS: 19,
    publicationLagS: 7,
    note: "stored parts only",
    observedAt: "2026-09-06T10:15:59.000Z",
    ...partial,
  };
}

const OBSERVED = "2026-09-06T10:15:59.000Z";

const storedTape: MarketTapeResponse = {
  ok: true,
  tape: {
    observed_at: OBSERVED,
    cache: { runsCached: 3, partsParsedThisCall: 1 },
    venues: [
      tapeVenue({
        id: "hl",
        chip: "HL",
        series: "DATA-1A",
        venue: "hyperliquid",
        contractProduct: "BTC-PERP",
        runId: TERRAPC_SHARED_RETAIN_RUN_ID,
        instruments: [
          instrument({
            product: "BTC-PERP",
            kind: "perpetual",
            quote: "USD",
            tradeCount: 4,
            lastTrade: {
              at: "2026-09-06T10:15:50.000Z",
              price: "81150.0",
              size: "0.01",
              side: "buy",
            },
            lastBbo: {
              at: "2026-09-06T10:15:48.000Z",
              bid: "81149.0",
              bidSize: "1",
              ask: "81151.0",
              askSize: "1",
              spread: "2.0",
              spreadBps: "0.25",
            },
            channelsSeen: ["bbo", "trades"],
          }),
        ],
      }),
      tapeVenue({
        id: "binance",
        chip: "BINANCE",
        contractProduct: "BTCUSDT",
        instruments: [
          instrument({
            product: "BTCUSDT-USDS-M-PERPETUAL",
            kind: "perpetual",
            tradeCount: 9,
            lastTrade: {
              at: "2026-09-06T10:15:41.000Z",
              price: "81200.10",
              size: "0.5",
              side: "sell",
            },
            channelsSeen: ["normalized_usdm_agg_trade"],
          }),
          instrument({
            product: "BTCUSDT-SPOT",
            tradeCount: 2,
            bboCount: 5,
            lastTrade: {
              at: "2026-09-06T10:15:30.000Z",
              price: "81180.10",
              size: "0.002",
              side: "buy",
            },
            lastBbo: {
              at: "2026-09-06T10:15:45.000Z",
              bid: "81179.90",
              bidSize: "1",
              ask: "81180.10",
              askSize: "2",
              spread: "0.20",
              spreadBps: "0.02",
            },
            channelsSeen: ["normalized_spot_book_ticker", "normalized_spot_trade"],
          }),
        ],
      }),
      tapeVenue({
        id: "bitvavo",
        chip: "BITVAVO",
        series: "DATA-1E",
        venue: "bitvavo",
        contractProduct: "BTC-EUR",
        runId: undefined,
        status: "missing",
        error: "DATA1E_RUN_ID unset and no live retain found",
      }),
    ],
  },
};

describe("MARKETS quotes", () => {
  it("leaves every venue UNAVAILABLE until a stored trade or BBO is applied", () => {
    const rows = marketsRowsFromBoundVenues(venues);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({
      id: "hl",
      product: "BTC-PERP",
      mid: MARKET_QUOTE_UNAVAILABLE,
      last: MARKET_QUOTE_UNAVAILABLE,
      quoteKind: "unavailable",
      quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
      quoteState: "unavailable",
      captureStatus: "RUNNING",
      runId: TERRAPC_SHARED_RETAIN_RUN_ID,
    });
    expect(rows[1]).toMatchObject({
      id: "binance",
      product: "BTCUSDT",
      last: MARKET_QUOTE_UNAVAILABLE,
      mid: MARKET_QUOTE_UNAVAILABLE,
      quoteKind: "unavailable",
      quoteState: "unavailable",
      quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
      captureStatus: "STALE",
      runId: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    });
    expect(rows[2]?.last).toBe(MARKET_QUOTE_UNAVAILABLE);
    expect(rows[2]?.quoteReason).toMatch(/capture MISSING/);
    expect(rows[2]?.runId).toBe("n/a");
  });

  it("fills Last and Mid from the stored tape, attributed to run and channels", () => {
    const rows = applyStoredTapeQuote(marketsRowsFromBoundVenues(venues), storedTape, 180);

    const binance = rows.find((row) => row.id === "binance");
    // The BTCUSDT contract maps to the BTCUSDT-SPOT instrument, not the USDⓈ-M perpetual.
    expect(binance).toMatchObject({
      instrument: "BTCUSDT-SPOT",
      last: "81180.10",
      mid: "81180.00",
      quoteKind: "stored-tape",
      quoteState: "ok",
      quoteAge: "14s",
      quoteAt: "2026-09-06T10:15:45.000Z",
      quoteReason: undefined,
    });
    expect(binance?.quoteSource).toBe(
      `stored capture · run ${TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID} · normalized_spot_book_ticker+normalized_spot_trade`,
    );

    const hl = rows.find((row) => row.id === "hl");
    // HL mid is the stored BBO midpoint, never a public /info stand-in.
    expect(hl).toMatchObject({
      mid: "81150.0",
      last: "81150.0",
      quoteKind: "stored-tape",
      quoteState: "ok",
      quoteAge: "9s",
      quoteAt: "2026-09-06T10:15:50.000Z",
    });
    expect(hl?.quoteSource).toMatch(/^stored capture · run /);
    expect(hl?.quoteSource).not.toMatch(/allMids/);

    const bitvavo = rows.find((row) => row.id === "bitvavo");
    expect(bitvavo).toMatchObject({
      last: MARKET_QUOTE_UNAVAILABLE,
      mid: MARKET_QUOTE_UNAVAILABLE,
      quoteState: "unavailable",
    });
    expect(bitvavo?.quoteReason).toMatch(/stored data missing · DATA1E_RUN_ID unset/);
  });

  it("marks a stored quote stale beyond fresh_max_s and explains a half-empty tape", () => {
    const rows = applyStoredTapeQuote(marketsRowsFromBoundVenues(venues), storedTape, 10);
    const binance = rows.find((row) => row.id === "binance");
    expect(binance?.quoteState).toBe("stale");
    expect(binance?.quoteReason).toBe("newest stored tick is 14s old (fresh ≤ 10s)");
    expect(binance?.last).toBe("81180.10");

    const tradeOnly: MarketTapeResponse = {
      ok: true,
      tape: {
        observed_at: OBSERVED,
        cache: { runsCached: 1, partsParsedThisCall: 0 },
        venues: [
          tapeVenue({
            id: "binance",
            chip: "BINANCE",
            contractProduct: "BTCUSDT",
            instruments: [
              instrument({
                product: "BTCUSDT-SPOT",
                tradeCount: 1,
                lastTrade: {
                  at: "2026-09-06T10:15:58.000Z",
                  price: "81181",
                  size: "0.1",
                  side: "buy",
                },
                channelsSeen: ["normalized_spot_trade"],
              }),
            ],
          }),
        ],
      },
    };
    const partial = applyStoredTapeQuote(marketsRowsFromBoundVenues(venues), tradeOnly, 180);
    const row = partial.find((item) => item.id === "binance");
    expect(row).toMatchObject({ last: "81181", mid: MARKET_QUOTE_UNAVAILABLE, quoteState: "ok" });
    expect(row?.quoteReason).toBe("no BBO decoded yet · last from stored trades");

    const empty: MarketTapeResponse = {
      ok: true,
      tape: {
        observed_at: OBSERVED,
        cache: { runsCached: 1, partsParsedThisCall: 0 },
        venues: [
          tapeVenue({
            id: "binance",
            chip: "BINANCE",
            contractProduct: "BTCUSDT",
            instruments: [instrument({ product: "BTCUSDT-SPOT", channelsSeen: ["subscribe_ack"] })],
          }),
        ],
      },
    };
    const none = applyStoredTapeQuote(marketsRowsFromBoundVenues(venues), empty, 180);
    expect(none.find((item) => item.id === "binance")).toMatchObject({
      instrument: "BTCUSDT-SPOT",
      last: MARKET_QUOTE_UNAVAILABLE,
      mid: MARKET_QUOTE_UNAVAILABLE,
      quoteState: "unavailable",
    });
    expect(none.find((item) => item.id === "binance")?.quoteReason).toMatch(
      /no trade or BBO decoded yet in run .* · channels subscribe_ack/,
    );
  });

  it("fails closed every row when the tape read itself failed and never invents a mid", () => {
    const rows = applyStoredTapeQuote(
      marketsRowsFromBoundVenues(venues),
      { ok: false, error: "ARTIFACT_ROOT unset" },
      180,
    );
    expect(rows[0]).toMatchObject({
      mid: MARKET_QUOTE_UNAVAILABLE,
      quoteKind: "unavailable",
      quoteState: "unavailable",
    });
    expect(rows[1]).toMatchObject({ last: MARKET_QUOTE_UNAVAILABLE, quoteState: "unavailable" });
    expect(rows[1]?.quoteReason).toBe("stored market data unavailable · ARTIFACT_ROOT unset");
    expect(JSON.stringify(rows)).not.toMatch(/81156/);
  });

  it("keeps sibling capture quotes when one venue tape is unreadable", () => {
    const gapped: MarketTapeResponse = {
      ok: true,
      tape: {
        ...storedTape.tape,
        venues: storedTape.tape.venues.map((venue) =>
          venue.id === "binance"
            ? {
                ...venue,
                status: "ok",
                error: "parquet gap",
                instruments: [
                  instrument({
                    product: "BTCUSDT-SPOT",
                    channelsSeen: undefined as unknown as string[],
                  }),
                ],
              }
            : venue,
        ),
      },
    };
    const rows = applyStoredTapeQuote(marketsRowsFromBoundVenues(venues), gapped, 180);
    expect(rows.find((row) => row.id === "hl")).toMatchObject({
      last: "81150.0",
      mid: "81150.0",
      quoteKind: "stored-tape",
    });
    expect(rows.find((row) => row.id === "binance")).toMatchObject({
      last: MARKET_QUOTE_UNAVAILABLE,
      mid: MARKET_QUOTE_UNAVAILABLE,
      quoteState: "unavailable",
    });
    expect(rows.find((row) => row.id === "binance")?.quoteReason).toMatch(/parquet gap|unreadable/);
    expect(rows.find((row) => row.id === "bitvavo")?.quoteState).toBe("unavailable");
  });

  it("prefers the contract instrument, then -SPOT, then the first instrument with a tick", () => {
    const perp = instrument({ product: "BTCUSDT-USDS-M-PERPETUAL", kind: "perpetual" });
    const spot = instrument({ product: "BTCUSDT-SPOT" });
    const kraken = instrument({
      product: "BTC/USD",
      lastTrade: { at: OBSERVED, price: "1", size: "1", side: "buy" },
    });
    expect(
      pickQuoteInstrument({ instruments: [perp, spot], contractProduct: "BTCUSDT" }, "BTCUSDT"),
    ).toBe(spot);
    expect(
      pickQuoteInstrument({ instruments: [kraken], contractProduct: "BTC/USD" }, "BTC/USD"),
    ).toBe(kraken);
    expect(
      pickQuoteInstrument({ instruments: [perp, kraken], contractProduct: "ETHUSDT" }, "ETHUSDT"),
    ).toBe(kraken);
    expect(
      pickQuoteInstrument({ instruments: [], contractProduct: "BTCUSDT" }, "BTCUSDT"),
    ).toBeUndefined();
  });

  it("computes the midpoint in exact decimal string arithmetic", () => {
    expect(decimalMidpoint("93801.0", "93813.0")).toBe("93807.0");
    expect(decimalMidpoint("1.1", "1.2")).toBe("1.15");
    expect(decimalMidpoint("81179.90", "81180.10")).toBe("81180.00");
    expect(decimalMidpoint("100", "101")).toBe("100.5");
    expect(decimalMidpoint("0.00000001", "0.00000003")).toBe("0.00000002");
    expect(decimalMidpoint("abc", "1")).toBeUndefined();
  });

  it("does not copy a last trade into the mid when the BBO is missing", () => {
    const tradeOnlyHl: MarketTapeResponse = {
      ok: true,
      tape: {
        observed_at: OBSERVED,
        cache: { runsCached: 1, partsParsedThisCall: 0 },
        venues: [
          tapeVenue({
            id: "hl",
            chip: "HL",
            series: "DATA-1A",
            venue: "hyperliquid",
            contractProduct: "BTC-PERP",
            instruments: [
              instrument({
                product: "BTC-PERP",
                kind: "perpetual",
                lastTrade: {
                  at: "2026-09-06T10:15:50.000Z",
                  price: "81150.0",
                  size: "0.01",
                  side: "buy",
                },
                channelsSeen: ["trades"],
              }),
            ],
          }),
        ],
      },
    };
    const row = applyStoredTapeQuote(marketsRowsFromBoundVenues(venues), tradeOnlyHl, 180).find(
      (item) => item.id === "hl",
    );
    expect(row).toMatchObject({
      last: "81150.0",
      mid: MARKET_QUOTE_UNAVAILABLE,
      quoteKind: "stored-tape",
    });
    expect(row?.quoteReason).toBe("no BBO decoded yet · last from stored trades");
  });

  it("copies the soak mark from paper-pnl.json and never treats it as a live last", () => {
    const pnl: PaperPnl = {
      kind: "paper-pnl",
      schema: "course1-cockpit-paper-artifacts-v1",
      path_contract: "course1-live-public-paper-cockpit-v1",
      mode: "PAPER",
      assumed: true,
      venue_pnl: false,
      instrument_id: "BTC-USD-PERP.HYPERLIQUID",
      starting_cash_usdc_assumed: "100000",
      ending_cash_usdc_assumed: "99999",
      ending_equity_usdc_assumed: "99999",
      net_pnl_usdc_assumed: "-0.0126583080",
      fee_cost_usdc: "0",
      half_spread_cost_usdc: "0",
      slippage_cost_usdc: "0",
      funding_payment_usdc: "0",
      mark_price: "81143.0",
      final_position_btc: "0.00000",
      limitations: [],
    };
    const row = soakMarkRow(pnl);
    expect(row).toMatchObject({
      id: "soak-mark",
      venue: "PAPER",
      mid: "81143.0",
      last: MARKET_QUOTE_UNAVAILABLE,
      quoteKind: "soak-mark",
      quoteSource: MARKET_SOAK_MARK_SOURCE,
      live: false,
    });
    expect(soakMarkRow(undefined)).toBeUndefined();
  });
});
