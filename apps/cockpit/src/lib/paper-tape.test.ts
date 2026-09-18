import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { joinIntentsToFills } from "./intent-fill";
import {
  GATE_EXAMPLE_SOURCE,
  buildPaperBotView,
  paperRiskGateExamples,
  summariseTapeRejects,
} from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import type { PaperFillRow, PaperOrderIntent } from "./types";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

const entry: PaperOrderIntent = {
  client_order_id: "O-1",
  side: "BUY",
  quantity: "0.001",
  order_type: "MARKET",
  reason: "entry",
  reduce_only: false,
};

const fill: PaperFillRow = {
  fill_ordinal: 1,
  side: "BUY",
  price: "81143.0",
  quantity: "0.001",
  liquidity_side: "TAKER",
  position_after: "0.001",
};

describe("PAPER intent tape stays run history", () => {
  it("contains exactly the intents the run recorded, with no illustrative row", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.tape).toHaveLength(snapshot.orders.intents.length);
    const copiedIds = snapshot.orders.intents.map((intent) => intent.client_order_id);
    expect(view.tape.map((row) => row.clientOrderId)).toEqual(copiedIds);
    expect(view.tape.every((row) => !row.clientOrderId.startsWith("DEMO"))).toBe(true);
  });

  it("reports honestly that this ACCEPT-only run has no reject", () => {
    const snapshot = loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot);
    const view = buildPaperBotView(snapshot, undefined);
    expect(view.tapeRejects.count).toBe(0);
    expect(view.tapeRejects.none).toBe(true);
    expect(view.tapeRejects.note).toMatch(/no paper_risk reject/i);
  });

  it("keeps catalog examples in a separate shape that cannot be a tape row", () => {
    const examples = paperRiskGateExamples();
    expect(examples.length).toBeGreaterThan(0);
    expect(examples.map((example) => example.gateCode)).toContain("risk_based_size");
    for (const example of examples) {
      expect(example.source).toBe(GATE_EXAMPLE_SOURCE);
      expect(example).not.toHaveProperty("clientOrderId");
      expect(example).not.toHaveProperty("outcome");
    }
  });

  it("surfaces a real reject with its copied gate code when the run has one", () => {
    const rejected: PaperOrderIntent = {
      ...entry,
      client_order_id: "O-reject",
      risk_reasons: ["entry notional exceeds risk-based size (risk budget / stop distance)"],
    };
    const tape = joinIntentsToFills([entry, rejected], [fill]);
    const summary = summariseTapeRejects(tape);
    expect(summary.count).toBe(1);
    expect(summary.none).toBe(false);
    expect(summary.gateCodes).toEqual(["risk_based_size"]);
    expect(summary.note).toMatch(/copied from orders\.json/);
  });
});
