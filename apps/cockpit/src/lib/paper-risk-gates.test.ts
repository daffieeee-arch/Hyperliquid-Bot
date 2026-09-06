import { describe, expect, it } from "vitest";

import {
  PAPER_HARD_LIMIT_GATES,
  REDUCE_ONLY_AFTER_HALT_RULE,
  paperRiskGateIds,
  paperRiskRejectionView,
  resolvePaperRiskGate,
} from "./paper-risk-gates";

describe("paper_risk gate catalog", () => {
  it("copies the #65 documented gates and does not invent a halt state", () => {
    expect(paperRiskGateIds()).toEqual([
      "risk_based_size",
      "max_paper_leverage",
      "max_gross_exposure",
      "max_net_exposure",
      "max_simultaneous_positions",
      "max_asset_concentration",
      "max_strategy_concentration",
      "max_venue_concentration",
      "daily_loss_guard",
      "weekly_loss_guard",
      "drawdown_kill",
      "no_averaging_down",
    ]);
    const haltIds = PAPER_HARD_LIMIT_GATES.filter((gate) => gate.kind === "halt").map(
      (gate) => gate.id,
    );
    expect(haltIds).toEqual(["daily_loss_guard", "weekly_loss_guard", "drawdown_kill"]);
    const view = paperRiskRejectionView(0);
    expect(view.copiedRejectionCount).toBe("0");
    expect(view.haltState).toBe("UNAVAILABLE");
    expect(view.reduceOnlyAfterHalt).toBe(REDUCE_ONLY_AFTER_HALT_RULE);
    expect(resolvePaperRiskGate("risk_based_size")?.id).toBe("risk_based_size");
    expect(resolvePaperRiskGate("drawdown_kill")?.kind).toBe("halt");
    expect(
      resolvePaperRiskGate("entry notional exceeds risk-based size (risk budget / stop distance)")
        ?.id,
    ).toBe("risk_based_size");
  });
});
