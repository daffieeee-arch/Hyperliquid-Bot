import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  classifyRunBinding,
  loadPanelSummaryFromRoot,
  panelSummaryRunIds,
  parsePanelSummary,
} from "./research-p0";
import {
  RESEARCH_UNAVAILABLE,
  researchVerdictTone,
  type ResearchRunBinding,
} from "./research-p0-view";

const PANEL_VERSION = "panel_hl_binance/wp-q1.1";

function summary(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    trading_mode: "PAPER",
    verdict: "panel_ready",
    panel_version: PANEL_VERSION,
    sufficiency: { enough_data: true, reasons: [] },
    hl_gap_fraction: 0.01,
    bn_gap_fraction: 0.02,
    overlap_bucket_count: 48,
    ...overrides,
  };
}

describe("research verdict tone", () => {
  it("never paints a failed sufficiency gate as a positive result", () => {
    expect(researchVerdictTone("not_enough_data")).toBe("warn");
    expect(researchVerdictTone("NOT_ENOUGH_DATA")).toBe("warn");
    expect(researchVerdictTone("insufficient")).toBe("warn");
    expect(researchVerdictTone("gate_pending")).toBe("warn");
    expect(researchVerdictTone("blocked")).toBe("warn");
  });

  it("only greens an allowlisted verdict that is attributable to the bound runs", () => {
    expect(researchVerdictTone("panel_ready")).toBe("ok");
    expect(researchVerdictTone("panel_ready", "mismatched")).toBe("warn");
    expect(researchVerdictTone("panel_ready", "unknown")).toBe("warn");
  });

  it("leaves unknown and unavailable verdicts muted rather than borrowing a colour", () => {
    expect(researchVerdictTone(RESEARCH_UNAVAILABLE)).toBe("muted");
    expect(researchVerdictTone("")).toBe("muted");
    expect(researchVerdictTone("something_new")).toBe("muted");
  });
});

describe("research summary run attribution", () => {
  it("copies every declared run_id and never guesses one", () => {
    expect(panelSummaryRunIds(summary({ run_id: "run-a" }))).toEqual(["run-a"]);
    expect(panelSummaryRunIds(summary({ hl_run_id: "run-a", bn_run_id: "run-b" }))).toEqual([
      "run-a",
      "run-b",
    ]);
    expect(panelSummaryRunIds(summary({ run_ids: ["run-a", "run-a", "run-c"] }))).toEqual([
      "run-a",
      "run-c",
    ]);
    expect(panelSummaryRunIds(summary())).toEqual([]);
  });

  it("classifies binding against the bound selection", () => {
    const cases: [string[], string[], ResearchRunBinding][] = [
      [["run-a"], ["run-a", "run-b"], "matched"],
      [["run-z"], ["run-a"], "mismatched"],
      [[], ["run-a"], "unknown"],
      [["run-a"], [], "unknown"],
    ];
    for (const [summaryRuns, boundRuns, expected] of cases) {
      expect(classifyRunBinding(summaryRuns, boundRuns)).toBe(expected);
    }
  });

  it("parses binding into the sufficiency payload", () => {
    const copied = parsePanelSummary(summary({ run_id: "run-a" }), "wp-q1/panel-summary.json", [
      "run-a",
    ]);
    expect(copied.runIds).toEqual(["run-a"]);
    expect(copied.runBinding).toBe("matched");
    expect(researchVerdictTone(copied.verdict, copied.runBinding)).toBe("ok");
  });

  it("picks the summary that belongs to the bound run instead of the first file on disk", () => {
    const root = mkdtempSync(join(tmpdir(), "research-binding-"));
    mkdirSync(join(root, "aaa-other"), { recursive: true });
    mkdirSync(join(root, "zzz-selected"), { recursive: true });
    writeFileSync(
      join(root, "aaa-other", "panel-summary.json"),
      JSON.stringify(summary({ run_id: "run-other", verdict: "panel_ready" })),
    );
    writeFileSync(
      join(root, "zzz-selected", "panel-summary.json"),
      JSON.stringify(summary({ run_id: "run-selected", verdict: "not_enough_data" })),
    );

    const chosen = loadPanelSummaryFromRoot(root, ["run-selected"]);
    expect(chosen.runIds).toEqual(["run-selected"]);
    expect(chosen.runBinding).toBe("matched");
    expect(chosen.verdict).toBe("not_enough_data");
    expect(researchVerdictTone(chosen.verdict, chosen.runBinding)).toBe("warn");
  });

  it("flags a fallback summary as not attributable and refuses to green it", () => {
    const root = mkdtempSync(join(tmpdir(), "research-fallback-"));
    mkdirSync(join(root, "wp-q1"), { recursive: true });
    writeFileSync(
      join(root, "wp-q1", "panel-summary.json"),
      JSON.stringify(summary({ run_id: "run-other" })),
    );

    const fallback = loadPanelSummaryFromRoot(root, ["run-selected"]);
    expect(fallback.runBinding).toBe("mismatched");
    expect(fallback.reasons[0]).toMatch(/different run_id/i);
    expect(researchVerdictTone(fallback.verdict, fallback.runBinding)).toBe("warn");
  });

  it("stays UNAVAILABLE when research-out is not pointed", () => {
    const missing = loadPanelSummaryFromRoot(undefined, ["run-a"]);
    expect(missing.verdict).toBe(RESEARCH_UNAVAILABLE);
    expect(missing.runBinding).toBe("unavailable");
    expect(researchVerdictTone(missing.verdict, missing.runBinding)).toBe("muted");
  });
});
