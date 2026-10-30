import { closeSync, constants, fstatSync, openSync, readSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

import type { DataState } from "./data-state";
import {
  NATIVE_PAPER_SCHEMA,
  type NativePaperFill,
  type NativePaperOrder,
  type NativePaperProjection,
  type NativePaperRejection,
  type NativePaperSnapshot,
} from "./native-paper-model";
import { findRepoRoot, requirePaperTradingMode } from "./paths";

const JSON_LIMIT = 256 * 1024;
export const NATIVE_LEDGER_BYTE_LIMIT = 2 * 1024 * 1024;
export const NATIVE_LEDGER_RECORD_LIMIT = 5000;
const FRESH_MS = 180_000;
const FILES = ["run-claim.json", "state.json", "health.json", "ledger.jsonl"] as const;
const RECORD_TYPES = new Set([
  "order_accepted",
  "order_completed",
  "order_canceled",
  "fill",
  "risk_rejected",
  "kill_switch",
  "stop_set",
  "stop_triggered",
  "stop_lockout_cleared",
]);
type ObjectValue = Record<string, unknown>;

class ReadProblem extends Error {
  constructor(
    readonly state: DataState,
    message: string,
  ) {
    super(message);
  }
}

function malformed(): never {
  throw new ReadProblem("error", "Native PAPER artifacts violate the supported contract.");
}
function incomplete(): never {
  throw new ReadProblem(
    "pending",
    "Native PAPER writes are incomplete or disagree; values are unavailable.",
  );
}
function object(value: unknown): ObjectValue {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return malformed();
  return value as ObjectValue;
}
function text(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 512 || value.trim() !== value)
    return malformed();
  return value;
}
function nullableText(value: unknown): string | null {
  return value === null ? null : text(value);
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") return malformed();
  return value;
}
function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return malformed();
  return value;
}
function decimal(value: unknown): string {
  const result = text(value);
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d{1,40})?$/.test(result) || result.length > 80) return malformed();
  return result;
}
function units(value: string): bigint {
  const negative = value.startsWith("-");
  const [whole = "0", fraction = ""] = (negative ? value.slice(1) : value).split(".");
  return (negative ? -1n : 1n) * BigInt(whole + fraction.padEnd(40, "0"));
}
function positive(value: unknown): string {
  const result = decimal(value);
  if (units(result) <= 0n) return malformed();
  return result;
}
function nullableDecimal(value: unknown): string | null {
  return value === null ? null : decimal(value);
}
function utc(value: unknown): string {
  const result = text(value);
  if (
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(result) ||
    !Number.isFinite(Date.parse(result)) ||
    new Date(result).toISOString().slice(0, 19) !== result.slice(0, 19)
  )
    return malformed();
  return result;
}
function side(value: unknown): "BUY" | "SELL" {
  if (value !== "BUY" && value !== "SELL") return malformed();
  return value;
}
function parseJson(input: string): unknown {
  // Node 24's JSON source context preserves Python nanosecond integers. They
  // are compared internally as text and are not exported as rounded JS times.
  return JSON.parse(
    input,
    (key: string, value: unknown, context?: { source?: string }): unknown => {
      if (key !== "received_utc_ns" || value === null) return value;
      if (typeof value !== "number" || !context?.source || !/^\d{1,24}$/.test(context.source))
        return malformed();
      return context.source;
    },
  );
}
function objects(value: unknown): ObjectValue[] {
  if (!Array.isArray(value)) return malformed();
  return value.map(object);
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

/** Nonblocking, no-follow fd reads prevent FIFOs, symlinks and growing files from hanging a route. */
function readBounded(path: string, limit: number): string {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = fstatSync(fd, { bigint: true });
    if (!before.isFile() || before.size > BigInt(limit))
      throw new ReadProblem("error", "Native PAPER input is not a bounded regular file.");
    const buffer = Buffer.alloc(Number(before.size) + 1);
    let length = 0;
    while (length < buffer.length) {
      const next = readSync(fd, buffer, length, buffer.length - length, null);
      if (next === 0) break;
      length += next;
    }
    const after = fstatSync(fd, { bigint: true });
    if (
      BigInt(length) !== before.size ||
      after.size !== before.size ||
      after.mtimeNs !== before.mtimeNs ||
      after.ctimeNs !== before.ctimeNs
    )
      return incomplete();
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, length));
  } finally {
    closeSync(fd);
  }
}

// Operator-bound runtime artifacts are read in place, never bundled as build inputs.
function readArtifacts(runDir: string): Map<string, string> {
  const result = new Map<string, string>();
  for (const name of FILES)
    result.set(
      name,
      readBounded(
        join(/* turbopackIgnore: true */ runDir, name),
        name === "ledger.jsonl" ? NATIVE_LEDGER_BYTE_LIMIT : JSON_LIMIT,
      ),
    );
  // Files are individually atomic, but the producer has no shared transaction or watermark.
  for (const name of FILES)
    if (
      result.get(name) !==
      readBounded(
        join(/* turbopackIgnore: true */ runDir, name),
        name === "ledger.jsonl" ? NATIVE_LEDGER_BYTE_LIMIT : JSON_LIMIT,
      )
    )
      return incomplete();
  return result;
}

function validateHeaders(claim: ObjectValue, state: ObjectValue, health: ObjectValue): void {
  if (
    claim.schema !== "paper-engine-run-claim-v1" ||
    state.schema !== "paper-engine-state-v2" ||
    health.schema !== "paper-engine-health-v1"
  )
    return malformed();
  for (const data of [claim, state, health]) {
    if (
      data.mode !== "PAPER" ||
      data.venue !== "hyperliquid" ||
      data.instrument_id !== "BTC-PERP" ||
      data.venue_orders_submitted !== false ||
      data.resume !== false
    )
      return malformed();
    bool(data.production_eligible);
  }
  if (
    claim.create_only !== true ||
    health.twenty_four_seven !== false ||
    !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(text(claim.run_id))
  )
    return malformed();
  for (const field of [
    "run_id",
    "strategy_id",
    "strategy_configuration_version",
    "source_commit",
    "image_digest",
  ]) {
    text(claim[field]);
    if (claim[field] !== state[field] || state[field] !== health[field]) return malformed();
  }
  if (
    claim.production_eligible !== state.production_eligible ||
    state.production_eligible !== health.production_eligible
  )
    return malformed();
  if (text(claim.strategy_label) !== text(health.strategy_label)) return malformed();
  bool(health.run_closed);
  if (bool(health.stale) !== (health.kill_reason === "stale_data")) return malformed();
  if (bool(health.ledger_write_failed) || health.status === "FAILED")
    throw new ReadProblem(
      "error",
      "Native PAPER engine failed; financial values and history are not trusted.",
    );
  if (!["RUNNING", "COMPLETED", "HALT_NEW", "FLATTEN_HALT"].includes(text(health.status)))
    return malformed();
  const kill = text(state.kill_switch);
  if (!["NONE", "HALT_NEW", "FLATTEN_HALT"].includes(kill)) return malformed();
  if (
    (health.status === "COMPLETED" && (!health.run_closed || kill !== "NONE")) ||
    (health.status === "RUNNING" && (health.run_closed || kill !== "NONE")) ||
    (["HALT_NEW", "FLATTEN_HALT"].includes(String(health.status)) && health.status !== kill)
  )
    return malformed();
  nullableText(state.kill_reason);
  nullableDecimal(state.stop_price);
  bool(state.stop_exit_pending);
  if (![null, "long", "short"].includes(state.stop_lockout as string | null)) return malformed();
  for (const field of [
    "position_quantity",
    "average_entry_price",
    "realized_pnl_usdc",
    "fees_usdc",
  ]) {
    decimal(state[field]);
    decimal(health[field]);
  }
  nullableDecimal(state.unrealized_pnl_usdc);
  for (const field of [
    "position_quantity",
    "average_entry_price",
    "realized_pnl_usdc",
    "unrealized_pnl_usdc",
    "fees_usdc",
    "kill_switch",
    "kill_reason",
    "stop_price",
    "stop_exit_pending",
    "stop_lockout",
    "order_count",
    "fill_count",
    "risk_rejection_count",
  ]) {
    if (state[field] !== health[field]) return incomplete();
  }
}

function history(ledger: string, claim: ObjectValue, state: ObjectValue) {
  if (ledger !== "" && !ledger.endsWith("\n")) return incomplete();
  const lines = ledger === "" ? [] : ledger.slice(0, -1).split("\n");
  if (lines.length > NATIVE_LEDGER_RECORD_LIMIT)
    throw new ReadProblem("error", "Native PAPER ledger exceeds the bounded snapshot limit.");
  const orders: NativePaperOrder[] = [],
    fills: NativePaperFill[] = [],
    rejections: NativePaperRejection[] = [];
  const rawOrders: ObjectValue[] = [],
    rawFills: ObjectValue[] = [],
    rawRejections: ObjectValue[] = [];
  const accepted = new Map<string, ObjectValue>(),
    filled = new Map<string, string>();
  const seen = new Set<string>();
  let position = 0n,
    fees = 0n,
    kill = "NONE";
  let killReason: string | null = null,
    stopPrice: string | null = null,
    stopLockout: string | null = null;
  let stopPending = false;
  for (const [index, line] of lines.entries()) {
    const row = object(parseJson(line));
    const type = text(row.type);
    if (!RECORD_TYPES.has(type) || (row.mode !== undefined && row.mode !== "PAPER"))
      return malformed();
    const { type: _type, mode: _mode, ...raw } = row;
    void _type;
    void _mode;
    const ordinal = index + 1;
    if (type === "order_accepted") {
      const id = text(row.client_order_id);
      if (
        row.mode !== "PAPER" ||
        row.venue_orders_submitted !== false ||
        !id.startsWith(`${String(claim.run_id)}-`) ||
        !/^\d{6}$/.test(id.slice(String(claim.run_id).length + 1)) ||
        seen.has(id) ||
        accepted.size !== 0
      )
        return malformed();
      text(row.received_utc_ns);
      side(row.side);
      positive(row.quantity);
      positive(row.limit_price);
      bool(row.reduce_only);
      text(row.reason);
      seen.add(id);
      accepted.set(id, row);
    } else if (type === "fill") {
      const id = text(row.client_order_id),
        order = accepted.get(id);
      if (
        !order ||
        filled.has(id) ||
        row.mode !== "PAPER" ||
        row.venue_fill !== false ||
        row.side !== order.side ||
        !["bbo", "trade"].includes(String(row.source))
      )
        return malformed();
      const quantity = positive(row.quantity),
        fee = decimal(row.fee_usdc);
      if (units(quantity) > units(decimal(order.quantity)) || units(fee) < 0n) return malformed();
      fills.push({
        ordinal,
        orderId: id,
        side: side(row.side),
        quantity,
        price: positive(row.price),
        feeUsdc: fee,
        source: text(row.source),
      });
      rawFills.push(raw);
      filled.set(id, quantity);
      position += (row.side === "BUY" ? 1n : -1n) * units(quantity);
      fees += units(fee);
      if (position === 0n) {
        stopPrice = null;
        stopPending = false;
      }
    } else if (type === "order_completed" || type === "order_canceled") {
      const id = text(row.client_order_id),
        order = accepted.get(id);
      if (
        !order ||
        row.venue_orders_submitted !== false ||
        row.side !== order.side ||
        row.quantity !== order.quantity ||
        row.reduce_only !== order.reduce_only ||
        row.limit_price !== order.limit_price
      )
        return malformed();
      const quantity = positive(row.quantity),
        completed = decimal(row.filled_quantity);
      if (units(completed) !== units(filled.get(id) ?? "0")) return malformed();
      const status = row.status;
      if (
        (status === "FILLED" && units(completed) !== units(quantity)) ||
        (status === "PARTIALLY_FILLED" &&
          (units(completed) <= 0n || units(completed) >= units(quantity))) ||
        (status === "CANCELED" && units(completed) !== 0n) ||
        !["FILLED", "PARTIALLY_FILLED", "CANCELED"].includes(String(status))
      )
        return malformed();
      if (
        type === "order_completed" &&
        (row.environment !== "PAPER" ||
          row.venue !== "hyperliquid" ||
          row.strategy_id !== claim.strategy_id ||
          row.configuration_version !== claim.strategy_configuration_version ||
          row.source_commit !== claim.source_commit ||
          row.image_digest !== claim.image_digest ||
          row.correlation_id !== id)
      )
        return malformed();
      if (type === "order_canceled" && status !== "CANCELED") return malformed();
      orders.push({
        ordinal,
        id,
        side: side(row.side),
        quantity,
        filledQuantity: completed,
        status: status as NativePaperOrder["status"],
        reason: text(row.reason),
        reduceOnly: bool(row.reduce_only),
      });
      rawOrders.push(raw);
      accepted.delete(id);
    } else if (type === "risk_rejected") {
      if (row.mode !== "PAPER") return malformed();
      rejections.push({ ordinal, reason: text(row.reason), detail: nullableText(row.detail) });
      rawRejections.push(raw);
    } else if (type === "kill_switch") {
      kill = text(row.state);
      if (!["NONE", "HALT_NEW", "FLATTEN_HALT"].includes(kill)) return malformed();
      const reason = text(row.reason);
      killReason = kill === "NONE" ? null : reason;
    } else {
      if (row.mode !== "PAPER") return malformed();
      if (type === "stop_set") {
        stopPrice = positive(row.stop_price);
        positive(row.average_entry_price);
        positive(row.stop_distance_fraction);
        if (units(decimal(row.position_quantity)) !== position || position === 0n)
          return malformed();
      } else if (type === "stop_triggered") {
        if (
          positive(row.stop_price) !== stopPrice ||
          units(decimal(row.position_quantity)) !== position ||
          position === 0n
        )
          return malformed();
        positive(row.mark_price);
        text(row.mark_source);
        stopPending = true;
        stopLockout = position > 0n ? "long" : "short";
      } else {
        decimal(row.target_quantity);
        stopLockout = null;
      }
    }
  }
  const recentLimit = count(state.recent_record_limit);
  if (recentLimit !== 100) return malformed();
  for (const [rows, field, counter] of [
    [rawOrders, "orders", "order_count"],
    [rawFills, "fills", "fill_count"],
    [rawRejections, "risk_rejections", "risk_rejection_count"],
  ] as const) {
    if (
      count(state[counter]) !== rows.length ||
      canonical(objects(state[field])) !== canonical(rows.slice(-recentLimit))
    )
      return incomplete();
  }
  const openOrder = nullableText(state.open_order);
  if (
    position !== units(decimal(state.position_quantity)) ||
    fees !== units(decimal(state.fees_usdc)) ||
    kill !== state.kill_switch ||
    killReason !== state.kill_reason ||
    stopPrice !== state.stop_price ||
    stopPending !== state.stop_exit_pending ||
    stopLockout !== state.stop_lockout ||
    (openOrder === null ? accepted.size !== 0 : accepted.size !== 1 || !accepted.has(openOrder))
  )
    return incomplete();
  return { orders, fills, rejections, ledgerRecords: lines.length, openOrder };
}

function snapshot(files: Map<string, string>): NativePaperSnapshot {
  const claim = object(parseJson(files.get("run-claim.json") ?? ""));
  const state = object(parseJson(files.get("state.json") ?? ""));
  const health = object(parseJson(files.get("health.json") ?? ""));
  validateHeaders(claim, state, health);
  const tape = history(files.get("ledger.jsonl") ?? "", claim, state);
  const mark = nullableDecimal(health.mark_price),
    unrealized = nullableDecimal(health.unrealized_pnl_usdc),
    equity = nullableDecimal(health.equity_usdc);
  if (mark !== null && units(mark) <= 0n) return malformed();
  if (
    units(decimal(state.position_quantity)) !== 0n &&
    mark === null &&
    (unrealized !== null || equity !== null)
  )
    return malformed();
  return {
    runId: text(claim.run_id),
    strategyId: text(claim.strategy_id),
    strategyVersion: text(claim.strategy_configuration_version),
    strategyLabel: text(claim.strategy_label),
    sourceCommit: text(claim.source_commit),
    imageDigest: text(claim.image_digest),
    status: health.status as NativePaperSnapshot["status"],
    runClosed: bool(health.run_closed),
    observedAtUtc: utc(health.observed_at_utc),
    lastEventAtUtc: health.last_event_at_utc === null ? null : utc(health.last_event_at_utc),
    positionBtc: decimal(state.position_quantity),
    averageEntry: decimal(state.average_entry_price),
    cashUsdc: decimal(state.cash_usdc),
    equityUsdc: equity,
    realizedGrossUsdc: decimal(state.realized_pnl_usdc),
    unrealizedUsdc: unrealized,
    feesUsdc: decimal(state.fees_usdc),
    markPrice: mark,
    killSwitch: text(state.kill_switch),
    killReason: nullableText(state.kill_reason),
    ...tape,
  };
}

/** Reads one operator-bound snapshot. RUNNING is never evidence of a live source. */
export function loadNativePaperProjection(
  env: NodeJS.Dict<string> = process.env,
  repoRoot: string = findRepoRoot(),
  now: string = new Date().toISOString(),
): NativePaperProjection {
  const projection: NativePaperProjection = {
    schema: NATIVE_PAPER_SCHEMA,
    origin: "unbound",
    state: "missing",
    message: "No native PAPER run is bound.",
    observed_at: now,
    snapshot: null,
  };
  try {
    requirePaperTradingMode(env.TRADING_MODE);
    const configured = env.COCKPIT_NATIVE_PAPER_RUN_DIR;
    if (configured === undefined || configured === "") return projection;
    if (!configured.trim() || !Number.isFinite(Date.parse(now))) return malformed();
    const runDir = realpathSync(resolve(configured));
    const fixture = realpathSync(join(repoRoot, "tests", "fixtures"));
    const inside = relative(fixture, runDir);
    projection.origin =
      inside !== ".." && !inside.startsWith(`..${sep}`) && !isAbsolute(inside)
        ? "demo"
        : "historical";
    const value = snapshot(readArtifacts(runDir));
    const age = Date.parse(now) - Date.parse(value.observedAtUtc);
    if (age < -1000)
      throw new ReadProblem("error", "Native PAPER snapshot has a future source timestamp.");
    projection.snapshot = value;
    projection.state = age > FRESH_MS || value.killReason === "stale_data" ? "stale" : "ok";
    projection.message =
      "Validated bounded PAPER snapshot; liveness and atomic event completion are unverified. PnL is assumed, not venue-reconciled; funding is not settled.";
    return projection;
  } catch (error: unknown) {
    if (error instanceof ReadProblem) {
      projection.state = error.state;
      projection.message = error.message;
    } else if (
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "ENOENT"
    ) {
      projection.state = "missing";
      projection.message = "A bound native PAPER directory or required artifact is missing.";
    } else {
      projection.state = "error";
      projection.message = "Native PAPER artifacts could not be read safely.";
    }
    return projection;
  }
}
