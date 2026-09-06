import { describe, expect, it } from "vitest";

import { RISK_REASON_UNAVAILABLE, joinIntentsToFills } from "./intent-fill";
import type { PaperFillRow, PaperOrderIntent } from "./types";

const entry: PaperOrderIntent = {
  client_order_id: "O-1",
  side: "BUY",
  quantity: "0.00013",
  order_type: "MARKET",
  reason: "entry",
  reduce_only: false,
};

const exit: PaperOrderIntent = {
  client_order_id: "O-2",
  side: "SELL",
  quantity: "0.00013",
  order_type: "MARKET",
  reason: "exit",
  reduce_only: true,
};

const fill: PaperFillRow = {
  fill_ordinal: 1,
  side: "BUY",
  price: "81143.0",
  quantity: "0.00013",
  liquidity_side: "TAKER",
  position_after: "0.00013",
};

describe("intent to fill tape", () => {
  it("copies optional paper_risk reason codes and otherwise stays UNAVAILABLE", () => {
    const rejected: PaperOrderIntent = {
      ...entry,
      risk_reasons: ["entry notional exceeds risk-based size (risk budget / stop distance)"],
    };
    const [copied] = joinIntentsToFills([rejected], []);
    expect(copied?.riskReasons).toBe(
      "entry notional exceeds risk-based size (risk budget / stop distance)",
    );
    expect(copied?.outcome).toBe("REJECT");
    expect(copied?.gateCode).toBe("risk_based_size");
    const [missing] = joinIntentsToFills([entry], []);
    expect(missing?.riskReasons).toBe(RISK_REASON_UNAVAILABLE);
    expect(missing?.outcome).toBe("UNAVAILABLE");
    expect(missing?.matched).toBe(false);
  });

  it("matches same-side same-qty fill without inventing a fill", () => {
    const [row] = joinIntentsToFills([entry, exit], [fill]);
    expect(row?.matched).toBe(true);
    expect(row?.outcome).toBe("ACCEPT");
    expect(row?.fillPrice).toBe("81143.0");
    const unmatched = joinIntentsToFills([{ ...entry, side: "SELL" }], [fill]);
    expect(unmatched[0]?.matched).toBe(false);
    expect(unmatched[0]?.fillPrice).toBe(RISK_REASON_UNAVAILABLE);
  });
});
