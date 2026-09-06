import { existsSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { isLastPartFresh } from "./capture-freshness";
import { listPublishedParquetParts } from "./parquet-parts";
import { parseRunIdStartedAt, uniqueStrings } from "./display";
import {
  isRunId,
  requirePaperTradingMode,
  resolveVenueCaptureRunDir,
  venueCockpitRunDir,
  type VenueCaptureContract,
  type VenueCaptureQuery,
  type VenueRunResolution,
} from "./paths";
import type {
  CaptureBindingSource,
  CaptureRunCandidate,
  CaptureRunProvenance,
  CaptureRunProvenanceRow,
  VenueCaptureCatalog,
  VenueCaptureChip,
} from "./types";

export type BoundVenueRun = VenueRunResolution & {
  binding_source: CaptureBindingSource;
};

export type CaptureBindClock = {
  now: string;
  freshMaxSeconds: number;
};

function firstEnvRunId(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
): string | undefined {
  for (const key of contract.envRunIdKeys) {
    const value = env[key]?.trim();
    if (value) {
      return value;
    }
  }
  return undefined;
}

function compareCaptureRunCandidates(left: CaptureRunCandidate, right: CaptureRunCandidate): number {
  if (left.live !== right.live) {
    return left.live ? -1 : 1;
  }
  const leftMtime = left.last_part_mtime_utc ?? "";
  const rightMtime = right.last_part_mtime_utc ?? "";
  if (leftMtime !== rightMtime) {
    return rightMtime.localeCompare(leftMtime);
  }
  return right.run_id.localeCompare(left.run_id);
}

export function listVenueCaptureRuns(
  contract: VenueCaptureContract,
  artifactRoot: string,
  observedAt: string,
  freshMaxSeconds: number,
): CaptureRunCandidate[] {
  const seriesDir = join(resolve(artifactRoot), ...contract.relativePrefix);
  if (!existsSync(seriesDir)) {
    return [];
  }

  const candidates: CaptureRunCandidate[] = [];
  for (const entry of readdirSync(seriesDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || !isRunId(entry.name)) {
      continue;
    }
    const runDir = join(seriesDir, entry.name);
    if (!existsSync(join(runDir, "capture-claim.json"))) {
      continue;
    }
    const hasHealth = existsSync(join(runDir, "capture-health.json"));
    const parts = listPublishedParquetParts(join(runDir, "raw"));
    const live =
      !hasHealth &&
      parts.raw_dir_present &&
      (parts.count ?? 0) > 0 &&
      isLastPartFresh(parts.last_part_mtime_utc, observedAt, freshMaxSeconds);
    candidates.push({
      run_id: entry.name,
      has_claim: true,
      has_health: hasHealth,
      live,
      last_part_mtime_utc: parts.last_part_mtime_utc,
      part_count: parts.raw_dir_present ? parts.count : undefined,
    });
  }
  return candidates.sort(compareCaptureRunCandidates);
}

export function pickFreshestLiveRetain(
  candidates: readonly CaptureRunCandidate[],
): CaptureRunCandidate | undefined {
  return candidates.find((candidate) => candidate.live);
}

function classifyExplicitBinding(
  queryId: string | undefined,
  envId: string | undefined,
  explicitDir: string | undefined,
  source: VenueRunResolution["source"],
): CaptureBindingSource {
  if (queryId) {
    return "query";
  }
  if (explicitDir) {
    return "explicit-dir";
  }
  if (envId) {
    return "env";
  }
  if (source === "default-fixture") {
    return "default-fixture";
  }
  return "unbound";
}

export function bindVenueCaptureRun(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: VenueCaptureQuery,
  clock: CaptureBindClock,
): BoundVenueRun {
  requirePaperTradingMode(env.TRADING_MODE);

  const queryId = query[contract.queryRunIdKey]?.trim();
  const envId = firstEnvRunId(contract, env);
  const explicitDir = env[contract.envRunDirKey]?.trim();
  const artifactRoot = env.ARTIFACT_ROOT?.trim();
  const hasExplicitBind = Boolean(queryId || envId || explicitDir);

  if (hasExplicitBind || artifactRoot === undefined || artifactRoot === "") {
    const resolved = resolveVenueCaptureRunDir(contract, env, repoRoot, query);
    return {
      ...resolved,
      binding_source: classifyExplicitBinding(queryId, envId, explicitDir, resolved.source),
    };
  }

  const picked = pickFreshestLiveRetain(
    listVenueCaptureRuns(contract, artifactRoot, clock.now, clock.freshMaxSeconds),
  );
  if (picked !== undefined) {
    return {
      runDir: venueCockpitRunDir(contract, artifactRoot, picked.run_id),
      runId: picked.run_id,
      source: "auto-detect",
      binding_source: "auto-detect",
    };
  }

  throw new Error(
    `${contract.refuseLabel} capture view found no live retain to auto-detect under ARTIFACT_ROOT. Set the venue run_id or ?${contract.queryRunIdKey}=. Missing venues fail closed; zeros are not invented.`,
  );
}

export function venueCaptureCatalog(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
  clock: CaptureBindClock,
): VenueCaptureCatalog {
  const artifactRoot = env.ARTIFACT_ROOT?.trim();
  return {
    id: contract.id,
    chip: contract.chip,
    series: contract.series,
    query_key: contract.queryRunIdKey,
    candidates:
      artifactRoot === undefined || artifactRoot === ""
        ? []
        : listVenueCaptureRuns(contract, artifactRoot, clock.now, clock.freshMaxSeconds),
  };
}

export function captureRunProvenance(venues: readonly VenueCaptureChip[]): CaptureRunProvenance {
  const rows: CaptureRunProvenanceRow[] = [];
  for (const venue of venues) {
    if (venue.run_id === undefined || venue.run_id === "") {
      continue;
    }
    rows.push({
      id: venue.id,
      chip: venue.chip,
      series: venue.series,
      run_id: venue.run_id,
      binding_source: venue.binding_source,
      started_at_utc: parseRunIdStartedAt(venue.run_id),
    });
  }

  const distinctRunIds = uniqueStrings(rows.map((row) => row.run_id));
  const sharedRunIds = distinctRunIds.filter(
    (runId) => rows.filter((row) => row.run_id === runId).length >= 2,
  );
  const dated = rows.filter((row) => row.started_at_utc !== undefined);
  const missingBound = venues.some(
    (venue) => venue.status === "MISSING" || venue.run_id === undefined || venue.run_id === "",
  );

  if (rows.length === 0) {
    return {
      rows,
      distinct_run_ids: [],
      shared_run_ids: [],
      overlap_starts_utc: undefined,
      overlap_note:
        "No venue is bound to a reconstructable run_id. Overlap and counts are not invented.",
    };
  }
  if (dated.length === 0) {
    return {
      rows,
      distinct_run_ids: distinctRunIds,
      shared_run_ids: sharedRunIds,
      overlap_starts_utc: undefined,
      overlap_note:
        "Bound run_ids are not YYYYMMDDtHHMMSSz-prefixed; overlap start is not invented.",
    };
  }

  const latest = dated.reduce((current, row) =>
    (row.started_at_utc ?? "") > (current.started_at_utc ?? "") ? row : current,
  );
  const earliest = dated.reduce((current, row) =>
    (row.started_at_utc ?? "") < (current.started_at_utc ?? "") ? row : current,
  );
  const laterChips = dated
    .filter((row) => row.started_at_utc === latest.started_at_utc)
    .map((row) => row.chip);
  const earlierChips = dated
    .filter((row) => row.started_at_utc === earliest.started_at_utc)
    .map((row) => row.chip);

  let overlapNote: string;
  if (latest.started_at_utc !== earliest.started_at_utc) {
    overlapNote = `Comparable overlap starts at ${latest.started_at_utc ?? "n/a"} when ${laterChips.join("/")} started. Earlier-only data on ${earlierChips.join("/")} is not aligned and is not invented.`;
  } else if (distinctRunIds.length === 1) {
    overlapNote = `Bound venues share run_id ${distinctRunIds[0] ?? "n/a"}.`;
  } else {
    overlapNote = `Bound venues start at the same reconstructed UTC prefix ${latest.started_at_utc ?? "n/a"}. Distinct run_ids are shown; series are not merged.`;
  }
  if (missingBound) {
    overlapNote = `${overlapNote} MISSING venues are omitted from overlap.`;
  }

  return {
    rows,
    distinct_run_ids: distinctRunIds,
    shared_run_ids: sharedRunIds,
    overlap_starts_utc: latest.started_at_utc,
    overlap_note: overlapNote,
  };
}
