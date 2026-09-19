import {
  DEFAULT_CAPTURE_FRESH_MAX_S,
  STALE_MTIME_REASON,
  isLastPartFresh,
} from "./capture-freshness";
import { isTerrapcActiveRetain } from "./terrapc-defaults";
import type { VenueCaptureId } from "./paths";
import type { CaptureBindingSource, CaptureRunCandidate, VenueCaptureChipStatus } from "./types";

export type SignedTone = "up" | "down" | "flat" | "unknown";
export type StatusTone = "ok" | "warn" | "down" | "neutral";

const NUMERIC = /^[+-]?\d+(?:\.\d+)?$/;

export function signedTone(raw: string): SignedTone {
  const trimmed = raw.trim();
  if (!NUMERIC.test(trimmed)) {
    return "unknown";
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    return "unknown";
  }
  if (value > 0) {
    return "up";
  }
  if (value < 0) {
    return "down";
  }
  return "flat";
}

/** Operator scan: 4 dp when |USDC| < 1, else 2 dp. Unparseable stays copied. */
export function formatAssumedUsdcDisplay(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === "" || !NUMERIC.test(trimmed)) {
    return raw;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    return raw;
  }
  return value.toFixed(Math.abs(value) < 1 ? 4 : 2);
}

export function formatGroupedNumber(raw: string): string {
  const trimmed = raw.trim();
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(trimmed);
  if (match === null) {
    return raw;
  }
  const sign = match[1] ?? "";
  const whole = match[2] ?? trimmed;
  const fraction = match[3];
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? `${sign}${grouped}` : `${sign}${grouped}.${fraction}`;
}

export function yesNo(value: boolean): "yes" | "no" {
  return value ? "yes" : "no";
}

export function healthTone(status: string): StatusTone {
  if (status === "COMPLETED_FLAT" || status === "COMPLETED") {
    return "ok";
  }
  if (status === "BOUNDED_TIMEOUT" || status === "OPERATOR_STOP") {
    return "warn";
  }
  if (status === "RISK_REJECTED" || status === "FAILED") {
    return "down";
  }
  return "neutral";
}

export const DATA1A_RUNNING_PENDING_HEALTH = "RUNNING (health JSON pending until stop)";
export const DATA1A_STALE_MTIME_LABEL = "STALE (stale_mtime)";

export type Data1AHealthView = {
  health?: { status: string } | undefined;
  health_missing: boolean;
  health_error?: string | undefined;
  observed_at?: string;
  fresh_max_s?: number;
  parts: {
    raw_dir_present: boolean;
    count?: number | undefined;
    last_part_mtime_utc?: string | undefined;
  };
};

export type Data1AHealthPresentation = {
  statusLabel: string;
  tileLabel: string;
  tone: StatusTone;
  note: string;
  live: boolean;
  reason?: string;
};

export function data1aCaptureHealthPresentation(
  snapshot: Data1AHealthView,
): Data1AHealthPresentation {
  if (snapshot.health !== undefined) {
    return {
      statusLabel: snapshot.health.status,
      tileLabel: snapshot.health.status,
      tone: healthTone(snapshot.health.status),
      note: "Copied from capture-health.json",
      live: false,
    };
  }
  if (!snapshot.health_missing) {
    return {
      statusLabel: "UNREADABLE",
      tileLabel: "UNREADABLE",
      tone: "warn",
      note: snapshot.health_error ?? "capture-health.json is unreadable.",
      live: false,
    };
  }
  const partCount = snapshot.parts.count ?? 0;
  const partsLookLive =
    snapshot.parts.raw_dir_present &&
    partCount > 0 &&
    snapshot.parts.last_part_mtime_utc !== undefined;
  if (partsLookLive) {
    const freshMaxSeconds = snapshot.fresh_max_s ?? DEFAULT_CAPTURE_FRESH_MAX_S;
    if (
      isLastPartFresh(snapshot.parts.last_part_mtime_utc, snapshot.observed_at, freshMaxSeconds)
    ) {
      return {
        statusLabel: DATA1A_RUNNING_PENDING_HEALTH,
        tileLabel: "RUNNING",
        tone: "ok",
        note: "capture-health.json is written at stop; growing part count and last mtime are the live signal.",
        live: true,
      };
    }
    return {
      statusLabel: DATA1A_STALE_MTIME_LABEL,
      tileLabel: "STALE",
      tone: "warn",
      note: `last part mtime older than COCKPIT_CAPTURE_FRESH_MAX_S=${String(freshMaxSeconds)}s; not RUNNING`,
      live: false,
      reason: STALE_MTIME_REASON,
    };
  }
  return {
    statusLabel: "NOT WRITTEN",
    tileLabel: "NOT WRITTEN",
    tone: "warn",
    note: snapshot.health_error ?? "capture-health.json is not written yet",
    live: false,
  };
}

export function presentCopiedNumber(value: number | undefined): string {
  return value === undefined ? "n/a" : String(value);
}

export function presentCopiedText(value: string | undefined): string {
  return value === undefined || value === "" ? "n/a" : value;
}

/** Gaps/reconnects only when reconstructable health JSON already has them. */
export function presentGapReconnect(
  gaps: number | undefined,
  reconnects: number | undefined,
): string {
  if (gaps === undefined && reconnects === undefined) {
    return "n/a";
  }
  return `${presentCopiedNumber(gaps)}/${presentCopiedNumber(reconnects)}`;
}

/** Keep reconnect attempts distinct from optional 5-second wall-clock clusters. */
export function presentGapReconnectClusters(
  gaps: number | undefined,
  reconnects: number | undefined,
  reconnectClusters: number | undefined,
): string {
  if (gaps === undefined && reconnects === undefined && reconnectClusters === undefined) {
    return "n/a";
  }
  return `${presentCopiedNumber(gaps)} gaps · ${presentCopiedNumber(reconnects)} raw / ${presentCopiedNumber(reconnectClusters)} clusters`;
}

/** Compact UTC mtime for the strip. Fail closed if unparseable. */
export function presentLastPartMtime(mtimeUtc: string | undefined): string {
  if (mtimeUtc === undefined || mtimeUtc === "") {
    return "n/a";
  }
  if (!Number.isFinite(Date.parse(mtimeUtc))) {
    return "n/a";
  }
  return mtimeUtc.replace("T", " ").replace(/\.000Z$/, "Z");
}

export function captureRunOptionLabel(
  venueId: VenueCaptureId,
  candidate: CaptureRunCandidate,
): string {
  const tags: string[] = [];
  if (candidate.live) {
    tags.push("live");
  } else if (candidate.has_health) {
    tags.push("stopped");
  }
  if (
    (venueId === "hl" ||
      venueId === "binance" ||
      venueId === "bitvavo" ||
      venueId === "kraken") &&
    isTerrapcActiveRetain(venueId, candidate.run_id)
  ) {
    tags.push("TerraPC");
  }
  return tags.length === 0 ? candidate.run_id : `${candidate.run_id} · ${tags.join(" · ")}`;
}

const RUN_ID_UTC_PREFIX = /^(\d{4})(\d{2})(\d{2})t(\d{2})(\d{2})(\d{2})z/;

/** Reconstruct UTC start from a `YYYYMMDDtHHMMSSz…` run_id. Fail closed if unparseable. */
export function parseRunIdStartedAt(runId: string): string | undefined {
  const match = RUN_ID_UTC_PREFIX.exec(runId);
  if (match === null) {
    return undefined;
  }
  const started = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}Z`;
  return Number.isFinite(Date.parse(started)) ? started : undefined;
}

/** Elapsed seconds from a `YYYYMMDDtHHMMSSz…` run_id to `observed_at`. Fail closed if unparseable. */
export function elapsedSecondsSinceRunId(runId: string, observedAt: string): number | undefined {
  const match = RUN_ID_UTC_PREFIX.exec(runId);
  if (match === null) {
    return undefined;
  }
  const startedMs = Date.parse(
    `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}Z`,
  );
  const observedMs = Date.parse(observedAt);
  if (!Number.isFinite(startedMs) || !Number.isFinite(observedMs) || observedMs < startedMs) {
    return undefined;
  }
  return Math.floor((observedMs - startedMs) / 1000);
}

export function presentLastPartAge(
  lastPartMtimeUtc: string | undefined,
  observedAt: string,
): string {
  if (lastPartMtimeUtc === undefined || lastPartMtimeUtc === "") {
    return "n/a";
  }
  const lastMs = Date.parse(lastPartMtimeUtc);
  const observedMs = Date.parse(observedAt);
  if (!Number.isFinite(lastMs) || !Number.isFinite(observedMs) || observedMs < lastMs) {
    return "n/a";
  }
  const seconds = Math.floor((observedMs - lastMs) / 1000);
  if (seconds < 60) {
    return `${String(seconds)}s`;
  }
  if (seconds < 3600) {
    return `${String(Math.floor(seconds / 60))}m`;
  }
  if (seconds < 86400) {
    return `${String(Math.floor(seconds / 3600))}h`;
  }
  return `${String(Math.floor(seconds / 86400))}d`;
}

export function venueCaptureChipStatus(
  presentation: Data1AHealthPresentation,
): Exclude<VenueCaptureChipStatus, "MISSING"> {
  if (presentation.live) {
    return "RUNNING";
  }
  if (presentation.reason === STALE_MTIME_REASON || presentation.tileLabel === "STALE") {
    return "STALE";
  }
  if (
    presentation.tileLabel === "UNREADABLE" ||
    presentation.tileLabel === "NOT WRITTEN" ||
    presentation.tone === "down"
  ) {
    return "DEGRADED";
  }
  if (presentation.tone === "ok" || presentation.tone === "warn") {
    return "STOPPED";
  }
  return "DEGRADED";
}

export function presentData1ADuration(snapshot: {
  runId: string;
  observed_at: string;
  claim: { duration_seconds?: number | undefined };
}): string {
  const claimed =
    snapshot.claim.duration_seconds === undefined
      ? undefined
      : `${String(snapshot.claim.duration_seconds)}s claimed`;
  const elapsed = elapsedSecondsSinceRunId(snapshot.runId, snapshot.observed_at);
  if (elapsed === undefined) {
    return claimed ?? "n/a";
  }
  return claimed === undefined
    ? `${String(elapsed)}s elapsed`
    : `${String(elapsed)}s elapsed / ${claimed}`;
}

export function uniqueStrings(items: string[]): string[] {
  return [...new Set(items)];
}

export function captureBindingSourceLabel(source: CaptureBindingSource): string {
  switch (source) {
    case "query":
      return "query";
    case "env":
      return "env";
    case "explicit-dir":
      return "explicit-dir";
    case "auto-detect":
      return "auto-detect";
    case "default-fixture":
      return "default-fixture";
    case "unbound":
      return "unbound";
    default: {
      const exhaustive: never = source;
      throw new Error(`Unhandled capture binding source: ${String(exhaustive)}`);
    }
  }
}

export function captureRunSourceLabel(
  source: "data1a-run-dir" | "venue-run-dir" | "path-contract" | "auto-detect" | "default-fixture",
): string {
  switch (source) {
    case "data1a-run-dir":
      return "data1a-run-dir";
    case "venue-run-dir":
      return "venue-run-dir";
    case "path-contract":
      return "path-contract";
    case "auto-detect":
      return "auto-detect";
    case "default-fixture":
      return "default-fixture";
    default: {
      const exhaustive: never = source;
      throw new Error(`Unhandled capture run source: ${String(exhaustive)}`);
    }
  }
}

export function paperRunSourceLabel(
  source: "paper-run-dir" | "path-contract" | "default-fixture",
): string {
  switch (source) {
    case "paper-run-dir":
      return "paper-run-dir";
    case "path-contract":
      return "path-contract";
    case "default-fixture":
      return "default-fixture";
    default: {
      const exhaustive: never = source;
      throw new Error(`Unhandled PAPER run source: ${String(exhaustive)}`);
    }
  }
}
