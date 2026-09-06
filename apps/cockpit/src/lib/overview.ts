import {
  captureChipDataState,
  dataStateSeverity,
  worstDataState,
  type DataState,
} from "./data-state";
import type { PaperBotView, PaperRunLifecycle } from "./paper-bot";
import { clockLabel, type PollState } from "./poll-state";
import {
  RESEARCH_RUN_BINDING_NOTE,
  RESEARCH_UNAVAILABLE,
  researchVerdictTone,
  type ResearchP0View,
  type ResearchRunBinding,
  type ResearchVerdictTone,
} from "./research-p0-view";
import type { VenueCaptureStripResponse } from "./types";

export type AttentionItem = {
  id: string;
  state: DataState;
  title: string;
  detail: string;
  /** Where the operator goes to act on this. */
  href: string;
  /** Short label for the destination workspace. */
  target: string;
};

export type OverviewCapture = {
  bound: number;
  live: number;
  stale: number;
  stopped: number;
  degraded: number;
  missing: number;
  worst: DataState;
  freshMaxS: number;
  observedAt: string | undefined;
  error: string | undefined;
};

export type OverviewResearch = {
  registryRows: number;
  runsWithArtifacts: number;
  verdict: string;
  verdictTone: ResearchVerdictTone;
  runBinding: ResearchRunBinding;
  bindingNote: string;
  summarySource: string;
  available: boolean;
};

export type OverviewPaper = {
  available: boolean;
  runId: string;
  kind: string;
  lifecycle: PaperRunLifecycle;
  lastOutcome: PaperBotView["why"]["outcome"];
  lastGate: string;
  assumedPnl: string;
  assumedPnlExact: string;
  side: string;
  size: string;
  intents: number;
  rejects: number;
  error: string | undefined;
};

export type OverviewView = {
  capture: OverviewCapture;
  research: OverviewResearch;
  paper: OverviewPaper;
  attention: AttentionItem[];
};

/** Client read state of the polled sources the overview is built from. */
export type OverviewReadMeta = Pick<
  PollState<unknown>,
  "error" | "lastSuccessAt" | "failures" | "origin"
>;

export type OverviewReads = {
  strip?: OverviewReadMeta;
  research?: OverviewReadMeta;
  paper?: OverviewReadMeta;
};

const READ_TARGETS: Record<keyof OverviewReads, { title: string; href: string; target: string }> = {
  strip: { title: "Capture strip request failed", href: "/system", target: "System" },
  research: { title: "Research request failed", href: "/research", target: "Research" },
  paper: { title: "PAPER run request failed", href: "/paper", target: "PAPER" },
};

/**
 * A failed client read is an attention item in its own right.
 *
 * The values on screen are then from an earlier successful read (or the server
 * render), so the operator must be told that the ticking clock is not proof of
 * fresh data.
 */
export function readFailureAttention(reads: OverviewReads): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const key of Object.keys(READ_TARGETS) as (keyof OverviewReads)[]) {
    const meta = reads[key];
    if (meta === undefined || meta.error === undefined) {
      continue;
    }
    const shown =
      meta.lastSuccessAt === undefined
        ? meta.origin === "server"
          ? "Showing values from the server render."
          : "No successful read yet."
        : `Showing values from the last successful read at ${clockLabel(meta.lastSuccessAt)}.`;
    const target = READ_TARGETS[key];
    items.push({
      id: `read-${key}`,
      state: "error",
      title: target.title,
      detail: `${meta.error} ${shown} ${String(meta.failures)} consecutive failure(s).`,
      href: target.href,
      target: target.target,
    });
  }
  return items;
}

function captureSummary(strip: VenueCaptureStripResponse): OverviewCapture {
  if (!strip.ok) {
    return {
      bound: 0,
      live: 0,
      stale: 0,
      stopped: 0,
      degraded: 0,
      missing: 0,
      worst: "error",
      freshMaxS: 0,
      observedAt: undefined,
      error: strip.error,
    };
  }
  const venues = strip.strip.venues;
  return {
    bound: venues.length,
    live: venues.filter((venue) => venue.live).length,
    stale: venues.filter((venue) => venue.status === "STALE").length,
    stopped: venues.filter((venue) => venue.status === "STOPPED").length,
    degraded: venues.filter((venue) => venue.status === "DEGRADED").length,
    missing: venues.filter((venue) => venue.status === "MISSING").length,
    worst: worstDataState(venues.map((venue) => captureChipDataState(venue.status))),
    freshMaxS: strip.strip.fresh_max_s,
    observedAt: strip.strip.observed_at,
    error: undefined,
  };
}

function researchSummary(research: ResearchP0View): OverviewResearch {
  const sufficiency = research.sufficiency;
  const runsWithArtifacts = research.registry.filter(
    (row) => row.runId !== "n/a" && row.status !== "MISSING",
  ).length;
  return {
    registryRows: research.registry.length,
    runsWithArtifacts,
    verdict: sufficiency.verdict,
    verdictTone: researchVerdictTone(sufficiency.verdict, sufficiency.runBinding),
    runBinding: sufficiency.runBinding,
    bindingNote: RESEARCH_RUN_BINDING_NOTE[sufficiency.runBinding],
    summarySource: sufficiency.source,
    available: sufficiency.verdict !== RESEARCH_UNAVAILABLE,
  };
}

function paperSummary(paper: PaperBotView): OverviewPaper {
  return {
    available: paper.available,
    runId: paper.what.runId,
    kind: paper.what.kind,
    lifecycle: paper.lifecycle,
    lastOutcome: paper.why.outcome,
    lastGate: paper.why.gateName,
    assumedPnl: paper.results.assumedPnl,
    assumedPnlExact: paper.results.assumedPnlExact,
    side: paper.results.side,
    size: paper.results.size,
    intents: paper.tape.length,
    rejects: paper.tapeRejects.count,
    error: paper.error,
  };
}

/**
 * Everything the overview screen needs, derived from data already on disk.
 *
 * The attention list is the point of the screen: it turns per-panel states
 * into a ranked list of things a human should look at, each with the
 * workspace that can explain it. Nothing here invents a value — an item only
 * appears because a capture, research or PAPER artifact said so.
 */
export function buildOverviewView(
  strip: VenueCaptureStripResponse,
  research: ResearchP0View,
  paper: PaperBotView,
  reads: OverviewReads = {},
): OverviewView {
  const capture = captureSummary(strip);
  const attention: AttentionItem[] = readFailureAttention(reads);

  if (!strip.ok) {
    attention.push({
      id: "capture-strip",
      state: "error",
      title: "Capture strip could not be read",
      detail: strip.error,
      href: "/system",
      target: "System",
    });
  } else {
    for (const venue of strip.strip.venues) {
      const state = captureChipDataState(venue.status);
      if (state === "ok") {
        continue;
      }
      attention.push({
        id: `capture-${venue.id}`,
        state,
        title: `${venue.chip} ${venue.series} capture is ${venue.status.toLowerCase()}`,
        detail: venue.status_detail,
        href: "/system",
        target: "System",
      });
    }
  }

  if (research.error !== undefined) {
    attention.push({
      id: "research-error",
      state: "error",
      title: "Research view could not be built",
      detail: research.error,
      href: "/research",
      target: "Research",
    });
  }

  const sufficiency = research.sufficiency;
  if (sufficiency.verdict === RESEARCH_UNAVAILABLE) {
    attention.push({
      id: "research-sufficiency",
      state: "missing",
      title: "No WP-Q1 panel summary is pointed",
      detail: sufficiency.source,
      href: "/research",
      target: "Research",
    });
  } else if (sufficiency.runBinding !== "matched") {
    attention.push({
      id: "research-binding",
      state: "stale",
      title: "Research summary does not match the bound runs",
      detail: RESEARCH_RUN_BINDING_NOTE[sufficiency.runBinding],
      href: "/research",
      target: "Research",
    });
  }

  if (!paper.available) {
    attention.push({
      id: "paper-run",
      state: "missing",
      title: "PAPER run artifacts are unavailable",
      detail: paper.error ?? "COURSE-1 soak JSON is not pointed.",
      href: "/paper",
      target: "PAPER",
    });
  } else if (paper.why.outcome === "REJECT") {
    attention.push({
      id: "paper-reject",
      state: "stale",
      title: `Last PAPER decision was rejected by ${paper.why.gateName}`,
      detail: paper.why.reason,
      href: "/paper",
      target: "PAPER",
    });
  } else if (paper.why.outcome === "UNAVAILABLE") {
    attention.push({
      id: "paper-decision",
      state: "missing",
      title: "Last PAPER decision is not recorded",
      detail: paper.why.source,
      href: "/paper",
      target: "PAPER",
    });
  }

  if (!paper.preflight.available && paper.available) {
    attention.push({
      id: "paper-preflight",
      state: "missing",
      title: "Run claim has no preflight block",
      detail: `${paper.preflight.source}. Documented #65 caps are shown instead of run-specific ones.`,
      href: "/paper",
      target: "PAPER",
    });
  }

  attention.sort((left, right) => dataStateSeverity(right.state) - dataStateSeverity(left.state));

  return {
    capture,
    research: researchSummary(research),
    paper: paperSummary(paper),
    attention,
  };
}
