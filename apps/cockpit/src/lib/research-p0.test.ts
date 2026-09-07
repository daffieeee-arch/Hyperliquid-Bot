import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { CANONICAL_DATA1A_FIXTURE_RUN_ID } from "./paths";
import {
  BINANCE_IDENTITY_WARNING,
  BINANCE_IMPULSE_DEFAULT,
  H1_LEADLAG_NOTE,
  PANEL_VERSION_EXPECTED,
  PUBLIC_MID_NOT_RESEARCH,
  RESEARCH_UNAVAILABLE,
  RESEARCH_ZONE_KICKER,
  buildOverlapClock,
  buildResearchIdentity,
  buildResearchP0View,
  parsePanelSummary,
} from "./research-p0";
import { loadVenueCaptureStrip } from "./venue-capture";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const observedAt = "2026-09-06T12:00:00.000Z";

const panelReady = {
  trading_mode: "PAPER",
  verdict: "panel_ready",
  panel_version: PANEL_VERSION_EXPECTED,
  sufficiency: { enough_data: true, reasons: [] },
  hl_gap_fraction: 0.01,
  bn_gap_fraction: 0.02,
  overlap_bucket_count: 48,
};

describe("research P0", () => {
  it("keeps instrument identity explicit and never blends Spot with USDM", () => {
    const identity = buildResearchIdentity();
    expect(identity.map((row) => row.id)).toEqual(["hl", "binance", "bitvavo", "kraken"]);
    expect(identity[0]?.product).toBe("BTC-PERP");
    expect(identity[1]?.impulse).toContain(BINANCE_IMPULSE_DEFAULT);
    expect(identity[1]?.impulse).toContain("explicit switch");
    expect(identity[2]?.product).toBe("BTC-EUR");
    expect(identity[3]?.product).toBe("BTC-USD");
    expect(BINANCE_IDENTITY_WARNING).toMatch(/Never blend Spot and USDM/);
    expect(H1_LEADLAG_NOTE).toMatch(/promotion_decision=forbidden/);
  });

  it("never claims 72h elapsed on the overlap clock", () => {
    const strip = loadVenueCaptureStrip({ TRADING_MODE: "PAPER" }, repoRoot, {}, () => observedAt);
    const clock = buildOverlapClock(strip);
    expect(clock.elapsed).toBe(RESEARCH_UNAVAILABLE);
    expect(clock.elapsed).not.toMatch(/72/);
    expect(["partial", "gate_pending", "aligned"]).toContain(clock.badge);
  });

  it("copies WP-Q1 panel-summary fields and fails closed on LIVE or edge tokens", () => {
    const copied = parsePanelSummary(panelReady, "wp-q1/panel-summary.json");
    expect(copied.verdict).toBe("panel_ready");
    expect(copied.hlGapFraction).toBe("0.01");
    expect(copied.bnGapFraction).toBe("0.02");
    expect(copied.overlapBuckets).toBe("48");
    expect(copied.panelVersion).toBe(PANEL_VERSION_EXPECTED);
    expect(() => parsePanelSummary({ ...panelReady, trading_mode: "LIVE" }, "bad.json")).toThrow(
      /PAPER/,
    );
    expect(() => parsePanelSummary({ ...panelReady, edge: 0.12 }, "bad.json")).toThrow(/edge/);
    expect(() =>
      parsePanelSummary({ ...panelReady, promotion_decision: "forbidden" }, "bad.json"),
    ).toThrow(/promotion/);
    expect(() => parsePanelSummary({ ...panelReady, live_mid: "81156.0" }, "bad.json")).toThrow(
      /live mid as research truth/,
    );
  });

  it("builds a fail-closed P0 view from default fixtures without inventing Quant fields", () => {
    const strip = loadVenueCaptureStrip({ TRADING_MODE: "PAPER" }, repoRoot, {}, () => observedAt);
    const view = buildResearchP0View(
      { ok: true, strip },
      { TRADING_MODE: "PAPER" },
      repoRoot,
      {},
      observedAt,
    );
    expect(view.registry[0]?.runId).toBe(CANONICAL_DATA1A_FIXTURE_RUN_ID);
    expect(view.registry[0]?.retained).toBe("yes");
    expect(view.health[0]?.transportProfiles).toBe(RESEARCH_UNAVAILABLE);
    expect(view.health[1]?.gapsReconnects).toBe("n/a");
    expect(view.sufficiency.verdict).toBe(RESEARCH_UNAVAILABLE);
    expect(view.sufficiency.reasons[0]).toMatch(/UNAVAILABLE|not pointed|No panel-summary/i);
    expect(view.overlap.elapsed).toBe(RESEARCH_UNAVAILABLE);
    expect(view.identityWarning).toBe(BINANCE_IDENTITY_WARNING);
    expect(RESEARCH_ZONE_KICKER).toMatch(/Artifact \/ summary cards only/);
    expect(PUBLIC_MID_NOT_RESEARCH).toMatch(/public mid/);
  });

  it("copies a pointed panel-summary.json and leaves missing mid-run health as UNAVAILABLE", () => {
    const researchOut = mkdtempSync(join(tmpdir(), "research-out-"));
    mkdirSync(join(researchOut, "wp-q1"), { recursive: true });
    writeFileSync(
      join(researchOut, "wp-q1", "panel-summary.json"),
      `${JSON.stringify(panelReady)}\n`,
      { encoding: "utf8" },
    );
    const strip = loadVenueCaptureStrip({ TRADING_MODE: "PAPER" }, repoRoot, {}, () => observedAt);
    const view = buildResearchP0View(
      { ok: true, strip },
      { TRADING_MODE: "PAPER", COCKPIT_RESEARCH_OUT: researchOut },
      repoRoot,
      {},
      observedAt,
    );
    expect(view.sufficiency.verdict).toBe("panel_ready");
    expect(view.sufficiency.source).toBe(join("wp-q1", "panel-summary.json"));
    expect(view.health[0]?.healthPending).toBe(false);
  });
});
