import {
  DEFAULT_CAPTURE_FRESH_MAX_S,
  STALE_MTIME_REASON,
  isLastPartFresh,
  lastPartAgeSeconds,
} from "./capture-freshness";
import { formatAgeSeconds } from "./poll-state";
import { isTerrapcActiveRetain } from "./terrapc-defaults";
import { CLOCK_UNKNOWN, localDateTimeLabel } from "./time-display";
import type { VenueCaptureId } from "./paths";
import type {
  CaptureBindingSource,
  CaptureLiveFeed,
  CaptureLiveStatus,
  CaptureRunCandidate,
  VenueCaptureChipStatus,
} from "./types";

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

export const DATA1A_UNKNOWN_PENDING_HEALTH =
  "UNKNOWN (storage activity only; capture-health.json not written yet)";
export const DATA1A_STALE_MTIME_LABEL = "STALE (stale_mtime)";
export const LIVE_STATUS_STALLED_REASON = "live_status_stalled";
export const MARKET_DATA_NOT_FRESH_REASON = "market_data_not_fresh";

export type Data1AHealthView = {
  health?: { status: string } | undefined;
  health_missing: boolean;
  health_error?: string | undefined;
  live?: CaptureLiveStatus | undefined;
  live_error?: string | undefined;
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

function writerBacklog(live: CaptureLiveStatus): string {
  return live.writer_pending_records === null ? "n/a" : String(live.writer_pending_records);
}

function requiredFeedIsFresh(feed: CaptureLiveFeed, observedAt: string | undefined): boolean {
  if (feed.state !== "fresh") {
    return false;
  }
  if (observedAt === undefined || feed.last_market_utc === undefined) {
    return false;
  }
  const ageSeconds = lastPartAgeSeconds(feed.last_market_utc, observedAt);
  return ageSeconds !== undefined && ageSeconds >= 0 && ageSeconds <= feed.silence_bound_seconds;
}

function presentLiveCapture(snapshot: Data1AHealthView): Data1AHealthPresentation | undefined {
  if (snapshot.live_error !== undefined) {
    return {
      statusLabel: "UNREADABLE",
      tileLabel: "UNREADABLE",
      tone: "warn",
      note: snapshot.live_error,
      live: false,
    };
  }
  const live = snapshot.live;
  if (live === undefined) {
    return undefined;
  }
  const backlog = writerBacklog(live);
  const freshMaxSeconds = snapshot.fresh_max_s ?? DEFAULT_CAPTURE_FRESH_MAX_S;
  if (!isLastPartFresh(live.file_mtime_utc, snapshot.observed_at, freshMaxSeconds)) {
    return {
      statusLabel: "STALE (live status publication stalled)",
      tileLabel: "STALE",
      tone: "warn",
      note: `capture-live.json is older than the freshness window. Writer backlog ${backlog}. A stalled status file is not a healthy feed.`,
      live: false,
      reason: LIVE_STATUS_STALLED_REASON,
    };
  }
  const required = live.feeds.filter((feed) => feed.role === "required");
  if (required.length === 0) {
    return {
      statusLabel: DATA1A_UNKNOWN_PENDING_HEALTH,
      tileLabel: "UNKNOWN",
      tone: "warn",
      note: `capture-live.json has no required-feed timestamps. Writer backlog ${backlog}. Status publication is not market-data proof.`,
      live: false,
    };
  }
  if (
    live.definitive_outage !== null ||
    required.some((feed) => feed.state === "definitive_outage")
  ) {
    const errorClass = live.definitive_outage?.error_class ?? "definitive_outage";
    const message = live.definitive_outage?.error_message ?? errorClass;
    return {
      statusLabel: `DEGRADED (${errorClass})`,
      tileLabel: "DEGRADED",
      tone: "down",
      note: `Required feed gave up while the run continued: ${message} Writer backlog ${backlog}.`,
      live: false,
    };
  }
  if (!required.every((feed) => requiredFeedIsFresh(feed, snapshot.observed_at))) {
    if (required.some((feed) => feed.state === "recovering")) {
      return {
        statusLabel: "STALE (reconnecting)",
        tileLabel: "STALE",
        tone: "warn",
        note: `A required feed is reconnecting. Writer backlog ${backlog}. A recent timestamp during recovery is not a healthy feed.`,
        live: false,
        reason: MARKET_DATA_NOT_FRESH_REASON,
      };
    }
    if (required.some((feed) => feed.state === "unknown" || feed.last_market_utc === undefined)) {
      return {
        statusLabel: "UNKNOWN (required feed is not recovered)",
        tileLabel: "UNKNOWN",
        tone: "warn",
        note: `A required feed is missing or still unknown. Writer backlog ${backlog}. A recent timestamp is not proof the feed recovered.`,
        live: false,
      };
    }
    return {
      statusLabel: "STALE (market data not fresh)",
      tileLabel: "STALE",
      tone: "warn",
      note: `capture-live.json was just written, but a required feed timestamp is outside its silence bound. Writer backlog ${backlog}.`,
      live: false,
      reason: MARKET_DATA_NOT_FRESH_REASON,
    };
  }
  return {
    statusLabel: "RUNNING",
    tileLabel: "RUNNING",
    tone: "ok",
    note: `Required feeds are recovered and inside their silence bounds. Writer backlog ${backlog}.`,
    live: true,
  };
}

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
  const livePresentation = presentLiveCapture(snapshot);
  if (livePresentation !== undefined) {
    return livePresentation;
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
        statusLabel: DATA1A_UNKNOWN_PENDING_HEALTH,
        tileLabel: "UNKNOWN",
        tone: "warn",
        note: "Parquet parts are updating, but capture-health.json is missing. Storage activity is not a proven healthy feed.",
        live: false,
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

/**
 * Whole hours stay `72h` so the Phase A window reads as hours.
 * Any other duration uses the shared age formatter (`5s`, `10h 49m`).
 */
export function formatOperatorDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "n/a";
  }
  const whole = Math.floor(seconds);
  if (whole > 0 && whole % 3600 === 0) {
    return `${String(whole / 3600)}h`;
  }
  return formatAgeSeconds(whole);
}

/** Amsterdam date and clock for a part mtime. Fail closed if unparseable. */
export function presentLastPartMtime(mtimeUtc: string | undefined): string {
  if (mtimeUtc === undefined || mtimeUtc === "") {
    return "n/a";
  }
  const label = localDateTimeLabel(mtimeUtc);
  return label === CLOCK_UNKNOWN ? "n/a" : label;
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
    (venueId === "hl" || venueId === "binance" || venueId === "bitvavo" || venueId === "kraken") &&
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
  if (presentation.tileLabel === "UNKNOWN") {
    return "UNKNOWN";
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
      : `${formatOperatorDuration(snapshot.claim.duration_seconds)} claimed`;
  const elapsed = elapsedSecondsSinceRunId(snapshot.runId, snapshot.observed_at);
  if (elapsed === undefined) {
    return claimed ?? "n/a";
  }
  return claimed === undefined
    ? `${formatOperatorDuration(elapsed)} elapsed`
    : `${formatOperatorDuration(elapsed)} elapsed / ${claimed}`;
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
