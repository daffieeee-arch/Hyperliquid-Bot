import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { HYPOTHESIS_RESULT_SCHEMA, RESULT_UNAVAILABLE } from "./hypothesis-result-model";
import {
  HYPOTHESIS_RESULTS_UNPOINTED_NOTE,
  hypothesisResultsRoot,
  listHypothesisResults,
} from "./hypothesis-results";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixtureRoot = join(repoRoot, "tests", "fixtures", "hypothesis_results");
const observedAt = "2026-10-06T08:20:00.000Z";

describe("hypothesis results directory", () => {
  it("stays UNAVAILABLE when no directory is configured", () => {
    expect(hypothesisResultsRoot({ TRADING_MODE: "PAPER" })).toBeUndefined();
    const list = listHypothesisResults({ TRADING_MODE: "PAPER" }, observedAt);
    expect(list.items).toEqual([]);
    expect(list.note).toBe(HYPOTHESIS_RESULTS_UNPOINTED_NOTE);
    expect(list.observedAt).toBe(observedAt);
  });

  it("uses the artifact-root subdirectory and never a baked VPS path", () => {
    const root = hypothesisResultsRoot({
      TRADING_MODE: "PAPER",
      ARTIFACT_ROOT: join(tmpdir(), "capture-root"),
    });
    expect(root).toMatch(/hypothesis-results$/);
    expect(root).not.toMatch(/Hyperliquid Project/);
  });

  it("fails closed when the cockpit is not PAPER", () => {
    expect(() => listHypothesisResults({ TRADING_MODE: "LIVE" })).toThrow(/PAPER-only/);
    expect(() => listHypothesisResults({ TRADING_MODE: "TESTNET" })).toThrow(/PAPER-only/);
    expect(() => listHypothesisResults({ TRADING_MODE: "SHADOW" })).toThrow(/PAPER-only/);
  });

  it("reads the synthetic fixture without inventing the omitted net", () => {
    const list = listHypothesisResults(
      { TRADING_MODE: "PAPER", COCKPIT_HYPOTHESIS_RESULTS: fixtureRoot },
      observedAt,
    );
    expect(list.items.map((item) => item.id)).toEqual(["wp1-h1-taker-flow", "wp2-partial"]);
    const wp1 = list.items[0];
    expect(wp1?.schemaVersion).toBe(HYPOTHESIS_RESULT_SCHEMA);
    expect(wp1?.label).toBe("interesting_but_fragile");
    expect(wp1?.passesH1).toBe("no");
    expect(wp1?.configsTested).toBe("12");
    expect(wp1?.configsPassed).toBe("0");
    expect(wp1?.grossBps).toBe("1.4");
    expect(wp1?.netBps).toBe(RESULT_UNAVAILABLE);
    expect(wp1?.promotionDecision).toBe("forbidden");
    expect(wp1?.reportMarkdown).toMatch(/cost-killed/);
    expect(wp1?.synthetic).toBe(true);

    const wp2 = list.items[1];
    expect(wp2?.passesH1).toBe(RESULT_UNAVAILABLE);
    expect(wp2?.configsPassed).toBe(RESULT_UNAVAILABLE);
    expect(wp2?.netBps).toBe(RESULT_UNAVAILABLE);
    expect(wp2?.holdoutWindow).toBe(RESULT_UNAVAILABLE);
    expect(wp2?.oosWindow).toBe(RESULT_UNAVAILABLE);
    expect(wp2?.grossBps).toBe("0.2");
    expect(wp2?.reportMarkdown).toBeUndefined();
    expect(wp2?.dataRange).toContain("CET");
  });

  it("keeps a malformed file as an unreadable row beside a valid one", () => {
    const root = mkdtempSync(join(tmpdir(), "hypothesis-results-"));
    mkdirSync(join(root, "good"));
    mkdirSync(join(root, "bad"));
    writeFileSync(
      join(root, "good", "result.json"),
      `${JSON.stringify({ ...{ schema_version: HYPOTHESIS_RESULT_SCHEMA, trading_mode: "PAPER", work_package: "WP9", hypothesis_id: "H-9", passes_h1: false, promotion_decision: "forbidden" } })}\n`,
    );
    writeFileSync(join(root, "bad", "result.json"), "{", "utf8");
    const list = listHypothesisResults(
      { TRADING_MODE: "PAPER", COCKPIT_HYPOTHESIS_RESULTS: root },
      observedAt,
    );
    expect(list.items).toHaveLength(2);
    const bad = list.items.find((item) => item.id === "bad");
    const good = list.items.find((item) => item.id === "good");
    expect(bad?.status).toBe("unreadable");
    expect(bad?.grossBps).toBe(RESULT_UNAVAILABLE);
    expect(bad?.problem).toMatch(/not valid JSON/);
    expect(good?.status).toBe("ok");
    expect(good?.workPackage).toBe("WP9");
    expect(list.note).toMatch(/UNAVAILABLE/);
  });

  it("does not follow a report path that escapes the results directory", () => {
    const root = mkdtempSync(join(tmpdir(), "hypothesis-results-escape-"));
    const outside = mkdtempSync(join(tmpdir(), "hypothesis-secret-"));
    writeFileSync(join(outside, "secret.md"), "secret-net-99.9", "utf8");
    mkdirSync(join(root, "wp"));
    writeFileSync(
      join(root, "wp", "result.json"),
      JSON.stringify({
        schema_version: HYPOTHESIS_RESULT_SCHEMA,
        trading_mode: "PAPER",
        work_package: "WP3",
        hypothesis_id: "H-3",
        passes_h1: false,
        promotion_decision: "forbidden",
        report_markdown_path: "../../secret.md",
      }),
    );
    try {
      symlinkSync(join(outside, "secret.md"), join(root, "wp", "report.md"));
    } catch {
      // A platform that refuses the symlink still must refuse the escaped path.
    }
    const list = listHypothesisResults({
      TRADING_MODE: "PAPER",
      COCKPIT_HYPOTHESIS_RESULTS: root,
    });
    const item = list.items[0];
    expect(item?.workPackage).toBe("WP3");
    expect(item?.reportMarkdown ?? "").not.toContain("secret-net");
    expect(item?.reportNote).toMatch(/UNAVAILABLE|escaped|not a regular file/);
  });
});
