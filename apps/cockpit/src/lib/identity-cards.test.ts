import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DEFAULT_CAPTURE_FRESH_MAX_S } from "./capture-freshness";
import {
  DATA_RETAIN_IDENTITY_KIND,
  SOAK_IDENTITY_KIND,
  buildSeparateIdentityCards,
  identityCardsAreSeparate,
} from "./identity-cards";
import { loadPaperRunSnapshot } from "./paper-run";
import {
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
} from "./terrapc-defaults";
import type { VenueCaptureChip, VenueCaptureStrip } from "./types";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

function chip(
  partial: Partial<VenueCaptureChip> & Pick<VenueCaptureChip, "id" | "chip">,
): VenueCaptureChip {
  return {
    series: "DATA-1A",
    venue: "hyperliquid",
    product: "BTC-PERP",
    path_contract: "data-1a-hyperliquid-btc-perp-v1",
    status: "MISSING",
    status_detail: "not pointed",
    tone: "warn",
    live: false,
    part_count: undefined,
    last_part_age: "n/a",
    last_part_mtime_utc: undefined,
    gaps: undefined,
    reconnects: undefined,
    run_id: undefined,
    binding_source: "unbound",
    observed_at: "2026-09-06T10:15:59.000Z",
    error: "fail closed",
    ...partial,
  };
}

const boundStrip: VenueCaptureStrip = {
  observed_at: "2026-09-06T10:16:00.000Z",
  fresh_max_s: DEFAULT_CAPTURE_FRESH_MAX_S,
  venues: [
    chip({
      id: "hl",
      chip: "HL",
      status: "RUNNING",
      tone: "ok",
      live: true,
      last_part_age: "12s",
      run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
      binding_source: "env",
      error: undefined,
    }),
    chip({
      id: "binance",
      chip: "BINANCE",
      series: "DATA-1F",
      venue: "binance",
      product: "BTCUSDT",
      path_contract: "data-1f-binance-btcusdt-v1",
      status: "STALE",
      tone: "warn",
      live: false,
      last_part_age: "4m",
      run_id: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
      binding_source: "env",
      error: undefined,
    }),
    chip({ id: "bitvavo", chip: "BITVAVO", series: "DATA-1E" }),
    chip({ id: "kraken", chip: "KRAKEN", series: "DATA-1B" }),
  ],
  catalog: [],
  provenance: {
    rows: [],
    distinct_run_ids: [TERRAPC_SHARED_RETAIN_RUN_ID, TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID],
    shared_run_ids: [],
    overlap_note: "Binance started later.",
  },
};

describe("separate identity cards", () => {
  it("keeps COURSE-1 soak and DATA retain as two cards, never one blended identity", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const cards = buildSeparateIdentityCards(snapshot, undefined, { ok: true, strip: boundStrip });
    expect(identityCardsAreSeparate(cards)).toBe(true);
    expect(cards.soak.kind).toBe(SOAK_IDENTITY_KIND);
    expect(cards.retain.kind).toBe(DATA_RETAIN_IDENTITY_KIND);
    expect(cards.soak.runId).toBe("20260904t001800z-live-paper");
    expect(cards.retain.venues.map((venue) => venue.runId)).toEqual([
      TERRAPC_SHARED_RETAIN_RUN_ID,
      TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
      "n/a",
      "n/a",
    ]);
    expect(cards.soak.runId).not.toBe(TERRAPC_SHARED_RETAIN_RUN_ID);
    expect(JSON.stringify(cards.soak)).not.toContain("assumed overlay");
    expect(JSON.stringify(cards.retain)).not.toContain(cards.soak.runId);
    expect(JSON.stringify(cards.retain)).not.toContain("net_pnl");
  });

  it("fails closed on missing soak JSON without inventing a retain blend", () => {
    const cards = buildSeparateIdentityCards(undefined, "missing soak", {
      ok: true,
      strip: boundStrip,
    });
    expect(cards.soak.runId).toBe("UNAVAILABLE");
    expect(cards.soak.error).toBe("missing soak");
    expect(cards.retain.venues[0]?.runId).toBe(TERRAPC_SHARED_RETAIN_RUN_ID);
  });
});
