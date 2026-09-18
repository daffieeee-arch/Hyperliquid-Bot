import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DEFAULT_CAPTURE_FRESH_MAX_S } from "./capture-freshness";
import { loadPaperRunSnapshot } from "./paper-run";
import {
  BINANCE_USDM_PUBLIC_CHANNEL,
  BINANCE_USDM_PUBLIC_NOTE,
  BINANCE_USDM_PUBLIC_PROFILE,
  SECOND_ROW_UNAVAILABLE,
  buildSecondRowView,
} from "./second-row";
import { TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID } from "./terrapc-defaults";
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

const strip: VenueCaptureStrip = {
  observed_at: "2026-09-06T10:16:00.000Z",
  fresh_max_s: DEFAULT_CAPTURE_FRESH_MAX_S,
  venues: [
    chip({ id: "hl", chip: "HL", status: "RUNNING", tone: "ok", live: true, run_id: "hl-run" }),
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
      gaps: 0,
      reconnects: 2,
      reconnect_clusters: 1,
      error: undefined,
    }),
    chip({ id: "bitvavo", chip: "BITVAVO", series: "DATA-1E" }),
    chip({ id: "kraken", chip: "KRAKEN", series: "DATA-1B" }),
  ],
  catalog: [],
  provenance: {
    rows: [],
    distinct_run_ids: [],
    shared_run_ids: [],
    overlap_note: "",
  },
};

describe("second-row operator cards", () => {
  it("copies D01 bind, read-only capture, and BN usdm_public without mixing soak PnL", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildSecondRowView(snapshot, { ok: true, strip });
    expect(view.bind.sameD01SmokeRisk).toBe("yes");
    expect(view.bind.strategyClass).toContain("D01SmokeStrategy");
    expect(view.bind.note).toMatch(/not a live risk-engine heartbeat/);
    expect(view.capture.startStop).toBe("vetoed");
    expect(view.capture.freshMaxS).toBe(`${String(DEFAULT_CAPTURE_FRESH_MAX_S)}s`);
    expect(view.binance.profile).toBe(BINANCE_USDM_PUBLIC_PROFILE);
    expect(view.binance.channel).toBe(BINANCE_USDM_PUBLIC_CHANNEL);
    expect(view.binance.runId).toBe(TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID);
    expect(view.binance.gapsReconnects).toBe("0 gaps · 2 raw / 1 clusters");
    expect(view.binance.note).toBe(BINANCE_USDM_PUBLIC_NOTE);
    expect(JSON.stringify(view)).not.toContain("net_pnl");
    expect(JSON.stringify(view)).not.toContain("-0.0126583080");
  });

  it("fails closed when preflight and BN chip are missing", () => {
    const view = buildSecondRowView(undefined, {
      ok: false,
      error: "strip unavailable",
    });
    expect(view.bind.sameD01SmokeRisk).toBe(SECOND_ROW_UNAVAILABLE);
    expect(view.capture.glanceLine).toBe(SECOND_ROW_UNAVAILABLE);
    expect(view.binance.status).toBe(SECOND_ROW_UNAVAILABLE);
    expect(view.binance.profile).toBe(BINANCE_USDM_PUBLIC_PROFILE);
  });
});
