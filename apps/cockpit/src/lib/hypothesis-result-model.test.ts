import { describe, expect, it } from "vitest";

import {
  HYPOTHESIS_RESULT_SCHEMA,
  RESULT_UNAVAILABLE,
  configsLabel,
  parseHypothesisResultListPayload,
  parseHypothesisResultObject,
  parseReportMarkdown,
  presentPromotion,
  promotionSummary,
} from "./hypothesis-result-model";

const base = {
  schema_version: HYPOTHESIS_RESULT_SCHEMA,
  trading_mode: "PAPER",
  work_package: "WP1",
  hypothesis_id: "H-1",
  title: "taker-flow x vol",
  label: "interesting_but_fragile",
  passes_h1: false,
  configs_tested: 12,
  configs_passed: 0,
  best_gross_bps_per_trade: 1.4,
  promotion_decision: "forbidden",
  updated_at: "2026-10-06T08:15:00.000Z",
};

describe("hypothesis result object", () => {
  it("copies recorded fields and leaves an omitted net as UNAVAILABLE", () => {
    const item = parseHypothesisResultObject("wp1", "wp1/result.json", {
      ...base,
      oos: { start: "2026-04-01T00:00:00.000Z", end: "2026-05-01T00:00:00.000Z" },
      data_range: { start: "2026-01-01T00:00:00.000Z", end: "2026-06-01T00:00:00.000Z" },
    });
    expect(item.status).toBe("ok");
    expect(item.passesH1).toBe("no");
    expect(item.grossBps).toBe("1.4");
    expect(item.netBps).toBe(RESULT_UNAVAILABLE);
    expect(item.configsPassed).toBe("0");
    expect(configsLabel(item.configsPassed, item.configsTested)).toBe("0 / 12");
    expect(item.oosWindow).toBe("2026-04-01 02:00:00 CEST – 2026-05-01 02:00:00 CEST");
    expect(item.dataRange).toContain("2026-01-01 01:00:00 CET");
    expect(item.holdoutWindow).toBe(RESULT_UNAVAILABLE);
    expect(item.promotionDecision).toBe("forbidden");
  });

  it("does not treat a string pass, a missing count, or a blank object as numbers", () => {
    const coerced = parseHypothesisResultObject("wp", "wp/result.json", {
      ...base,
      passes_h1: "true",
      configs_passed: null,
      best_net_bps_per_trade: null,
    });
    expect(coerced.passesH1).toBe(RESULT_UNAVAILABLE);
    expect(coerced.configsPassed).toBe(RESULT_UNAVAILABLE);
    expect(coerced.netBps).toBe(RESULT_UNAVAILABLE);
    expect(coerced.problems).toMatch(/passes_h1/);

    const empty = parseHypothesisResultObject("empty", "empty/result.json", {});
    expect(empty.status).toBe("ok");
    expect(empty.grossBps).toBe(RESULT_UNAVAILABLE);
    expect(empty.configsTested).toBe(RESULT_UNAVAILABLE);
    expect(empty.passesH1).toBe(RESULT_UNAVAILABLE);
    expect(promotionSummary([empty]).value).toBe("forbidden");
  });

  it("refuses an unknown schema and a non-PAPER trading mode without copying metrics", () => {
    const unknown = parseHypothesisResultObject("wp", "wp/result.json", {
      ...base,
      schema_version: "hypothesis-result-v2",
    });
    expect(unknown.status).toBe("unreadable");
    expect(unknown.grossBps).toBe(RESULT_UNAVAILABLE);
    expect(unknown.problem).toMatch(/schema_version/);

    const live = parseHypothesisResultObject("wp", "wp/result.json", {
      ...base,
      trading_mode: "LIVE",
      best_net_bps_per_trade: 9,
    });
    expect(live.status).toBe("unreadable");
    expect(live.netBps).toBe(RESULT_UNAVAILABLE);
    expect(live.problem).toMatch(/PAPER/);
  });

  it("accepts a digit-string nanosecond window and refuses an unsafe JSON number", () => {
    const fromString = parseHypothesisResultObject("wp", "wp/result.json", {
      ...base,
      oos: { start_utc_ns: "1700000000000000000", end_utc_ns: "1700086400000000000" },
    });
    expect(fromString.oosWindow).not.toBe(RESULT_UNAVAILABLE);
    expect(fromString.oosWindow).toMatch(/CEST|CET/);

    const unsafe = parseHypothesisResultObject("wp", "wp/result.json", {
      ...base,
      oos: { start_utc_ns: 1.7e18, end_utc_ns: 1.7e18 },
    });
    expect(unsafe.oosWindow).toBe(RESULT_UNAVAILABLE);
  });

  it("keeps promotion forbidden unless H1 actually passed", () => {
    expect(presentPromotion("no", "forbidden").gate).toBe("forbidden");
    expect(presentPromotion("no", "promote").conflict).toMatch(/stays forbidden/);
    expect(presentPromotion(RESULT_UNAVAILABLE, "allowed").badge).toBe("forbidden");
    expect(presentPromotion("yes", "forbidden")).toMatchObject({
      gate: "forbidden",
      badge: "forbidden",
    });
    const recorded = presentPromotion("yes", "eligible");
    expect(recorded.gate).toBe("recorded");
    expect(recorded.badge).toBe("eligible");
    expect(recorded.tone).toBe("warn");
  });

  it("renders report markdown as text blocks and does not interpret HTML", () => {
    const blocks = parseReportMarkdown("# Title\n\nGross **1.4** bps.\n\n- first\n\n`<script>`");
    expect(blocks[0]).toMatchObject({ kind: "heading", level: 2, text: "Title" });
    expect(blocks[1]).toMatchObject({ kind: "paragraph" });
    expect(blocks[2]).toMatchObject({ kind: "list", ordered: false });
    const paragraph = blocks[3];
    expect(paragraph?.kind).toBe("paragraph");
    if (paragraph?.kind === "paragraph") {
      expect(paragraph.inlines.map((inline) => inline.text).join("")).toContain("<script>");
    }
  });

  it("drops a corrupt poll row instead of inventing its metrics", () => {
    const list = parseHypothesisResultListPayload({
      ok: true,
      observedAt: "2026-10-06T08:15:00.000Z",
      note: "copied",
      items: [null, { id: "wp1", status: "ok", grossBps: 1.4, passesH1: true }],
    });
    expect(list.items[0]?.status).toBe("unreadable");
    expect(list.items[0]?.grossBps).toBe(RESULT_UNAVAILABLE);
    expect(list.items[1]?.grossBps).toBe(RESULT_UNAVAILABLE);
    expect(list.items[1]?.passesH1).toBe(RESULT_UNAVAILABLE);
    expect(() => parseHypothesisResultListPayload({ ok: false, error: "PAPER-only" })).toThrow(
      /PAPER-only/,
    );
  });
});
