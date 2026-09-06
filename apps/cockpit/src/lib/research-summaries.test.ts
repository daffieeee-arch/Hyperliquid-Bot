import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PANEL_VERSION_EXPECTED } from "./research-p0";
import {
  listResearchSummaries,
  readResearchSummary,
  resolveResearchSummaryPath,
} from "./research-summaries";

describe("research-out summary viewer", () => {
  it("stays UNAVAILABLE when research-out is not pointed", () => {
    const list = listResearchSummaries({ TRADING_MODE: "PAPER" });
    expect(list.items).toEqual([]);
    expect(list.note).toMatch(/UNAVAILABLE/);
    expect(readResearchSummary({ TRADING_MODE: "PAPER" }, "wp-q1/panel-summary.json").verdict).toBe(
      "UNAVAILABLE",
    );
  });

  it("lists and reads panel-summary.json without inventing paths", () => {
    const root = mkdtempSync(join(tmpdir(), "research-summaries-"));
    mkdirSync(join(root, "wp-q1"), { recursive: true });
    writeFileSync(
      join(root, "wp-q1", "panel-summary.json"),
      `${JSON.stringify({
        trading_mode: "PAPER",
        verdict: "not_enough_data",
        panel_version: PANEL_VERSION_EXPECTED,
        sufficiency: { enough_data: false, reasons: ["overlap too short"] },
        hl_gap_fraction: 0.4,
        bn_gap_fraction: 0.1,
        overlap_bucket_count: 3,
      })}\n`,
      { encoding: "utf8" },
    );
    const env = { TRADING_MODE: "PAPER", COCKPIT_RESEARCH_OUT: root };
    const list = listResearchSummaries(env);
    expect(list.items).toEqual([
      {
        path: join("wp-q1", "panel-summary.json"),
        verdict: "not_enough_data",
        panelVersion: PANEL_VERSION_EXPECTED,
      },
    ]);
    const summary = readResearchSummary(env, "wp-q1/panel-summary.json");
    expect(summary.verdict).toBe("not_enough_data");
    expect(summary.reasons).toEqual(["overlap too short"]);
    expect(summary.hlGapFraction).toBe("0.4");
    expect(() => resolveResearchSummaryPath(root, "../secret/panel-summary.json")).toThrow(
      /not a panel-summary/,
    );
    expect(() => resolveResearchSummaryPath(root, "notes.txt")).toThrow(/panel-summary.json/);
  });
});
