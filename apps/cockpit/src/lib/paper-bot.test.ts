import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { RISK_REASON_UNAVAILABLE } from "./intent-fill";
import {
  ASSUMED_OVERLAY_NOT_VENUE_PNL,
  NOT_VENUE_RECONCILED,
  PAPER_BOT_UNAVAILABLE,
  buildPaperBotView,
} from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import { HALT_STATE_UNAVAILABLE_REASON, REDUCE_ONLY_AFTER_HALT_RULE } from "./paper-risk-gates";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

describe("PAPER bot what / why / results", () => {
  it("labels position and assumed PnL as not venue-reconciled", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.available).toBe(true);
    expect(view.what.positionBtc).toBe("0.00000 BTC");
    expect(view.what.positionLabel).toBe(NOT_VENUE_RECONCILED);
    expect(view.results.assumedNetPnl).toBe("-0.0126583080 USDC");
    expect(view.results.assumedPnlLabel).toBe(ASSUMED_OVERLAY_NOT_VENUE_PNL);
    expect(view.results.venuePnl).toBe("no");
    expect(view.what.venueOrdersSubmitted).toBe("no");
  });

  it("joins intents to fills and leaves missing paper_risk codes UNAVAILABLE", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.tape).toHaveLength(2);
    expect(view.tape[0]?.intentReason).toBe("entry");
    expect(view.tape[0]?.matched).toBe(true);
    expect(view.tape[0]?.fillPrice).toBe("81143.0");
    expect(view.tape[0]?.riskReasons).toBe(RISK_REASON_UNAVAILABLE);
    expect(view.tape[1]?.intentReason).toBe("exit");
    expect(view.tape[1]?.reduceOnly).toBe(true);
    expect(view.preflight.available).toBe(true);
    expect(view.preflight.maxEntryNotionalUsdc).toBe("15 USDC");
    expect(view.preflight.maxAssumedLossUsdc).toBe("0.25 USDC");
    expect(view.riskRejections.copiedRejectionCount).toBe("0");
    expect(view.riskRejections.haltState).toBe("UNAVAILABLE");
    expect(view.riskRejections.haltStateSource).toBe(HALT_STATE_UNAVAILABLE_REASON);
    expect(view.riskRejections.reduceOnlyAfterHalt).toBe(REDUCE_ONLY_AFTER_HALT_RULE);
    expect(view.riskRejections.catalog).toHaveLength(12);
  });

  it("fails closed when PAPER JSON is missing", () => {
    const view = buildPaperBotView(undefined, "PAPER run data is unavailable.");
    expect(view.available).toBe(false);
    expect(view.what.positionBtc).toBe(PAPER_BOT_UNAVAILABLE);
    expect(view.results.assumedNetPnl).toBe(PAPER_BOT_UNAVAILABLE);
    expect(view.preflight.available).toBe(false);
    expect(view.tape).toEqual([]);
  });
});
