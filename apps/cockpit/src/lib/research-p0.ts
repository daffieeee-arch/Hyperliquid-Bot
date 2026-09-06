import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { bindVenueCaptureRun } from "./capture-runs";
import { loadCaptureSnapshotForContract } from "./data1a-capture";
import { presentCopiedText, presentGapReconnect } from "./display";
import { captureArtifactRoot, VENUE_CAPTURE_CONTRACTS, type VenueCaptureQuery } from "./paths";
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
  type ResearchVenueRun,
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
  ResearchVenueRun,
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
  // Derived only from the bound runs; no historical run_id is special-cased.
  let badge: ResearchOverlapClock["badge"] = "gate_pending";
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

type SummaryVenue = ResearchVenueRun["venue"];

/** Summary field → venue. `run_id` alone does not say which venue it describes. */
const RUN_ID_FIELDS: Record<string, SummaryVenue> = {
  run_id: "any",
  hl_run_id: "hl",
  hyperliquid_run_id: "hl",
  bn_run_id: "binance",
  binance_run_id: "binance",
  bv_run_id: "bitvavo",
  bitvavo_run_id: "bitvavo",
  kr_run_id: "kraken",
  kraken_run_id: "kraken",
};

/** Keys accepted inside a `run_ids` / `products` object. */
const VENUE_KEYS: Record<string, SummaryVenue> = {
  hl: "hl",
  hyperliquid: "hl",
  bn: "binance",
  binance: "binance",
  bv: "bitvavo",
  bitvavo: "bitvavo",
  kr: "kraken",
  kraken: "kraken",
};

const PRODUCT_FIELDS: Record<string, SummaryVenue> = {
  hl_product: "hl",
  hyperliquid_product: "hl",
  bn_product: "binance",
  binance_product: "binance",
  bv_product: "bitvavo",
  bitvavo_product: "bitvavo",
  kr_product: "kraken",
  kraken_product: "kraken",
};

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function declaredProducts(payload: Record<string, unknown>): Map<SummaryVenue, string> {
  const products = new Map<SummaryVenue, string>();
  for (const [field, venue] of Object.entries(PRODUCT_FIELDS)) {
    const value = nonEmptyString(payload[field]);
    if (value !== undefined) {
      products.set(venue, value);
    }
  }
  if (isRecord(payload.products)) {
    for (const [key, value] of Object.entries(payload.products)) {
      const venue = VENUE_KEYS[key.toLowerCase()];
      const product = nonEmptyString(value);
      if (venue !== undefined && product !== undefined) {
        products.set(venue, product);
      }
    }
  }
  return products;
}

/**
 * Copy every run the summary declares, keeping the venue it was declared for.
 *
 * Accepts the `hl_run_id` / `bn_run_id` scalars the WP-Q1 panel writes, the
 * other venue-prefixed scalars, and a `run_ids` list/object. Nothing is
 * inferred from the file path: an anonymous summary stays anonymous.
 */
export function panelSummaryRunRefs(payload: unknown): ResearchVenueRun[] {
  if (!isRecord(payload)) {
    return [];
  }
  const products = declaredProducts(payload);
  const found: ResearchVenueRun[] = [];
  const push = (venue: SummaryVenue, runId: string | undefined): void => {
    if (runId === undefined) {
      return;
    }
    if (found.some((ref) => ref.venue === venue && ref.runId === runId)) {
      return;
    }
    const product = products.get(venue);
    found.push(product === undefined ? { venue, runId } : { venue, runId, product });
  };
  for (const [field, venue] of Object.entries(RUN_ID_FIELDS)) {
    push(venue, nonEmptyString(payload[field]));
  }
  const list = payload.run_ids;
  if (Array.isArray(list)) {
    for (const item of list) {
      push("any", nonEmptyString(item));
    }
  } else if (isRecord(list)) {
    for (const [key, item] of Object.entries(list)) {
      push(VENUE_KEYS[key.toLowerCase()] ?? "any", nonEmptyString(item));
    }
  }
  return found;
}

export function panelSummaryRunIds(payload: unknown): string[] {
  return [...new Set(panelSummaryRunRefs(payload).map((ref) => ref.runId))];
}

function runRefMatches(declared: ResearchVenueRun, bound: ResearchVenueRun): boolean {
  if (declared.runId !== bound.runId) {
    return false;
  }
  if (declared.venue !== "any" && declared.venue !== bound.venue) {
    return false;
  }
  return declared.product === undefined || declared.product === bound.product;
}

/**
 * Compare a summary with the full bound combination, venue by venue.
 *
 * A venue-tagged run is only checked against the run bound for that same
 * venue, so `bn_run_id` can never be satisfied by a Hyperliquid run that
 * happens to share the id. One matching venue is not enough: a summary about
 * the current HL run and a restarted (older) BN run is `mismatched`.
 */
export function classifyRunBinding(
  summaryRuns: readonly ResearchVenueRun[],
  boundRuns: readonly ResearchVenueRun[],
): ResearchRunBinding {
  if (summaryRuns.length === 0 || boundRuns.length === 0) {
    return "unknown";
  }
  let unbound = 0;
  for (const declared of summaryRuns) {
    if (declared.venue === "any") {
      if (!boundRuns.some((bound) => runRefMatches(declared, bound))) {
        return "mismatched";
      }
      continue;
    }
    const boundForVenue = boundRuns.filter((bound) => bound.venue === declared.venue);
    if (boundForVenue.length === 0) {
      unbound += 1;
      continue;
    }
    if (!boundForVenue.some((bound) => runRefMatches(declared, bound))) {
      return "mismatched";
    }
  }
  return unbound === 0 ? "matched" : "partial";
}

/** Bound runs as the capture strip reports them; MISSING chips are not bound. */
export function boundRunsFromChips(venues: readonly VenueCaptureChip[]): ResearchVenueRun[] {
  const refs: ResearchVenueRun[] = [];
  for (const venue of venues) {
    if (venue.run_id === undefined || venue.run_id === "" || venue.status === "MISSING") {
      continue;
    }
    refs.push({ venue: venue.id, runId: venue.run_id, product: venue.product });
  }
  return refs;
}

export function parsePanelSummary(
  payload: unknown,
  source: string,
  boundRuns: readonly ResearchVenueRun[] = [],
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
  const runRefs = panelSummaryRunRefs(payload);
  return {
    runIds: [...new Set(runRefs.map((ref) => ref.runId))],
    runRefs,
    runBinding: classifyRunBinding(runRefs, boundRuns),
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
    runRefs: [],
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

const BINDING_RANK: Record<ResearchRunBinding, number> = {
  matched: 4,
  partial: 3,
  mismatched: 2,
  unknown: 1,
  unavailable: 0,
};

/**
 * Pick the panel summary that belongs to the full bound combination.
 *
 * Reading the first file on disk silently attributes an unrelated verdict to
 * whatever the operator happens to have selected. Instead every summary is
 * parsed and classified per venue; a `matched` summary wins. If none matches,
 * the best-classified readable summary is returned already flagged
 * (`partial` / `mismatched` / `unknown`) so the UI can refuse to present it as
 * a verdict about the current runs.
 */
export function loadPanelSummaryFromRoot(
  root: string | undefined,
  boundRuns: readonly ResearchVenueRun[] = [],
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
      parsed.push(parsePanelSummary(payload, relative(root, file), boundRuns));
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
  const fallback = parsed.reduce<ResearchSufficiency | undefined>(
    (best, summary) =>
      best === undefined || BINDING_RANK[summary.runBinding] > BINDING_RANK[best.runBinding]
        ? summary
        : best,
    undefined,
  );
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
  const boundRuns = boundRunsFromChips(venues);
  const boundRunIds = [...new Set(boundRuns.map((ref) => ref.runId))];
  return {
    registry,
    health,
    identity: buildResearchIdentity(),
    identityWarning: BINANCE_IDENTITY_WARNING,
    overlap: buildOverlapClock(strip.ok ? strip.strip : undefined),
    sufficiency: loadPanelSummaryFromRoot(researchOutRoot(env), boundRuns),
    boundRunIds,
    boundRuns,
    // elapsed vs 72h stays UNAVAILABLE; observed_at only timestamps the read.
    observedAt: strip.ok ? strip.strip.observed_at : nowIso,
    error: strip.ok ? undefined : strip.error,
  };
}
