import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  captureChipOrigin,
  describeOriginSummary,
  marketTapeOrigin,
  paperOrigin,
  stripOrigin,
  summariseOrigins,
} from "./data-origin";
import { loadMarketTapeStrip, resetMarketTapeCache } from "./market-tape";
import {
  formatBytes,
  instrumentRows,
  summariseVenueTape,
  venueTapeSummaries,
} from "./market-tape-rows";
import { buildOverviewView, storedDataAttention } from "./overview";
import { buildPaperBotView, buildPaperRunLifecycle } from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import { buildResearchP0View } from "./research-p0";
import type { VenueCaptureChip, VenueCaptureStripResponse } from "./types";
import { loadVenueCaptureStrip } from "./venue-capture";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixtureRoot = resolve(repoRoot, "tests", "fixtures", "market_tape", "artifact-root");
const observedAt = "2026-09-05T18:01:00.000Z";

const tapeEnv = {
  TRADING_MODE: "PAPER",
  ARTIFACT_ROOT: fixtureRoot,
  COCKPIT_DATA1A_RUN_ID: "20260905t180000z-live-retained",
  COCKPIT_DATA1F_RUN_ID: "20260905t180100z-live-retained",
  COCKPIT_DATA1D_RUN_ID: "20260905t180130z-live-retained",
  COCKPIT_DATA1E_RUN_ID: "20260905t180200z-live-retained",
  COCKPIT_DATA1B_RUN_ID: "20260905t180300z-live-retained",
};

function chip(overrides: Partial<VenueCaptureChip>): VenueCaptureChip {
  return {
    id: "binance",
    chip: "BINANCE",
    series: "DATA-1F",
    venue: "binance",
    product: "BTCUSDT",
    path_contract: "x",
    status: "RUNNING",
    status_detail: "",
    tone: "ok",
    live: true,
    part_count: 3,
    last_part_age: "10s",
    last_part_mtime_utc: observedAt,
    gaps: undefined,
    reconnects: undefined,
    run_id: "20260905t180100z-live-retained",
    binding_source: "env",
    observed_at: observedAt,
    error: undefined,
    ...overrides,
  };
}

describe("data origin", () => {
  it("never lets the repository fixture pass as a capture", () => {
    expect(captureChipOrigin(chip({ binding_source: "default-fixture", live: true }))).toBe("demo");
    expect(captureChipOrigin(chip({ binding_source: "auto-detect", live: true }))).toBe("live");
    expect(captureChipOrigin(chip({ binding_source: "env", live: false, status: "STOPPED" }))).toBe(
      "historical",
    );
    expect(captureChipOrigin(chip({ binding_source: "env", live: false, status: "STALE" }))).toBe(
      "historical",
    );
    expect(captureChipOrigin(chip({ status: "MISSING", live: false, run_id: undefined }))).toBe(
      "unbound",
    );
  });

  it("marks a screen demo as soon as one venue is the fixture, mixed when live and historical meet", () => {
    expect(summariseOrigins(["live", "live", "demo", "live"]).origin).toBe("demo");
    expect(summariseOrigins(["live", "historical"]).origin).toBe("mixed");
    expect(summariseOrigins(["live", "live"]).origin).toBe("live");
    expect(summariseOrigins(["historical", "unbound"]).origin).toBe("historical");
    expect(summariseOrigins(["unbound"]).origin).toBe("unbound");
    expect(summariseOrigins([]).origin).toBe("unbound");
    expect(describeOriginSummary(summariseOrigins(["live", "historical", "unbound"]))).toBe(
      "1 live · 1 historical · 1 unbound",
    );
  });

  it("classifies the default cockpit fixtures as demo, and a failed strip as unbound", () => {
    const strip: VenueCaptureStripResponse = {
      ok: true,
      strip: loadVenueCaptureStrip({ TRADING_MODE: "PAPER" }, repoRoot, {}, () => observedAt),
    };
    expect(stripOrigin(strip).origin).toBe("demo");
    expect(stripOrigin({ ok: false, error: "boom" }).origin).toBe("unbound");
  });

  it("labels PAPER artifacts by source and lifecycle", () => {
    const fixture = buildPaperRunLifecycle(
      loadPaperRunSnapshot({ TRADING_MODE: "PAPER" }, repoRoot),
    );
    expect(paperOrigin(fixture)).toBe("demo");
    expect(paperOrigin(buildPaperRunLifecycle(undefined))).toBe("unbound");
    expect(paperOrigin({ ...fixture, artifactSource: "paper-run-dir", state: "historical" })).toBe(
      "historical",
    );
    expect(paperOrigin({ ...fixture, artifactSource: "path-contract", state: "in-flight" })).toBe(
      "live",
    );
  });
});

describe("market tape rows", () => {
  it("formats byte volumes for operators", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(3463)).toBe("3.4 KB");
    expect(formatBytes(150 * 1024 * 1024)).toBe("150 MB");
    expect(formatBytes(-1)).toBe("n/a");
  });

  it("keeps Binance spot and USDS-M perpetual on separate rows with their own quote", async () => {
    resetMarketTapeCache();
    const tape = await loadMarketTapeStrip(tapeEnv, repoRoot, {}, () => observedAt);
    const binance = tape.venues.find((venue) => venue.id === "binance");
    expect(binance).toBeDefined();
    if (binance === undefined) return;
    const rows = instrumentRows(binance, observedAt);
    expect(rows.map((row) => `${row.product}|${row.kind}|${row.quote}`)).toEqual([
      "BTCUSDT-SPOT|spot|USDT",
      "BTCUSDT-USDS-M-PERPETUAL|perpetual|USDT",
    ]);
    expect(rows[0]?.lastPrice).toBe("109480.10");
    expect(rows[0]?.spreadBps).toBe("0.03");
    // The perpetual only carried context (mark/funding), so no trade is invented.
    expect(rows[1]?.lastPrice).toBe("—");
    expect(rows[1]?.bid).toBe("—");
  });

  it("derives origin, freshness and publication facts per venue and flags run-id drift", async () => {
    resetMarketTapeCache();
    const tape = await loadMarketTapeStrip(tapeEnv, repoRoot, {}, () => observedAt);
    const hl = tape.venues[0];
    expect(hl).toBeDefined();
    if (hl === undefined) return;

    const liveStrip: VenueCaptureStripResponse = {
      ok: true,
      strip: {
        observed_at: observedAt,
        fresh_max_s: 180,
        venues: [chip({ id: "hl", chip: "HL", series: "DATA-1A", run_id: hl.runId })],
        catalog: [],
        provenance: { rows: [], distinct_run_ids: [], shared_run_ids: [], overlap_note: "" },
      },
    };
    const summary = summariseVenueTape(hl, liveStrip, 180);
    expect(summary.origin).toBe("live");
    expect(summary.dataState).toBe("ok");
    expect(summary.published).toBe("2 parts");
    // Presented in Europe/Amsterdam (CEST in September); the UTC ISO stays in the tooltip.
    expect(summary.lastData).toBe("20:00:05 CEST");
    expect(summary.lastDataAge).toBe("55s");
    expect(summary.volume).toMatch(/KB$/);

    // Same venue, but the strip bound a different (older) run: that is drift, not a match.
    const driftStrip: VenueCaptureStripResponse = {
      ...liveStrip,
      strip: {
        ...liveStrip.strip,
        venues: [
          chip({
            id: "hl",
            chip: "HL",
            series: "DATA-1A",
            run_id: "20260901t000000z-live-retained",
          }),
        ],
      },
    };
    const [drifted] = venueTapeSummaries({ ok: true, tape }, driftStrip);
    expect(drifted?.chip?.run_id).not.toBe(drifted?.tape.runId);

    // Live capture but the newest stored event is older than the fresh window: stale, not ok.
    const stale = summariseVenueTape(hl, liveStrip, 30);
    expect(stale.dataState).toBe("stale");

    // Without a chip the tape cannot claim liveness.
    expect(marketTapeOrigin(hl)).toBe("historical");
    expect(summariseVenueTape(hl, { ok: false, error: "x" }, 180).origin).toBe("historical");
  });

  it("reports unbound origin and no rows when the venue run is missing", async () => {
    resetMarketTapeCache();
    const tape = await loadMarketTapeStrip(
      { ...tapeEnv, COCKPIT_DATA1F_RUN_ID: "20260101t000000z-live-retained" },
      repoRoot,
      {},
      () => observedAt,
    );
    const binance = tape.venues.find((venue) => venue.id === "binance");
    expect(binance?.status).toBe("missing");
    if (binance === undefined) return;
    expect(marketTapeOrigin(binance)).toBe("unbound");
    expect(instrumentRows(binance, observedAt)).toEqual([]);
  });
});

describe("overview stored-data attention", () => {
  it("raises unreadable parts as errors and excessive publication lag as stale", async () => {
    resetMarketTapeCache();
    const read = await loadMarketTapeStrip(tapeEnv, repoRoot, {}, () => observedAt);
    // Part mtimes are checkout times in CI, so pin the lag to a healthy value first.
    const tape = {
      ...read,
      venues: read.venues.map((venue) => ({ ...venue, publicationLagS: 12 })),
    };
    expect(storedDataAttention({ ok: true, tape })).toEqual([]);
    expect(storedDataAttention(undefined)).toEqual([]);

    const broken = {
      ...tape,
      venues: tape.venues.map((venue, index) =>
        index === 0
          ? { ...venue, status: "error" as const, error: "zstd frame corrupt" }
          : index === 1
            ? { ...venue, publicationLagS: 400 }
            : venue,
      ),
    };
    const items = storedDataAttention({ ok: true, tape: broken });
    expect(items.map((item) => `${item.id}:${item.state}`)).toEqual([
      "tape-hl:error",
      "tape-lag-binance:stale",
    ]);
    expect(items[0]?.detail).toBe("zstd frame corrupt");
    expect(items[1]?.title).toContain("6m 40s");

    const failed = storedDataAttention({ ok: false, error: "PAPER-only" });
    expect(failed[0]).toMatchObject({ id: "tape", state: "error", href: "/markets" });
  });

  it("threads a failed stored-data read into the overview attention list", () => {
    const env = { TRADING_MODE: "PAPER" };
    const strip: VenueCaptureStripResponse = {
      ok: true,
      strip: loadVenueCaptureStrip(env, repoRoot, {}, () => observedAt),
    };
    const research = buildResearchP0View(strip, env, repoRoot, {}, observedAt);
    const paper = buildPaperBotView(loadPaperRunSnapshot(env, repoRoot), undefined);
    const view = buildOverviewView(
      strip,
      research,
      paper,
      {
        tape: {
          error: "fetch failed",
          lastSuccessAt: "2026-09-05T18:00:30.000Z",
          failures: 2,
          origin: "client",
        },
      },
      { ok: false, error: "fetch failed" },
    );
    const readItem = view.attention.find((item) => item.id === "read-tape");
    expect(readItem).toMatchObject({ state: "error", target: "Markets" });
    expect(readItem?.detail).toContain("20:00:30 CEST");
    expect(readItem?.detail).not.toMatch(/Z/);
    expect(readItem?.detail).toContain("2 consecutive failure(s)");
    expect(view.attention.some((item) => item.id === "tape")).toBe(true);
  });
});
