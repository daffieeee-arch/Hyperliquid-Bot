import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CANONICAL_LIVE_PAPER_RUN_ID,
  COURSE1_COCKPIT_FILE_NAMES,
  COURSE1_PATH_CONTRACT_ID,
  course1CockpitRunDir,
  defaultFixtureRunDir,
  findRepoRoot,
  repoRootFromModuleUrl,
  requirePaperTradingMode,
  requireRunId,
  resolvePaperRunDir,
} from "./paths";

const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));

describe("COURSE-1 path contract", () => {
  it("joins the reconstructable live-public-paper layout", () => {
    const runDir = course1CockpitRunDir("/var/lib/hyperliquid-bot/reconstructable", "sample-run");
    expect(runDir).toBe(
      "/var/lib/hyperliquid-bot/reconstructable/course1/live-public-paper/sample-run",
    );
    expect(COURSE1_COCKPIT_FILE_NAMES).toEqual([
      "run-claim.json",
      "paper-position.json",
      "paper-pnl.json",
      "orders.json",
      "fills.json",
      "capture-health.json",
    ]);
    expect(COURSE1_PATH_CONTRACT_ID).toBe("course1-live-public-paper-cockpit-v1");
  });

  it("refuses invalid run ids", () => {
    expect(() => requireRunId("SAMPLE")).toThrow(/run_id/);
    expect(() => requireRunId("has space")).toThrow(/run_id/);
    expect(() => requireRunId("")).toThrow(/run_id/);
  });

  it("fails closed unless TRADING_MODE is unset or PAPER", () => {
    expect(requirePaperTradingMode(undefined)).toBe("PAPER");
    expect(requirePaperTradingMode("PAPER")).toBe("PAPER");
    expect(() => requirePaperTradingMode("LIVE")).toThrow(/PAPER-only/);
    expect(() => requirePaperTradingMode("TESTNET")).toThrow(/PAPER-only/);
    expect(() => requirePaperTradingMode("SHADOW")).toThrow(/PAPER-only/);
  });

  it("defaults to the committed live-public-soak fixture", () => {
    const resolved = resolvePaperRunDir({ TRADING_MODE: "PAPER" }, repoRoot);
    expect(resolved.source).toBe("default-fixture");
    expect(resolved.runId).toBe(CANONICAL_LIVE_PAPER_RUN_ID);
    expect(resolved.runDir).toBe(defaultFixtureRunDir(repoRoot));
  });

  it("uses the path-contract pair for later VPS artifacts", () => {
    const resolved = resolvePaperRunDir(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_ARTIFACT_ROOT: "/var/lib/hyperliquid-bot/reconstructable",
        COCKPIT_RUN_ID: CANONICAL_LIVE_PAPER_RUN_ID,
      },
      repoRoot,
    );
    expect(resolved.source).toBe("path-contract");
    expect(resolved.runDir).toBe(
      `/var/lib/hyperliquid-bot/reconstructable/course1/live-public-paper/${CANONICAL_LIVE_PAPER_RUN_ID}`,
    );
  });

  it("prefers an explicit run directory so fixtures and VPS folders share file names", () => {
    const runDir = mkdtempSync(join(tmpdir(), "cockpit-run-"));
    writeFileSync(join(runDir, "paper-position.json"), "{}", { encoding: "utf8" });
    const resolved = resolvePaperRunDir(
      { COCKPIT_PAPER_RUN_DIR: runDir, COCKPIT_RUN_ID: "sample-run" },
      repoRoot,
    );
    expect(resolved.source).toBe("paper-run-dir");
    expect(resolved.runDir).toBe(runDir);
    expect(resolved.runId).toBe("sample-run");
  });

  it("resolves the repository root from this module URL and the working directory", () => {
    expect(repoRootFromModuleUrl(import.meta.url)).toBe(repoRoot);
    expect(findRepoRoot(join(repoRoot, "apps", "cockpit"))).toBe(repoRoot);
  });
});
