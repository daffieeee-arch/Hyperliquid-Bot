import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const COURSE1_PATH_CONTRACT_ID = "course1-live-public-paper-cockpit-v1";
export const COCKPIT_ARTIFACT_SCHEMA = "course1-cockpit-paper-artifacts-v1";
export const COURSE1_RELATIVE_PREFIX = ["course1", "live-public-paper"] as const;
export const CANONICAL_LIVE_PAPER_RUN_ID = "20260904t001800z-live-paper";
export const DEFAULT_FIXTURE_RELATIVE_DIR = join(
  "tests",
  "fixtures",
  "course1_cockpit",
  "live-public-soak",
);

export const DATA1A_PATH_CONTRACT_ID = "data-1a-hyperliquid-btc-perp-v1";
export const DATA1A_CLAIM_SCHEMA = "data-1a-retained-capture-claim-v1";
export const DATA1A_HEALTH_SCHEMA = "data-1a-retained-capture-health-v1";
export const DATA1A_RELATIVE_PREFIX = ["data-1a", "hyperliquid", "BTC-PERP"] as const;
export const DATA1B_PATH_CONTRACT_ID = "data-1b-kraken-btc-usd-v1";
export const DATA1B_CLAIM_SCHEMA = "data-1b-retained-capture-claim-v1";
export const DATA1B_HEALTH_SCHEMA = "data-1b-retained-capture-health-v1";
export const DATA1B_RELATIVE_PREFIX = ["data-1b", "kraken", "BTC-USD"] as const;
export const DATA1E_PATH_CONTRACT_ID = "data-1e-bitvavo-btc-eur-v1";
export const DATA1E_CLAIM_SCHEMA = "data-1e-retained-capture-claim-v1";
export const DATA1E_HEALTH_SCHEMA = "data-1e-retained-capture-health-v1";
export const DATA1E_RELATIVE_PREFIX = ["data-1e", "bitvavo", "BTC-EUR"] as const;
export const DATA1F_PATH_CONTRACT_ID = "data-1f-binance-btcusdt-v1";
export const DATA1F_CLAIM_SCHEMA = "data-1f-retained-capture-claim-v1";
export const DATA1F_HEALTH_SCHEMA = "data-1f-retained-capture-health-v1";
export const DATA1F_RELATIVE_PREFIX = ["data-1f", "binance", "BTCUSDT"] as const;
export const DATA1A_PARQUET_GLOB_PREFIX = "part-";
export const DATA1A_PARQUET_SUFFIX = ".parquet";
export const CANONICAL_DATA1A_FIXTURE_RUN_ID = "sample-run";
export const DEFAULT_DATA1A_FIXTURE_RELATIVE_DIR = join(
  "tests",
  "fixtures",
  "data_1a_retained",
  "sample-run",
);
export const DATA1A_LIVE_EVIDENCE_RELATIVE_DIR = join(
  "tests",
  "fixtures",
  "data_1a_live_evidence",
  "20260904t001700z-live-retained",
);

export type VenueCaptureId = "hl" | "binance" | "bitvavo" | "kraken";
export type VenueCaptureChipLabel = "HL" | "BINANCE" | "BITVAVO" | "KRAKEN";
export type VenueCaptureSeries = "DATA-1A" | "DATA-1B" | "DATA-1E" | "DATA-1F";
export type CaptureRunSource =
  "data1a-run-dir" | "venue-run-dir" | "path-contract" | "auto-detect" | "default-fixture";

export type VenueCaptureQuery = {
  data1a_run_id?: string;
  data1b_run_id?: string;
  data1e_run_id?: string;
  data1f_run_id?: string;
};

export type VenueCaptureContract = {
  id: VenueCaptureId;
  chip: VenueCaptureChipLabel;
  series: VenueCaptureSeries;
  venue: string;
  product: string;
  pathContractId: string;
  claimSchema: string;
  healthSchema: string;
  relativePrefix: readonly [string, string, string];
  queryRunIdKey: keyof VenueCaptureQuery;
  envRunIdKeys: readonly string[];
  envRunDirKey: string;
  refuseLabel: string;
};

export const VENUE_CAPTURE_CONTRACTS: Record<VenueCaptureId, VenueCaptureContract> = {
  hl: {
    id: "hl",
    chip: "HL",
    series: "DATA-1A",
    venue: "hyperliquid",
    product: "BTC-PERP",
    pathContractId: DATA1A_PATH_CONTRACT_ID,
    claimSchema: DATA1A_CLAIM_SCHEMA,
    healthSchema: DATA1A_HEALTH_SCHEMA,
    relativePrefix: DATA1A_RELATIVE_PREFIX,
    queryRunIdKey: "data1a_run_id",
    envRunIdKeys: ["COCKPIT_DATA1A_RUN_ID", "DATA1A_RUN_ID"],
    envRunDirKey: "COCKPIT_DATA1A_RUN_DIR",
    refuseLabel: "DATA-1A",
  },
  binance: {
    id: "binance",
    chip: "BINANCE",
    series: "DATA-1F",
    venue: "binance",
    product: "BTCUSDT",
    pathContractId: DATA1F_PATH_CONTRACT_ID,
    claimSchema: DATA1F_CLAIM_SCHEMA,
    healthSchema: DATA1F_HEALTH_SCHEMA,
    relativePrefix: DATA1F_RELATIVE_PREFIX,
    queryRunIdKey: "data1f_run_id",
    envRunIdKeys: ["COCKPIT_DATA1F_RUN_ID", "DATA1F_RUN_ID"],
    envRunDirKey: "COCKPIT_DATA1F_RUN_DIR",
    refuseLabel: "DATA-1F",
  },
  bitvavo: {
    id: "bitvavo",
    chip: "BITVAVO",
    series: "DATA-1E",
    venue: "bitvavo",
    product: "BTC-EUR",
    pathContractId: DATA1E_PATH_CONTRACT_ID,
    claimSchema: DATA1E_CLAIM_SCHEMA,
    healthSchema: DATA1E_HEALTH_SCHEMA,
    relativePrefix: DATA1E_RELATIVE_PREFIX,
    queryRunIdKey: "data1e_run_id",
    envRunIdKeys: ["COCKPIT_DATA1E_RUN_ID", "DATA1E_RUN_ID"],
    envRunDirKey: "COCKPIT_DATA1E_RUN_DIR",
    refuseLabel: "DATA-1E",
  },
  kraken: {
    id: "kraken",
    chip: "KRAKEN",
    series: "DATA-1B",
    venue: "kraken",
    product: "BTC-USD",
    pathContractId: DATA1B_PATH_CONTRACT_ID,
    claimSchema: DATA1B_CLAIM_SCHEMA,
    healthSchema: DATA1B_HEALTH_SCHEMA,
    relativePrefix: DATA1B_RELATIVE_PREFIX,
    queryRunIdKey: "data1b_run_id",
    envRunIdKeys: ["COCKPIT_DATA1B_RUN_ID", "DATA1B_RUN_ID"],
    envRunDirKey: "COCKPIT_DATA1B_RUN_DIR",
    refuseLabel: "DATA-1B",
  },
};

export const VENUE_CAPTURE_STRIP_ORDER: readonly VenueCaptureId[] = [
  "hl",
  "binance",
  "bitvavo",
  "kraken",
];

export const COURSE1_COCKPIT_FILE_NAMES = [
  "run-claim.json",
  "paper-position.json",
  "paper-pnl.json",
  "orders.json",
  "fills.json",
  "capture-health.json",
] as const;

export type Course1CockpitFileName = (typeof COURSE1_COCKPIT_FILE_NAMES)[number];

export type PaperRunResolution = {
  runDir: string;
  runId: string;
  source: "paper-run-dir" | "path-contract" | "default-fixture";
};

export type Data1ARunResolution = {
  runDir: string;
  runId: string;
  source: "data1a-run-dir" | "path-contract" | "default-fixture";
};

export type VenueRunResolution = {
  runDir: string;
  runId: string;
  source: CaptureRunSource;
};

export type Data1AQuery = {
  data1a_run_id?: string;
};

// A run id is a single path segment; `.`/`..` would resolve to the venue dir.
const RUN_ID_PATTERN = /^(?!\.{1,2}$)[a-z0-9._-]{1,64}$/;

export function repoRootFromModuleUrl(moduleUrl: string = import.meta.url): string {
  return resolve(fileURLToPath(new URL("../../../../", moduleUrl)));
}

export function findRepoRoot(startDirectory: string = process.cwd()): string {
  let current = resolve(startDirectory);
  for (let index = 0; index < 8; index += 1) {
    if (existsSync(join(current, DEFAULT_FIXTURE_RELATIVE_DIR))) {
      return current;
    }
    const parent = resolve(current, "..");
    if (parent === current) {
      break;
    }
    current = parent;
  }
  throw new Error(
    "Could not locate the Hyperliquid-Bot repository root from the working directory.",
  );
}

export function isRunId(runId: string): boolean {
  return RUN_ID_PATTERN.test(runId);
}

export function requireRunId(runId: string): string {
  if (!isRunId(runId)) {
    throw new Error(
      "run_id must be 1-64 lowercase ASCII letters, digits, dot, dash or underscore.",
    );
  }
  return runId;
}

export function requirePaperTradingMode(tradingMode: string | undefined): "PAPER" {
  if (tradingMode === undefined || tradingMode === "" || tradingMode === "PAPER") {
    return "PAPER";
  }
  throw new Error("Cockpit first screen is PAPER-only and fails closed.");
}

export function course1CockpitRunDir(artifactRoot: string, runId: string): string {
  return join(resolve(artifactRoot), ...COURSE1_RELATIVE_PREFIX, requireRunId(runId));
}

export function data1aCockpitRunDir(artifactRoot: string, runId: string): string {
  return join(resolve(artifactRoot), ...DATA1A_RELATIVE_PREFIX, requireRunId(runId));
}

export function venueCockpitRunDir(
  contract: VenueCaptureContract,
  artifactRoot: string,
  runId: string,
): string {
  return join(resolve(artifactRoot), ...contract.relativePrefix, requireRunId(runId));
}

export function defaultFixtureRunDir(repoRoot: string): string {
  return resolve(repoRoot, DEFAULT_FIXTURE_RELATIVE_DIR);
}

export function defaultData1AFixtureRunDir(repoRoot: string): string {
  return resolve(repoRoot, DEFAULT_DATA1A_FIXTURE_RELATIVE_DIR);
}

export function firstQueryValue(value: string | string[] | undefined): string | undefined {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed === "" ? undefined : trimmed;
  }
  if (Array.isArray(value) && typeof value[0] === "string") {
    const trimmed = value[0].trim();
    return trimmed === "" ? undefined : trimmed;
  }
  return undefined;
}

export function captureArtifactRoot(env: NodeJS.Dict<string>): string | undefined {
  const captureRoot = env.ARTIFACT_ROOT?.trim();
  if (captureRoot) {
    return captureRoot;
  }
  const cockpitRoot = env.COCKPIT_ARTIFACT_ROOT?.trim();
  return cockpitRoot ? cockpitRoot : undefined;
}

function data1aRunId(env: NodeJS.Dict<string>, query: Data1AQuery): string | undefined {
  const fromQuery = query.data1a_run_id?.trim();
  if (fromQuery) {
    return fromQuery;
  }
  const fromCockpit = env.COCKPIT_DATA1A_RUN_ID?.trim();
  if (fromCockpit) {
    return fromCockpit;
  }
  const fromCapture = env.DATA1A_RUN_ID?.trim();
  return fromCapture ? fromCapture : undefined;
}

export function resolveData1ARunDir(
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: Data1AQuery = {},
): Data1ARunResolution {
  requirePaperTradingMode(env.TRADING_MODE);

  const explicitDir = env.COCKPIT_DATA1A_RUN_DIR?.trim();
  if (explicitDir) {
    const claimedRunId = data1aRunId(env, query);
    return {
      runDir: resolve(explicitDir),
      runId: claimedRunId ? requireRunId(claimedRunId) : CANONICAL_DATA1A_FIXTURE_RUN_ID,
      source: "data1a-run-dir",
    };
  }

  const runId = data1aRunId(env, query);
  const captureRoot = env.ARTIFACT_ROOT?.trim();
  if (runId) {
    const artifactRoot = captureArtifactRoot(env);
    if (artifactRoot === undefined) {
      throw new Error(
        "DATA-1A capture view needs ARTIFACT_ROOT or COCKPIT_ARTIFACT_ROOT together with the run_id.",
      );
    }
    const resolvedRunId = requireRunId(runId);
    return {
      runDir: data1aCockpitRunDir(artifactRoot, resolvedRunId),
      runId: resolvedRunId,
      source: "path-contract",
    };
  }
  if (captureRoot) {
    throw new Error(
      "DATA-1A capture view needs a run_id (COCKPIT_DATA1A_RUN_ID, DATA1A_RUN_ID, or ?data1a_run_id=) together with ARTIFACT_ROOT.",
    );
  }

  const fixtureDir = defaultData1AFixtureRunDir(repoRoot);
  if (!existsSync(fixtureDir)) {
    throw new Error(`Default DATA-1A Cockpit fixture is missing: ${fixtureDir}`);
  }
  return {
    runDir: fixtureDir,
    runId: CANONICAL_DATA1A_FIXTURE_RUN_ID,
    source: "default-fixture",
  };
}

export function resolvePaperRunDir(env: NodeJS.Dict<string>, repoRoot: string): PaperRunResolution {
  requirePaperTradingMode(env.TRADING_MODE);

  const paperRunDir = env.COCKPIT_PAPER_RUN_DIR?.trim();
  if (paperRunDir) {
    const runDir = resolve(paperRunDir);
    const claimedRunId = env.COCKPIT_RUN_ID?.trim();
    return {
      runDir,
      runId: claimedRunId ? requireRunId(claimedRunId) : CANONICAL_LIVE_PAPER_RUN_ID,
      source: "paper-run-dir",
    };
  }

  const artifactRoot = env.COCKPIT_ARTIFACT_ROOT?.trim();
  const runId = env.COCKPIT_RUN_ID?.trim();
  if (artifactRoot !== undefined && artifactRoot !== "" && runId !== undefined && runId !== "") {
    const resolvedRunId = requireRunId(runId);
    return {
      runDir: course1CockpitRunDir(artifactRoot, resolvedRunId),
      runId: resolvedRunId,
      source: "path-contract",
    };
  }
  if (artifactRoot || runId) {
    throw new Error("COCKPIT_ARTIFACT_ROOT and COCKPIT_RUN_ID must be set together.");
  }

  const fixtureDir = defaultFixtureRunDir(repoRoot);
  if (!existsSync(fixtureDir)) {
    throw new Error(`Default COURSE-1 Cockpit fixture is missing: ${fixtureDir}`);
  }
  return {
    runDir: fixtureDir,
    runId: CANONICAL_LIVE_PAPER_RUN_ID,
    source: "default-fixture",
  };
}

export function cockpitFilePath(runDir: string, fileName: Course1CockpitFileName): string {
  return join(runDir, fileName);
}

function venueRunId(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
  query: VenueCaptureQuery,
): string | undefined {
  const fromQuery = query[contract.queryRunIdKey]?.trim();
  if (fromQuery) {
    return fromQuery;
  }
  for (const key of contract.envRunIdKeys) {
    const value = env[key]?.trim();
    if (value) {
      return value;
    }
  }
  return undefined;
}

export function resolveVenueCaptureRunDir(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: VenueCaptureQuery = {},
): VenueRunResolution {
  if (contract.id === "hl") {
    return resolveData1ARunDir(env, repoRoot, { data1a_run_id: query.data1a_run_id });
  }

  requirePaperTradingMode(env.TRADING_MODE);

  const explicitDir = env[contract.envRunDirKey]?.trim();
  if (explicitDir) {
    const claimedRunId = venueRunId(contract, env, query);
    return {
      runDir: resolve(explicitDir),
      runId: claimedRunId ? requireRunId(claimedRunId) : "unspecified",
      source: "venue-run-dir",
    };
  }

  const runId = venueRunId(contract, env, query);
  const captureRoot = env.ARTIFACT_ROOT?.trim();
  if (runId) {
    const artifactRoot = captureArtifactRoot(env);
    if (artifactRoot === undefined) {
      throw new Error(
        `${contract.refuseLabel} capture view needs ARTIFACT_ROOT or COCKPIT_ARTIFACT_ROOT together with the run_id. Counts are not invented.`,
      );
    }
    const resolvedRunId = requireRunId(runId);
    return {
      runDir: venueCockpitRunDir(contract, artifactRoot, resolvedRunId),
      runId: resolvedRunId,
      source: "path-contract",
    };
  }
  if (captureRoot) {
    throw new Error(
      `${contract.refuseLabel} capture view needs a run_id together with ARTIFACT_ROOT. Missing venues fail closed; zeros are not invented.`,
    );
  }
  throw new Error(
    `${contract.refuseLabel} capture is not pointed at a reconstructable run. Set ARTIFACT_ROOT and the venue run_id. Missing venues fail closed.`,
  );
}
