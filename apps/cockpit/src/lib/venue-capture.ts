import { existsSync } from "node:fs";
import { join } from "node:path";

import { DEFAULT_CAPTURE_FRESH_MAX_S, resolveCaptureFreshMaxSeconds } from "./capture-freshness";
import { loadCaptureSnapshotForContract } from "./data1a-capture";
import {
  data1aCaptureHealthPresentation,
  presentLastPartAge,
  venueCaptureChipStatus,
} from "./display";
import {
  VENUE_CAPTURE_CONTRACTS,
  VENUE_CAPTURE_STRIP_ORDER,
  findRepoRoot,
  requirePaperTradingMode,
  resolveVenueCaptureRunDir,
  type VenueCaptureContract,
  type VenueCaptureQuery,
} from "./paths";
import type { VenueCaptureChip, VenueCaptureStrip } from "./types";

const MISSING_ERROR_PATTERN =
  /missing|not pointed|needs ARTIFACT_ROOT|needs a run_id|fail closed|Counts are not invented/i;

function missingChip(
  contract: VenueCaptureContract,
  observedAt: string,
  error: string,
): VenueCaptureChip {
  return {
    id: contract.id,
    chip: contract.chip,
    series: contract.series,
    venue: contract.venue,
    product: contract.product,
    path_contract: contract.pathContractId,
    status: "MISSING",
    status_detail: error,
    tone: "warn",
    live: false,
    reason: undefined,
    part_count: undefined,
    last_part_age: "n/a",
    last_part_mtime_utc: undefined,
    run_id: undefined,
    observed_at: observedAt,
    error,
  };
}

function degradedChip(
  contract: VenueCaptureContract,
  observedAt: string,
  error: string,
  runId?: string,
): VenueCaptureChip {
  return {
    id: contract.id,
    chip: contract.chip,
    series: contract.series,
    venue: contract.venue,
    product: contract.product,
    path_contract: contract.pathContractId,
    status: "DEGRADED",
    status_detail: error,
    tone: "down",
    live: false,
    reason: undefined,
    part_count: undefined,
    last_part_age: "n/a",
    last_part_mtime_utc: undefined,
    run_id: runId,
    observed_at: observedAt,
    error,
  };
}

function classifyLoadError(
  contract: VenueCaptureContract,
  observedAt: string,
  error: unknown,
  runId?: string,
): VenueCaptureChip {
  const message =
    error instanceof Error
      ? error.message
      : `${contract.refuseLabel} capture health is unavailable.`;
  if (MISSING_ERROR_PATTERN.test(message)) {
    return missingChip(contract, observedAt, message);
  }
  return degradedChip(contract, observedAt, message, runId);
}

function chipTone(
  status: VenueCaptureChip["status"],
  presentationTone: VenueCaptureChip["tone"],
): VenueCaptureChip["tone"] {
  switch (status) {
    case "RUNNING":
      return "ok";
    case "STALE":
      return "warn";
    case "DEGRADED":
      return "down";
    case "STOPPED":
      return presentationTone;
    case "MISSING":
      return "warn";
    default: {
      const exhaustive: never = status;
      throw new Error(`Unhandled venue capture status: ${String(exhaustive)}`);
    }
  }
}

export function loadVenueCaptureChip(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: VenueCaptureQuery,
  observedAt: string,
  freshMaxSeconds: number = DEFAULT_CAPTURE_FRESH_MAX_S,
): VenueCaptureChip {
  let resolvedRunId: string | undefined;
  try {
    const resolved = resolveVenueCaptureRunDir(contract, env, repoRoot, query);
    resolvedRunId = resolved.runId;
    if (!existsSync(resolved.runDir)) {
      return missingChip(
        contract,
        observedAt,
        `${contract.refuseLabel} run directory is missing: ${resolved.runDir}. Counts are not invented.`,
      );
    }
    const claimPath = join(resolved.runDir, "capture-claim.json");
    if (!existsSync(claimPath)) {
      return missingChip(
        contract,
        observedAt,
        `${contract.refuseLabel} capture-claim.json is missing under ${resolved.runDir}. Counts are not invented.`,
      );
    }
    const snapshot = loadCaptureSnapshotForContract(
      resolved,
      contract,
      () => observedAt,
      freshMaxSeconds,
    );
    const presentation = data1aCaptureHealthPresentation(snapshot);
    const status = venueCaptureChipStatus(presentation);
    return {
      id: contract.id,
      chip: contract.chip,
      series: contract.series,
      venue: contract.venue,
      product: contract.product,
      path_contract: contract.pathContractId,
      status,
      status_detail: presentation.statusLabel,
      tone: chipTone(status, presentation.tone),
      live: presentation.live,
      reason: presentation.reason,
      part_count: snapshot.parts.raw_dir_present ? snapshot.parts.count : undefined,
      last_part_age: presentLastPartAge(snapshot.parts.last_part_mtime_utc, observedAt),
      last_part_mtime_utc: snapshot.parts.last_part_mtime_utc,
      run_id: snapshot.runId,
      observed_at: observedAt,
      error: snapshot.health_error,
    };
  } catch (error: unknown) {
    return classifyLoadError(contract, observedAt, error, resolvedRunId);
  }
}

export function loadVenueCaptureStrip(
  env: NodeJS.Dict<string> = process.env,
  repoRoot: string = findRepoRoot(),
  query: VenueCaptureQuery = {},
  now: () => string = () => new Date().toISOString(),
): VenueCaptureStrip {
  requirePaperTradingMode(env.TRADING_MODE);
  const observedAt = now();
  const freshMaxSeconds = resolveCaptureFreshMaxSeconds(env);
  return {
    observed_at: observedAt,
    fresh_max_s: freshMaxSeconds,
    venues: VENUE_CAPTURE_STRIP_ORDER.map((id) =>
      loadVenueCaptureChip(
        VENUE_CAPTURE_CONTRACTS[id],
        env,
        repoRoot,
        query,
        observedAt,
        freshMaxSeconds,
      ),
    ),
  };
}
