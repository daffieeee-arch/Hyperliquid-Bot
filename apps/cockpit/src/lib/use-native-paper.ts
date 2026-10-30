"use client";

import { DATA_STATES } from "./data-state";
import { NATIVE_PAPER_SCHEMA, type NativePaperProjection } from "./native-paper-model";
import type { PollState } from "./poll-state";
import { usePoll } from "./use-poll";

export const NATIVE_PAPER_REQUEST_URL = "/api/native-paper-run";

function rowsMatch(
  raw: unknown,
  strings: string[],
  kind: "orders" | "fills" | "rejections",
): boolean {
  return (
    Array.isArray(raw) &&
    raw.every((value: unknown) => {
      if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
      const row = value as Record<string, unknown>;
      if (
        !Number.isSafeInteger(row.ordinal) ||
        (row.ordinal as number) < 1 ||
        !strings.every((key) => typeof row[key] === "string")
      )
        return false;
      if (kind === "rejections") return row.detail === null || typeof row.detail === "string";
      if (row.side !== "BUY" && row.side !== "SELL") return false;
      return (
        kind === "fills" ||
        (typeof row.reduceOnly === "boolean" &&
          ["FILLED", "PARTIALLY_FILLED", "CANCELED"].includes(String(row.status)))
      );
    })
  );
}

function snapshotEnvelope(value: unknown): boolean {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const raw = value as Record<string, unknown>;
  const strings = [
    "runId",
    "strategyId",
    "strategyVersion",
    "strategyLabel",
    "sourceCommit",
    "imageDigest",
    "observedAtUtc",
    "positionBtc",
    "averageEntry",
    "cashUsdc",
    "realizedGrossUsdc",
    "feesUsdc",
    "killSwitch",
  ];
  const nullable = [
    "lastEventAtUtc",
    "equityUsdc",
    "unrealizedUsdc",
    "markPrice",
    "killReason",
    "openOrder",
  ];
  return (
    strings.every((key) => typeof raw[key] === "string") &&
    nullable.every((key) => raw[key] === null || typeof raw[key] === "string") &&
    ["RUNNING", "COMPLETED", "HALT_NEW", "FLATTEN_HALT"].includes(String(raw.status)) &&
    typeof raw.runClosed === "boolean" &&
    Number.isSafeInteger(raw.ledgerRecords) &&
    (raw.ledgerRecords as number) >= 0 &&
    rowsMatch(raw.orders, ["id", "quantity", "filledQuantity", "reason"], "orders") &&
    rowsMatch(raw.fills, ["orderId", "quantity", "price", "feeUsdc", "source"], "fills") &&
    rowsMatch(raw.rejections, ["reason"], "rejections")
  );
}

/** The server validates source artifacts; the browser checks its versioned envelope. */
export function parseNativePaperResponse(payload: unknown): NativePaperProjection {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("schema" in payload) ||
    payload.schema !== NATIVE_PAPER_SCHEMA ||
    !("origin" in payload) ||
    !["unbound", "demo", "historical"].includes(String(payload.origin)) ||
    !("state" in payload) ||
    !DATA_STATES.some((state) => state === payload.state) ||
    !("message" in payload) ||
    typeof payload.message !== "string" ||
    !("observed_at" in payload) ||
    typeof payload.observed_at !== "string" ||
    !Number.isFinite(Date.parse(payload.observed_at)) ||
    !("snapshot" in payload) ||
    (payload.snapshot !== null &&
      (!snapshotEnvelope(payload.snapshot) ||
        !["ok", "stale"].includes(String(payload.state)) ||
        payload.origin === "unbound"))
  ) {
    throw new Error("Native PAPER response violates its versioned contract.");
  }
  return payload as NativePaperProjection;
}

export async function fetchNativePaperProjection(): Promise<NativePaperProjection> {
  const response = await fetch(NATIVE_PAPER_REQUEST_URL, { cache: "no-store" });
  if (!response.ok) throw new Error("Native PAPER read failed; previous values are not current.");
  const payload: unknown = await response.json();
  return parseNativePaperResponse(payload);
}

export function useNativePaper(
  initial: NativePaperProjection,
  refreshToken = 0,
): PollState<NativePaperProjection> {
  return usePoll(NATIVE_PAPER_REQUEST_URL, fetchNativePaperProjection, initial, refreshToken);
}
