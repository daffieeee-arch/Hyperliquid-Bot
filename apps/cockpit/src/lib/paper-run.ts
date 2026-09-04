import { readFileSync } from "node:fs";

import {
  COCKPIT_ARTIFACT_SCHEMA,
  COURSE1_PATH_CONTRACT_ID,
  cockpitFilePath,
  findRepoRoot,
  resolvePaperRunDir,
  type Course1CockpitFileName,
} from "./paths";
import type {
  CaptureHealth,
  JsonObject,
  PaperFillRow,
  PaperFills,
  PaperOrderIntent,
  PaperOrders,
  PaperPosition,
  PaperPnl,
  PaperRunClaim,
  PaperRunSnapshot,
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

function requireInt(source: JsonObject, field: string, path: string): number {
  const value = source[field];
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`${path} is missing integer field ${field}.`);
  }
  return value;
}

function requireStringList(source: JsonObject, field: string, path: string): string[] {
  const value = source[field];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${path}.${field} must be a list of strings.`);
  }
  return value;
}

function requireObjectList(source: JsonObject, field: string, path: string): JsonObject[] {
  const value = source[field];
  if (!Array.isArray(value) || value.some((item) => !isRecord(item))) {
    throw new Error(`${path}.${field} must be a list of objects.`);
  }
  return value;
}

function requirePaperMode(source: JsonObject, path: string): "PAPER" {
  const mode = source.mode;
  if (mode !== "PAPER") {
    throw new Error(`${path} is not a PAPER artifact and the cockpit fails closed.`);
  }
  return "PAPER";
}

function requireContract(source: JsonObject, path: string): void {
  if (source.schema !== COCKPIT_ARTIFACT_SCHEMA) {
    throw new Error(`${path} schema is not ${COCKPIT_ARTIFACT_SCHEMA}.`);
  }
  if (source.path_contract !== COURSE1_PATH_CONTRACT_ID) {
    throw new Error(`${path} path_contract is not ${COURSE1_PATH_CONTRACT_ID}.`);
  }
}

function loadClaim(runDir: string): PaperRunClaim {
  const path = cockpitFilePath(runDir, "run-claim.json");
  const raw = readJsonObject(path);
  const claim: PaperRunClaim = {
    mode: requirePaperMode(raw, path),
    run_id: requireText(raw, "run_id", path),
    schema: requireText(raw, "schema", path),
  };
  if (typeof raw.seconds === "number" && Number.isInteger(raw.seconds)) {
    claim.seconds = raw.seconds;
  }
  if (typeof raw.feed === "string") {
    claim.feed = raw.feed;
  }
  if (typeof raw.state === "string") {
    claim.state = raw.state;
  }
  if (typeof raw.d22b_venue_authoritative_reconciliation === "boolean") {
    claim.d22b_venue_authoritative_reconciliation = raw.d22b_venue_authoritative_reconciliation;
  }
  return claim;
}

function loadPosition(runDir: string): PaperPosition {
  const path = cockpitFilePath(runDir, "paper-position.json");
  const raw = readJsonObject(path);
  requireContract(raw, path);
  if (raw.kind !== "paper-position") {
    throw new Error(`${path} kind is not paper-position.`);
  }
  return {
    kind: "paper-position",
    schema: COCKPIT_ARTIFACT_SCHEMA,
    path_contract: COURSE1_PATH_CONTRACT_ID,
    mode: requirePaperMode(raw, path),
    instrument_id: requireText(raw, "instrument_id", path),
    final_position_btc: requireText(raw, "final_position_btc", path),
    business_final_position_quantity: requireText(raw, "business_final_position_quantity", path),
    venue_authoritative: requireBoolean(raw, "venue_authoritative", path),
    twenty_four_seven: requireBoolean(raw, "twenty_four_seven", path),
    limitations: requireStringList(raw, "limitations", path),
  };
}

function loadPnl(runDir: string): PaperPnl {
  const path = cockpitFilePath(runDir, "paper-pnl.json");
  const raw = readJsonObject(path);
  requireContract(raw, path);
  if (raw.kind !== "paper-pnl") {
    throw new Error(`${path} kind is not paper-pnl.`);
  }
  const assumed = requireBoolean(raw, "assumed", path);
  const venuePnl = requireBoolean(raw, "venue_pnl", path);
  if (!assumed || venuePnl) {
    throw new Error(`${path} must be assumed overlay PnL, not venue PnL.`);
  }
  return {
    kind: "paper-pnl",
    schema: COCKPIT_ARTIFACT_SCHEMA,
    path_contract: COURSE1_PATH_CONTRACT_ID,
    mode: requirePaperMode(raw, path),
    assumed,
    venue_pnl: venuePnl,
    instrument_id: requireText(raw, "instrument_id", path),
    starting_cash_usdc_assumed: requireText(raw, "starting_cash_usdc_assumed", path),
    ending_cash_usdc_assumed: requireText(raw, "ending_cash_usdc_assumed", path),
    ending_equity_usdc_assumed: requireText(raw, "ending_equity_usdc_assumed", path),
    net_pnl_usdc_assumed: requireText(raw, "net_pnl_usdc_assumed", path),
    fee_cost_usdc: requireText(raw, "fee_cost_usdc", path),
    half_spread_cost_usdc: requireText(raw, "half_spread_cost_usdc", path),
    slippage_cost_usdc: requireText(raw, "slippage_cost_usdc", path),
    funding_payment_usdc: requireText(raw, "funding_payment_usdc", path),
    mark_price: requireText(raw, "mark_price", path),
    final_position_btc: requireText(raw, "final_position_btc", path),
    limitations: requireStringList(raw, "limitations", path),
  };
}

function loadHealth(runDir: string): CaptureHealth {
  const path = cockpitFilePath(runDir, "capture-health.json");
  const raw = readJsonObject(path);
  requireContract(raw, path);
  if (raw.kind !== "capture-health") {
    throw new Error(`${path} kind is not capture-health.`);
  }
  const twentyFourSeven = requireBoolean(raw, "twenty_four_seven", path);
  if (twentyFourSeven) {
    throw new Error(`${path} claims 24/7 service; the first screen refuses that.`);
  }
  return {
    kind: "capture-health",
    schema: COCKPIT_ARTIFACT_SCHEMA,
    path_contract: COURSE1_PATH_CONTRACT_ID,
    feed: requireText(raw, "feed", path),
    status: requireText(raw, "status", path),
    credentialless: requireBoolean(raw, "credentialless", path),
    twenty_four_seven: twentyFourSeven,
    trade_count: requireInt(raw, "trade_count", path),
    bbo_count: requireInt(raw, "bbo_count", path),
    adapter_rejected_count: requireInt(raw, "adapter_rejected_count", path),
    risk_rejections: requireInt(raw, "risk_rejections", path),
    subscriptions_acknowledged: requireStringList(raw, "subscriptions_acknowledged", path),
    limitations: requireStringList(raw, "limitations", path),
  };
}

function loadOrderIntents(raw: JsonObject, path: string): PaperOrderIntent[] {
  return requireObjectList(raw, "orders", path).map((item, index) => {
    const rowPath = `${path}.orders[${String(index)}]`;
    return {
      client_order_id: requireText(item, "client_order_id", rowPath),
      side: requireText(item, "side", rowPath),
      quantity: requireText(item, "quantity", rowPath),
      order_type: requireText(item, "order_type", rowPath),
      reason: requireText(item, "reason", rowPath),
      reduce_only: requireBoolean(item, "reduce_only", rowPath),
    };
  });
}

function loadFillRows(raw: JsonObject, path: string): PaperFillRow[] {
  return requireObjectList(raw, "fills", path).map((item, index) => {
    const rowPath = `${path}.fills[${String(index)}]`;
    return {
      fill_ordinal: requireInt(item, "fill_ordinal", rowPath),
      side: requireText(item, "side", rowPath),
      price: requireText(item, "price", rowPath),
      quantity: requireText(item, "quantity", rowPath),
      liquidity_side: requireText(item, "liquidity_side", rowPath),
      position_after: requireText(item, "position_after", rowPath),
    };
  });
}

function loadOrders(runDir: string): PaperOrders {
  const path = cockpitFilePath(runDir, "orders.json");
  const raw = readJsonObject(path);
  requireContract(raw, path);
  if (raw.kind !== "orders") {
    throw new Error(`${path} kind is not orders.`);
  }
  const intents = loadOrderIntents(raw, path);
  const orderCount = requireInt(raw, "order_count", path);
  if (orderCount !== intents.length) {
    throw new Error(`${path} order_count does not match the copied intents list.`);
  }
  return {
    kind: "orders",
    schema: COCKPIT_ARTIFACT_SCHEMA,
    path_contract: COURSE1_PATH_CONTRACT_ID,
    mode: requirePaperMode(raw, path),
    venue_orders_submitted: requireBoolean(raw, "venue_orders_submitted", path),
    order_count: orderCount,
    intents,
    limitations: requireStringList(raw, "limitations", path),
  };
}

function loadFills(runDir: string): PaperFills {
  const path = cockpitFilePath(runDir, "fills.json");
  const raw = readJsonObject(path);
  requireContract(raw, path);
  if (raw.kind !== "fills") {
    throw new Error(`${path} kind is not fills.`);
  }
  const fills = loadFillRows(raw, path);
  const fillCount = requireInt(raw, "fill_count", path);
  if (fillCount !== fills.length) {
    throw new Error(`${path} fill_count does not match the copied fills list.`);
  }
  return {
    kind: "fills",
    schema: COCKPIT_ARTIFACT_SCHEMA,
    path_contract: COURSE1_PATH_CONTRACT_ID,
    mode: requirePaperMode(raw, path),
    fill_count: fillCount,
    fills,
    limitations: requireStringList(raw, "limitations", path),
  };
}

export function requiredCockpitFiles(): Course1CockpitFileName[] {
  return [
    "run-claim.json",
    "paper-position.json",
    "paper-pnl.json",
    "orders.json",
    "fills.json",
    "capture-health.json",
  ];
}

export function loadPaperRunSnapshot(
  env: NodeJS.Dict<string> = process.env,
  repoRoot: string = findRepoRoot(),
): PaperRunSnapshot {
  const resolved = resolvePaperRunDir(env, repoRoot);
  const claim = loadClaim(resolved.runDir);
  if (resolved.source !== "paper-run-dir" && claim.run_id !== resolved.runId) {
    throw new Error(
      `run-claim.json run_id ${claim.run_id} does not match resolved run_id ${resolved.runId}.`,
    );
  }
  return {
    runDir: resolved.runDir,
    runId: claim.run_id,
    source: resolved.source,
    claim,
    position: loadPosition(resolved.runDir),
    pnl: loadPnl(resolved.runDir),
    health: loadHealth(resolved.runDir),
    orders: loadOrders(resolved.runDir),
    fills: loadFills(resolved.runDir),
  };
}
