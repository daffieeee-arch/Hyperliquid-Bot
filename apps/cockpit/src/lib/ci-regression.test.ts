import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { GET as getPublicBtcPerp } from "../app/api/public-btc-perp/route";
import { GET as getPublicCandles } from "../app/api/public-btc-perp-candles/route";
import { formatAssumedUsdcDisplay } from "./display";
import {
  ASSUMED_PNL_LABEL,
  D22B_BLOCKED,
  GATE_EXAMPLE_SOURCE,
  NOT_VENUE_RECONCILED,
  buildPaperBotView,
} from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import { parsePanelSummary } from "./research-p0";
import { PUBLIC_MID_NOT_RESEARCH } from "./research-p0-view";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

const panelReady = {
  trading_mode: "PAPER",
  verdict: "panel_ready",
  panel_version: "panel_hl_binance/wp-q1.1",
  sufficiency: { enough_data: true, reasons: [] },
  hl_gap_fraction: 0.01,
  bn_gap_fraction: 0.02,
  overlap_bucket_count: 48,
};

describe("CI PAPER regression contracts", () => {
  it("refuses cockpit public routes when TRADING_MODE is LIVE", async () => {
    const previous = process.env.TRADING_MODE;
    process.env.TRADING_MODE = "LIVE";
    try {
      const price = await getPublicBtcPerp();
      const candles = await getPublicCandles(
        new Request("http://127.0.0.1/api/public-btc-perp-candles?interval=15m"),
      );
      expect(price.status).toBe(403);
      expect(candles.status).toBe(403);
      expect(await price.json()).toMatchObject({ error: expect.stringMatching(/PAPER-only/) });
    } finally {
      if (previous === undefined) {
        delete process.env.TRADING_MODE;
      } else {
        process.env.TRADING_MODE = previous;
      }
    }
  });

  it("keeps assumed_pnl operator precision and refuses venue-PnL labeling", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(formatAssumedUsdcDisplay(snapshot.pnl.net_pnl_usdc_assumed)).toBe("-0.0127");
    expect(view.results.assumedPnl).toBe("-0.0127 USDC");
    expect(view.results.assumedPnlExact).toBe("-0.0126583080 USDC");
    expect(view.results.assumedPnlLabel).toBe(ASSUMED_PNL_LABEL);
    expect(view.results.venueReconciled).toBe(NOT_VENUE_RECONCILED);
    expect(view.results.d22b).toBe(D22B_BLOCKED);
    expect(view.results.venuePnl).toBe("no");
  });

  it("shows the documented #65 / risk_based_size REJECT next to, never inside, the soak tape", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    // The documented gate stays visible as a reference example (#66 design gate) ...
    const example = view.gateExamples.find((row) => row.gateCode === "risk_based_size");
    expect(example?.source).toBe(GATE_EXAMPLE_SOURCE);
    expect(example?.reason).not.toBe("");
    // ... but run history is exactly what orders.json recorded (#67 review finding):
    // the ACCEPT-only fixture must not gain an invented REJECT row.
    expect(view.tape.some((row) => row.outcome === "REJECT")).toBe(false);
    expect(view.tapeRejects.none).toBe(true);
  });

  it("fails closed when a research summary treats live mid as research truth", () => {
    expect(PUBLIC_MID_NOT_RESEARCH).toBe("public mid ≠ research truth");
    expect(() =>
      parsePanelSummary({ ...panelReady, live_mid: "81156.0" }, "mutated-live-mid.json"),
    ).toThrow(/live mid as research truth/);
    expect(() =>
      parsePanelSummary(
        { ...panelReady, research_truth: "public mid" },
        "mutated-research-truth.json",
      ),
    ).toThrow(/live mid as research truth/);
  });
});
