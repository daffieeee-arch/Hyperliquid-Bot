import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  boundRunsFromChips,
  classifyRunBinding,
  loadPanelSummaryFromRoot,
  panelSummaryRunIds,
  panelSummaryRunRefs,
  parsePanelSummary,
} from "./research-p0";
import {
  RESEARCH_UNAVAILABLE,
  researchVerdictTone,
  type ResearchRunBinding,
  type ResearchVenueRun,
} from "./research-p0-view";
import type { VenueCaptureChip } from "./types";

const PANEL_VERSION = "panel_hl_binance/wp-q1.1";

const HL_RUN = "20260905t232635z-live-retained";
const BN_OLD_RUN = "20260905t235830z-live-retained";
const BN_NEW_RUN = "20260906t101559z-live-retained";

const hlBound: ResearchVenueRun = { venue: "hl", runId: HL_RUN, product: "BTC-PERP" };
const bnNewBound: ResearchVenueRun = { venue: "binance", runId: BN_NEW_RUN, product: "BTCUSDT" };
const bnOldBound: ResearchVenueRun = { venue: "binance", runId: BN_OLD_RUN, product: "BTCUSDT" };

function chip(
  id: VenueCaptureChip["id"],
  runId: string | undefined,
  status: VenueCaptureChip["status"] = "RUNNING",
): VenueCaptureChip {
  const products: Record<VenueCaptureChip["id"], string> = {
    hl: "BTC-PERP",
    binance: "BTCUSDT",
    bitvavo: "BTC-EUR",
    kraken: "BTC-USD",
  };
  return {
    id,
    chip:
      id === "hl" ? "HL" : id === "binance" ? "BINANCE" : id === "bitvavo" ? "BITVAVO" : "KRAKEN",
    series:
      id === "hl"
        ? "DATA-1A"
        : id === "binance"
          ? "DATA-1F"
          : id === "bitvavo"
            ? "DATA-1E"
            : "DATA-1B",
    venue: id,
    product: products[id],
    path_contract: `${id}-contract`,
    status,
    status_detail: status,
    tone: "ok",
    live: status === "RUNNING",
    part_count: 1,
    last_part_age: "1s",
    last_part_mtime_utc: "2026-09-06T12:00:00.000Z",
    gaps: undefined,
    reconnects: undefined,
    run_id: runId,
    binding_source: "auto-detect",
    observed_at: "2026-09-06T12:00:00.000Z",
    error: undefined,
  };
}

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

  it("keeps the venue each run_id was declared for", () => {
    expect(panelSummaryRunRefs(summary({ hl_run_id: HL_RUN, bn_run_id: BN_NEW_RUN }))).toEqual([
      { venue: "hl", runId: HL_RUN },
      { venue: "binance", runId: BN_NEW_RUN },
    ]);
    expect(panelSummaryRunRefs(summary({ run_id: "run-a" }))).toEqual([
      { venue: "any", runId: "run-a" },
    ]);
    expect(
      panelSummaryRunRefs(
        summary({ run_ids: { hl: HL_RUN, binance: BN_NEW_RUN }, bn_product: "BTCUSDT" }),
      ),
    ).toEqual([
      { venue: "hl", runId: HL_RUN },
      { venue: "binance", runId: BN_NEW_RUN, product: "BTCUSDT" },
    ]);
  });

  it("classifies binding against the bound selection", () => {
    const any = (runId: string): ResearchVenueRun => ({ venue: "any", runId });
    const cases: [ResearchVenueRun[], ResearchVenueRun[], ResearchRunBinding][] = [
      [[any(HL_RUN)], [hlBound, bnNewBound], "matched"],
      [[any("run-z")], [hlBound], "mismatched"],
      [[], [hlBound], "unknown"],
      [[any(HL_RUN)], [], "unknown"],
      [
        [
          { venue: "hl", runId: HL_RUN },
          { venue: "binance", runId: BN_NEW_RUN },
        ],
        [hlBound, bnNewBound],
        "matched",
      ],
      [
        [
          { venue: "hl", runId: HL_RUN },
          { venue: "binance", runId: BN_NEW_RUN },
        ],
        [hlBound],
        "partial",
      ],
    ];
    for (const [summaryRuns, boundRuns, expected] of cases) {
      expect(classifyRunBinding(summaryRuns, boundRuns)).toBe(expected);
    }
  });

  it("regression: a Binance restart with the same HL run is mismatched, not matched", () => {
    // Summary was produced for HL + the pre-restart Binance retain.
    const summaryRuns = panelSummaryRunRefs(summary({ hl_run_id: HL_RUN, bn_run_id: BN_OLD_RUN }));
    // Binance restarted (#58 hotfix); HL/BV/KR keep the shared run.
    expect(classifyRunBinding(summaryRuns, [hlBound, bnNewBound])).toBe("mismatched");
    // Before the restart the same summary was a full match.
    expect(classifyRunBinding(summaryRuns, [hlBound, bnOldBound])).toBe("matched");
    // The copied verdict must not borrow the success colour after the restart.
    const copied = parsePanelSummary(
      summary({ hl_run_id: HL_RUN, bn_run_id: BN_OLD_RUN }),
      "wp-q1/panel-summary.json",
      [hlBound, bnNewBound],
    );
    expect(copied.runBinding).toBe("mismatched");
    expect(researchVerdictTone(copied.verdict, copied.runBinding)).toBe("warn");
  });

  it("does not let a Hyperliquid run satisfy a bn_run_id that happens to share the id", () => {
    const shared = "20260905t232635z-live-retained";
    const summaryRuns = panelSummaryRunRefs(summary({ hl_run_id: shared, bn_run_id: shared }));
    const bound: ResearchVenueRun[] = [
      { venue: "hl", runId: shared, product: "BTC-PERP" },
      { venue: "binance", runId: BN_NEW_RUN, product: "BTCUSDT" },
    ];
    expect(classifyRunBinding(summaryRuns, bound)).toBe("mismatched");
  });

  it("treats a declared product that differs from the bound instrument as a mismatch", () => {
    const summaryRuns = panelSummaryRunRefs(
      summary({ bn_run_id: BN_NEW_RUN, bn_product: "BTCUSDT-USDS-M-PERPETUAL" }),
    );
    expect(classifyRunBinding(summaryRuns, [bnNewBound])).toBe("mismatched");
  });

  it("derives bound runs from the capture strip and drops MISSING chips", () => {
    expect(
      boundRunsFromChips([
        chip("hl", HL_RUN),
        chip("binance", BN_NEW_RUN),
        chip("bitvavo", undefined),
        chip("kraken", HL_RUN, "MISSING"),
      ]),
    ).toEqual([
      { venue: "hl", runId: HL_RUN, product: "BTC-PERP" },
      { venue: "binance", runId: BN_NEW_RUN, product: "BTCUSDT" },
    ]);
  });

  it("parses binding into the sufficiency payload", () => {
    const copied = parsePanelSummary(summary({ run_id: HL_RUN }), "wp-q1/panel-summary.json", [
      hlBound,
    ]);
    expect(copied.runIds).toEqual([HL_RUN]);
    expect(copied.runRefs).toEqual([{ venue: "any", runId: HL_RUN }]);
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

    const chosen = loadPanelSummaryFromRoot(root, [{ venue: "any", runId: "run-selected" }]);
    expect(chosen.runIds).toEqual(["run-selected"]);
    expect(chosen.runBinding).toBe("matched");
    expect(chosen.verdict).toBe("not_enough_data");
    expect(researchVerdictTone(chosen.verdict, chosen.runBinding)).toBe("warn");
  });

  it("regression: after a Binance restart the summary for the new BN run wins over the old one", () => {
    const root = mkdtempSync(join(tmpdir(), "research-bn-restart-"));
    mkdirSync(join(root, "aaa-before-restart"), { recursive: true });
    mkdirSync(join(root, "zzz-after-restart"), { recursive: true });
    writeFileSync(
      join(root, "aaa-before-restart", "panel-summary.json"),
      JSON.stringify(summary({ hl_run_id: HL_RUN, bn_run_id: BN_OLD_RUN, verdict: "panel_ready" })),
    );
    writeFileSync(
      join(root, "zzz-after-restart", "panel-summary.json"),
      JSON.stringify(
        summary({ hl_run_id: HL_RUN, bn_run_id: BN_NEW_RUN, verdict: "not_enough_data" }),
      ),
    );

    const chosen = loadPanelSummaryFromRoot(root, [hlBound, bnNewBound]);
    expect(chosen.source).toBe(join("zzz-after-restart", "panel-summary.json"));
    expect(chosen.runBinding).toBe("matched");
    expect(chosen.verdict).toBe("not_enough_data");

    // Only the pre-restart summary exists: HL matches, BN does not → never green.
    const onlyOld = mkdtempSync(join(tmpdir(), "research-bn-restart-old-"));
    mkdirSync(join(onlyOld, "wp-q1"), { recursive: true });
    writeFileSync(
      join(onlyOld, "wp-q1", "panel-summary.json"),
      JSON.stringify(summary({ hl_run_id: HL_RUN, bn_run_id: BN_OLD_RUN, verdict: "panel_ready" })),
    );
    const stale = loadPanelSummaryFromRoot(onlyOld, [hlBound, bnNewBound]);
    expect(stale.runBinding).toBe("mismatched");
    expect(stale.reasons[0]).toMatch(/different run_id/i);
    expect(researchVerdictTone(stale.verdict, stale.runBinding)).toBe("warn");
  });

  it("flags a fallback summary as not attributable and refuses to green it", () => {
    const root = mkdtempSync(join(tmpdir(), "research-fallback-"));
    mkdirSync(join(root, "wp-q1"), { recursive: true });
    writeFileSync(
      join(root, "wp-q1", "panel-summary.json"),
      JSON.stringify(summary({ run_id: "run-other" })),
    );

    const fallback = loadPanelSummaryFromRoot(root, [{ venue: "any", runId: "run-selected" }]);
    expect(fallback.runBinding).toBe("mismatched");
    expect(fallback.reasons[0]).toMatch(/different run_id/i);
    expect(researchVerdictTone(fallback.verdict, fallback.runBinding)).toBe("warn");
  });

  it("stays UNAVAILABLE when research-out is not pointed", () => {
    const missing = loadPanelSummaryFromRoot(undefined, [hlBound]);
    expect(missing.verdict).toBe(RESEARCH_UNAVAILABLE);
    expect(missing.runBinding).toBe("unavailable");
    expect(researchVerdictTone(missing.verdict, missing.runBinding)).toBe("muted");
  });
});
