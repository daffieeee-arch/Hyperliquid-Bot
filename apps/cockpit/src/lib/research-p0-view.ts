import type { VenueCaptureChip } from "./types";

export const RESEARCH_UNAVAILABLE = "UNAVAILABLE";
export const RESEARCH_P0_SOURCE =
  "capture-claim.json / capture-health.json / research-out summaries";
export const PUBLIC_MID_NOT_RESEARCH = "public mid ≠ research truth";
export const RESEARCH_ZONE_KICKER =
  "Artifact / summary cards only · fail-closed UNAVAILABLE · public mid ≠ research truth";
export const BINANCE_IMPULSE_DEFAULT = "binance_usdm_mark";
export const BINANCE_IDENTITY_WARNING =
  "Never blend Spot and USDM into one price. Switch the impulse instrument explicitly.";
export const PANEL_VERSION_EXPECTED = "panel_hl_binance/wp-q1.1";
export const OVERLAP_72H_SECONDS = 72 * 60 * 60;
export const H1_LEADLAG_NOTE =
  "H1 lead-lag is P1. promotion_decision=forbidden. No edge is claimed.";

export type ResearchRegistryRow = {
  id: VenueCaptureChip["id"];
  venue: string;
  product: string;
  runId: string;
  retained: string;
  state: string;
  pathContract: string;
  wallSpan: string;
  status: string;
};

export type ResearchHealthRow = {
  id: VenueCaptureChip["id"];
  venue: string;
  gapsReconnects: string;
  transportProfiles: string;
  partCount: string;
  bytes: string;
  lastPartMtime: string;
  healthPending: boolean;
};

export type ResearchIdentityRow = {
  id: VenueCaptureChip["id"];
  venue: string;
  product: string;
  impulse: string;
};

export type ResearchOverlapClock = {
  hlBvKrRun: string;
  bnRun: string;
  overlapStart: string;
  note: string;
  elapsed: string;
  badge: "partial" | "gate_pending" | "aligned";
};

/**
 * A run reference that keeps its venue identity.
 *
 * `venue` is `"any"` only for a bare `run_id` field whose summary does not say
 * which venue it describes. `product` is present when the summary or the
 * capture contract names the instrument so spot and perpetual runs cannot be
 * confused even when a run_id happens to be shared.
 */
export type ResearchVenueRun = {
  venue: VenueCaptureChip["id"] | "any";
  runId: string;
  product?: string;
};

/**
 * How a panel summary relates to the runs the operator currently has selected.
 *
 * The comparison is made per venue: every run the summary names must equal the
 * run bound for that venue. A summary about the current Hyperliquid run and a
 * previous Binance run is `mismatched`, not "one of them matched". `partial`
 * means nothing contradicts the selection, but a venue the summary names is
 * not bound right now, so the verdict still cannot be counted.
 */
export type ResearchRunBinding = "matched" | "mismatched" | "partial" | "unknown" | "unavailable";

export const RESEARCH_RUN_BINDING_NOTE: Record<ResearchRunBinding, string> = {
  matched: "Every venue run the summary names equals the bound capture run for that venue.",
  mismatched:
    "At least one venue in the summary points at a different run_id than the current selection; it is not a verdict about the bound runs.",
  partial:
    "The summary names a venue run that is not bound in the current selection, so it cannot be attributed to the full combination.",
  unknown:
    "Summary carries no run_id; it cannot be attributed to the current selection and stays advisory.",
  unavailable: "No panel summary is pointed, so there is nothing to attribute.",
};

export type ResearchSufficiency = {
  verdict: string;
  reasons: string[];
  hlGapFraction: string;
  bnGapFraction: string;
  overlapBuckets: string;
  panelVersion: string;
  source: string;
  /** run_id values copied from the summary. Empty when the summary is anonymous. */
  runIds: string[];
  /** The same run_ids with the venue each one was declared for. */
  runRefs: ResearchVenueRun[];
  runBinding: ResearchRunBinding;
};

/**
 * Verdicts that may render as a positive (green) result.
 *
 * Everything else — including `not_enough_data` — is at best neutral. A
 * sufficiency gate that did not pass must never borrow the success colour.
 */
export const RESEARCH_POSITIVE_VERDICTS: readonly string[] = ["panel_ready", "sufficient"];

export const RESEARCH_NEGATIVE_VERDICTS: readonly string[] = [
  "not_enough_data",
  "insufficient",
  "gate_pending",
  "blocked",
  "failed",
];

export type ResearchVerdictTone = "ok" | "warn" | "down" | "muted";

/**
 * Tone for a copied sufficiency verdict.
 *
 * Allowlist-based on purpose: an unrecognised verdict stays muted rather than
 * inheriting a colour that implies a result the data does not support. A
 * positive verdict is downgraded to `warn` when it cannot be attributed to the
 * selected runs.
 */
export function researchVerdictTone(
  verdict: string,
  binding: ResearchRunBinding = "matched",
): ResearchVerdictTone {
  const normalized = verdict.trim().toLowerCase();
  if (normalized === "" || normalized === RESEARCH_UNAVAILABLE.toLowerCase()) {
    return "muted";
  }
  if (RESEARCH_NEGATIVE_VERDICTS.includes(normalized)) {
    return "warn";
  }
  if (RESEARCH_POSITIVE_VERDICTS.includes(normalized)) {
    return binding === "matched" ? "ok" : "warn";
  }
  return "muted";
}

export type ResearchP0View = {
  registry: ResearchRegistryRow[];
  health: ResearchHealthRow[];
  identity: ResearchIdentityRow[];
  identityWarning: typeof BINANCE_IDENTITY_WARNING;
  overlap: ResearchOverlapClock;
  sufficiency: ResearchSufficiency;
  /** run_id values currently bound by the capture strip, used for attribution. */
  boundRunIds: string[];
  /** Bound runs with venue and product so attribution keeps venue identity. */
  boundRuns: ResearchVenueRun[];
  /** Read timestamp so the research zone can show its own freshness. */
  observedAt: string;
  error: string | undefined;
};
