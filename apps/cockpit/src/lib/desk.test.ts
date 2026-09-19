import { describe, expect, it } from "vitest";

import {
  DESK_MODE,
  DESK_UNAVAILABLE,
  PHASE_A_CLAIMED_SECONDS,
  countCaptureStatuses,
  deskCaptureGlance,
  deskGlanceLine,
  deskPaperIdentity,
  deskPhaseAProgressLine,
} from "./desk";
import { DEFAULT_CAPTURE_FRESH_MAX_S } from "./capture-freshness";
import {
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
} from "./terrapc-defaults";
import type { VenueCaptureChip, VenueCaptureStrip } from "./types";

function chip(
  partial: Partial<VenueCaptureChip> & Pick<VenueCaptureChip, "id" | "chip">,
): VenueCaptureChip {
  return {
    series: "DATA-1A",
    venue: "hyperliquid",
    product: "BTC-PERP",
    path_contract: "data-1a-hyperliquid-btc-perp-v1",
    status: "MISSING",
    status_detail: "not pointed",
    tone: "warn",
    live: false,
    part_count: undefined,
    last_part_age: "n/a",
    last_part_mtime_utc: undefined,
    gaps: undefined,
    reconnects: undefined,
    run_id: undefined,
    binding_source: "unbound",
    observed_at: "2026-09-06T10:15:59.000Z",
    error: "fail closed",
    ...partial,
  };
}

const boundStrip: VenueCaptureStrip = {
  observed_at: "2026-09-06T10:16:00.000Z",
  fresh_max_s: DEFAULT_CAPTURE_FRESH_MAX_S,
  venues: [
    chip({
      id: "hl",
      chip: "HL",
      status: "RUNNING",
      status_detail: "RUNNING (health JSON pending until stop)",
      tone: "ok",
      live: true,
      last_part_age: "12s",
      run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
      binding_source: "env",
      error: undefined,
    }),
    chip({
      id: "binance",
      chip: "BINANCE",
      series: "DATA-1F",
      venue: "binance",
      product: "BTCUSDT",
      path_contract: "data-1f-binance-btcusdt-v1",
      status: "STALE",
      status_detail: "STALE (stale_mtime)",
      tone: "warn",
      live: false,
      last_part_age: "4m",
      run_id: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
      binding_source: "env",
      error: undefined,
    }),
    chip({
      id: "bitvavo",
      chip: "BITVAVO",
      series: "DATA-1E",
      venue: "bitvavo",
      product: "BTC-EUR",
      status: "RUNNING",
      tone: "ok",
      live: true,
      last_part_age: "8s",
      run_id: TERRAPC_SHARED_RETAIN_RUN_ID,
      binding_source: "env",
      error: undefined,
    }),
    chip({
      id: "kraken",
      chip: "KRAKEN",
      series: "DATA-1B",
      venue: "kraken",
      product: "BTC-USD",
      status: "MISSING",
      live: false,
    }),
  ],
  catalog: [],
  provenance: {
    rows: [],
    distinct_run_ids: [TERRAPC_SHARED_RETAIN_RUN_ID, TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID],
    shared_run_ids: [TERRAPC_SHARED_RETAIN_RUN_ID],
    overlap_note: "Overlap starts at the later venue start.",
  },
};

describe("DESK identity and capture glance", () => {
  it("stays PAPER-only and does not invent a run when JSON is missing", () => {
    const identity = deskPaperIdentity(undefined, "PAPER run data is unavailable.");
    expect(identity.mode).toBe(DESK_MODE);
    expect(identity.signing).toBe("off");
    expect(identity.jsonAvailable).toBe(false);
    expect(identity.runId).toBe(DESK_UNAVAILABLE);
    expect(identity.instrument).toBe(DESK_UNAVAILABLE);
    expect(identity.source).toBe("fail-closed");
    expect(identity.jsonError).toMatch(/unavailable/);
  });

  it("counts live vs stale from bound chips without inventing RUNNING", () => {
    const counts = countCaptureStatuses(boundStrip.venues);
    expect(counts).toEqual({
      running: 2,
      stale: 1,
      stopped: 0,
      degraded: 0,
      missing: 1,
    });
    expect(deskGlanceLine(counts, 180)).toBe(
      "2 live · 1 stale · 0 stopped · 0 degraded · 1 missing · fresh ≤ 180s",
    );
  });

  it("copies bound run ids and the overlap note from strip provenance", () => {
    const glance = deskCaptureGlance({ ok: true, strip: boundStrip });
    expect(glance.ok).toBe(true);
    if (!glance.ok) {
      throw new Error("expected bound glance");
    }
    expect(glance.freshMaxS).toBe(180);
    expect(glance.boundRunIds).toEqual([
      TERRAPC_SHARED_RETAIN_RUN_ID,
      TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
    ]);
    expect(glance.provenanceNote).toBe("Overlap starts at the later venue start.");
    expect(glance.venues.map((venue) => `${venue.chip}:${venue.status}`)).toEqual([
      "HL:RUNNING",
      "BINANCE:STALE",
      "BITVAVO:RUNNING",
      "KRAKEN:MISSING",
    ]);
  });

  it("fails closed when the venue strip is unavailable", () => {
    const glance = deskCaptureGlance({
      ok: false,
      error: "Cockpit first screen is PAPER-only and fails closed.",
    });
    expect(glance).toEqual({
      ok: false,
      error: "Cockpit first screen is PAPER-only and fails closed.",
    });
  });
});

describe("deskPhaseAProgressLine", () => {
  it("appends HL elapsed vs the claimed 72h window from a timestamp-shaped run_id", () => {
    const line = deskPhaseAProgressLine({ ok: true, strip: boundStrip });
    // 20260905t232635z → 2026-09-05T23:26:35Z; observed 2026-09-06T10:16:00Z → 38965s
    expect(line).toBe(
      `2 live · 1 stale · 0 stopped · 0 degraded · 1 missing · fresh ≤ 180s · HL 38965s / ${String(PHASE_A_CLAIMED_SECONDS)}s (15%)`,
    );
  });

  it("fails closed to unavailable when the strip is down", () => {
    expect(
      deskPhaseAProgressLine({
        ok: false,
        error: "Cockpit first screen is PAPER-only and fails closed.",
      }),
    ).toBe(DESK_UNAVAILABLE);
  });

  it("keeps the glance line when HL has no timestamp-shaped run_id", () => {
    const strip = {
      ...boundStrip,
      venues: boundStrip.venues.map((venue) =>
        venue.id === "hl" ? { ...venue, run_id: "sample-run" } : venue,
      ),
    };
    expect(deskPhaseAProgressLine({ ok: true, strip })).toBe(
      "2 live · 1 stale · 0 stopped · 0 degraded · 1 missing · fresh ≤ 180s",
    );
  });
});
