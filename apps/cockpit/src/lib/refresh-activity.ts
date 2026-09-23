import { formatAgeSeconds, secondsBetween } from "./poll-state";
import { localClockLabel } from "./time-display";

/**
 * What one polled fetch reports when it settles.
 *
 * `changed` is decided by the panel (content fingerprint of old vs new data),
 * so a refresh that returned byte-identical values can be reported honestly
 * as "already current" instead of pretending something moved.
 */
export type FetchSettlement = { changed: boolean; error?: string };

export type RefreshOutcome = {
  /** Refresh token the settled fetches were keyed on. */
  token: number;
  settledAt: string;
  fetches: number;
  changed: boolean;
  errors: string[];
};

type PendingToken = { pending: number; fetches: number; changed: boolean; errors: string[] };

/**
 * In-flight registry for the shared refresh clock.
 *
 * Every keyed poll calls `begin` when it starts a request for a token and
 * `end` when the request settles. `inFlight` is the total across tokens (a
 * slow tick can still be running when the next one starts). `lastOutcome`
 * is set the moment the last fetch of a token settles; `lastChangedAt` only
 * moves when that outcome actually carried new data.
 */
export type RefreshActivity = {
  inFlight: number;
  pending: Readonly<Record<number, PendingToken>>;
  lastOutcome: RefreshOutcome | null;
  lastChangedAt: string | null;
};

export function initialRefreshActivity(): RefreshActivity {
  return { inFlight: 0, pending: {}, lastOutcome: null, lastChangedAt: null };
}

export function refreshBegan(state: RefreshActivity, token: number): RefreshActivity {
  const current = state.pending[token] ?? { pending: 0, fetches: 0, changed: false, errors: [] };
  return {
    ...state,
    inFlight: state.inFlight + 1,
    pending: {
      ...state.pending,
      [token]: { ...current, pending: current.pending + 1, fetches: current.fetches + 1 },
    },
  };
}

export function refreshSettled(
  state: RefreshActivity,
  token: number,
  settlement: FetchSettlement,
  at: string,
): RefreshActivity {
  const current = state.pending[token];
  if (current === undefined) {
    // An `end` without a matching `begin` (e.g. a stale closure) must not drive the count negative.
    return state;
  }
  const updated: PendingToken = {
    pending: current.pending - 1,
    fetches: current.fetches,
    changed: current.changed || settlement.changed,
    errors: settlement.error === undefined ? current.errors : [...current.errors, settlement.error],
  };
  const inFlight = Math.max(0, state.inFlight - 1);
  if (updated.pending > 0) {
    return { ...state, inFlight, pending: { ...state.pending, [token]: updated } };
  }
  const { [token]: _settled, ...rest } = state.pending;
  const outcome: RefreshOutcome = {
    token,
    settledAt: at,
    fetches: updated.fetches,
    changed: updated.changed,
    errors: updated.errors,
  };
  return {
    inFlight,
    pending: rest,
    lastOutcome: outcome,
    lastChangedAt: outcome.changed && outcome.errors.length === 0 ? at : state.lastChangedAt,
  };
}

export type RefreshFeedback = {
  tone: "ok" | "warn" | "down" | "muted" | "info";
  label: string;
  detail: string;
  busy: boolean;
};

const MAX_REASON_CHARS = 60;

function shortReason(errors: readonly string[]): string {
  const first = errors[0] ?? "unknown error";
  const trimmed =
    first.length > MAX_REASON_CHARS ? `${first.slice(0, MAX_REASON_CHARS - 1)}…` : first;
  return errors.length > 1 ? `${trimmed} (+${String(errors.length - 1)} more)` : trimmed;
}

/**
 * Operator-facing feedback for the topbar refresh control.
 *
 * - busy: requests for the current token are still in flight;
 * - changed: "Updated HH:MM:SS CEST";
 * - unchanged: "Already current · age Xs" where the age counts from the last
 *   refresh that actually carried new data (or from the settle time when no
 *   refresh has changed anything yet);
 * - errors: the control is re-enabled and the first reason is shown.
 */
export function describeRefreshFeedback(
  activity: RefreshActivity,
  nowIso: string | undefined,
): RefreshFeedback {
  if (activity.inFlight > 0) {
    return {
      tone: "info",
      label: "Refreshing…",
      detail: `${String(activity.inFlight)} request(s) in flight on the shared refresh clock.`,
      busy: true,
    };
  }
  const outcome = activity.lastOutcome;
  if (outcome === null) {
    return {
      tone: "muted",
      label: "No refresh yet",
      detail: "Values come from the server render; the first client refresh has not completed.",
      busy: false,
    };
  }
  if (outcome.errors.length > 0) {
    return {
      tone: "down",
      label: `Refresh failed · ${shortReason(outcome.errors)}`,
      detail: `${String(outcome.errors.length)} of ${String(outcome.fetches)} request(s) failed at ${localClockLabel(outcome.settledAt)}. Previous values stay on screen. ${outcome.errors.join(" | ")}`,
      busy: false,
    };
  }
  if (outcome.changed) {
    return {
      tone: "ok",
      label: `Updated ${localClockLabel(outcome.settledAt)}`,
      detail: `${String(outcome.fetches)} request(s) settled at ${localClockLabel(outcome.settledAt)} with new data.`,
      busy: false,
    };
  }
  const since = activity.lastChangedAt ?? outcome.settledAt;
  const age = secondsBetween(since, nowIso);
  return {
    tone: "muted",
    label:
      age === undefined
        ? `Already current · ${localClockLabel(outcome.settledAt)}`
        : `Already current · age ${formatAgeSeconds(age)}`,
    detail: `${String(outcome.fetches)} request(s) settled at ${localClockLabel(outcome.settledAt)}; the backend returned the same values. Data last changed at ${localClockLabel(since)}.`,
    busy: false,
  };
}

/** Keys that change on every backend read without the underlying data moving. */
const VOLATILE_KEYS = new Set([
  "observed_at",
  "observedAt",
  "fetched_at",
  "fetchedAt",
  "cache",
  "lastEventAgeS",
]);

/**
 * Content fingerprint used to decide `changed`.
 *
 * Backend responses stamp their own read time (`observed_at`, `fetched_at`) and
 * per-call cache counters; those move every tick even when the market data
 * did not, so they are excluded. Anything else that differs is a real change.
 */
export function contentFingerprint(value: unknown): string | undefined {
  try {
    return (
      JSON.stringify(value, (key, item: unknown) =>
        VOLATILE_KEYS.has(key) ? undefined : typeof item === "bigint" ? item.toString() : item,
      ) ?? "undefined"
    );
  } catch {
    return undefined;
  }
}

/** Unfingerprintable values (circular structures) are reported as changed, never as current. */
export function contentChanged(previous: unknown, next: unknown): boolean {
  const before = contentFingerprint(previous);
  const after = contentFingerprint(next);
  if (before === undefined || after === undefined) {
    return true;
  }
  return before !== after;
}
