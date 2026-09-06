import { describe, expect, it } from "vitest";

import {
  MARKET_BBO_NOT_IN_COCKPIT_APIS,
  MARKET_PUBLIC_MID_SOURCE,
  MARKET_QUOTE_UNAVAILABLE,
  MARKET_SOAK_MARK_SOURCE,
  applyPublicMid,
  marketsRowsFromBoundVenues,
  soakMarkRow,
} from "./markets";
import { HYPERLIQUID_PUBLIC_INFO_URL } from "./public-price";
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

describe("MARKETS quotes", () => {
  it("uses the public HL mid and fails closed for sibling venues without a cockpit last/BBO", () => {
    const rows = marketsRowsFromBoundVenues(venues, {
      status: "ready",
      price: {
        coin: "BTC",
        instrument: "BTC-PERP",
        mid: "81156.0",
        source: MARKET_PUBLIC_MID_SOURCE,
        endpoint: HYPERLIQUID_PUBLIC_INFO_URL,
        signing: false,
        credentialless: true,
        fetched_at: "2026-09-06T10:16:00.000Z",
      },
    });
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({
      id: "hl",
      product: "BTC-PERP",
      quote: "81156.0",
      quoteKind: "public-mid",
      quoteSource: MARKET_PUBLIC_MID_SOURCE,
      captureStatus: "RUNNING",
      runId: TERRAPC_SHARED_RETAIN_RUN_ID,
    });
    expect(rows[1]).toMatchObject({
      id: "binance",
      product: "BTCUSDT",
      quote: MARKET_QUOTE_UNAVAILABLE,
      quoteKind: "unavailable",
      quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
      captureStatus: "STALE",
      runId: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    });
    expect(rows[2]?.quote).toBe(MARKET_QUOTE_UNAVAILABLE);
    expect(rows[2]?.runId).toBe("n/a");
  });

  it("does not invent an HL mid when public /info is missing", () => {
    const row = applyPublicMid(
      {
        id: "hl",
        venue: "HL",
        product: "BTC-PERP",
        quote: MARKET_QUOTE_UNAVAILABLE,
        quoteKind: "unavailable",
        quoteSource: MARKET_BBO_NOT_IN_COCKPIT_APIS,
        captureStatus: "RUNNING",
        lastPartAge: "12s",
        runId: TERRAPC_SHARED_RETAIN_RUN_ID,
        live: true,
        tone: "ok",
      },
      { status: "error", message: "Hyperliquid allMids response is missing a BTC mid string." },
    );
    expect(row.quote).toBe(MARKET_QUOTE_UNAVAILABLE);
    expect(row.quoteKind).toBe("unavailable");
    expect(row.quoteSource).toMatch(/missing a BTC mid/);
    expect(row.tone).toBe("warn");
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
      quote: "81143.0",
      quoteKind: "soak-mark",
      quoteSource: MARKET_SOAK_MARK_SOURCE,
      live: false,
    });
    expect(soakMarkRow(undefined)).toBeUndefined();
  });
});
