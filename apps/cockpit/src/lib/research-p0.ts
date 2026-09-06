import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { bindVenueCaptureRun } from "./capture-runs";
import { loadCaptureSnapshotForContract } from "./data1a-capture";
import { presentCopiedText, presentGapReconnect } from "./display";
import { captureArtifactRoot, VENUE_CAPTURE_CONTRACTS, type VenueCaptureQuery } from "./paths";
import {
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
} from "./terrapc-defaults";
import {
  BINANCE_IDENTITY_WARNING,
  BINANCE_IMPULSE_DEFAULT,
  RESEARCH_RUN_BINDING_NOTE,
  RESEARCH_UNAVAILABLE,
  type ResearchHealthRow,
  type ResearchIdentityRow,
  type ResearchOverlapClock,
  type ResearchP0View,
  type ResearchRegistryRow,
  type ResearchRunBinding,
  type ResearchSufficiency,
} from "./research-p0-view";
import type {
  Data1ACaptureSnapshot,
  TransportProfileRow,
  VenueCaptureChip,
  VenueCaptureStrip,
  VenueCaptureStripResponse,
} from "./types";

export {
  BINANCE_IDENTITY_WARNING,
  BINANCE_IMPULSE_DEFAULT,
  H1_LEADLAG_NOTE,
  OVERLAP_72H_SECONDS,
  PANEL_VERSION_EXPECTED,
  PUBLIC_MID_NOT_RESEARCH,
  RESEARCH_NEGATIVE_VERDICTS,
  RESEARCH_POSITIVE_VERDICTS,
  RESEARCH_P0_SOURCE,
  RESEARCH_RUN_BINDING_NOTE,
  RESEARCH_UNAVAILABLE,
  RESEARCH_ZONE_KICKER,
  researchVerdictTone,
} from "./research-p0-view";
export type {
  ResearchHealthRow,
  ResearchIdentityRow,
  ResearchOverlapClock,
  ResearchP0View,
  ResearchRegistryRow,
  ResearchRunBinding,
  ResearchSufficiency,
  ResearchVerdictTone,
} from "./research-p0-view";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatProfiles(profiles: TransportProfileRow[] | undefined): string {
  if (profiles === undefined || profiles.length === 0) {
    return RESEARCH_UNAVAILABLE;
  }
  return profiles
    .map((profile) => {
      const gaps = profile.gaps === undefined ? "n/a" : String(profile.gaps);
      const reconnects = profile.reconnects === undefined ? "n/a" : String(profile.reconnects);
      return `${profile.transport_profile}:${gaps}/${reconnects}`;
    })
    .join(" · ");
}

function wallSpan(snapshot: Data1ACaptureSnapshot | undefined): string {
  if (snapshot === undefined) {
    return RESEARCH_UNAVAILABLE;
  }
  if (snapshot.claim.duration_seconds !== undefined) {
    return `${String(snapshot.claim.duration_seconds)}s claim`;
  }
  return presentCopiedText(snapshot.claim.state);
}

function loadBoundSnapshot(
  chip: VenueCaptureChip,
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: VenueCaptureQuery,
): Data1ACaptureSnapshot | undefined {
  if (chip.run_id === undefined || chip.run_id === "" || chip.status === "MISSING") {
    return undefined;
  }
  try {
    const resolved = bindVenueCaptureRun(VENUE_CAPTURE_CONTRACTS[chip.id], env, repoRoot, query, {
      now: chip.observed_at,
      freshMaxSeconds: 180,
    });
    return loadCaptureSnapshotForContract(
      resolved,
      VENUE_CAPTURE_CONTRACTS[chip.id],
      () => chip.observed_at,
    );
  } catch {
    return undefined;
  }
}

export function buildResearchIdentity(): ResearchIdentityRow[] {
  return [
    { id: "hl", venue: "HL", product: "BTC-PERP", impulse: "hyperliquid mid / trades / bbo" },
    {
      id: "binance",
      venue: "BN",
      product: "BTCUSDT",
      impulse: `${BINANCE_IMPULSE_DEFAULT} default · usdm_agg · spot (explicit switch)`,
    },
    { id: "bitvavo", venue: "BV", product: "BTC-EUR", impulse: "spot book (+trades)" },
    { id: "kraken", venue: "KR", product: "BTC-USD", impulse: "L2 / L3 / trades separate" },
  ];
}

export function buildOverlapClock(strip: VenueCaptureStrip | undefined): ResearchOverlapClock {
  const rows = strip?.provenance.rows ?? [];
  const hl = rows.find((row) => row.id === "hl")?.run_id;
  const bn = rows.find((row) => row.id === "binance")?.run_id;
  const bv = rows.find((row) => row.id === "bitvavo")?.run_id;
  const kr = rows.find((row) => row.id === "kraken")?.run_id;
  const shared = [hl, bv, kr].filter((id) => id !== undefined);
  const hlBvKr =
    shared.length === 0
      ? RESEARCH_UNAVAILABLE
      : shared.every((id) => id === shared[0])
        ? (shared[0] ?? RESEARCH_UNAVAILABLE)
        : shared.join(" · ");
  const overlapStart = strip?.provenance.overlap_starts_utc ?? RESEARCH_UNAVAILABLE;
  const note = strip?.provenance.overlap_note ?? RESEARCH_UNAVAILABLE;
  let badge: ResearchOverlapClock["badge"] = "gate_pending";
  if (bn === TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID && hl === TERRAPC_SHARED_RETAIN_RUN_ID) {
    badge = "partial";
  }
  if (overlapStart !== RESEARCH_UNAVAILABLE && bn !== undefined && hl !== undefined && bn !== hl) {
    badge = "partial";
  }
  if (strip === undefined) {
    badge = "gate_pending";
  }
  return {
    hlBvKrRun: hlBvKr,
    bnRun: presentCopiedText(bn),
    overlapStart,
    note,
    elapsed: RESEARCH_UNAVAILABLE,
    badge,
  };
}

const RUN_ID_FIELDS = [
  "run_id",
  "hl_run_id",
  "bn_run_id",
  "bv_run_id",
  "kr_run_id",
  "binance_run_id",
  "hyperliquid_run_id",
] as const;

/**
 * Copy every run_id the summary declares.
 *
 * Accepts both scalar fields and a `run_ids` list/object so a summary produced
 * by a multi-venue panel can still be attributed. Nothing is inferred from the
 * file path: an anonymous summary stays anonymous.
 */
export function panelSummaryRunIds(payload: unknown): string[] {
  if (!isRecord(payload)) {
    return [];
  }
  const found: string[] = [];
  for (const field of RUN_ID_FIELDS) {
    const value = payload[field];
    if (typeof value === "string" && value.trim() !== "") {
      found.push(value.trim());
    }
  }
  const list = payload.run_ids;
  if (Array.isArray(list)) {
    for (const item of list) {
      if (typeof item === "string" && item.trim() !== "") {
        found.push(item.trim());
      }
    }
  } else if (isRecord(list)) {
    for (const item of Object.values(list)) {
      if (typeof item === "string" && item.trim() !== "") {
        found.push(item.trim());
      }
    }
  }
  return [...new Set(found)];
}

export function classifyRunBinding(
  summaryRunIds: readonly string[],
  boundRunIds: readonly string[],
): ResearchRunBinding {
  if (summaryRunIds.length === 0) {
    return "unknown";
  }
  if (boundRunIds.length === 0) {
    return "unknown";
  }
  return summaryRunIds.some((id) => boundRunIds.includes(id)) ? "matched" : "mismatched";
}

export function parsePanelSummary(
  payload: unknown,
  source: string,
  boundRunIds: readonly string[] = [],
): ResearchSufficiency {
  if (!isRecord(payload)) {
    return unavailableSufficiency("panel-summary.json is not an object");
  }
  if (payload.trading_mode !== undefined && payload.trading_mode !== "PAPER") {
    throw new Error("research-out panel-summary.json is not PAPER and the cockpit fails closed.");
  }
  const rendered = JSON.stringify(payload).toLowerCase();
  if (rendered.includes('"edge"') || rendered.includes("promotion_decision")) {
    throw new Error("research-out summary contains forbidden edge/promotion tokens.");
  }
  const sufficiency = isRecord(payload.sufficiency) ? payload.sufficiency : undefined;
  const reasons = Array.isArray(sufficiency?.reasons)
    ? sufficiency.reasons.filter((item): item is string => typeof item === "string")
    : [];
  const runIds = panelSummaryRunIds(payload);
  return {
    runIds,
    runBinding: classifyRunBinding(runIds, boundRunIds),
    verdict: typeof payload.verdict === "string" ? payload.verdict : RESEARCH_UNAVAILABLE,
    reasons,
    hlGapFraction:
      typeof payload.hl_gap_fraction === "number"
        ? String(payload.hl_gap_fraction)
        : RESEARCH_UNAVAILABLE,
    bnGapFraction:
      typeof payload.bn_gap_fraction === "number"
        ? String(payload.bn_gap_fraction)
        : RESEARCH_UNAVAILABLE,
    overlapBuckets:
      typeof payload.overlap_bucket_count === "number"
        ? String(payload.overlap_bucket_count)
        : RESEARCH_UNAVAILABLE,
    panelVersion:
      typeof payload.panel_version === "string" ? payload.panel_version : RESEARCH_UNAVAILABLE,
    source,
  };
}

export function unavailableSufficiency(reason: string): ResearchSufficiency {
  return {
    verdict: RESEARCH_UNAVAILABLE,
    reasons: [reason],
    hlGapFraction: RESEARCH_UNAVAILABLE,
    bnGapFraction: RESEARCH_UNAVAILABLE,
    overlapBuckets: RESEARCH_UNAVAILABLE,
    panelVersion: RESEARCH_UNAVAILABLE,
    source: reason,
    runIds: [],
    runBinding: "unavailable",
  };
}

export function listResearchOutSummaries(root: string, maxFiles = 16): string[] {
  const found: string[] = [];
  const walk = (dir: string, depth: number): void => {
    if (found.length >= maxFiles || depth > 4) {
      return;
    }
    let entries: string[] = [];
    try {
      entries = readdirSync(dir);
    } catch {
      return;
    }
    for (const name of entries) {
      const path = join(dir, name);
      let stat;
      try {
        stat = statSync(path);
      } catch {
        continue;
      }
      if (stat.isDirectory()) {
        walk(path, depth + 1);
      } else if (name === "panel-summary.json") {
        found.push(path);
      }
    }
  };
  walk(root, 0);
  return found;
}

/**
 * Pick the panel summary that belongs to the bound capture runs.
 *
 * Reading the first file on disk silently attributes an unrelated verdict to
 * whatever the operator happens to have selected. Instead every summary is
 * parsed, the ones whose run_id intersects the selection win, and if none
 * match the newest readable summary is returned already flagged as
 * `mismatched` / `unknown` so the UI can refuse to present it as a verdict
 * about the current runs.
 */
export function loadPanelSummaryFromRoot(
  root: string | undefined,
  boundRunIds: readonly string[] = [],
): ResearchSufficiency {
  if (root === undefined || root === "" || !existsSync(root)) {
    return unavailableSufficiency(
      "research-out/panel-summary.json is not pointed; sufficiency stays UNAVAILABLE",
    );
  }
  const files = listResearchOutSummaries(root);
  if (files.length === 0) {
    return unavailableSufficiency(
      "No panel-summary.json under research-out; WP-Q1 gates stay UNAVAILABLE",
    );
  }
  const parsed: ResearchSufficiency[] = [];
  let unreadable = 0;
  for (const file of files) {
    try {
      const payload: unknown = JSON.parse(readFileSync(file, "utf8"));
      parsed.push(parsePanelSummary(payload, relative(root, file), boundRunIds));
    } catch (error: unknown) {
      if (error instanceof Error && error.message.includes("fails closed")) {
        throw error;
      }
      unreadable += 1;
    }
  }
  const matched = parsed.find((summary) => summary.runBinding === "matched");
  if (matched !== undefined) {
    return matched;
  }
  const fallback = parsed[0];
  if (fallback === undefined) {
    return unavailableSufficiency(
      `panel-summary.json is unreadable (${String(unreadable)} file(s)); values are not invented`,
    );
  }
  return {
    ...fallback,
    reasons: [
      RESEARCH_RUN_BINDING_NOTE[fallback.runBinding],
      ...fallback.reasons.filter((reason) => reason !== ""),
    ],
  };
}

export function researchOutRoot(env: NodeJS.Dict<string>): string | undefined {
  const explicit = env.COCKPIT_RESEARCH_OUT?.trim();
  if (explicit) {
    return resolve(explicit);
  }
  const artifactRoot = captureArtifactRoot(env);
  if (artifactRoot === undefined) {
    return undefined;
  }
  return join(resolve(artifactRoot), "research-out");
}

export function buildResearchP0View(
  strip: VenueCaptureStripResponse,
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: VenueCaptureQuery,
  nowIso: string = new Date().toISOString(),
): ResearchP0View {
  const venues = strip.ok ? strip.strip.venues : [];
  const snapshots = new Map(
    venues.map((venue) => [venue.id, loadBoundSnapshot(venue, env, repoRoot, query)] as const),
  );
  const registry = venues.map((venue) => {
    const snapshot = snapshots.get(venue.id);
    return {
      id: venue.id,
      venue: venue.chip,
      product: venue.product,
      runId: presentCopiedText(venue.run_id),
      retained:
        snapshot === undefined ? RESEARCH_UNAVAILABLE : snapshot.claim.retained ? "yes" : "no",
      state: snapshot?.claim.state ?? RESEARCH_UNAVAILABLE,
      pathContract: venue.path_contract,
      wallSpan: wallSpan(snapshot),
      status: venue.status,
    };
  });
  const health = venues.map((venue) => {
    const snapshot = snapshots.get(venue.id);
    return {
      id: venue.id,
      venue: venue.chip,
      gapsReconnects: presentGapReconnect(snapshot?.health?.gaps, snapshot?.health?.reconnects),
      transportProfiles: formatProfiles(snapshot?.health?.transport_profiles),
      partCount:
        snapshot?.parts.count === undefined ? RESEARCH_UNAVAILABLE : String(snapshot.parts.count),
      bytes:
        snapshot?.parts.bytes === undefined ? RESEARCH_UNAVAILABLE : String(snapshot.parts.bytes),
      lastPartMtime: snapshot?.parts.last_part_mtime_utc ?? RESEARCH_UNAVAILABLE,
      healthPending: snapshot?.health_missing === true,
    };
  });
  const boundRunIds = [
    ...new Set(
      venues
        .map((venue) => venue.run_id)
        .filter((runId): runId is string => runId !== undefined && runId !== ""),
    ),
  ];
  return {
    registry,
    health,
    identity: buildResearchIdentity(),
    identityWarning: BINANCE_IDENTITY_WARNING,
    overlap: buildOverlapClock(strip.ok ? strip.strip : undefined),
    sufficiency: loadPanelSummaryFromRoot(researchOutRoot(env), boundRunIds),
    boundRunIds,
    // elapsed vs 72h stays UNAVAILABLE; observed_at only timestamps the read.
    observedAt: strip.ok ? strip.strip.observed_at : nowIso,
    error: strip.ok ? undefined : strip.error,
  };
}
