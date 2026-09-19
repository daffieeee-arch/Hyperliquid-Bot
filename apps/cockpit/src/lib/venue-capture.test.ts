import { mkdirSync, mkdtempSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CANONICAL_DATA1A_FIXTURE_RUN_ID,
  DATA1B_CLAIM_SCHEMA,
  DATA1B_PATH_CONTRACT_ID,
  DATA1D_CLAIM_SCHEMA,
  DATA1D_PATH_CONTRACT_ID,
  DATA1E_CLAIM_SCHEMA,
  DATA1E_PATH_CONTRACT_ID,
  DATA1F_CLAIM_SCHEMA,
  DATA1F_HEALTH_SCHEMA,
  DATA1F_PATH_CONTRACT_ID,
  VENUE_CAPTURE_STRIP_ORDER,
} from "./paths";
import { STALE_MTIME_REASON } from "./capture-freshness";
import { loadVenueCaptureStrip } from "./venue-capture";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const reconnectFixtureRoot = join(repoRoot, "tests", "fixtures", "market_tape", "artifact-root");
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
  it("preserves #81 reconnect clusters and sanitized disconnect fields from fixtures", () => {
    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: reconnectFixtureRoot,
        DATA1A_RUN_ID: "20260905t180000z-live-retained",
        DATA1F_RUN_ID: "20260905t180100z-live-retained",
        DATA1D_RUN_ID: "20260905t180130z-live-retained",
        DATA1E_RUN_ID: "20260905t180200z-live-retained",
        DATA1B_RUN_ID: "20260905t180300z-live-retained",
      },
      repoRoot,
      {},
      () => "2026-09-05T18:04:00.000Z",
    );
    const binance = strip.venues.find((venue) => venue.id === "binance");
    const bitvavo = strip.venues.find((venue) => venue.id === "bitvavo");
    const kraken = strip.venues.find((venue) => venue.id === "kraken");
    expect(binance).toMatchObject({
      reconnects: 4,
      reconnect_clusters: 2,
      close_code: 1008,
      close_code_rcvd: 1008,
      close_reason_rcvd: "Too many requests",
    });
    expect(bitvavo).toMatchObject({
      reconnects: 1,
      reconnect_clusters: 1,
      exception_class: "ConnectionResetError",
      errno: 104,
    });
    expect(kraken).toMatchObject({
      reconnects: 6,
      reconnect_clusters: 3,
      close_code_rcvd: 1011,
      close_code_sent: 1011,
      close_reason_rcvd: "internal error",
      close_reason_sent: "keepalive ping timeout",
    });
  });

  it("shows the DATA-1A fixture as STOPPED and fails closed for missing venues", () => {
    const strip = loadVenueCaptureStrip({ TRADING_MODE: "PAPER" }, repoRoot, {}, () => observedAt);
    expect(strip.venues.map((venue) => venue.id)).toEqual([...VENUE_CAPTURE_STRIP_ORDER]);
    const [hl, binance, bitvavoStd, bitvavo, kraken] = strip.venues;
    expect(hl?.chip).toBe("HL");
    expect(hl?.status).toBe("STOPPED");
    expect(hl?.run_id).toBe(CANONICAL_DATA1A_FIXTURE_RUN_ID);
    expect(hl?.binding_source).toBe("default-fixture");
    expect(hl?.part_count).toBeUndefined();
    expect(hl?.last_part_age).toBe("n/a");
    expect(hl?.status_detail).toBe("COMPLETED");
    expect(strip.fresh_max_s).toBe(180);
    expect(binance?.status).toBe("MISSING");
    expect(bitvavoStd?.status).toBe("MISSING");
    expect(bitvavo?.status).toBe("MISSING");
    expect(kraken?.status).toBe("MISSING");
    expect(binance?.part_count).toBeUndefined();
    expect(bitvavoStd?.chip).toBe("BV-STD");
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

  it("marks a claim with last_part_mtime older than FRESH_MAX as STALE, not RUNNING", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-stale-"));
    const runId = "20260904t134900z-live-retained";
    const runDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", runId);
    writeClaim(runDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, runId, {
      venue: "binance",
      product: "BTCUSDT",
    });
    mkdirSync(join(runDir, "raw"));
    const part = join(runDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(part, "binance-part", { encoding: "utf8" });
    utimesSync(part, new Date("2026-09-04T13:40:00Z"), new Date("2026-09-04T13:40:00Z"));

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
    expect(binance?.status).toBe("STALE");
    expect(binance?.live).toBe(false);
    expect(binance?.reason).toBe(STALE_MTIME_REASON);
    expect(binance?.status_detail).toBe("STALE (stale_mtime)");
    expect(binance?.part_count).toBe(1);
    expect(binance?.last_part_mtime_utc).toBe("2026-09-04T13:40:00.000Z");
    expect(binance?.tone).toBe("warn");
    expect(binance?.reconnect_clusters).toBeUndefined();
  });

  it("keeps finished COMPLETED health as STOPPED even when last part mtime is stale", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-stopped-"));
    const runId = "20260904t000000z-live-retained";
    const runDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", runId);
    writeClaim(runDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, runId, {
      venue: "binance",
      product: "BTCUSDT",
    });
    writeJson(join(runDir, "capture-health.json"), {
      schema: DATA1F_HEALTH_SCHEMA,
      kind: "capture-health",
      path_contract: DATA1F_PATH_CONTRACT_ID,
      run_id: runId,
      status: "COMPLETED",
      retained: true,
      twenty_four_seven: false,
    });
    mkdirSync(join(runDir, "raw"));
    const part = join(runDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(part, "stopped-part", { encoding: "utf8" });
    utimesSync(part, new Date("2026-09-04T12:00:00Z"), new Date("2026-09-04T12:00:00Z"));

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
    expect(binance?.status).toBe("STOPPED");
    expect(binance?.live).toBe(false);
    expect(binance?.reason).toBeUndefined();
    expect(binance?.status_detail).toBe("COMPLETED");
    expect(binance?.part_count).toBe(1);
    expect(binance?.gaps).toBeUndefined();
    expect(binance?.reconnects).toBeUndefined();
  });

  it("copies gaps/reconnects from capture-health.json and leaves them n/a while health is pending", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-gap-"));
    const stoppedId = "20260904t000000z-live-retained";
    const liveId = "20260904t134900z-live-retained";
    const stoppedDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", stoppedId);
    const liveDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", liveId);
    writeClaim(stoppedDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, stoppedId, {
      venue: "binance",
      product: "BTCUSDT",
    });
    writeJson(join(stoppedDir, "capture-health.json"), {
      schema: DATA1F_HEALTH_SCHEMA,
      kind: "capture-health",
      path_contract: DATA1F_PATH_CONTRACT_ID,
      run_id: stoppedId,
      status: "COMPLETED",
      retained: true,
      twenty_four_seven: false,
      gaps: 2,
      reconnects: 1,
    });
    writeClaim(
      liveDir,
      "data-1a-retained-capture-claim-v1",
      "data-1a-hyperliquid-btc-perp-v1",
      liveId,
    );
    mkdirSync(join(liveDir, "raw"));
    const part = join(liveDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(part, "hl-part", { encoding: "utf8" });
    utimesSync(part, new Date("2026-09-04T13:49:33Z"), new Date("2026-09-04T13:49:33Z"));

    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: artifactRoot,
        DATA1A_RUN_ID: liveId,
        DATA1F_RUN_ID: stoppedId,
      },
      repoRoot,
      {},
      () => observedAt,
    );
    const binance = strip.venues.find((venue) => venue.id === "binance");
    const hl = strip.venues.find((venue) => venue.id === "hl");
    expect(binance?.status).toBe("STOPPED");
    expect(binance?.gaps).toBe(2);
    expect(binance?.reconnects).toBe(1);
    expect(hl?.status).toBe("RUNNING");
    expect(hl?.gaps).toBeUndefined();
    expect(hl?.reconnects).toBeUndefined();
  });

  it("loads all five path-contract venues without inventing a missing claim", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-five-"));
    const hlId = "hl-run";
    const bnId = "bn-run";
    const bvStdId = "bv-std-run";
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
      join(artifactRoot, "data-1d", "bitvavo", "BTC-EUR", bvStdId),
      DATA1D_CLAIM_SCHEMA,
      DATA1D_PATH_CONTRACT_ID,
      bvStdId,
    );
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
        DATA1D_RUN_ID: bvStdId,
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
      "DEGRADED",
    ]);
    expect(strip.venues[1]?.status_detail).toBe("COMPLETED");
    expect(strip.venues[2]?.id).toBe("bitvavo-std");
    expect(strip.venues[2]?.series).toBe("DATA-1D");
    expect(strip.venues[3]?.id).toBe("bitvavo");
    expect(strip.venues[3]?.series).toBe("DATA-1E");
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

  it("auto-detects the freshest live retain under ARTIFACT_ROOT without inventing a run_id", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-auto-"));
    const hlId = "20260905t232635z-live-retained";
    const bnId = "20260905t235830z-live-retained";
    const hlDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", hlId);
    const bnDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", bnId);
    writeClaim(hlDir, "data-1a-retained-capture-claim-v1", "data-1a-hyperliquid-btc-perp-v1", hlId);
    mkdirSync(join(hlDir, "raw"));
    const hlPart = join(hlDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(hlPart, "hl-part", { encoding: "utf8" });
    utimesSync(hlPart, new Date("2026-09-04T13:49:33Z"), new Date("2026-09-04T13:49:33Z"));
    writeClaim(bnDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, bnId, {
      venue: "binance",
      product: "BTCUSDT",
    });
    mkdirSync(join(bnDir, "raw"));
    const bnPart = join(bnDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(bnPart, "bn-part", { encoding: "utf8" });
    utimesSync(bnPart, new Date("2026-09-04T13:49:40Z"), new Date("2026-09-04T13:49:40Z"));

    const strip = loadVenueCaptureStrip(
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: artifactRoot },
      repoRoot,
      {},
      () => observedAt,
    );
    const hl = strip.venues.find((venue) => venue.id === "hl");
    const binance = strip.venues.find((venue) => venue.id === "binance");
    expect(hl?.status).toBe("RUNNING");
    expect(hl?.run_id).toBe(hlId);
    expect(hl?.binding_source).toBe("auto-detect");
    expect(binance?.status).toBe("RUNNING");
    expect(binance?.run_id).toBe(bnId);
    expect(binance?.binding_source).toBe("auto-detect");
    expect(strip.venues.find((venue) => venue.id === "bitvavo")?.status).toBe("MISSING");
    expect(strip.venues.find((venue) => venue.id === "kraken")?.status).toBe("MISSING");
    expect(strip.catalog.find((entry) => entry.id === "hl")?.candidates[0]?.run_id).toBe(hlId);
    expect(strip.provenance.overlap_starts_utc).toBe("2026-09-05T23:58:30Z");
    expect(strip.provenance.overlap_note).toMatch(/BINANCE/);
    expect(strip.provenance.overlap_note).toMatch(/MISSING venues/);
  });

  it("refuses a non-positive COCKPIT_CAPTURE_FRESH_MAX_S before inventing RUNNING", () => {
    expect(() =>
      loadVenueCaptureStrip(
        { TRADING_MODE: "PAPER", COCKPIT_CAPTURE_FRESH_MAX_S: "nope" },
        repoRoot,
      ),
    ).toThrow(/COCKPIT_CAPTURE_FRESH_MAX_S/);
  });

  it("soft-loads DATA-1D claims that omit state when fresh parquet parts exist", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-std-state-"));
    const runId = "20260919t003959z-vps-phase-a-std-candles";
    const runDir = join(artifactRoot, "data-1d", "bitvavo", "BTC-EUR", runId);
    mkdirSync(join(runDir, "raw"), { recursive: true });
    writeJson(join(runDir, "capture-claim.json"), {
      schema: DATA1D_CLAIM_SCHEMA,
      path_contract: DATA1D_PATH_CONTRACT_ID,
      run_id: runId,
      // Intentionally omit state — production Standard retain before the writer fix.
      retained: true,
      twenty_four_seven: false,
      credentialless: true,
      signing: false,
      venue: "bitvavo",
      product: "BTC-EUR",
    });
    const part = join(runDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(part, "std-part", { encoding: "utf8" });
    utimesSync(part, new Date("2026-09-04T13:49:33Z"), new Date("2026-09-04T13:49:33Z"));

    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: artifactRoot,
        DATA1D_RUN_ID: runId,
      },
      repoRoot,
      {},
      () => observedAt,
    );
    const std = strip.venues.find((venue) => venue.id === "bitvavo-std");
    expect(std).toMatchObject({
      status: "RUNNING",
      series: "DATA-1D",
      chip: "BV-STD",
      run_id: runId,
      live: true,
      part_count: 1,
    });
    expect(std?.status_detail).toMatch(/health JSON pending/i);
    expect(std?.error).toBe("capture-health.json is not written yet");
  });

  it("still fails closed when DATA-1D omits state and has no fresh parquet parts", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "venue-std-nostate-"));
    const runId = "std-no-parts";
    const runDir = join(artifactRoot, "data-1d", "bitvavo", "BTC-EUR", runId);
    mkdirSync(runDir, { recursive: true });
    writeJson(join(runDir, "capture-claim.json"), {
      schema: DATA1D_CLAIM_SCHEMA,
      path_contract: DATA1D_PATH_CONTRACT_ID,
      run_id: runId,
      retained: true,
      twenty_four_seven: false,
      signing: false,
    });

    const strip = loadVenueCaptureStrip(
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: artifactRoot,
        DATA1D_RUN_ID: runId,
      },
      repoRoot,
      {},
      () => observedAt,
    );
    const std = strip.venues.find((venue) => venue.id === "bitvavo-std");
    expect(std?.status).toBe("MISSING");
    expect(std?.error).toMatch(/missing non-empty string field state/);
    expect(std?.part_count).toBeUndefined();
  });
});
