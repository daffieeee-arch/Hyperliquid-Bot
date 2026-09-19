import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DEFAULT_CAPTURE_FRESH_MAX_S } from "./capture-freshness";
import { loadPaperRunSnapshot } from "./paper-run";
import {
  RISK_ASSUMED_PNL_SOURCE,
  RISK_POSITION_SOURCE,
  RISK_PREFLIGHT_MISSING_SOURCE,
  RISK_PREFLIGHT_SOURCE,
  RISK_UNAVAILABLE,
  RISK_UNAVAILABLE_REASON,
  buildRiskView,
  unavailableRiskFields,
  withLiveStripRisk,
} from "./risk";
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
    chip({
      id: "bitvavo",
      chip: "BITVAVO",
      series: "DATA-1E",
      venue: "bitvavo",
      product: "BTC-EUR",
      status: "RUNNING",
      tone: "ok",
      live: true,
      last_part_age: "8s",
      run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
      binding_source: "env",
      error: undefined,
    }),
    chip({
      id: "kraken",
      chip: "KRAKEN",
      series: "DATA-1B",
      venue: "kraken",
      product: "BTC-USD",
      status: "MISSING",
      live: false,
    }),
  ],
  catalog: [],
  provenance: {
    rows: [],
    distinct_run_ids: [TERRAPC_SHARED_RETAIN_RUN_ID, TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID],
    shared_run_ids: [TERRAPC_SHARED_RETAIN_RUN_ID],
    overlap_note: "Overlap starts at the later venue start.",
  },
};

function field(view: ReturnType<typeof buildRiskView>, id: string) {
  return [...view.copied, ...view.unavailable].find((item) => item.id === id);
}

describe("RISK first PAPER slice", () => {
  it("copies assumed overlay PnL and PAPER position without inventing venue risk", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildRiskView(snapshot, undefined, { ok: true, strip: boundStrip });

    expect(view.available).toBe(true);
    expect(view.mode).toBe("PAPER");
    expect(view.instrument).toBe("BTC-USD-PERP.HYPERLIQUID");
    expect(view.positionBtc).toBe("0.00000 BTC");
    expect(view.assumedNetPnl).toBe("-0.0126583080 USDC");
    expect(view.maxAssumedLoss).toBe("0.25 USDC");
    expect(view.captureLine).toBe(
      "2 live · 1 stale · 0 stopped · 0 degraded · 1 missing · fresh ≤ 180s",
    );

    expect(field(view, "assumed-pnl")).toMatchObject({
      value: "-0.0126583080 USDC",
      kind: "assumed",
      source: RISK_ASSUMED_PNL_SOURCE,
    });
    expect(field(view, "assumed-flag")).toMatchObject({ value: "yes / no", kind: "assumed" });
    expect(field(view, "position")).toMatchObject({
      value: "0.00000 BTC",
      kind: "copied",
      source: RISK_POSITION_SOURCE,
    });
    expect(field(view, "venue-authoritative")).toMatchObject({ value: "no" });
    expect(field(view, "max-entry-notional")).toMatchObject({
      value: "15 USDC",
      source: RISK_PREFLIGHT_SOURCE,
      kind: "copied",
    });
    expect(field(view, "risk-rejections")).toMatchObject({ value: "0" });
    expect(field(view, "signing")).toMatchObject({ value: "off" });
    expect(field(view, "live-mid-for-pnl")).toMatchObject({ value: "no" });
  });

  it("keeps leverage, margin, liquidation, VaR, and venue risk UNAVAILABLE", () => {
    const missing = unavailableRiskFields();
    expect(missing.map((row) => row.id)).toEqual([
      "leverage",
      "margin",
      "liquidation",
      "var",
      "expected-shortfall",
      "venue-risk",
      "gross-exposure",
      "net-exposure",
      "drawdown",
      "correlation",
      "volatility",
    ]);
    expect(missing.every((row) => row.value === RISK_UNAVAILABLE)).toBe(true);
    expect(missing.every((row) => row.source === RISK_UNAVAILABLE_REASON)).toBe(true);

    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildRiskView(snapshot, undefined, { ok: true, strip: boundStrip });
    expect(view.unavailable.map((row) => `${row.label}:${row.value}`)).toEqual([
      "Leverage:UNAVAILABLE",
      "Margin:UNAVAILABLE",
      "Liquidation:UNAVAILABLE",
      "VaR:UNAVAILABLE",
      "Expected Shortfall:UNAVAILABLE",
      "Venue risk:UNAVAILABLE",
      "Gross exposure:UNAVAILABLE",
      "Net exposure:UNAVAILABLE",
      "Drawdown:UNAVAILABLE",
      "Correlation:UNAVAILABLE",
      "Volatility:UNAVAILABLE",
    ]);
  });

  it("does not invent preflight bounds when the claim omits them", () => {
    const snapshot = loadPaperRunSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_PAPER_RUN_DIR: resolve(repoRoot, "tests/fixtures/course1_cockpit/sample-run"),
      },
      repoRoot,
    );
    const view = buildRiskView(snapshot, undefined, { ok: true, strip: boundStrip });
    expect(view.maxAssumedLoss).toBe(RISK_UNAVAILABLE);
    expect(field(view, "max-assumed-loss")).toMatchObject({
      value: RISK_UNAVAILABLE,
      kind: "unavailable",
      source: RISK_PREFLIGHT_MISSING_SOURCE,
    });
    expect(field(view, "max-entry-notional")?.value).toBe(RISK_UNAVAILABLE);
    expect(field(view, "order-qty")?.value).toBe(RISK_UNAVAILABLE);
    expect(field(view, "same-d01-smoke-risk")?.value).toBe(RISK_UNAVAILABLE);
    expect(view.assumedNetPnl).toBe("0 USDC");
  });

  it("fails closed when PAPER JSON is missing and does not invent PnL", () => {
    const view = buildRiskView(undefined, "PAPER run data is unavailable.", {
      ok: true,
      strip: boundStrip,
    });
    expect(view.available).toBe(false);
    expect(view.positionBtc).toBe(RISK_UNAVAILABLE);
    expect(view.assumedNetPnl).toBe(RISK_UNAVAILABLE);
    expect(view.maxAssumedLoss).toBe(RISK_UNAVAILABLE);
    expect(view.captureLine).toMatch(/2 live · 1 stale/);
    expect(field(view, "assumed-pnl")?.value).toBe(RISK_UNAVAILABLE);
    expect(field(view, "leverage")?.value).toBe(RISK_UNAVAILABLE);
  });

  it("fails closed on capture freshness when the venue strip is unavailable", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildRiskView(snapshot, undefined, {
      ok: false,
      error: "Cockpit first screen is PAPER-only and fails closed.",
    });
    expect(view.captureLine).toBe(RISK_UNAVAILABLE);
    expect(field(view, "capture")).toMatchObject({
      value: RISK_UNAVAILABLE,
      kind: "unavailable",
    });
    expect(view.assumedNetPnl).toBe("-0.0126583080 USDC");
  });

  it("refreshes capture freshness from the live strip without inventing PnL or mids", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const initial = buildRiskView(snapshot, undefined, {
      ok: false,
      error: "Cockpit first screen is PAPER-only and fails closed.",
    });
    expect(initial.captureLine).toBe(RISK_UNAVAILABLE);

    const live = withLiveStripRisk(initial, { ok: true, strip: boundStrip });
    expect(live.assumedNetPnl).toBe(initial.assumedNetPnl);
    expect(live.positionBtc).toBe(initial.positionBtc);
    expect(live.captureLine).toBe(
      "2 live · 1 stale · 0 stopped · 0 degraded · 1 missing · fresh ≤ 180s",
    );
    expect(field(live, "capture")).toMatchObject({
      value: "2 live · 1 stale · 0 stopped · 0 degraded · 1 missing · fresh ≤ 180s",
      kind: "copied",
    });
    expect(JSON.stringify(live)).not.toContain("invented");
  });
});
