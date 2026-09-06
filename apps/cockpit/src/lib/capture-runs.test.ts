import { mkdirSync, mkdtempSync, writeFileSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  bindVenueCaptureRun,
  captureRunProvenance,
  listVenueCaptureRuns,
  pickFreshestLiveRetain,
} from "./capture-runs";
import {
  DATA1A_CLAIM_SCHEMA,
  DATA1A_PATH_CONTRACT_ID,
  DATA1F_CLAIM_SCHEMA,
  DATA1F_HEALTH_SCHEMA,
  DATA1F_PATH_CONTRACT_ID,
  VENUE_CAPTURE_CONTRACTS,
} from "./paths";
import {
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
} from "./terrapc-defaults";
import type { VenueCaptureChip } from "./types";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const observedAt = "2026-09-05T23:59:00.000Z";
const clock = { now: observedAt, freshMaxSeconds: 180 };

function writeJson(path: string, payload: unknown): void {
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8" });
}

function writeClaim(runDir: string, schema: string, pathContract: string, runId: string): void {
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
  });
}

function writePart(runDir: string, mtimeIso: string): void {
  const rawDir = join(runDir, "raw");
  mkdirSync(rawDir, { recursive: true });
  const part = join(rawDir, "part-000001-000000000001-000000000010-abc.parquet");
  writeFileSync(part, "part", { encoding: "utf8" });
  utimesSync(part, new Date(mtimeIso), new Date(mtimeIso));
}

function chip(
  partial: Partial<VenueCaptureChip> & Pick<VenueCaptureChip, "id" | "chip" | "series">,
): VenueCaptureChip {
  return {
    venue: "hyperliquid",
    product: "BTC-PERP",
    path_contract: DATA1A_PATH_CONTRACT_ID,
    status: "RUNNING",
    status_detail: "RUNNING",
    tone: "ok",
    live: true,
    part_count: 1,
    last_part_age: "12s",
    last_part_mtime_utc: "2026-09-05T23:58:48.000Z",
    gaps: undefined,
    reconnects: undefined,
    run_id: undefined,
    binding_source: "unbound",
    observed_at: observedAt,
    error: undefined,
    ...partial,
  };
}

describe("capture run discovery", () => {
  it("lists claimed runs and skips invalid names or directories without a claim", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "capture-list-"));
    const liveId = "20260905t232635z-live-retained";
    const liveDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", liveId);
    writeClaim(liveDir, DATA1A_CLAIM_SCHEMA, DATA1A_PATH_CONTRACT_ID, liveId);
    writePart(liveDir, "2026-09-05T23:58:40Z");
    mkdirSync(join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", "NOT-A-RUN"), {
      recursive: true,
    });
    mkdirSync(join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", "empty-run"), {
      recursive: true,
    });

    const candidates = listVenueCaptureRuns(
      VENUE_CAPTURE_CONTRACTS.hl,
      artifactRoot,
      observedAt,
      180,
    );
    expect(candidates.map((candidate) => candidate.run_id)).toEqual([liveId]);
    expect(candidates[0]?.live).toBe(true);
    expect(candidates[0]?.part_count).toBe(1);
  });

  it("auto-picks the freshest live retain and does not invent a stale or stopped run", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "capture-pick-"));
    const olderLive = "20260905t232635z-live-retained";
    const newerLive = "20260905t235830z-live-retained";
    const staleId = "20260904t134940z-live-retained";
    const stoppedId = "20260903t000000z-live-retained";
    const olderDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", olderLive);
    const newerDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", newerLive);
    const staleDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", staleId);
    const stoppedDir = join(artifactRoot, "data-1f", "binance", "BTCUSDT", stoppedId);
    writeClaim(olderDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, olderLive);
    writePart(olderDir, "2026-09-05T23:58:10Z");
    writeClaim(newerDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, newerLive);
    writePart(newerDir, "2026-09-05T23:58:50Z");
    writeClaim(staleDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, staleId);
    writePart(staleDir, "2026-09-05T23:50:00Z");
    writeClaim(stoppedDir, DATA1F_CLAIM_SCHEMA, DATA1F_PATH_CONTRACT_ID, stoppedId);
    writePart(stoppedDir, "2026-09-05T23:58:55Z");
    writeJson(join(stoppedDir, "capture-health.json"), {
      schema: DATA1F_HEALTH_SCHEMA,
      kind: "capture-health",
      path_contract: DATA1F_PATH_CONTRACT_ID,
      run_id: stoppedId,
      status: "COMPLETED",
      retained: true,
      twenty_four_seven: false,
    });

    const candidates = listVenueCaptureRuns(
      VENUE_CAPTURE_CONTRACTS.binance,
      artifactRoot,
      observedAt,
      180,
    );
    expect(pickFreshestLiveRetain(candidates)?.run_id).toBe(newerLive);
    expect(candidates.find((candidate) => candidate.run_id === staleId)?.live).toBe(false);
    expect(candidates.find((candidate) => candidate.run_id === stoppedId)?.live).toBe(false);

    const bound = bindVenueCaptureRun(
      VENUE_CAPTURE_CONTRACTS.binance,
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: artifactRoot },
      repoRoot,
      {},
      clock,
    );
    expect(bound.source).toBe("auto-detect");
    expect(bound.binding_source).toBe("auto-detect");
    expect(bound.runId).toBe(newerLive);
  });

  it("does not auto-bind when ARTIFACT_ROOT has only stale or stopped retains", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "capture-stale-"));
    const staleId = "20260905t232635z-live-retained";
    const staleDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", staleId);
    writeClaim(staleDir, DATA1A_CLAIM_SCHEMA, DATA1A_PATH_CONTRACT_ID, staleId);
    writePart(staleDir, "2026-09-05T23:50:00Z");

    expect(() =>
      bindVenueCaptureRun(
        VENUE_CAPTURE_CONTRACTS.hl,
        { TRADING_MODE: "PAPER", ARTIFACT_ROOT: artifactRoot },
        repoRoot,
        {},
        clock,
      ),
    ).toThrow(/no live retain to auto-detect/);
  });

  it("lets query override auto-detect and env", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "capture-query-"));
    const liveId = "20260905t232635z-live-retained";
    const otherId = "20260905t235830z-live-retained";
    const liveDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", liveId);
    const otherDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", otherId);
    writeClaim(liveDir, DATA1A_CLAIM_SCHEMA, DATA1A_PATH_CONTRACT_ID, liveId);
    writePart(liveDir, "2026-09-05T23:58:50Z");
    writeClaim(otherDir, DATA1A_CLAIM_SCHEMA, DATA1A_PATH_CONTRACT_ID, otherId);
    writePart(otherDir, "2026-09-05T23:50:00Z");

    const bound = bindVenueCaptureRun(
      VENUE_CAPTURE_CONTRACTS.hl,
      {
        TRADING_MODE: "PAPER",
        ARTIFACT_ROOT: artifactRoot,
        DATA1A_RUN_ID: liveId,
      },
      repoRoot,
      { data1a_run_id: otherId },
      clock,
    );
    expect(bound.binding_source).toBe("query");
    expect(bound.runId).toBe(otherId);
    expect(bound.source).toBe("path-contract");
  });

  it("states overlap from a later Binance start against a shared HL/BV/KR run_id", () => {
    const shared = "20260905t232635z-live-retained";
    const laterBn = "20260905t235830z-live-retained";
    const provenance = captureRunProvenance([
      chip({
        id: "hl",
        chip: "HL",
        series: "DATA-1A",
        run_id: shared,
        binding_source: "auto-detect",
      }),
      chip({
        id: "binance",
        chip: "BINANCE",
        series: "DATA-1F",
        venue: "binance",
        product: "BTCUSDT",
        run_id: laterBn,
        binding_source: "auto-detect",
      }),
      chip({
        id: "bitvavo",
        chip: "BITVAVO",
        series: "DATA-1E",
        venue: "bitvavo",
        product: "BTC-EUR",
        run_id: shared,
        binding_source: "auto-detect",
      }),
      chip({
        id: "kraken",
        chip: "KRAKEN",
        series: "DATA-1B",
        venue: "kraken",
        product: "BTC-USD",
        run_id: shared,
        binding_source: "auto-detect",
      }),
    ]);
    expect(provenance.shared_run_ids).toEqual([shared]);
    expect(provenance.distinct_run_ids).toEqual([shared, laterBn]);
    expect(provenance.overlap_starts_utc).toBe("2026-09-05T23:58:30Z");
    expect(provenance.overlap_note).toMatch(/2026-09-05T23:58:30Z/);
    expect(provenance.overlap_note).toMatch(/BINANCE/);
    expect(provenance.overlap_note).toMatch(/not aligned/);
    expect(provenance.rows.map((row) => row.started_at_utc)).toEqual([
      "2026-09-05T23:26:35Z",
      "2026-09-05T23:58:30Z",
      "2026-09-05T23:26:35Z",
      "2026-09-05T23:26:35Z",
    ]);
  });

  it("auto-picks the post-#58 Binance retain and does not prefer the stopped earlier BN id", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "capture-bn-pref-"));
    const now = "2026-09-06T10:16:20.000Z";
    const stoppedDir = join(
      artifactRoot,
      "data-1f",
      "binance",
      "BTCUSDT",
      TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
    );
    const liveDir = join(
      artifactRoot,
      "data-1f",
      "binance",
      "BTCUSDT",
      TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    );
    writeClaim(
      stoppedDir,
      DATA1F_CLAIM_SCHEMA,
      DATA1F_PATH_CONTRACT_ID,
      TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
    );
    writePart(stoppedDir, "2026-09-06T10:16:15Z");
    writeJson(join(stoppedDir, "capture-health.json"), {
      schema: DATA1F_HEALTH_SCHEMA,
      kind: "capture-health",
      path_contract: DATA1F_PATH_CONTRACT_ID,
      run_id: TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
      status: "COMPLETED",
      retained: true,
      twenty_four_seven: false,
    });
    writeClaim(
      liveDir,
      DATA1F_CLAIM_SCHEMA,
      DATA1F_PATH_CONTRACT_ID,
      TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    );
    writePart(liveDir, "2026-09-06T10:16:10Z");

    const candidates = listVenueCaptureRuns(
      VENUE_CAPTURE_CONTRACTS.binance,
      artifactRoot,
      now,
      180,
    );
    expect(pickFreshestLiveRetain(candidates)?.run_id).toBe(TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID);
    expect(
      candidates.find((candidate) => candidate.run_id === TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID)
        ?.live,
    ).toBe(false);

    const bound = bindVenueCaptureRun(
      VENUE_CAPTURE_CONTRACTS.binance,
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: artifactRoot },
      repoRoot,
      {},
      { now, freshMaxSeconds: 180 },
    );
    expect(bound.binding_source).toBe("auto-detect");
    expect(bound.runId).toBe(TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID);
  });

  it("states overlap from the current TerraPC Binance start against the shared HL/BV/KR retain", () => {
    const provenance = captureRunProvenance([
      chip({
        id: "hl",
        chip: "HL",
        series: "DATA-1A",
        run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
        binding_source: "auto-detect",
      }),
      chip({
        id: "binance",
        chip: "BINANCE",
        series: "DATA-1F",
        venue: "binance",
        product: "BTCUSDT",
        run_id: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
        binding_source: "auto-detect",
      }),
      chip({
        id: "bitvavo",
        chip: "BITVAVO",
        series: "DATA-1E",
        venue: "bitvavo",
        product: "BTC-EUR",
        run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
        binding_source: "auto-detect",
      }),
      chip({
        id: "kraken",
        chip: "KRAKEN",
        series: "DATA-1B",
        venue: "kraken",
        product: "BTC-USD",
        run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
        binding_source: "auto-detect",
      }),
    ]);
    expect(provenance.shared_run_ids).toEqual([TERRAPC_SHARED_RETAIN_RUN_ID]);
    expect(provenance.distinct_run_ids).toEqual([
      TERRAPC_SHARED_RETAIN_RUN_ID,
      TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    ]);
    expect(provenance.overlap_starts_utc).toBe("2026-09-06T10:15:59Z");
    expect(provenance.overlap_note).toMatch(/2026-09-06T10:15:59Z/);
    expect(provenance.overlap_note).toMatch(/BINANCE/);
  });
});
