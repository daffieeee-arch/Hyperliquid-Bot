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

export type ResearchSufficiency = {
  verdict: string;
  reasons: string[];
  hlGapFraction: string;
  bnGapFraction: string;
  overlapBuckets: string;
  panelVersion: string;
  source: string;
};

export type ResearchP0View = {
  registry: ResearchRegistryRow[];
  health: ResearchHealthRow[];
  identity: ResearchIdentityRow[];
  identityWarning: typeof BINANCE_IDENTITY_WARNING;
  overlap: ResearchOverlapClock;
  sufficiency: ResearchSufficiency;
};
