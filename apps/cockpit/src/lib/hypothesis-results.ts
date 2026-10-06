import { existsSync, lstatSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

import {
  parseHypothesisResultObject,
  type HypothesisResultItem,
  type HypothesisResultList,
} from "./hypothesis-result-model";
import { captureArtifactRoot, requirePaperTradingMode } from "./paths";

export const HYPOTHESIS_RESULT_FILE = "result.json";
export const HYPOTHESIS_REPORT_FILE = "report.md";

const MAX_RESULTS = 200;
const MAX_DEPTH = 6;
const MAX_JSON_BYTES = 1_048_576;
const MAX_REPORT_BYTES = 262_144;
const MAX_REPORT_CHARS = 64_000;

export const HYPOTHESIS_RESULTS_UNPOINTED_NOTE =
  "COCKPIT_HYPOTHESIS_RESULTS is not set and no artifact root is pointed; hypothesis results stay UNAVAILABLE.";

type ReportLoad = {
  markdown: string | undefined;
  note: string;
};

/**
 * Results directory. `COCKPIT_HYPOTHESIS_RESULTS` wins. Otherwise
 * `<artifact-root>/hypothesis-results` when an artifact root is configured.
 * There is no baked VPS path. Unset stays unpointed.
 */
export function hypothesisResultsRoot(env: NodeJS.Dict<string>): string | undefined {
  const explicit = env.COCKPIT_HYPOTHESIS_RESULTS?.trim();
  if (explicit) {
    return resolve(explicit);
  }
  const artifactRoot = captureArtifactRoot(env);
  if (artifactRoot === undefined) {
    return undefined;
  }
  return join(resolve(artifactRoot), "hypothesis-results");
}

function isInside(root: string, candidate: string): boolean {
  const rootResolved = resolve(root);
  const resolved = resolve(candidate);
  const prefix = rootResolved.endsWith(sep) ? rootResolved : `${rootResolved}${sep}`;
  return resolved === rootResolved || resolved.startsWith(prefix);
}

function resultId(root: string, filePath: string): string {
  const relativeDir = relative(root, dirname(filePath));
  if (relativeDir === "") {
    return ".";
  }
  return relativeDir.split(sep).join("/");
}

function collectResultFiles(
  root: string,
  directory: string,
  depth: number,
  files: string[],
): boolean {
  if (files.length >= MAX_RESULTS || depth > MAX_DEPTH) {
    return files.length >= MAX_RESULTS;
  }
  let entries;
  try {
    entries = readdirSync(/*turbopackIgnore: true*/ directory, { withFileTypes: true });
  } catch {
    if (directory === root) {
      throw new Error("Hypothesis results directory could not be read.");
    }
    return false;
  }
  entries.sort((left, right) => left.name.localeCompare(right.name));
  for (const entry of entries) {
    if (files.length >= MAX_RESULTS) {
      return true;
    }
    if (entry.name.startsWith(".") || entry.name === "node_modules") {
      continue;
    }
    const full = join(directory, entry.name);
    if (!isInside(root, full) || entry.isSymbolicLink()) {
      continue;
    }
    if (entry.isDirectory()) {
      if (collectResultFiles(root, full, depth + 1, files)) {
        return true;
      }
      continue;
    }
    if (entry.isFile() && entry.name === HYPOTHESIS_RESULT_FILE) {
      files.push(full);
    }
  }
  return files.length >= MAX_RESULTS;
}

function capReport(text: string): ReportLoad {
  const cleaned = text.replaceAll("\u0000", "");
  if (cleaned.trim() === "") {
    return { markdown: undefined, note: "Report markdown is empty; the report stays UNAVAILABLE." };
  }
  if (cleaned.length > MAX_REPORT_CHARS) {
    return {
      markdown: cleaned.slice(0, MAX_REPORT_CHARS),
      note: "Report markdown was truncated to the cockpit read cap.",
    };
  }
  return { markdown: cleaned, note: "Report copied from the result artifact." };
}

function readMarkdownFile(root: string, filePath: string): ReportLoad {
  if (!isInside(root, filePath)) {
    return {
      markdown: undefined,
      note: "Report path escaped the results directory; the report stays UNAVAILABLE.",
    };
  }
  try {
    const stat = lstatSync(/*turbopackIgnore: true*/ filePath);
    if (stat.isSymbolicLink() || !stat.isFile()) {
      return {
        markdown: undefined,
        note: "Report is not a regular file; the report stays UNAVAILABLE.",
      };
    }
    if (stat.size > MAX_REPORT_BYTES) {
      return {
        markdown: undefined,
        note: "Report exceeds the read cap; the report stays UNAVAILABLE.",
      };
    }
    return capReport(readFileSync(/*turbopackIgnore: true*/ filePath, "utf8"));
  } catch {
    return {
      markdown: undefined,
      note: "Report markdown could not be read; the report stays UNAVAILABLE.",
    };
  }
}

function resolveReportPath(root: string, resultDir: string, requested: string): string | undefined {
  const trimmed = requested.trim();
  if (trimmed === "" || trimmed.includes("\0") || trimmed.includes("..")) {
    return undefined;
  }
  const normalized = trimmed.replaceAll("\\", "/");
  if (normalized.startsWith("/") || !normalized.toLowerCase().endsWith(".md")) {
    return undefined;
  }
  const resolved = resolve(resultDir, normalized);
  if (!isInside(root, resolved)) {
    return undefined;
  }
  return resolved;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function loadReport(root: string, resultDir: string, value: unknown): ReportLoad {
  if (!isRecord(value)) {
    return { markdown: undefined, note: "Report stays UNAVAILABLE." };
  }
  if (Object.hasOwn(value, "report_markdown") || Object.hasOwn(value, "reportMarkdown")) {
    const inline =
      value.report_markdown !== undefined ? value.report_markdown : value.reportMarkdown;
    if (typeof inline !== "string") {
      return {
        markdown: undefined,
        note: "report_markdown is not text; the report stays UNAVAILABLE.",
      };
    }
    return capReport(inline);
  }
  const pathValue = value.report_markdown_path ?? value.report_path;
  if (pathValue !== undefined && pathValue !== null) {
    if (typeof pathValue !== "string") {
      return {
        markdown: undefined,
        note: "report path is not text; the report stays UNAVAILABLE.",
      };
    }
    const resolved = resolveReportPath(root, resultDir, pathValue);
    if (resolved === undefined) {
      return {
        markdown: undefined,
        note: "Report path escaped the results directory; the report stays UNAVAILABLE.",
      };
    }
    return readMarkdownFile(root, resolved);
  }
  const sibling = join(resultDir, HYPOTHESIS_REPORT_FILE);
  if (existsSync(/*turbopackIgnore: true*/ sibling)) {
    return readMarkdownFile(root, sibling);
  }
  return { markdown: undefined, note: "No report markdown was recorded." };
}

function refuseFile(id: string, sourcePath: string, problem: string): HypothesisResultItem {
  const item = parseHypothesisResultObject(id, sourcePath, undefined);
  return { ...item, problem, reportNote: problem, problems: problem };
}

function readResultFile(root: string, filePath: string): HypothesisResultItem {
  const id = resultId(root, filePath);
  const sourcePath = relative(root, filePath).split(sep).join("/");
  let raw: string;
  try {
    const stat = lstatSync(/*turbopackIgnore: true*/ filePath);
    if (stat.isSymbolicLink() || !stat.isFile()) {
      return refuseFile(
        id,
        sourcePath,
        "result.json is not a regular file; values are not invented.",
      );
    }
    if (stat.size > MAX_JSON_BYTES) {
      return refuseFile(
        id,
        sourcePath,
        "result.json exceeds the read cap; values are not invented.",
      );
    }
    raw = readFileSync(/*turbopackIgnore: true*/ filePath, "utf8");
  } catch {
    return refuseFile(id, sourcePath, "result.json could not be read; values are not invented.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    return refuseFile(id, sourcePath, "result.json is not valid JSON; values are not invented.");
  }
  const item = parseHypothesisResultObject(id, sourcePath, parsed);
  if (item.status !== "ok") {
    return item;
  }
  const report = loadReport(root, dirname(filePath), parsed);
  return { ...item, reportMarkdown: report.markdown, reportNote: report.note };
}

function listNote(
  items: readonly HypothesisResultItem[],
  truncated: boolean,
  rootMissing: boolean,
): string {
  if (rootMissing) {
    return "Hypothesis results directory is missing. Values are not invented.";
  }
  if (items.length === 0) {
    return "No result.json under the hypothesis results directory. Values stay UNAVAILABLE.";
  }
  const unreadable = items.filter((item) => item.status === "unreadable").length;
  const parts = [`Read ${String(items.length)} result.json file(s).`];
  if (unreadable > 0) {
    parts.push(`${String(unreadable)} could not be read; their metrics stay UNAVAILABLE.`);
  }
  if (truncated) {
    parts.push(`Stopped after ${String(MAX_RESULTS)} files.`);
  }
  return parts.join(" ");
}

/**
 * Read every `result.json` under the configured directory.
 * One malformed file becomes an unreadable row; it does not drop the others.
 */
export function listHypothesisResults(
  env: NodeJS.Dict<string>,
  nowIso: string = new Date().toISOString(),
): HypothesisResultList {
  requirePaperTradingMode(env.TRADING_MODE);
  const root = hypothesisResultsRoot(env);
  if (root === undefined) {
    return {
      ok: true,
      root: undefined,
      observedAt: nowIso,
      note: HYPOTHESIS_RESULTS_UNPOINTED_NOTE,
      items: [],
    };
  }
  if (!existsSync(/*turbopackIgnore: true*/ root)) {
    return {
      ok: true,
      root,
      observedAt: nowIso,
      note: listNote([], false, true),
      items: [],
    };
  }
  let directory: boolean;
  try {
    directory = statSync(/*turbopackIgnore: true*/ root).isDirectory();
  } catch {
    directory = false;
  }
  if (!directory) {
    return {
      ok: true,
      root,
      observedAt: nowIso,
      note: "Hypothesis results path is not a directory. Values are not invented.",
      items: [],
    };
  }
  const files: string[] = [];
  let truncated: boolean;
  try {
    truncated = collectResultFiles(root, root, 0, files);
  } catch {
    return {
      ok: true,
      root,
      observedAt: nowIso,
      note: "Hypothesis results directory could not be read. Values are not invented.",
      items: [],
    };
  }
  const items = files.map((filePath) => readResultFile(root, filePath));
  items.sort((left, right) => {
    const byPackage = left.workPackage.localeCompare(right.workPackage);
    if (byPackage !== 0) {
      return byPackage;
    }
    const byHypothesis = left.hypothesisId.localeCompare(right.hypothesisId);
    if (byHypothesis !== 0) {
      return byHypothesis;
    }
    return left.id.localeCompare(right.id);
  });
  return {
    ok: true,
    root,
    observedAt: nowIso,
    note: listNote(items, truncated, false),
    items,
  };
}
