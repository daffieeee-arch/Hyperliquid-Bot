import type { DataState } from "./data-state";

export const NATIVE_PAPER_SCHEMA = "native-paper-cockpit-v1";

export type NativePaperOrder = {
  ordinal: number;
  id: string;
  side: "BUY" | "SELL";
  quantity: string;
  filledQuantity: string;
  status: "FILLED" | "PARTIALLY_FILLED" | "CANCELED";
  reason: string;
  reduceOnly: boolean;
};

export type NativePaperFill = {
  ordinal: number;
  orderId: string;
  side: "BUY" | "SELL";
  quantity: string;
  price: string;
  feeUsdc: string;
  source: string;
};

export type NativePaperRejection = {
  ordinal: number;
  reason: string;
  detail: string | null;
};

export type NativePaperSnapshot = {
  runId: string;
  strategyId: string;
  strategyVersion: string;
  strategyLabel: string;
  sourceCommit: string;
  imageDigest: string;
  status: "RUNNING" | "COMPLETED" | "HALT_NEW" | "FLATTEN_HALT";
  runClosed: boolean;
  observedAtUtc: string;
  lastEventAtUtc: string | null;
  positionBtc: string;
  averageEntry: string;
  cashUsdc: string;
  equityUsdc: string | null;
  realizedGrossUsdc: string;
  unrealizedUsdc: string | null;
  feesUsdc: string;
  markPrice: string | null;
  killSwitch: string;
  killReason: string | null;
  openOrder: string | null;
  ledgerRecords: number;
  orders: NativePaperOrder[];
  fills: NativePaperFill[];
  rejections: NativePaperRejection[];
};

export type NativePaperProjection = {
  schema: typeof NATIVE_PAPER_SCHEMA;
  origin: "unbound" | "demo" | "historical";
  state: DataState;
  message: string;
  observed_at: string;
  snapshot: NativePaperSnapshot | null;
};
