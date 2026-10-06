/**
 * Cockpit read model for `hypothesis-result-v1`.
 *
 * The research harness writes one directory per work package:
 *
 * ```text
 * result.json    schema_version hypothesis-result-v1, trading_mode PAPER
 * report.md      optional markdown (or report_markdown / report_markdown_path)
 * ```
 *
 * Canonical fields (camelCase aliases accepted): `work_package`,
 * `hypothesis_id`, `title`, `label`, `passes_h1`, `configs_tested`,
 * `configs_passed`, `best_gross_bps_per_trade` (alias `best_gross_bps`),
 * `best_net_bps_per_trade` (alias `best_net_bps`), `oos`, `holdout`,
 * `data_range`, `promotion_decision`, `updated_at`, `notes`, `synthetic`.
 *
 * Window bounds are ISO-8601 instants. A UTC nanosecond bound is accepted
 * only as a digit string; a JSON number above 2^53 is not exact, so it stays
 * UNAVAILABLE rather than becoming a wrong clock.
 *
 * Missing fields stay UNAVAILABLE. Numbers are never invented, and a missing
 * count is never shown as zero. An unknown schema_version or a trading_mode
 * other than PAPER is unreadable: metrics from that file are not shown.
 */

import { localDateTimeLabel } from "./time-display";

export const HYPOTHESIS_RESULT_SCHEMA = "hypothesis-result-v1";
export const RESULT_UNAVAILABLE = "UNAVAILABLE";

export type HypothesisPassesH1 = "yes" | "no" | typeof RESULT_UNAVAILABLE;
export type HypothesisResultStatus = "ok" | "unreadable";

export type HypothesisResultItem = {
  id: string;
  status: HypothesisResultStatus;
  sourcePath: string;
  problem: string;
  schemaVersion: string;
  tradingMode: string;
  synthetic: boolean;
  workPackage: string;
  hypothesisId: string;
  title: string;
  label: string;
  passesH1: HypothesisPassesH1;
  configsTested: string;
  configsPassed: string;
  grossBps: string;
  netBps: string;
  oosWindow: string;
  holdoutWindow: string;
  dataRange: string;
  promotionDecision: string;
  updatedAt: string;
  reportMarkdown: string | undefined;
  reportNote: string;
  notes: string;
  problems: string;
};

export type HypothesisResultList = {
  ok: true;
  root: string | undefined;
  observedAt: string;
  note: string;
  items: HypothesisResultItem[];
};

export type PromotionGate = "forbidden" | "recorded";

export type PromotionPresentation = {
  gate: PromotionGate;
  badge: string;
  tone: "warn" | "down";
  conflict: string | undefined;
};

export type ReportInline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "strong"; text: string };

export type ReportBlock =
  | { kind: "heading"; level: 2 | 3; text: string }
  | { kind: "paragraph"; inlines: ReportInline[] }
  | { kind: "list"; ordered: boolean; items: ReportInline[][] }
  | { kind: "code"; text: string };

const MAX_TEXT = 180;
const MAX_BPS = 1_000_000;
const MAX_COUNT = 1_000_000;

/** Drop ASCII controls other than tab and newline. Avoids a control-character regex. */
function stripControls(value: string): string {
  let cleaned = "";
  for (const char of value) {
    const code = char.charCodeAt(0);
    const isControl =
      code <= 8 || code === 11 || code === 12 || (code >= 14 && code <= 31) || code === 127;
    if (!isControl) {
      cleaned += char;
    }
  }
  return cleaned;
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function pick(record: JsonRecord, keys: readonly string[]): unknown {
  for (const key of keys) {
    if (Object.hasOwn(record, key)) {
      const value = record[key];
      if (value !== undefined) {
        return value;
      }
    }
  }
  return undefined;
}

function unreadableItem(id: string, sourcePath: string, problem: string): HypothesisResultItem {
  return {
    id,
    status: "unreadable",
    sourcePath,
    problem,
    schemaVersion: RESULT_UNAVAILABLE,
    tradingMode: RESULT_UNAVAILABLE,
    synthetic: false,
    workPackage: RESULT_UNAVAILABLE,
    hypothesisId: RESULT_UNAVAILABLE,
    title: RESULT_UNAVAILABLE,
    label: RESULT_UNAVAILABLE,
    passesH1: RESULT_UNAVAILABLE,
    configsTested: RESULT_UNAVAILABLE,
    configsPassed: RESULT_UNAVAILABLE,
    grossBps: RESULT_UNAVAILABLE,
    netBps: RESULT_UNAVAILABLE,
    oosWindow: RESULT_UNAVAILABLE,
    holdoutWindow: RESULT_UNAVAILABLE,
    dataRange: RESULT_UNAVAILABLE,
    promotionDecision: RESULT_UNAVAILABLE,
    updatedAt: RESULT_UNAVAILABLE,
    reportMarkdown: undefined,
    reportNote: problem,
    notes: RESULT_UNAVAILABLE,
    problems: problem,
  };
}

function readText(value: unknown, max = MAX_TEXT): { text: string; problem: string | undefined } {
  if (value === undefined || value === null) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (typeof value !== "string") {
    return { text: RESULT_UNAVAILABLE, problem: "is not text" };
  }
  const cleaned = stripControls(value).trim();
  if (cleaned === "") {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (cleaned.length > max) {
    return { text: `${cleaned.slice(0, max)}…`, problem: undefined };
  }
  return { text: cleaned, problem: undefined };
}

function readCount(value: unknown): { text: string; problem: string | undefined } {
  if (value === undefined || value === null) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= MAX_COUNT) {
    return { text: String(value), problem: undefined };
  }
  if (typeof value === "string" && /^\d+$/.test(value.trim())) {
    const parsed = Number(value.trim());
    if (Number.isSafeInteger(parsed) && parsed >= 0 && parsed <= MAX_COUNT) {
      return { text: String(parsed), problem: undefined };
    }
  }
  return { text: RESULT_UNAVAILABLE, problem: "is not a non-negative integer" };
}

function formatBps(value: number): string | undefined {
  if (!Number.isFinite(value) || Math.abs(value) > MAX_BPS) {
    return undefined;
  }
  const rounded = Math.round(value * 1e6) / 1e6;
  if (Object.is(rounded, -0)) {
    return "0";
  }
  return String(rounded);
}

function readBps(value: unknown): { text: string; problem: string | undefined } {
  if (value === undefined || value === null) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (typeof value === "number") {
    const formatted = formatBps(value);
    return formatted === undefined
      ? { text: RESULT_UNAVAILABLE, problem: "is not a finite per-trade bps value" }
      : { text: formatted, problem: undefined };
  }
  if (typeof value === "string" && /^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(value.trim())) {
    const formatted = formatBps(Number(value.trim()));
    return formatted === undefined
      ? { text: RESULT_UNAVAILABLE, problem: "is not a finite per-trade bps value" }
      : { text: formatted, problem: undefined };
  }
  return { text: RESULT_UNAVAILABLE, problem: "is not a finite per-trade bps value" };
}

function readPasses(value: unknown): { text: HypothesisPassesH1; problem: string | undefined } {
  if (value === undefined || value === null) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (value === true) {
    return { text: "yes", problem: undefined };
  }
  if (value === false) {
    return { text: "no", problem: undefined };
  }
  return { text: RESULT_UNAVAILABLE, problem: "is not a boolean" };
}

function readNotes(value: unknown): { text: string; problem: string | undefined } {
  if (value === undefined || value === null) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (typeof value === "string") {
    return readText(value, 500);
  }
  if (!Array.isArray(value)) {
    return { text: RESULT_UNAVAILABLE, problem: "is not text" };
  }
  const parts: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") {
      return { text: RESULT_UNAVAILABLE, problem: "contains a non-text note" };
    }
    const cleaned = stripControls(entry).trim();
    if (cleaned !== "") {
      parts.push(cleaned);
    }
  }
  if (parts.length === 0) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  const joined = parts.join(" · ");
  return joined.length > 500
    ? { text: `${joined.slice(0, 500)}…`, problem: undefined }
    : { text: joined, problem: undefined };
}

function nsToIso(value: unknown): string | undefined {
  let ns: bigint;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || value < 0) {
      return undefined;
    }
    ns = BigInt(value);
  } else if (typeof value === "string" && /^\d+$/.test(value.trim())) {
    try {
      ns = BigInt(value.trim());
    } catch {
      return undefined;
    }
  } else {
    return undefined;
  }
  const ms = ns / 1_000_000n;
  if (ms > BigInt(Number.MAX_SAFE_INTEGER)) {
    return undefined;
  }
  const date = new Date(Number(ms));
  if (Number.isNaN(date.getTime())) {
    return undefined;
  }
  return date.toISOString();
}

function formatInstant(value: unknown): string {
  if (typeof value !== "string") {
    return RESULT_UNAVAILABLE;
  }
  const trimmed = value.trim();
  if (trimmed === "") {
    return RESULT_UNAVAILABLE;
  }
  if (!/^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$/.test(trimmed)) {
    return trimmed.length > MAX_TEXT ? `${trimmed.slice(0, MAX_TEXT)}…` : trimmed;
  }
  const parsed = Date.parse(trimmed);
  if (!Number.isFinite(parsed)) {
    return RESULT_UNAVAILABLE;
  }
  return localDateTimeLabel(new Date(parsed).toISOString());
}

function formatEndpoint(
  record: JsonRecord,
  textKeys: readonly string[],
  nsKeys: readonly string[],
): string {
  const text = pick(record, textKeys);
  if (text !== undefined && text !== null) {
    return formatInstant(text);
  }
  const ns = pick(record, nsKeys);
  if (ns === undefined || ns === null) {
    return RESULT_UNAVAILABLE;
  }
  const iso = nsToIso(ns);
  return iso === undefined ? RESULT_UNAVAILABLE : localDateTimeLabel(iso);
}

function readWindow(value: unknown): { text: string; problem: string | undefined } {
  if (value === undefined || value === null) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  if (typeof value === "string") {
    return { text: formatInstant(value), problem: undefined };
  }
  if (!isRecord(value)) {
    return { text: RESULT_UNAVAILABLE, problem: "is not a window" };
  }
  const start = formatEndpoint(value, ["start", "start_utc", "from"], ["start_utc_ns"]);
  const end = formatEndpoint(value, ["end", "end_utc", "to"], ["end_utc_ns"]);
  if (start === RESULT_UNAVAILABLE && end === RESULT_UNAVAILABLE) {
    return { text: RESULT_UNAVAILABLE, problem: undefined };
  }
  return { text: `${start} – ${end}`, problem: undefined };
}

function pushProblem(problems: string[], field: string, problem: string | undefined): void {
  if (problem !== undefined) {
    problems.push(`${field} ${problem}; shown as UNAVAILABLE`);
  }
}

function readSchema(value: unknown): "missing" | "ok" | "rejected" {
  if (value === undefined || value === null) {
    return "missing";
  }
  return value === HYPOTHESIS_RESULT_SCHEMA ? "ok" : "rejected";
}

function readMode(value: unknown): "missing" | "paper" | "rejected" {
  if (value === undefined || value === null) {
    return "missing";
  }
  return value === "PAPER" ? "paper" : "rejected";
}

/**
 * Copy one result object. Absent fields become UNAVAILABLE. This function
 * does not read the filesystem and does not attach report markdown.
 */
export function parseHypothesisResultObject(
  id: string,
  sourcePath: string,
  value: unknown,
): HypothesisResultItem {
  if (!isRecord(value)) {
    return unreadableItem(id, sourcePath, "result.json is not an object; values are not invented.");
  }
  if (readSchema(pick(value, ["schema_version", "schemaVersion"])) === "rejected") {
    return unreadableItem(
      id,
      sourcePath,
      `schema_version is not ${HYPOTHESIS_RESULT_SCHEMA}; metrics are not shown.`,
    );
  }
  if (readMode(pick(value, ["trading_mode", "tradingMode"])) === "rejected") {
    return unreadableItem(id, sourcePath, "trading_mode is not PAPER; metrics are not shown.");
  }

  const problems: string[] = [];
  const schema = readText(pick(value, ["schema_version", "schemaVersion"]), 80);
  const mode = readText(pick(value, ["trading_mode", "tradingMode"]), 40);
  const workPackage = readText(pick(value, ["work_package", "workPackage"]), 80);
  const hypothesisId = readText(pick(value, ["hypothesis_id", "hypothesisId"]), 80);
  const title = readText(pick(value, ["title"]), 160);
  const label = readText(pick(value, ["label"]), 80);
  const passes = readPasses(pick(value, ["passes_h1", "passesH1"]));
  const tested = readCount(pick(value, ["configs_tested", "configsTested"]));
  const passed = readCount(pick(value, ["configs_passed", "configsPassed"]));
  const gross = readBps(
    pick(value, ["best_gross_bps_per_trade", "bestGrossBpsPerTrade", "best_gross_bps"]),
  );
  const net = readBps(
    pick(value, ["best_net_bps_per_trade", "bestNetBpsPerTrade", "best_net_bps"]),
  );
  const oos = readWindow(pick(value, ["oos", "oos_window", "oosWindow"]));
  const holdout = readWindow(pick(value, ["holdout", "holdout_window", "holdoutWindow"]));
  const dataRange = readWindow(pick(value, ["data_range", "dataRange"]));
  const promotion = readText(pick(value, ["promotion_decision", "promotionDecision"]), 80);
  const updated = readText(pick(value, ["updated_at", "updatedAt"]), 80);
  const notes = readNotes(pick(value, ["notes"]));

  pushProblem(problems, "work_package", workPackage.problem);
  pushProblem(problems, "hypothesis_id", hypothesisId.problem);
  pushProblem(problems, "title", title.problem);
  pushProblem(problems, "label", label.problem);
  pushProblem(problems, "passes_h1", passes.problem);
  pushProblem(problems, "configs_tested", tested.problem);
  pushProblem(problems, "configs_passed", passed.problem);
  pushProblem(problems, "best_gross_bps_per_trade", gross.problem);
  pushProblem(problems, "best_net_bps_per_trade", net.problem);
  pushProblem(problems, "oos", oos.problem);
  pushProblem(problems, "holdout", holdout.problem);
  pushProblem(problems, "data_range", dataRange.problem);
  pushProblem(problems, "promotion_decision", promotion.problem);
  pushProblem(problems, "updated_at", updated.problem);
  pushProblem(problems, "notes", notes.problem);

  const updatedText = updated.text;
  const updatedAt =
    updatedText === RESULT_UNAVAILABLE || Number.isFinite(Date.parse(updatedText))
      ? updatedText
      : RESULT_UNAVAILABLE;
  if (updatedAt === RESULT_UNAVAILABLE && updatedText !== RESULT_UNAVAILABLE) {
    problems.push("updated_at is not an ISO instant; shown as UNAVAILABLE");
  }

  return {
    id,
    status: "ok",
    sourcePath,
    problem: "",
    schemaVersion: schema.text,
    tradingMode: mode.text,
    synthetic: pick(value, ["synthetic"]) === true,
    workPackage: workPackage.text,
    hypothesisId: hypothesisId.text,
    title: title.text,
    label: label.text,
    passesH1: passes.text,
    configsTested: tested.text,
    configsPassed: passed.text,
    grossBps: gross.text,
    netBps: net.text,
    oosWindow: oos.text,
    holdoutWindow: holdout.text,
    dataRange: dataRange.text,
    promotionDecision: promotion.text,
    updatedAt,
    reportMarkdown: undefined,
    reportNote: "No report markdown was recorded.",
    notes: notes.text,
    problems: problems.join(" · "),
  };
}

export function configsLabel(passed: string, tested: string): string {
  if (passed === RESULT_UNAVAILABLE && tested === RESULT_UNAVAILABLE) {
    return RESULT_UNAVAILABLE;
  }
  return `${passed} / ${tested}`;
}

export function hypothesisHeadline(
  item: Pick<HypothesisResultItem, "workPackage" | "hypothesisId" | "title" | "id">,
): string {
  const wp = item.workPackage === RESULT_UNAVAILABLE ? item.id : item.workPackage;
  const hypothesis = item.hypothesisId === RESULT_UNAVAILABLE ? "" : ` ${item.hypothesisId}`;
  const title = item.title === RESULT_UNAVAILABLE ? "" : ` · ${item.title}`;
  return `${wp}${hypothesis}${title}`;
}

/**
 * Promotion stays forbidden unless the artifact records an H1 pass.
 * A pass does not promote: the recorded decision is shown, and the cockpit
 * still has no promote or order action.
 */
export function presentPromotion(
  passesH1: HypothesisPassesH1,
  recorded: string,
): PromotionPresentation {
  const recordedForbidden = recorded.trim().toLowerCase() === "forbidden";
  if (passesH1 !== "yes") {
    const conflict =
      recorded !== RESULT_UNAVAILABLE && !recordedForbidden
        ? `Recorded promotion_decision is ${recorded}; H1 did not pass, so promotion stays forbidden.`
        : undefined;
    return {
      gate: "forbidden",
      badge: "forbidden",
      tone: passesH1 === "no" ? "down" : "warn",
      conflict,
    };
  }
  if (recordedForbidden || recorded === RESULT_UNAVAILABLE) {
    return {
      gate: "forbidden",
      badge: recordedForbidden ? "forbidden" : RESULT_UNAVAILABLE,
      tone: "warn",
      conflict: undefined,
    };
  }
  return {
    gate: "recorded",
    badge: recorded,
    tone: "warn",
    conflict: undefined,
  };
}

export function hypothesisLabelTone(label: string): "warn" | "muted" | "info" {
  if (label === RESULT_UNAVAILABLE) {
    return "muted";
  }
  const normalized = label.toLowerCase();
  if (
    normalized.includes("fragile") ||
    normalized.includes("fail") ||
    normalized.includes("kill") ||
    normalized.includes("reject")
  ) {
    return "warn";
  }
  return "info";
}

export function passesH1Tone(value: HypothesisPassesH1): "ok" | "down" | "muted" {
  switch (value) {
    case "yes":
      return "ok";
    case "no":
      return "down";
    case RESULT_UNAVAILABLE:
      return "muted";
    default: {
      const exhaustive: never = value;
      throw new Error(`Unhandled passes_h1: ${String(exhaustive)}`);
    }
  }
}

export function promotionSummary(items: readonly HypothesisResultItem[]): {
  value: string;
  meta: string;
} {
  const passed = items.filter((item) => item.passesH1 === "yes").length;
  if (passed === 0) {
    return { value: "forbidden", meta: "No row records an H1 pass." };
  }
  return {
    value: "per row",
    meta: `${String(passed)} row(s) record an H1 pass. This view still does not promote.`,
  };
}

function parseInlines(source: string): ReportInline[] {
  const inlines: ReportInline[] = [];
  let buffer = "";
  let index = 0;
  const flush = (): void => {
    if (buffer !== "") {
      inlines.push({ kind: "text", text: buffer });
      buffer = "";
    }
  };
  while (index < source.length) {
    if (source.startsWith("**", index)) {
      const end = source.indexOf("**", index + 2);
      if (end !== -1) {
        flush();
        inlines.push({ kind: "strong", text: source.slice(index + 2, end) });
        index = end + 2;
        continue;
      }
    }
    if (source[index] === "`") {
      const end = source.indexOf("`", index + 1);
      if (end !== -1) {
        flush();
        inlines.push({ kind: "code", text: source.slice(index + 1, end) });
        index = end + 1;
        continue;
      }
    }
    buffer += source[index] ?? "";
    index += 1;
  }
  flush();
  return inlines;
}

/** Small markdown subset. Raw HTML is left as text so a report cannot inject markup. */
export function parseReportMarkdown(source: string): ReportBlock[] {
  const lines = source.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  const blocks: ReportBlock[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? "").startsWith("```")) {
        body.push(lines[index] ?? "");
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading !== null) {
      const marks = heading[1] ?? "#";
      const text = (heading[2] ?? "").trim();
      blocks.push({ kind: "heading", level: marks.length === 1 ? 2 : 3, text });
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: ReportInline[][] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index] ?? "")) {
        items.push(parseInlines((lines[index] ?? "").replace(/^[-*]\s+/, "")));
        index += 1;
      }
      blocks.push({ kind: "list", ordered: false, items });
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: ReportInline[][] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index] ?? "")) {
        items.push(parseInlines((lines[index] ?? "").replace(/^\d+\.\s+/, "")));
        index += 1;
      }
      blocks.push({ kind: "list", ordered: true, items });
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index] ?? "";
      if (
        current.trim() === "" ||
        current.startsWith("```") ||
        /^(#{1,3})\s+/.test(current) ||
        /^[-*]\s+/.test(current) ||
        /^\d+\.\s+/.test(current)
      ) {
        break;
      }
      paragraph.push(current.trim());
      index += 1;
    }
    blocks.push({ kind: "paragraph", inlines: parseInlines(paragraph.join(" ")) });
  }
  return blocks;
}

function coercePasses(value: unknown): HypothesisPassesH1 {
  return value === "yes" || value === "no" || value === RESULT_UNAVAILABLE
    ? value
    : RESULT_UNAVAILABLE;
}

function coerceText(value: unknown): string {
  return typeof value === "string" && value.trim() !== "" ? value : RESULT_UNAVAILABLE;
}

function coerceItem(value: unknown, index: number): HypothesisResultItem {
  if (!isRecord(value) || typeof value.id !== "string" || value.id.trim() === "") {
    return unreadableItem(
      `response-${String(index)}`,
      RESULT_UNAVAILABLE,
      "Hypothesis result row was not an object; values are not invented.",
    );
  }
  const status: HypothesisResultStatus = value.status === "unreadable" ? "unreadable" : "ok";
  const base = unreadableItem(value.id, coerceText(value.sourcePath), coerceText(value.problem));
  if (status === "unreadable") {
    return { ...base, status, problem: coerceText(value.problem) };
  }
  return {
    ...base,
    status: "ok",
    problem: typeof value.problem === "string" ? value.problem : "",
    schemaVersion: coerceText(value.schemaVersion),
    tradingMode: coerceText(value.tradingMode),
    synthetic: value.synthetic === true,
    workPackage: coerceText(value.workPackage),
    hypothesisId: coerceText(value.hypothesisId),
    title: coerceText(value.title),
    label: coerceText(value.label),
    passesH1: coercePasses(value.passesH1),
    configsTested: coerceText(value.configsTested),
    configsPassed: coerceText(value.configsPassed),
    grossBps: coerceText(value.grossBps),
    netBps: coerceText(value.netBps),
    oosWindow: coerceText(value.oosWindow),
    holdoutWindow: coerceText(value.holdoutWindow),
    dataRange: coerceText(value.dataRange),
    promotionDecision: coerceText(value.promotionDecision),
    updatedAt: coerceText(value.updatedAt),
    reportMarkdown: typeof value.reportMarkdown === "string" ? value.reportMarkdown : undefined,
    reportNote: typeof value.reportNote === "string" ? value.reportNote : base.reportNote,
    notes: coerceText(value.notes),
    problems: typeof value.problems === "string" ? value.problems : "",
  };
}

/** Fail closed on a poll payload that is not the list contract. One bad row does not drop the rest. */
export function parseHypothesisResultListPayload(payload: unknown): HypothesisResultList {
  if (!isRecord(payload) || payload.ok !== true || !Array.isArray(payload.items)) {
    const message =
      isRecord(payload) && typeof payload.error === "string"
        ? payload.error
        : "Hypothesis results response was not a fail-closed object.";
    throw new Error(message);
  }
  return {
    ok: true,
    root: typeof payload.root === "string" ? payload.root : undefined,
    observedAt: typeof payload.observedAt === "string" ? payload.observedAt : "",
    note: typeof payload.note === "string" ? payload.note : RESULT_UNAVAILABLE,
    items: payload.items.map((item, index) => coerceItem(item, index)),
  };
}
