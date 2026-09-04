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

const RUN_ID_PATTERN = /^[a-z0-9._-]{1,64}$/;

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

export function requireRunId(runId: string): string {
  if (!RUN_ID_PATTERN.test(runId)) {
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

export function defaultFixtureRunDir(repoRoot: string): string {
  return resolve(repoRoot, DEFAULT_FIXTURE_RELATIVE_DIR);
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
