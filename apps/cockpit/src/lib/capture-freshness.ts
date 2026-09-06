export const CAPTURE_FRESH_MAX_ENV = "COCKPIT_CAPTURE_FRESH_MAX_S";
/** Default wall-clock freshness window. Tunable via COCKPIT_CAPTURE_FRESH_MAX_S. */
export const DEFAULT_CAPTURE_FRESH_MAX_S = 180;
export const STALE_MTIME_REASON = "stale_mtime";

/**
 * Age of the newest published part versus `observedAt`, in seconds.
 * Negative ages mean last_part_mtime is ahead of the cockpit clock.
 * Host clock must be sane: this is a wall-clock compare, not venue time.
 */
export function lastPartAgeSeconds(
  lastPartMtimeUtc: string | undefined,
  observedAt: string,
): number | undefined {
  if (lastPartMtimeUtc === undefined || lastPartMtimeUtc === "") {
    return undefined;
  }
  const lastMs = Date.parse(lastPartMtimeUtc);
  const observedMs = Date.parse(observedAt);
  if (!Number.isFinite(lastMs) || !Number.isFinite(observedMs)) {
    return undefined;
  }
  return (observedMs - lastMs) / 1000;
}

export function isLastPartFresh(
  lastPartMtimeUtc: string | undefined,
  observedAt: string | undefined,
  freshMaxSeconds: number,
): boolean {
  if (observedAt === undefined || observedAt === "") {
    return false;
  }
  const ageSeconds = lastPartAgeSeconds(lastPartMtimeUtc, observedAt);
  return ageSeconds !== undefined && ageSeconds <= freshMaxSeconds;
}

export function resolveCaptureFreshMaxSeconds(env: NodeJS.Dict<string> = {}): number {
  const raw = env[CAPTURE_FRESH_MAX_ENV]?.trim();
  if (raw === undefined || raw === "") {
    return DEFAULT_CAPTURE_FRESH_MAX_S;
  }
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(
      `${CAPTURE_FRESH_MAX_ENV} must be a positive integer of seconds (default ${String(DEFAULT_CAPTURE_FRESH_MAX_S)}).`,
    );
  }
  return parsed;
}
