import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { loadPaperRunSnapshot } from "./paper-run";
import { CANONICAL_LIVE_PAPER_RUN_ID } from "./paths";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

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
    expect(snapshot.orders.intents.map((intent) => intent.client_order_id)).toEqual([
      "O-20260904-002014-001-D01-1",
      "O-20260904-002014-001-D01-2",
    ]);
    expect(snapshot.fills.fills.map((fill) => fill.price)).toEqual(["81143.0", "81143.0"]);
    expect(snapshot.orders.intents.every((intent) => intent.risk_reasons === undefined)).toBe(true);
  });

  it("surfaces run provenance and preflight risk caps from the claim", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    expect(snapshot.claim.run_identity).toBe(
      "f779d9e7273309fc56e6368fda5edeb782f2696b0c55a00d059c7ee89fb84151",
    );
    expect(snapshot.claim.config_sha256).toBe(
      "4cf8f12887996488639ef188ecd400d66fdff62d10e24d81a6600a6d6d67ba9a",
    );
    expect(snapshot.claim.source_sha256).toBe(
      "835c55df712d65077b9cb117d8c95f0572c57e643ce23b0181622d3a59076b59",
    );
    expect(snapshot.claim.websocket_url).toBe("wss://api.hyperliquid.xyz/ws");
    expect(snapshot.claim.resume_policy).toBe(
      "never resume or overwrite an existing soak run directory",
    );
    expect(snapshot.claim.preflight).toEqual({
      strategy_class: "vertical_slices.d01_btc_perp.slice.D01SmokeStrategy",
      order_quantity_btc: "0.00013",
      max_entry_notional_usdc: "15",
      max_assumed_loss_usdc: "0.25",
      same_d01_smoke_risk: true,
    });
  });

  it("leaves provenance and preflight undefined when the claim omits them", () => {
    const snapshot = loadPaperRunSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_PAPER_RUN_DIR: join(repoRoot, "tests/fixtures/course1_cockpit/sample-run"),
      },
      repoRoot,
    );
    expect(snapshot.claim.run_identity).toBeUndefined();
    expect(snapshot.claim.config_sha256).toBeUndefined();
    expect(snapshot.claim.source_sha256).toBeUndefined();
    expect(snapshot.claim.websocket_url).toBeUndefined();
    expect(snapshot.claim.preflight).toBeUndefined();
  });

  it("fails closed on a malformed preflight block instead of inventing caps", () => {
    const runDir = mkdtempSync(join(tmpdir(), "cockpit-preflight-"));
    writeFileSync(
      join(runDir, "run-claim.json"),
      JSON.stringify({
        mode: "PAPER",
        run_id: "malformed-preflight",
        schema: "course1-live-public-paper-v1",
        preflight: {
          order_quantity_btc: "0.00013",
          max_entry_notional_usdc: "15",
          max_assumed_loss_usdc: "0.25",
          same_d01_smoke_risk: true,
        },
      }),
    );
    expect(() =>
      loadPaperRunSnapshot({ TRADING_MODE: "PAPER", COCKPIT_PAPER_RUN_DIR: runDir }, repoRoot),
    ).toThrow(/preflight is missing non-empty string field strategy_class/);
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

  it("copies an empty PAPER blotter without inventing intents", () => {
    const snapshot = loadPaperRunSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_PAPER_RUN_DIR: join(repoRoot, "tests/fixtures/course1_cockpit/sample-run"),
      },
      repoRoot,
    );
    expect(snapshot.orders.order_count).toBe(0);
    expect(snapshot.orders.intents).toEqual([]);
    expect(snapshot.fills.fill_count).toBe(0);
    expect(snapshot.fills.fills).toEqual([]);
    expect(snapshot.pnl.net_pnl_usdc_assumed).toBe("0");
  });
});
