import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { loadPaperRunSnapshot } from "./paper-run";
import { CANONICAL_LIVE_PAPER_RUN_ID } from "./paths";

const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));

describe("PAPER run loader", () => {
  it("copies assumed overlay fields from the live-public-soak fixture", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    expect(snapshot.runId).toBe(CANONICAL_LIVE_PAPER_RUN_ID);
    expect(snapshot.claim.mode).toBe("PAPER");
    expect(snapshot.position.final_position_btc).toBe("0.00000");
    expect(snapshot.position.venue_authoritative).toBe(false);
    expect(snapshot.pnl.assumed).toBe(true);
    expect(snapshot.pnl.venue_pnl).toBe(false);
    expect(snapshot.pnl.net_pnl_usdc_assumed).toBe("-0.0126583080");
    expect(snapshot.pnl.funding_payment_usdc).toBe("0");
    expect(snapshot.pnl.starting_cash_usdc_assumed).toBe("100000");
    expect(snapshot.health.status).toBe("COMPLETED_FLAT");
    expect(snapshot.health.twenty_four_seven).toBe(false);
    expect(snapshot.health.trade_count).toBe(20);
    expect(snapshot.orders.order_count).toBe(2);
    expect(snapshot.fills.fill_count).toBe(2);
    expect(snapshot.orders.venue_orders_submitted).toBe(false);
  });

  it("does not invent PnL when a required file is missing", () => {
    const runDir = mkdtempSync(join(tmpdir(), "cockpit-empty-"));
    expect(() =>
      loadPaperRunSnapshot({ TRADING_MODE: "PAPER", COCKPIT_PAPER_RUN_DIR: runDir }, repoRoot),
    ).toThrow(/missing or unreadable/);
  });

  it("refuses LIVE trading mode before reading numbers", () => {
    expect(() => loadPaperRunSnapshot({ TRADING_MODE: "LIVE" }, repoRoot)).toThrow(/PAPER-only/);
  });
});
