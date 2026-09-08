import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { RISK_REASON_UNAVAILABLE } from "./intent-fill";
import {
  ASSUMED_PNL_LABEL,
  COURSE1_SOAK_KIND,
  D22B_BLOCKED,
  NOT_VENUE_RECONCILED,
  PAPER_BOT_UNAVAILABLE,
  buildLastDecision,
  buildPaperBotView,
  course1SoakClaimPath,
} from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import {
  DOCUMENTED_MAX_LEVERAGE,
  DOCUMENTED_MAX_POSITIONS,
  DOCUMENTED_RISK_PER_TRADE,
} from "./paper-risk-gates";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

describe("DESK COURSE-1 soak card", () => {
  it("labels What as COURSE-1 soak and keeps DATA retain off the card", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.available).toBe(true);
    expect(view.what.kind).toBe(COURSE1_SOAK_KIND);
    expect(view.what.mode).toBe("PAPER");
    expect(view.what.runId).toBe("20260904t001800z-live-paper");
    expect(view.what.claimPath).toBe(course1SoakClaimPath(snapshot.runId));
    expect(view.what.claimPath).toBe("course1/live-public-paper/20260904t001800z-live-paper");
    expect(view.what.pathContract).toBe("course1-live-public-paper-cockpit-v1");
    expect(view.what.claimPath).not.toMatch(/data-1|retain/i);
    expect(view.what.venueOrdersSubmitted).toBe("no");
  });

  it("copies last ACCEPT from a filled soak intent and does not invent a gate", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.why.outcome).toBe("ACCEPT");
    expect(view.why.gateName).toBe(PAPER_BOT_UNAVAILABLE);
    expect(view.why.reason).toBe("exit");
    expect(view.why.intentId).toBe("O-20260904-002014-001-D01-2");
  });

  it("labels results assumed_pnl and blocks D22-B / venue reconcile", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.results.side).toBe("FLAT");
    expect(view.results.size).toBe("0.00000 BTC");
    expect(view.results.entry).toBe(PAPER_BOT_UNAVAILABLE);
    expect(view.results.mark).toBe("81156.0");
    expect(view.results.assumedPnl).toBe("-0.0127 USDC");
    expect(view.results.assumedPnlExact).toBe("-0.0126583080 USDC");
    expect(view.results.assumedPnlLabel).toBe(ASSUMED_PNL_LABEL);
    expect(view.results.venueReconciled).toBe(NOT_VENUE_RECONCILED);
    expect(view.results.d22b).toBe(D22B_BLOCKED);
    expect(view.results.venuePnl).toBe("no");
  });

  it("shows last-N tape with rejected rows and a read-only #65 caps strip", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.tape).toHaveLength(2);
    expect(view.tape[0]?.outcome).toBe("ACCEPT");
    expect(view.tape[0]?.gateCode).toBe(RISK_REASON_UNAVAILABLE);
    expect(view.tape[view.tape.length - 1]?.outcome).toBe("ACCEPT");
    expect(view.preflight.equity).toBe("100000 USDC assumed");
    expect(view.preflight.riskPerTrade).toBe(DOCUMENTED_RISK_PER_TRADE);
    expect(view.preflight.maxLeverage).toBe(DOCUMENTED_MAX_LEVERAGE);
    expect(view.preflight.maxGross).toBe("1.0×");
    expect(view.preflight.maxNet).toBe("1.0×");
    expect(view.preflight.maxPositions).toBe(DOCUMENTED_MAX_POSITIONS);
    expect(view.preflight.maxEntryNotionalUsdc).toBe("15 USDC");
    expect(view.preflight.maxAssumedLossUsdc).toBe("0.25 USDC");
    expect(view.preflight.documentedSource).toMatch(/read-only/);
  });

  it("copies a reject gate name from paper_risk codes", () => {
    const why = buildLastDecision([
      {
        clientOrderId: "O-reject",
        side: "BUY",
        quantity: "0.01",
        orderType: "MARKET",
        intentReason: "entry",
        reduceOnly: false,
        riskReasons: "entry notional exceeds risk-based size (risk budget / stop distance)",
        riskReasonSource: "orders.json paper_risk reason codes",
        gateCode: "risk_based_size",
        gateReason: "entry notional exceeds risk-based size (risk budget / stop distance)",
        outcome: "REJECT",
        fillOrdinal: RISK_REASON_UNAVAILABLE,
        fillPrice: RISK_REASON_UNAVAILABLE,
        fillLiquidity: RISK_REASON_UNAVAILABLE,
        positionAfter: RISK_REASON_UNAVAILABLE,
        matched: false,
      },
    ]);
    expect(why.outcome).toBe("REJECT");
    expect(why.gateName).toBe("risk_based_size");
    expect(why.reason).toMatch(/risk-based size/);
  });

  it("fails closed when PAPER JSON is missing", () => {
    const view = buildPaperBotView(undefined, "PAPER run data is unavailable.");
    expect(view.available).toBe(false);
    expect(view.what.kind).toBe(COURSE1_SOAK_KIND);
    expect(view.what.runId).toBe(PAPER_BOT_UNAVAILABLE);
    expect(view.results.assumedPnl).toBe(PAPER_BOT_UNAVAILABLE);
    expect(view.preflight.available).toBe(false);
    expect(view.tape).toEqual([]);
  });
});
