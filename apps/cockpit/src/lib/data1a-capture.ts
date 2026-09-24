import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { bindVenueCaptureRun } from "./capture-runs";
import {
  DEFAULT_CAPTURE_FRESH_MAX_S,
  isLastPartFresh,
  resolveCaptureFreshMaxSeconds,
} from "./capture-freshness";
import { listPublishedParquetParts } from "./parquet-parts";

export { listPublishedParquetParts };
import {
  VENUE_CAPTURE_CONTRACTS,
  findRepoRoot,
  requirePaperTradingMode,
  type Data1AQuery,
  type VenueCaptureContract,
  type VenueRunResolution,
} from "./paths";
import type {
  Data1ACaptureClaim,
  Data1ACaptureHealth,
  Data1ACaptureSnapshot,
  JsonObject,
  TransportDisconnectFields,
  TransportProfileRow,
} from "./types";

function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readJsonObject(path: string): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
  } catch {
    throw new Error(`Cockpit JSON is missing or unreadable: ${path}`);
  }
  if (!isRecord(parsed)) {
    throw new Error(`Cockpit JSON must be an object: ${path}`);
  }
  return parsed;
}

function requireText(source: JsonObject, field: string, path: string): string {
  const value = source[field];
  if (typeof value !== "string" || value === "") {
    throw new Error(`${path} is missing non-empty string field ${field}.`);
  }
  return value;
}

function requireBoolean(source: JsonObject, field: string, path: string): boolean {
  const value = source[field];
  if (typeof value !== "boolean") {
    throw new Error(`${path} is missing boolean field ${field}.`);
  }
  return value;
}

function optionalBoolean(source: JsonObject, field: string, path: string): boolean | undefined {
  if (!(field in source)) {
    return undefined;
  }
  return requireBoolean(source, field, path);
}

function optionalText(source: JsonObject, field: string): string | undefined {
  const value = source[field];
  return typeof value === "string" && value !== "" ? value : undefined;
}

function optionalStrictText(source: JsonObject, field: string, path: string): string | undefined {
  if (!(field in source)) {
    return undefined;
  }
  return requireText(source, field, path);
}

function optionalFiniteNumber(source: JsonObject, field: string, path: string): number | undefined {
  if (!(field in source)) {
    return undefined;
  }
  const value = source[field];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${path} field ${field} must be a finite number when present.`);
  }
  return value;
}

function optionalInt(source: JsonObject, field: string, path: string): number | undefined {
  if (!(field in source)) {
    return undefined;
  }
  const value = source[field];
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`${path} field ${field} must be an integer when present.`);
  }
  return value;
}

function optionalDisconnectFields(source: JsonObject, path: string): TransportDisconnectFields {
  const fields: TransportDisconnectFields = {};
  const textFields = ["exception_class", "close_reason_rcvd", "close_reason_sent"] as const;
  for (const field of textFields) {
    const value = optionalStrictText(source, field, path);
    if (value !== undefined) {
      fields[field] = value;
    }
  }
  const intFields = ["close_code", "close_code_rcvd", "close_code_sent", "errno"] as const;
  for (const field of intFields) {
    const value = optionalInt(source, field, path);
    if (value !== undefined) {
      fields[field] = value;
    }
  }
  return fields;
}

function optionalStringList(source: JsonObject, field: string, path: string): string[] {
  if (!(field in source)) {
    return [];
  }
  const value = source[field];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${path}.${field} must be a list of strings.`);
  }
  return value;
}

function optionalTransportProfiles(
  source: JsonObject,
  path: string,
): TransportProfileRow[] | undefined {
  if (!("transport_profiles" in source)) {
    return undefined;
  }
  const value = source.transport_profiles;
  if (!Array.isArray(value)) {
    throw new Error(`${path}.transport_profiles must be a list when present.`);
  }
  return value.map((item, index) => {
    const itemPath = `${path}.transport_profiles[${String(index)}]`;
    if (!isRecord(item)) {
      throw new Error(`${itemPath} must be an object.`);
    }
    const name = item.transport_profile;
    if (typeof name !== "string" || name === "") {
      throw new Error(`${itemPath} is missing transport_profile.`);
    }
    const reconnectClusters = optionalInt(item, "reconnect_clusters", itemPath);
    return {
      transport_profile: name,
      gaps: optionalInt(item, "gaps", itemPath),
      reconnects: optionalInt(item, "reconnects", itemPath),
      ...(reconnectClusters === undefined ? {} : { reconnect_clusters: reconnectClusters }),
      ...optionalDisconnectFields(item, itemPath),
    };
  });
}

function refuseTwentyFourSeven(source: JsonObject, path: string, refuseLabel: string): false {
  const flag = requireBoolean(source, "twenty_four_seven", path);
  if (flag) {
    throw new Error(`${path} claims 24/7 service; the ${refuseLabel} cockpit view refuses that.`);
  }
  return false;
}

function loadClaim(
  runDir: string,
  contract: VenueCaptureContract,
  options: { allowMissingState?: boolean } = {},
): Data1ACaptureClaim {
  const path = join(runDir, "capture-claim.json");
  const raw = readJsonObject(path);
  if (raw.schema !== contract.claimSchema) {
    throw new Error(`${path} schema is not ${contract.claimSchema}.`);
  }
  if (raw.path_contract !== contract.pathContractId) {
    throw new Error(`${path} path_contract is not ${contract.pathContractId}.`);
  }
  const signing = optionalBoolean(raw, "signing", path);
  if (signing === true) {
    throw new Error(
      `${path} claims signing; the ${contract.refuseLabel} cockpit view refuses that.`,
    );
  }
  const stateValue = raw.state;
  let state: string;
  if (typeof stateValue === "string" && stateValue !== "") {
    state = stateValue;
  } else if (options.allowMissingState === true) {
    // In-flight retains that omitted state (e.g. early DATA-1D) still load.
    // Fresh parquet without capture-health.json is UNKNOWN, not a healthy feed.
    state = "STARTED_FAIL_CLOSED";
  } else {
    throw new Error(`${path} is missing non-empty string field state.`);
  }
  return {
    schema: contract.claimSchema,
    path_contract: contract.pathContractId,
    run_id: requireText(raw, "run_id", path),
    state,
    retained: requireBoolean(raw, "retained", path),
    twenty_four_seven: refuseTwentyFourSeven(raw, path, contract.refuseLabel),
    credentialless: optionalBoolean(raw, "credentialless", path),
    signing,
    venue: optionalText(raw, "venue"),
    product: optionalText(raw, "product"),
    feed: optionalText(raw, "feed"),
    duration_seconds: optionalFiniteNumber(raw, "duration_seconds", path),
    resume_policy: optionalText(raw, "resume_policy"),
  };
}

function loadHealth(
  runDir: string,
  contract: VenueCaptureContract,
): {
  health: Data1ACaptureHealth | undefined;
  health_missing: boolean;
  health_error: string | undefined;
} {
  const path = join(runDir, "capture-health.json");
  if (!existsSync(path)) {
    return {
      health: undefined,
      health_missing: true,
      health_error: "capture-health.json is not written yet",
    };
  }
  try {
    const raw = readJsonObject(path);
    if (raw.schema !== contract.healthSchema) {
      throw new Error(`${path} schema is not ${contract.healthSchema}.`);
    }
    if (raw.kind !== "capture-health") {
      throw new Error(`${path} kind is not capture-health.`);
    }
    if (raw.path_contract !== contract.pathContractId) {
      throw new Error(`${path} path_contract is not ${contract.pathContractId}.`);
    }
    return {
      health: {
        schema: contract.healthSchema,
        kind: "capture-health",
        path_contract: contract.pathContractId,
        run_id: requireText(raw, "run_id", path),
        status: requireText(raw, "status", path),
        retained: requireBoolean(raw, "retained", path),
        twenty_four_seven: refuseTwentyFourSeven(raw, path, contract.refuseLabel),
        credentialless: optionalBoolean(raw, "credentialless", path),
        gaps: optionalInt(raw, "gaps", path),
        reconnects: optionalInt(raw, "reconnects", path),
        reconnect_clusters: optionalInt(raw, "reconnect_clusters", path),
        events: optionalInt(raw, "events", path),
        parquet_files: optionalInt(raw, "parquet_files", path),
        parquet_bytes: optionalInt(raw, "parquet_bytes", path),
        transport_profiles: optionalTransportProfiles(raw, path),
        limitations: optionalStringList(raw, "limitations", path),
        ...optionalDisconnectFields(raw, path),
      },
      health_missing: false,
      health_error: undefined,
    };
  } catch (error: unknown) {
    return {
      health: undefined,
      health_missing: false,
      health_error: error instanceof Error ? error.message : "capture-health.json is unreadable.",
    };
  }
}

function skipResolvedRunIdMatch(source: VenueRunResolution["source"]): boolean {
  return source === "data1a-run-dir" || source === "venue-run-dir";
}

export function loadCaptureSnapshotForContract(
  resolved: VenueRunResolution,
  contract: VenueCaptureContract,
  now: () => string = () => new Date().toISOString(),
  freshMaxSeconds: number = DEFAULT_CAPTURE_FRESH_MAX_S,
): Data1ACaptureSnapshot {
  if (!existsSync(resolved.runDir)) {
    throw new Error(
      `${contract.refuseLabel} run directory is missing: ${resolved.runDir}. Set ARTIFACT_ROOT and the venue run_id to an existing reconstructable capture. Counts are not invented.`,
    );
  }
  const observedAt = now();
  const parts = listPublishedParquetParts(join(resolved.runDir, "raw"));
  let claim: Data1ACaptureClaim;
  try {
    claim = loadClaim(resolved.runDir, contract);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "";
    const missingState = /missing non-empty string field state/.test(message);
    const partsLookLive =
      parts.raw_dir_present &&
      (parts.count ?? 0) > 0 &&
      parts.last_part_mtime_utc !== undefined &&
      isLastPartFresh(parts.last_part_mtime_utc, observedAt, freshMaxSeconds);
    if (!missingState || !partsLookLive) {
      throw error;
    }
    claim = loadClaim(resolved.runDir, contract, { allowMissingState: true });
  }
  if (!skipResolvedRunIdMatch(resolved.source) && claim.run_id !== resolved.runId) {
    throw new Error(
      `capture-claim.json run_id ${claim.run_id} does not match resolved run_id ${resolved.runId}.`,
    );
  }
  const healthState = loadHealth(resolved.runDir, contract);
  if (
    healthState.health !== undefined &&
    !skipResolvedRunIdMatch(resolved.source) &&
    healthState.health.run_id !== resolved.runId
  ) {
    throw new Error(
      `capture-health.json run_id ${healthState.health.run_id} does not match resolved run_id ${resolved.runId}.`,
    );
  }
  return {
    runDir: resolved.runDir,
    runId: claim.run_id,
    source: resolved.source,
    observed_at: observedAt,
    fresh_max_s: freshMaxSeconds,
    path_contract: contract.pathContractId,
    claim,
    health: healthState.health,
    health_missing: healthState.health_missing,
    health_error: healthState.health_error,
    parts,
    duckdb_present: existsSync(join(resolved.runDir, "research.duckdb")),
  };
}

export function loadData1ACaptureSnapshot(
  env: NodeJS.Dict<string> = process.env,
  repoRoot: string = findRepoRoot(),
  query: Data1AQuery = {},
  now: () => string = () => new Date().toISOString(),
): Data1ACaptureSnapshot {
  requirePaperTradingMode(env.TRADING_MODE);
  const observedAt = now();
  const freshMaxSeconds = resolveCaptureFreshMaxSeconds(env);
  const resolved = bindVenueCaptureRun(
    VENUE_CAPTURE_CONTRACTS.hl,
    env,
    repoRoot,
    { data1a_run_id: query.data1a_run_id },
    { now: observedAt, freshMaxSeconds },
  );
  try {
    return loadCaptureSnapshotForContract(
      resolved,
      VENUE_CAPTURE_CONTRACTS.hl,
      () => observedAt,
      freshMaxSeconds,
    );
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "";
    if (message.includes("DATA-1A run directory is missing:")) {
      throw new Error(
        `DATA-1A run directory is missing: ${resolved.runDir}. Set ARTIFACT_ROOT and DATA1A_RUN_ID (or COCKPIT_DATA1A_RUN_ID / ?data1a_run_id=) to an existing reconstructable capture. Counts are not invented.`,
        { cause: error },
      );
    }
    throw error;
  }
}
