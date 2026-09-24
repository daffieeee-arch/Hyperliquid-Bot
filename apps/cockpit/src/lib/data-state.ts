import type { VenueCaptureChipStatus } from "./types";

/**
 * One vocabulary for "why is this not a number".
 *
 * The cockpit must never blur three very different situations:
 * `missing`  — the artifact was never pointed at / never written;
 * `stale`    — the artifact exists but its last write is older than the freshness bound;
 * `error`    — the artifact exists but could not be read or violates its contract.
 *
 * `pending` covers "written at stop" contracts where absence is expected mid-run.
 */
export const DATA_STATES = ["ok", "pending", "stale", "missing", "error"] as const;

export type DataState = (typeof DATA_STATES)[number];

export type DataTone = "ok" | "warn" | "down" | "info" | "muted";

export type DataStateMeta = {
  state: DataState;
  label: string;
  tone: DataTone;
  /** Short operator-facing explanation of what this state means. */
  meaning: string;
};

const META: Record<DataState, DataStateMeta> = {
  ok: {
    state: "ok",
    label: "OK",
    tone: "ok",
    meaning: "Reconstructable and inside the freshness bound.",
  },
  pending: {
    state: "pending",
    label: "Pending",
    tone: "info",
    meaning: "Written at stop. Absence mid-run is expected, not a fault.",
  },
  stale: {
    state: "stale",
    label: "Stale",
    tone: "warn",
    meaning: "Artifact exists but the last write is older than the freshness bound.",
  },
  missing: {
    state: "missing",
    label: "Missing",
    tone: "muted",
    meaning: "Never pointed at or never written. Values stay UNAVAILABLE, never zero.",
  },
  error: {
    state: "error",
    label: "Error",
    tone: "down",
    meaning: "Artifact is present but unreadable or breaks its contract.",
  },
};

export function dataStateMeta(state: DataState): DataStateMeta {
  return META[state];
}

export function dataStateTone(state: DataState): DataTone {
  return META[state].tone;
}

/** Rank used to surface the most urgent state first in summaries. */
export function dataStateSeverity(state: DataState): number {
  switch (state) {
    case "error":
      return 4;
    case "stale":
      return 3;
    case "missing":
      return 2;
    case "pending":
      return 1;
    case "ok":
      return 0;
    default: {
      const exhaustive: never = state;
      throw new Error(`Unhandled data state: ${String(exhaustive)}`);
    }
  }
}

export function worstDataState(states: readonly DataState[]): DataState {
  return states.reduce<DataState>(
    (worst, next) => (dataStateSeverity(next) > dataStateSeverity(worst) ? next : worst),
    "ok",
  );
}

/**
 * Map a venue capture chip status onto the shared vocabulary.
 *
 * STOPPED is deliberately `ok`: a bounded retained capture that finished is a
 * correct terminal state, not a fault. DEGRADED is the read/contract failure.
 * UNKNOWN is fresh storage without a health file: not a proven healthy feed.
 */
export function captureChipDataState(status: VenueCaptureChipStatus): DataState {
  switch (status) {
    case "RUNNING":
      return "ok";
    case "STOPPED":
      return "ok";
    case "STALE":
      return "stale";
    case "MISSING":
      return "missing";
    case "DEGRADED":
      return "error";
    case "UNKNOWN":
      return "pending";
    default: {
      const exhaustive: never = status;
      throw new Error(`Unhandled capture chip status: ${String(exhaustive)}`);
    }
  }
}
