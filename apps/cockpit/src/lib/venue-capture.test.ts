import { mkdirSync, mkdtempSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CANONICAL_DATA1A_FIXTURE_RUN_ID,
  DATA1B_CLAIM_SCHEMA,
  DATA1B_PATH_CONTRACT_ID,
  DATA1E_CLAIM_SCHEMA,
  DATA1E_PATH_CONTRACT_ID,
  DATA1F_CLAIM_SCHEMA,
  DATA1F_HEALTH_SCHEMA,
  DATA1F_PATH_CONTRACT_ID,
  VENUE_CAPTURE_STRIP_ORDER,
} from "./paths";
import { loadVenueCaptureStrip } from "./venue-capture";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const observedAt = "2026-09-04T13:49:45.000Z";

function writeJson(path: string, payload: unknown): void {
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8" });
}

function writeClaim(
  runDir: string,
  schema: string,
  pathContract: string,
  runId: string,
  extras: Record<string, unknown> = {},
): void {
  mkdirSync(runDir, { recursive: true });
  writeJson(join(runDir, "capture-claim.json"), {
    schema,
    path_contract: pathContract,
    run_id: runId,
    state: "STARTED_FAIL_CLOSED",
    retained: true,
    twenty_four_seven: false,
    credentialless: true,
    signing: false,
    ...extras,
  });
}

describe("multi-venue capture-health strip", () => {
  it("shows the DATA-1A fixture as STOPPED and fails closed for missing venues", () => {
    const strip = loadVenueCaptureStrip({ TRADING_MODE: "PAPER" }, repoRoot, {}, () => observedAt);
    expect(strip.venues.map((venue) => venue.id)).toEqual([...VENUE_CAPTURE_STRIP_ORDER]);
    const [hl, binance, bitvavo, kraken] = strip.venues;
    expect(hl?.chip).toBe("HL");
    expect(hl?.status).toBe("STOPPED");
    expect(hl?.run_id).toBe(CANONICAL_DATA1A_FIXTURE_RUN_ID);
    expect(hl?.part_count).toBeUndefined();
    expect(hl?.last_part_age).toBe("n/a");
    expect(hl?.status_detail).toBe("COMPLETED");
    expect(binance?.status).toBe("MISSING");
    expect(bitvavo?.status).toBe("MISSING");
    expect(kraken?.status).toBe("MISSING");
    expect(binance?.part_count).toBeUndefined();
    expect(bitvavo?.last_part_age).toBe("n/a");
    expect(kraken?.error).toMatch(/not pointed|run_id|fail closed/i);
  });

  it("does not invent zeros when a pointed Binance run directory is missing", () => {
    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: join(tmpdir(), "missing-multi-venue-root"),
        DATA1A_RUN_ID: "20260904t134940z-live-retained",
        DATA1F_RUN_ID: "20260904t000000z-live-retained",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(strip.venues[0]?.status).toBe("MISSING");
    expect(strip.venues[1]?.chip).toBe("BINANCE");
    expect(strip.venues[1]?.status).toBe("MISSING");
    expect(strip.venues[1]?.part_count).toBeUndefined();
    expect(strip.venues[1]?.last_part_age).toBe("n/a");
    expect(strip.venues[1]?.error).toMatch(/run directory is missing/);
    expect(strip.venues[2]?.status).toBe("MISSING");
    expect(strip.venues[3]?.status).toBe("MISSING");
  });

  it("copies a live-looking Binance run as RUNNING and leaves unpointed venues MISSING", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-strip-"));
    const runId = "20260904t134900z-live-retained";
    const runDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", runId);
    writeClaim(runDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, runId, {
      venue: "binance",
      product: "BTCUSDT",
    });
    mkdirSync(join(runDir, "raw"));
    const part = join(runDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(part, "binance-part", { encoding: "utf8" });
    utimesSync(part, new Date("2026-09-04T13:49:33Z"), new Date("2026-09-04T13:49:33Z"));

    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: artifactRoot,
        DATA1F_RUN_ID: runId,
      },
      repoRoot,
      {},
      () => observedAt,
    );
    const binance = strip.venues.find((venue) => venue.id === "binance");
    expect(binance?.status).toBe("RUNNING");
    expect(binance?.live).toBe(true);
    expect(binance?.part_count).toBe(1);
    expect(binance?.last_part_age).toBe("12s");
    expect(binance?.last_part_mtime_utc).toBe("2026-09-04T13:49:33.000Z");
    expect(strip.venues.find((venue) => venue.id === "hl")?.status).toBe("MISSING");
    expect(strip.venues.find((venue) => venue.id === "bitvavo")?.status).toBe("MISSING");
    expect(strip.venues.find((venue) => venue.id === "kraken")?.status).toBe("MISSING");
  });

  it("loads all four path-contract venues without inventing a missing claim", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-four-"));
    const hlId = "hl-run";
    const bnId = "bn-run";
    const bvId = "bv-run";
    const krId = "kr-run";
    writeClaim(
      join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", hlId),
      "data-1a-retained-capture-claim-v1",
      "data-1a-hyperliquid-btc-perp-v1",
      hlId,
    );
    writeClaim(
      join(artifactRoot, "data-1f", "binance", "BTCUSDT", bnId),
      DATA1F_CLAIM_SCHEMA,
      DATA1F_PATH_CONTRACT_ID,
      bnId,
    );
    writeJson(join(artifactRoot, "data-1f", "binance", "BTCUSDT", bnId, "capture-health.json"), {
      schema: DATA1F_HEALTH_SCHEMA,
      kind: "capture-health",
      path_contract: DATA1F_PATH_CONTRACT_ID,
      run_id: bnId,
      status: "COMPLETED",
      retained: true,
      twenty_four_seven: false,
    });
    writeClaim(
      join(artifactRoot, "data-1e", "bitvavo", "BTC-EUR", bvId),
      DATA1E_CLAIM_SCHEMA,
      DATA1E_PATH_CONTRACT_ID,
      bvId,
    );
    writeClaim(
      join(artifactRoot, "data-1b", "kraken", "BTC-USD", krId),
      DATA1B_CLAIM_SCHEMA,
      DATA1B_PATH_CONTRACT_ID,
      krId,
    );

    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: artifactRoot,
        DATA1A_RUN_ID: hlId,
        DATA1F_RUN_ID: bnId,
        DATA1E_RUN_ID: bvId,
        DATA1B_RUN_ID: krId,
      },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(strip.venues.map((venue) => venue.status)).toEqual([
      "DEGRADED",
      "STOPPED",
      "DEGRADED",
      "DEGRADED",
    ]);
    expect(strip.venues[1]?.status_detail).toBe("COMPLETED");
    expect(strip.venues.every((venue) => venue.part_count === undefined)).toBe(true);
  });

  it("treats a present run directory without a claim as MISSING, not zero parts", () => {
    const runDir = mkdtempSync(join(tmpdir(), "venue-empty-"));
    mkdirSync(join(runDir, "raw"));
    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_DATA1E_RUN_DIR: runDir,
        COCKPIT_DATA1E_RUN_ID: "empty-run",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    const bitvavo = strip.venues.find((venue) => venue.id === "bitvavo");
    expect(bitvavo?.status).toBe("MISSING");
    expect(bitvavo?.part_count).toBeUndefined();
    expect(bitvavo?.error).toMatch(/capture-claim.json is missing/);
  });

  it("refuses signing claims as DEGRADED instead of inventing RUNNING", () => {
    const runDir = mkdtempSync(join(tmpdir(), "venue-sign-"));
    writeClaim(runDir, DATA1B_CLAIM_SCHEMA, DATA1B_PATH_CONTRACT_ID, "signed-run", {
      signing: true,
    });
    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_DATA1B_RUN_DIR: runDir,
        COCKPIT_DATA1B_RUN_ID: "signed-run",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    const kraken = strip.venues.find((venue) => venue.id === "kraken");
    expect(kraken?.status).toBe("DEGRADED");
    expect(kraken?.part_count).toBeUndefined();
    expect(kraken?.error).toMatch(/signing/);
  });

  it("refuses LIVE trading mode before reading any venue files", () => {
    expect(() => loadVenueCaptureStrip({ TRADING_MODE: "LIVE" }, repoRoot)).toThrow(/PAPER-only/);
  });
});
