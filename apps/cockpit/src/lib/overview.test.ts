import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { captureChipDataState, worstDataState } from "./data-state";
import { activeRouteId, cockpitRouteOrder } from "./navigation";
import { buildOverviewView } from "./overview";
import { buildPaperBotView } from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import { buildResearchP0View } from "./research-p0";
import { loadVenueCaptureStrip } from "./venue-capture";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const env = { TRADING_MODE: "PAPER" };
const observedAt = "2026-09-06T12:00:00.000Z";

function fixtureOverview() {
  const strip = {
    ok: true as const,
    strip: loadVenueCaptureStrip(env, repoRoot, {}, () => observedAt),
  };
  const research = buildResearchP0View(strip, env, repoRoot, {}, observedAt);
  const paper = buildPaperBotView(loadPaperRunSnapshot(env, repoRoot), undefined);
  return { strip, research, paper, view: buildOverviewView(strip, research, paper) };
}

describe("capture data states", () => {
  it("separates missing, stale and error instead of blurring them into one warning", () => {
    expect(captureChipDataState("RUNNING")).toBe("ok");
    expect(captureChipDataState("STOPPED")).toBe("ok");
    expect(captureChipDataState("STALE")).toBe("stale");
    expect(captureChipDataState("MISSING")).toBe("missing");
    expect(captureChipDataState("DEGRADED")).toBe("error");
  });

  it("ranks the worst state so summaries surface faults first", () => {
    expect(worstDataState(["ok", "missing", "stale"])).toBe("stale");
    expect(worstDataState(["ok", "missing"])).toBe("missing");
    expect(worstDataState(["stale", "error"])).toBe("error");
    expect(worstDataState(["ok", "ok"])).toBe("ok");
  });
});

describe("cockpit navigation", () => {
  it("keeps one workspace per question and highlights nested paths", () => {
    expect(cockpitRouteOrder()).toEqual(["overview", "markets", "research", "paper", "system"]);
    expect(activeRouteId("/")).toBe("overview");
    expect(activeRouteId("/markets")).toBe("markets");
    expect(activeRouteId("/research?data1a_run_id=x")).toBe("research");
    expect(activeRouteId("/system/anything")).toBe("system");
  });
});

describe("overview view", () => {
  it("summarises capture, research and PAPER from the default fixtures", () => {
    const { view } = fixtureOverview();
    expect(view.capture.bound).toBe(5);
    expect(view.capture.error).toBeUndefined();
    expect(view.paper.runId).toBe("20260904t001800z-live-paper");
    expect(view.paper.lastOutcome).toBe("ACCEPT");
    expect(view.research.verdict).toBe("UNAVAILABLE");
  });

  it("raises an attention item for every venue that is not OK, ranked by severity", () => {
    const { strip, view } = fixtureOverview();
    const notOk = strip.strip.venues.filter((venue) => captureChipDataState(venue.status) !== "ok");
    for (const venue of notOk) {
      expect(view.attention.some((item) => item.id === `capture-${venue.id}`)).toBe(true);
    }
    const severities = view.attention.map((item) => item.state);
    const errorIndex = severities.indexOf("error");
    const missingIndex = severities.indexOf("missing");
    if (errorIndex !== -1 && missingIndex !== -1) {
      expect(errorIndex).toBeLessThan(missingIndex);
    }
  });

  it("flags a missing WP-Q1 summary rather than implying research is available", () => {
    const { view } = fixtureOverview();
    expect(view.research.available).toBe(false);
    expect(view.attention.some((item) => item.id === "research-sufficiency")).toBe(true);
    const item = view.attention.find((item) => item.id === "research-sufficiency");
    expect(item?.href).toBe("/research");
    expect(item?.state).toBe("missing");
  });

  it("surfaces a failed research request on the overview while keeping the old view", () => {
    const { strip, research, paper } = fixtureOverview();
    const view = buildOverviewView(strip, research, paper, {
      research: {
        error: "Research P0 response was not a fail-closed object.",
        lastSuccessAt: "2026-09-06T12:00:00.000Z",
        failures: 2,
        origin: "client",
      },
    });
    const item = view.attention.find((entry) => entry.id === "read-research");
    expect(item?.state).toBe("error");
    expect(item?.href).toBe("/research");
    expect(item?.detail).toMatch(/last successful read at 14:00:00 CEST \(12:00:00Z\)/);
    expect(item?.detail).toMatch(/2 consecutive failure/);
    // The research summary itself is still the last good one, not blanked.
    expect(view.research.registryRows).toBe(research.registry.length);
    // Read failures rank first.
    expect(view.attention[0]?.id).toBe("read-research");
  });

  it("says when failed reads are still showing the server render", () => {
    const { strip, research, paper } = fixtureOverview();
    const view = buildOverviewView(strip, research, paper, {
      strip: { error: "fetch failed", lastSuccessAt: undefined, failures: 1, origin: "server" },
    });
    const item = view.attention.find((entry) => entry.id === "read-strip");
    expect(item?.detail).toMatch(/server render/);
    expect(item?.href).toBe("/system");
  });

  it("labels the fixture soak as a historical snapshot from the repository fixture", () => {
    const { view, paper } = fixtureOverview();
    expect(paper.lifecycle.state).toBe("historical");
    expect(paper.lifecycle.healthStatus).toBe("COMPLETED_FLAT");
    expect(paper.lifecycle.artifactSource).toBe("default-fixture");
    expect(view.paper.lifecycle.label).toBe("Historical snapshot");
    // A finished run is legitimate history, not an attention item.
    expect(view.attention.some((item) => item.id.startsWith("paper-lifecycle"))).toBe(false);
  });

  it("fails closed on an unreadable capture strip", () => {
    const research = buildResearchP0View(
      { ok: false, error: "strip broke" },
      env,
      repoRoot,
      {},
      observedAt,
    );
    const paper = buildPaperBotView(undefined, "no paper run");
    const view = buildOverviewView({ ok: false, error: "strip broke" }, research, paper);
    expect(view.capture.worst).toBe("error");
    expect(view.attention[0]?.state).toBe("error");
    expect(view.attention.some((item) => item.id === "paper-run")).toBe(true);
  });
});
