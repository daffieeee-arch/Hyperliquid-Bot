import type { PaperFillRow, PaperOrderIntent } from "./types";

export const RISK_REASON_UNAVAILABLE = "UNAVAILABLE";
export const RISK_REASON_UNAVAILABLE_SOURCE =
  "orders.json has no paper_risk reason codes; codes are not invented";

export type IntentFillRow = {
  clientOrderId: string;
  side: string;
  quantity: string;
  orderType: string;
  intentReason: string;
  reduceOnly: boolean;
  riskReasons: string;
  riskReasonSource: string;
  fillOrdinal: string;
  fillPrice: string;
  fillLiquidity: string;
  positionAfter: string;
  matched: boolean;
};

function copiedRiskReasons(intent: PaperOrderIntent): { value: string; source: string } {
  if (intent.risk_reasons === undefined || intent.risk_reasons.length === 0) {
    return { value: RISK_REASON_UNAVAILABLE, source: RISK_REASON_UNAVAILABLE_SOURCE };
  }
  return {
    value: intent.risk_reasons.join(" · "),
    source: "orders.json paper_risk reason codes",
  };
}

function sameSideAndQty(intent: PaperOrderIntent, fill: PaperFillRow): boolean {
  return intent.side === fill.side && intent.quantity === fill.quantity;
}

export function joinIntentsToFills(
  intents: readonly PaperOrderIntent[],
  fills: readonly PaperFillRow[],
): IntentFillRow[] {
  const usedFills = new Set<number>();
  return intents.map((intent, index) => {
    const reasons = copiedRiskReasons(intent);
    const candidate = fills[index];
    const matched =
      candidate !== undefined && !usedFills.has(index) && sameSideAndQty(intent, candidate);
    if (matched && candidate !== undefined) {
      usedFills.add(index);
      return {
        clientOrderId: intent.client_order_id,
        side: intent.side,
        quantity: intent.quantity,
        orderType: intent.order_type,
        intentReason: intent.reason,
        reduceOnly: intent.reduce_only,
        riskReasons: reasons.value,
        riskReasonSource: reasons.source,
        fillOrdinal: String(candidate.fill_ordinal),
        fillPrice: candidate.price,
        fillLiquidity: candidate.liquidity_side,
        positionAfter: candidate.position_after,
        matched: true,
      };
    }
    return {
      clientOrderId: intent.client_order_id,
      side: intent.side,
      quantity: intent.quantity,
      orderType: intent.order_type,
      intentReason: intent.reason,
      reduceOnly: intent.reduce_only,
      riskReasons: reasons.value,
      riskReasonSource: reasons.source,
      fillOrdinal: RISK_REASON_UNAVAILABLE,
      fillPrice: RISK_REASON_UNAVAILABLE,
      fillLiquidity: RISK_REASON_UNAVAILABLE,
      positionAfter: RISK_REASON_UNAVAILABLE,
      matched: false,
    };
  });
}
