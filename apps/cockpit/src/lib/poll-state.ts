/**
 * Client-side state of one polled resource.
 *
 * A ticking refresh clock is not evidence that anything was fetched. This
 * record separates three things the UI must never conflate:
 *
 * - `lastAttemptAt` — the last time a request was made (the clock ticked);
 * - `lastSuccessAt` — the last time a request actually returned usable data;
 * - the source's own timestamp, which lives inside `data` and is reported by
 *   the backend (e.g. `observed_at`, last Parquet part mtime, last event time).
 *
 * On failure the previous `data` is kept so the operator still sees the last
 * known values, but `error` and `degraded` mark them as not-current.
 */
export type PollState<T> = {
  data: T;
  /** ISO time of the last successful fetch; undefined while only SSR data is shown. */
  lastSuccessAt: string | undefined;
  /** ISO time of the last attempt, successful or not. */
  lastAttemptAt: string | undefined;
  /** Error of the most recent attempt; undefined when it succeeded. */
  error: string | undefined;
  /** Consecutive failed attempts since the last success. */
  failures: number;
  /** True when `data` is from an earlier read and the latest attempt failed. */
  degraded: boolean;
  /** Where `data` came from: the server render or a client fetch. */
  origin: "server" | "client";
};

export function initialPollState<T>(data: T): PollState<T> {
  return {
    data,
    lastSuccessAt: undefined,
    lastAttemptAt: undefined,
    error: undefined,
    failures: 0,
    degraded: false,
    origin: "server",
  };
}

export function pollSucceeded<T>(previous: PollState<T>, data: T, at: string): PollState<T> {
  return {
    data,
    lastSuccessAt: at,
    lastAttemptAt: at,
    error: undefined,
    failures: 0,
    degraded: false,
    origin: "client",
  };
}

export function pollFailed<T>(previous: PollState<T>, error: unknown, at: string): PollState<T> {
  return {
    ...previous,
    lastAttemptAt: at,
    error: error instanceof Error ? error.message : String(error),
    failures: previous.failures + 1,
    degraded: true,
  };
}

/** Compact clock label (HH:MM:SSZ) for a read timestamp; "—" when unknown. */
export function clockLabel(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) {
    return "—";
  }
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) {
    return "—";
  }
  return `${new Date(parsed).toISOString().slice(11, 19)}Z`;
}

/** Whole seconds between two ISO times, or undefined when either is missing/invalid. */
export function secondsBetween(
  earlierIso: string | undefined,
  laterIso: string | undefined,
): number | undefined {
  if (earlierIso === undefined || laterIso === undefined) {
    return undefined;
  }
  const earlier = Date.parse(earlierIso);
  const later = Date.parse(laterIso);
  if (!Number.isFinite(earlier) || !Number.isFinite(later)) {
    return undefined;
  }
  return Math.max(0, Math.round((later - earlier) / 1000));
}

/** Human age such as "12s", "3m 05s", "2h 10m", "1d 4h". */
export function formatAgeSeconds(seconds: number | undefined): string {
  if (seconds === undefined) {
    return "n/a";
  }
  if (seconds < 60) {
    return `${String(seconds)}s`;
  }
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return `${String(minutes)}m ${rest.toString().padStart(2, "0")}s`;
  }
  if (seconds < 86_400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${String(hours)}h ${minutes.toString().padStart(2, "0")}m`;
  }
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3600);
  return `${String(days)}d ${String(hours)}h`;
}

/**
 * One-line read status for a polled panel.
 *
 * Distinguishes "never fetched on the client yet" from "fetched OK" from
 * "last attempt failed, showing values from <time>".
 */
export function describePollRead(state: PollState<unknown>): {
  tone: "ok" | "down" | "muted";
  label: string;
  detail: string;
} {
  if (state.error !== undefined) {
    const shown =
      state.lastSuccessAt === undefined
        ? state.origin === "server"
          ? "values from the server render"
          : "no successful read yet"
        : `values from ${clockLabel(state.lastSuccessAt)}`;
    return {
      tone: "down",
      label: `read failed ${clockLabel(state.lastAttemptAt)}`,
      detail: `${state.error} — ${shown}. ${String(state.failures)} consecutive failure(s).`,
    };
  }
  if (state.lastSuccessAt === undefined) {
    return {
      tone: "muted",
      label: "server render",
      detail: "Values come from the initial server render; no client read has completed yet.",
    };
  }
  return {
    tone: "ok",
    label: `read ${clockLabel(state.lastSuccessAt)}`,
    detail: "Last successful read from the backend (browser clock).",
  };
}
