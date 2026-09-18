import { mkdirSync, mkdtempSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { loadData1ACaptureSnapshot } from "./data1a-capture";
import { CANONICAL_DATA1A_FIXTURE_RUN_ID, DATA1A_LIVE_EVIDENCE_RELATIVE_DIR } from "./paths";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const observedAt = "2026-09-04T13:49:40.000Z";

function writeJson(path: string, payload: unknown): void {
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8" });
}

describe("DATA-1A capture loader", () => {
  it("copies retained claim/health from the committed sample fixture without inventing parts", () => {
    const snapshot = loadData1ACaptureSnapshot(
      { TRADING_MODE: "PAPER" },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(snapshot.runId).toBe(CANONICAL_DATA1A_FIXTURE_RUN_ID);
    expect(snapshot.source).toBe("default-fixture");
    expect(snapshot.claim.retained).toBe(true);
    expect(snapshot.claim.state).toBe("STARTED_FAIL_CLOSED");
    expect(snapshot.claim.twenty_four_seven).toBe(false);
    expect(snapshot.health?.status).toBe("COMPLETED");
    expect(snapshot.health?.gaps).toBe(0);
    expect(snapshot.health?.reconnects).toBe(0);
    expect(snapshot.health?.transport_profiles).toBeUndefined();
    expect(snapshot.health_missing).toBe(false);
    expect(snapshot.parts.raw_dir_present).toBe(false);
    expect(snapshot.parts.count).toBeUndefined();
    expect(snapshot.parts.last_part_mtime_utc).toBeUndefined();
    expect(snapshot.duckdb_present).toBe(false);
    expect(snapshot.observed_at).toBe(observedAt);
    expect(snapshot.fresh_max_s).toBe(180);
  });

  it("copies live-evidence OPERATOR_STOP health and does not invent parquet payloads", () => {
    const snapshot = loadData1ACaptureSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_DATA1A_RUN_DIR: join(repoRoot, DATA1A_LIVE_EVIDENCE_RELATIVE_DIR),
        COCKPIT_DATA1A_RUN_ID: "20260904t001700z-live-retained",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(snapshot.runId).toBe("20260904t001700z-live-retained");
    expect(snapshot.claim.retained).toBe(true);
    expect(snapshot.health?.status).toBe("OPERATOR_STOP");
    expect(snapshot.health?.gaps).toBe(1);
    expect(snapshot.health?.reconnects).toBe(1);
    expect(snapshot.health?.parquet_files).toBe(41);
    expect(snapshot.parts.raw_dir_present).toBe(false);
    expect(snapshot.parts.count).toBeUndefined();
  });

  it("treats missing health as an explicit empty state during a live-looking run", () => {
    const runDir = mkdtempSync(join(tmpdir(), "data1a-live-"));
    mkdirSync(join(runDir, "raw"));
    writeJson(join(runDir, "capture-claim.json"), {
      schema: "data-1a-retained-capture-claim-v1",
      path_contract: "data-1a-hyperliquid-btc-perp-v1",
      run_id: "20260904t134940z-live-retained",
      state: "STARTED_FAIL_CLOSED",
      retained: true,
      twenty_four_seven: false,
      credentialless: true,
      signing: false,
      venue: "hyperliquid",
      product: "BTC-PERP",
      feed: "hyperliquid-public-btc-perp-trades-bbo-l2-ctx",
      duration_seconds: 259200,
    });
    const older = join(runDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    const newer = join(runDir, "raw", "part-000002-000000000011-000000000020-def.parquet");
    writeFileSync(older, "alpha", { encoding: "utf8" });
    writeFileSync(newer, "bravo-charlie", { encoding: "utf8" });
    utimesSync(older, new Date("2026-09-04T13:40:00Z"), new Date("2026-09-04T13:40:00Z"));
    utimesSync(newer, new Date("2026-09-04T13:49:00Z"), new Date("2026-09-04T13:49:00Z"));

    const snapshot = loadData1ACaptureSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_DATA1A_RUN_DIR: runDir,
        COCKPIT_DATA1A_RUN_ID: "20260904t134940z-live-retained",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(snapshot.health).toBeUndefined();
    expect(snapshot.health_missing).toBe(true);
    expect(snapshot.health_error).toMatch(/not written yet/);
    expect(snapshot.parts.raw_dir_present).toBe(true);
    expect(snapshot.parts.count).toBe(2);
    expect(snapshot.parts.last_part_name).toBe("part-000002-000000000011-000000000020-def.parquet");
    expect(snapshot.parts.last_part_mtime_utc).toBe("2026-09-04T13:49:00.000Z");
    expect(snapshot.parts.bytes).toBe("alpha".length + "bravo-charlie".length);
    expect(snapshot.duckdb_present).toBe(false);
    expect(snapshot.fresh_max_s).toBe(180);
  });

  it("copies a custom COCKPIT_CAPTURE_FRESH_MAX_S onto the snapshot without inventing parts", () => {
    const runDir = mkdtempSync(join(tmpdir(), "data1a-fresh-"));
    mkdirSync(join(runDir, "raw"));
    writeJson(join(runDir, "capture-claim.json"), {
      schema: "data-1a-retained-capture-claim-v1",
      path_contract: "data-1a-hyperliquid-btc-perp-v1",
      run_id: "fresh-max-run",
      state: "STARTED_FAIL_CLOSED",
      retained: true,
      twenty_four_seven: false,
      signing: false,
    });
    const snapshot = loadData1ACaptureSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_DATA1A_RUN_DIR: runDir,
        COCKPIT_DATA1A_RUN_ID: "fresh-max-run",
        COCKPIT_CAPTURE_FRESH_MAX_S: "60",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(snapshot.fresh_max_s).toBe(60);
    expect(snapshot.parts.count).toBe(0);
  });

  it("copies transport_profiles when health JSON already has them", () => {
    const runDir = mkdtempSync(join(tmpdir(), "data1a-profiles-"));
    writeJson(join(runDir, "capture-claim.json"), {
      schema: "data-1a-retained-capture-claim-v1",
      path_contract: "data-1a-hyperliquid-btc-perp-v1",
      run_id: "profile-run",
      state: "STARTED_FAIL_CLOSED",
      retained: true,
      twenty_four_seven: false,
      signing: false,
    });
    writeJson(join(runDir, "capture-health.json"), {
      schema: "data-1a-retained-capture-health-v1",
      kind: "capture-health",
      path_contract: "data-1a-hyperliquid-btc-perp-v1",
      run_id: "profile-run",
      status: "COMPLETED",
      retained: true,
      twenty_four_seven: false,
      gaps: 1,
      reconnects: 2,
      reconnect_clusters: 1,
      exception_class: "ConnectionClosedError",
      close_code: 1011,
      close_code_rcvd: 1011,
      close_code_sent: 1011,
      close_reason_rcvd: "internal error",
      close_reason_sent: "keepalive ping timeout",
      limitations: [],
      transport_profiles: [
        { transport_profile: "spot", gaps: 0, reconnects: 0 },
        {
          transport_profile: "usdm_public",
          gaps: 1,
          reconnects: 2,
          reconnect_clusters: 1,
          exception_class: "ConnectionClosedError",
          close_code: 1008,
          close_code_rcvd: 1008,
          close_reason_rcvd: "Too many requests",
          errno: 104,
        },
      ],
    });
    const snapshot = loadData1ACaptureSnapshot(
      {
        TRADING_MODE: "PAPER",
        COCKPIT_DATA1A_RUN_DIR: runDir,
        COCKPIT_DATA1A_RUN_ID: "profile-run",
      },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(snapshot.health?.transport_profiles).toEqual([
      { transport_profile: "spot", gaps: 0, reconnects: 0 },
      {
        transport_profile: "usdm_public",
        gaps: 1,
        reconnects: 2,
        reconnect_clusters: 1,
        exception_class: "ConnectionClosedError",
        close_code: 1008,
        close_code_rcvd: 1008,
        close_reason_rcvd: "Too many requests",
        errno: 104,
      },
    ]);
    expect(snapshot.health).toMatchObject({
      reconnect_clusters: 1,
      exception_class: "ConnectionClosedError",
      close_code: 1011,
      close_code_rcvd: 1011,
      close_code_sent: 1011,
      close_reason_rcvd: "internal error",
      close_reason_sent: "keepalive ping timeout",
    });
  });

  it("does not invent part counts when the run directory is missing", () => {
    expect(() =>
      loadData1ACaptureSnapshot(
        {
          TRADING_MODE: "PAPER",
          ARTIFACT_ROOT: join(tmpdir(), "missing-data1a-root"),
          COCKPIT_DATA1A_RUN_ID: "20260904t134940z-live-retained",
        },
        repoRoot,
      ),
    ).toThrow(/run directory is missing/);
    expect(() =>
      loadData1ACaptureSnapshot(
        {
          TRADING_MODE: "PAPER",
          ARTIFACT_ROOT: join(tmpdir(), "missing-data1a-root"),
          DATA1A_RUN_ID: "20260904t134940z-live-retained",
        },
        repoRoot,
      ),
    ).toThrow(/DATA1A_RUN_ID/);
  });

  it("refuses a non-positive COCKPIT_CAPTURE_FRESH_MAX_S before inventing RUNNING", () => {
    expect(() =>
      loadData1ACaptureSnapshot(
        { TRADING_MODE: "PAPER", COCKPIT_CAPTURE_FRESH_MAX_S: "0" },
        repoRoot,
      ),
    ).toThrow(/COCKPIT_CAPTURE_FRESH_MAX_S/);
  });

  it("refuses LIVE trading mode before reading capture files", () => {
    expect(() => loadData1ACaptureSnapshot({ TRADING_MODE: "LIVE" }, repoRoot)).toThrow(
      /PAPER-only/,
    );
  });

  it("auto-detects a live DATA-1A retain when ARTIFACT_ROOT has no run_id", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "data1a-auto-"));
    const runId = "20260905t232635z-live-retained";
    const runDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", runId);
    mkdirSync(join(runDir, "raw"), { recursive: true });
    writeJson(join(runDir, "capture-claim.json"), {
      schema: "data-1a-retained-capture-claim-v1",
      path_contract: "data-1a-hyperliquid-btc-perp-v1",
      run_id: runId,
      state: "STARTED_FAIL_CLOSED",
      retained: true,
      twenty_four_seven: false,
      signing: false,
    });
    const part = join(runDir, "raw", "part-000001-000000000001-000000000010-abc.parquet");
    writeFileSync(part, "hl-part", { encoding: "utf8" });
    utimesSync(part, new Date("2026-09-04T13:49:20Z"), new Date("2026-09-04T13:49:20Z"));

    const snapshot = loadData1ACaptureSnapshot(
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: artifactRoot },
      repoRoot,
      {},
      () => observedAt,
    );
    expect(snapshot.source).toBe("auto-detect");
    expect(snapshot.runId).toBe(runId);
    expect(snapshot.parts.count).toBe(1);
    expect(snapshot.health_missing).toBe(true);
  });

  it("uses query run_id with ARTIFACT_ROOT for the reconstructable path contract", () => {
    const artifactRoot = mkdtempSync(join(tmpdir(), "data1a-root-"));
    const runDir = join(artifactRoot, "data-1a", "hyperliquid", "BTC-PERP", "query-run");
    mkdirSync(runDir, { recursive: true });
    writeJson(join(runDir, "capture-claim.json"), {
      schema: "data-1a-retained-capture-claim-v1",
      path_contract: "data-1a-hyperliquid-btc-perp-v1",
      run_id: "query-run",
      state: "STARTED_FAIL_CLOSED",
      retained: true,
      twenty_four_seven: false,
    });

    const snapshot = loadData1ACaptureSnapshot(
      { TRADING_MODE: "PAPER", ARTIFACT_ROOT: artifactRoot },
      repoRoot,
      { data1a_run_id: "query-run" },
      () => observedAt,
    );
    expect(snapshot.source).toBe("path-contract");
    expect(snapshot.runId).toBe("query-run");
    expect(snapshot.runDir).toBe(runDir);
    expect(snapshot.health_missing).toBe(true);
    expect(snapshot.parts.count).toBeUndefined();
  });
});
