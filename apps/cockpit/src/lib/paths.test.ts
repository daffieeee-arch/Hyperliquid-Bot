import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CANONICAL_DATA1A_FIXTURE_RUN_ID,
  CANONICAL_LIVE_PAPER_RUN_ID,
  COURSE1_COCKPIT_FILE_NAMES,
  COURSE1_PATH_CONTRACT_ID,
  DATA1A_PATH_CONTRACT_ID,
  DATA1B_PATH_CONTRACT_ID,
  DATA1E_PATH_CONTRACT_ID,
  DATA1F_PATH_CONTRACT_ID,
  VENUE_CAPTURE_CONTRACTS,
  VENUE_CAPTURE_STRIP_ORDER,
  course1CockpitRunDir,
  data1aCockpitRunDir,
  defaultData1AFixtureRunDir,
  defaultFixtureRunDir,
  findRepoRoot,
  firstQueryValue,
  repoRootFromModuleUrl,
  isRunId,
  requirePaperTradingMode,
  requireRunId,
  resolveData1ARunDir,
  resolvePaperRunDir,
  resolveVenueCaptureRunDir,
  venueCockpitRunDir,
} from "./paths";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));

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
    expect(isRunId("20260905t232635z-live-retained")).toBe(true);
    expect(isRunId("sample-run")).toBe(true);
    expect(isRunId("SAMPLE")).toBe(false);
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

describe("DATA-1A path contract", () => {
  it("joins the reconstructable Hyperliquid BTC-PERP layout", () => {
    const runDir = data1aCockpitRunDir(
      "/var/lib/hyperliquid-bot/reconstructable",
      "20260904t134940z-live-retained",
    );
    expect(runDir).toBe(
      "/var/lib/hyperliquid-bot/reconstructable/data-1a/hyperliquid/BTC-PERP/20260904t134940z-live-retained",
    );
    expect(DATA1A_PATH_CONTRACT_ID).toBe("data-1a-hyperliquid-btc-perp-v1");
  });

  it("defaults to the committed retained sample fixture", () => {
    const resolved = resolveData1ARunDir({ TRADING_MODE: "PAPER" }, repoRoot);
    expect(resolved.source).toBe("default-fixture");
    expect(resolved.runId).toBe(CANONICAL_DATA1A_FIXTURE_RUN_ID);
    expect(resolved.runDir).toBe(defaultData1AFixtureRunDir(repoRoot));
  });

  it("keeps PAPER COCKPIT_ARTIFACT_ROOT from forcing a DATA-1A path without a DATA-1A run_id", () => {
    const resolved = resolveData1ARunDir(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_ARTIFACT_ROOT: "/var/lib/hyperliquid-bot/reconstructable",
        COCKPIT_RUN_ID: CANONICAL_LIVE_PAPER_RUN_ID,
      },
      repoRoot,
    );
    expect(resolved.source).toBe("default-fixture");
    expect(resolved.runId).toBe(CANONICAL_DATA1A_FIXTURE_RUN_ID);
  });

  it("uses ARTIFACT_ROOT plus query run_id for a live reconstructable run", () => {
    const resolved = resolveData1ARunDir(
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: "/data/reconstructable" },
      repoRoot,
      { data1a_run_id: "20260904t134940z-live-retained" },
    );
    expect(resolved.source).toBe("path-contract");
    expect(resolved.runDir).toBe(
      "/data/reconstructable/data-1a/hyperliquid/BTC-PERP/20260904t134940z-live-retained",
    );
  });

  it("fails closed when ARTIFACT_ROOT is set without a DATA-1A run_id", () => {
    expect(() => resolveData1ARunDir({ ARTIFACT_ROOT: "/data/reconstructable" }, repoRoot)).toThrow(
      /run_id/,
    );
  });

  it("resolves the documented TerraPC WSL reconstructable layout from ARTIFACT_ROOT plus DATA1A_RUN_ID", () => {
    const resolved = resolveData1ARunDir(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: "/home/dmesdary/hyperliquid-artifacts/reconstructable",
        DATA1A_RUN_ID: "20260904t134940z-live-retained",
      },
      repoRoot,
    );
    expect(resolved.source).toBe("path-contract");
    expect(resolved.runId).toBe("20260904t134940z-live-retained");
    expect(resolved.runDir).toBe(
      "/home/dmesdary/hyperliquid-artifacts/reconstructable/data-1a/hyperliquid/BTC-PERP/20260904t134940z-live-retained",
    );
  });

  it("treats DATA1A_RUN_ID as equivalent to COCKPIT_DATA1A_RUN_ID and lets the query override both", () => {
    const env = {
      TRADING_MODE: "PAPER",
      ARTIFACT_ROOT: "/home/dmesdary/hyperliquid-artifacts/reconstructable",
      DATA1A_RUN_ID: "20260904t134940z-live-retained",
      COCKPIT_DATA1A_RUN_ID: "cockpit-alias-run",
    };
    const fromCockpit = resolveData1ARunDir(env, repoRoot);
    expect(fromCockpit.runId).toBe("cockpit-alias-run");
    const fromQuery = resolveData1ARunDir(env, repoRoot, {
      data1a_run_id: "20260904t134940z-live-retained",
    });
    expect(fromQuery.runId).toBe("20260904t134940z-live-retained");
    expect(fromQuery.runDir).toBe(
      "/home/dmesdary/hyperliquid-artifacts/reconstructable/data-1a/hyperliquid/BTC-PERP/20260904t134940z-live-retained",
    );
  });

  it("joins the documented Binance / Bitvavo / Kraken reconstructable layouts", () => {
    expect(
      venueCockpitRunDir(
        VENUE_CAPTURE_CONTRACTS.binance,
        "/var/lib/hyperliquid-bot/reconstructable",
        "20260904t000000z-live-retained",
      ),
    ).toBe(
      "/var/lib/hyperliquid-bot/reconstructable/data-1f/binance/BTCUSDT/20260904t000000z-live-retained",
    );
    expect(
      venueCockpitRunDir(
        VENUE_CAPTURE_CONTRACTS.bitvavo,
        "/var/lib/hyperliquid-bot/reconstructable",
        "20260904t000000z-live-retained",
      ),
    ).toBe(
      "/var/lib/hyperliquid-bot/reconstructable/data-1e/bitvavo/BTC-EUR/20260904t000000z-live-retained",
    );
    expect(
      venueCockpitRunDir(
        VENUE_CAPTURE_CONTRACTS.kraken,
        "/var/lib/hyperliquid-bot/reconstructable",
        "20260904t000000z-live-retained",
      ),
    ).toBe(
      "/var/lib/hyperliquid-bot/reconstructable/data-1b/kraken/BTC-USD/20260904t000000z-live-retained",
    );
    expect(DATA1F_PATH_CONTRACT_ID).toBe("data-1f-binance-btcusdt-v1");
    expect(DATA1E_PATH_CONTRACT_ID).toBe("data-1e-bitvavo-btc-eur-v1");
    expect(DATA1B_PATH_CONTRACT_ID).toBe("data-1b-kraken-btc-usd-v1");
    expect(VENUE_CAPTURE_STRIP_ORDER).toEqual(["hl", "binance", "bitvavo", "kraken"]);
  });

  it("resolves a Binance path-contract run and fails closed without a run_id", () => {
    const resolved = resolveVenueCaptureRunDir(
      VENUE_CAPTURE_CONTRACTS.binance,
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: "/data/reconstructable",
        DATA1F_RUN_ID: "20260904t000000z-live-retained",
      },
      repoRoot,
    );
    expect(resolved.source).toBe("path-contract");
    expect(resolved.runDir).toBe(
      "/data/reconstructable/data-1f/binance/BTCUSDT/20260904t000000z-live-retained",
    );
    expect(() =>
      resolveVenueCaptureRunDir(
        VENUE_CAPTURE_CONTRACTS.kraken,
        { TRADING_MODE: "PAPER", ARTIFACT_ROOT: "/data/reconstructable" },
        repoRoot,
      ),
    ).toThrow(/run_id/);
    expect(() =>
      resolveVenueCaptureRunDir(
        VENUE_CAPTURE_CONTRACTS.bitvavo,
        { TRADING_MODE: "PAPER" },
        repoRoot,
      ),
    ).toThrow(/not pointed/);
  });

  it("lets query run_ids override env aliases for non-HL venues", () => {
    const resolved = resolveVenueCaptureRunDir(
      VENUE_CAPTURE_CONTRACTS.kraken,
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: "/home/dmesdary/hyperliquid-artifacts/reconstructable",
        DATA1B_RUN_ID: "env-run",
        COCKPIT_DATA1B_RUN_ID: "cockpit-alias-run",
      },
      repoRoot,
      { data1b_run_id: "query-run" },
    );
    expect(resolved.runId).toBe("query-run");
    expect(resolved.runDir).toBe(
      "/home/dmesdary/hyperliquid-artifacts/reconstructable/data-1b/kraken/BTC-USD/query-run",
    );
  });

  it("reads the first query value and ignores empty strings", () => {
    expect(firstQueryValue("20260904t134940z-live-retained")).toBe(
      "20260904t134940z-live-retained",
    );
    expect(firstQueryValue(["sample-run", "ignored"])).toBe("sample-run");
    expect(firstQueryValue("")).toBeUndefined();
    expect(firstQueryValue(undefined)).toBeUndefined();
  });
});
