import { formatAssumedUsdcDisplay } from "./display";
import { joinIntentsToFills, RISK_REASON_UNAVAILABLE, type IntentFillRow } from "./intent-fill";
import { COURSE1_PATH_CONTRACT_ID, COURSE1_RELATIVE_PREFIX } from "./paths";
import {
  DOCUMENTED_CAPS_SOURCE,
  DOCUMENTED_MAX_GROSS,
  DOCUMENTED_MAX_LEVERAGE,
  DOCUMENTED_MAX_NET,
  DOCUMENTED_MAX_POSITIONS,
  DOCUMENTED_RISK_PER_TRADE,
  paperRiskRejectionView,
  resolvePaperRiskGate,
  type PaperRiskRejectionView,
} from "./paper-risk-gates";
import type { PaperFillRow, PaperRunSnapshot } from "./types";

export const PAPER_BOT_UNAVAILABLE = "UNAVAILABLE";
export const NOT_VENUE_RECONCILED = "not venue-reconciled";
export const ASSUMED_PNL_LABEL = "assumed_pnl · not venue-reconciled · D22-B blocked";
export const ASSUMED_OVERLAY_NOT_VENUE_PNL = ASSUMED_PNL_LABEL;
export const COURSE1_SOAK_KIND = "COURSE-1 soak";
export const TAPE_LAST_N = 8;
export const D22B_BLOCKED = "blocked";

export type PaperBotWhat = {
  kind: typeof COURSE1_SOAK_KIND;
  mode: "PAPER";
  runId: string;
  claimPath: string;
  pathContract: string;
  instrument: string;
  strategyClass: string;
  venueOrdersSubmitted: string;
};

export type LastDecisionOutcome = "ACCEPT" | "REJECT" | "UNAVAILABLE";

export type PaperBotWhy = {
  outcome: LastDecisionOutcome;
  gateName: string;
  reason: string;
  intentId: string;
  source: string;
};

export type PaperBotResults = {
  side: string;
  size: string;
  entry: string;
  mark: string;
  assumedPnl: string;
  assumedPnlExact: string;
  assumedPnlLabel: string;
  assumedNetPnl: string;
  endingEquity: string;
  d22b: string;
  venueReconciled: typeof NOT_VENUE_RECONCILED;
  venuePnl: string;
};

export type PreflightCapsView = {
  available: boolean;
  equity: string;
  riskPerTrade: string;
  maxLeverage: string;
  maxGross: string;
  maxNet: string;
  maxPositions: string;
  strategyClass: string;
  orderQuantityBtc: string;
  maxEntryNotionalUsdc: string;
  maxAssumedLossUsdc: string;
  sameD01SmokeRisk: string;
  documentedSource: string;
  source: string;
};

export type PaperBotView = {
  available: boolean;
  error: string | undefined;
  what: PaperBotWhat;
  why: PaperBotWhy;
  results: PaperBotResults;
  preflight: PreflightCapsView;
  tape: IntentFillRow[];
  tapeLimit: number;
  tapeRejects: TapeRejectSummary;
  gateExamples: PaperRiskGateExample[];
  riskRejections: PaperRiskRejectionView;
};

const MISSING_PREFLIGHT = "run-claim.json preflight omitted";
const LAST_DECISION_MISSING =
  "orders.json has no last paper_risk outcome; ACCEPT/REJECT is not invented";

export const GATE_EXAMPLE_SOURCE =
  "documented #65 / D01 paper_risk catalog · illustrative shape · never mixed into run history";

export type PaperRiskGateExample = {
  gateCode: string;
  label: string;
  reason: string;
  onBreach: string;
  source: typeof GATE_EXAMPLE_SOURCE;
};

/**
 * Illustrative gate rows for the "how a reject reads" reference card.
 *
 * These describe the documented catalog only. They are deliberately a separate
 * type from `IntentFillRow` so an example can never be appended to a real
 * intent tape: run history stays exactly what `orders.json` recorded.
 */
export function paperRiskGateExamples(): PaperRiskGateExample[] {
  return ["risk_based_size", "drawdown_kill", "no_averaging_down"].flatMap((id) => {
    const gate = resolvePaperRiskGate(id);
    if (gate === undefined) {
      return [];
    }
    return [
      {
        gateCode: gate.id,
        label: gate.label,
        reason: gate.reason,
        onBreach: gate.onBreach,
        source: GATE_EXAMPLE_SOURCE,
      },
    ];
  });
}

export type TapeRejectSummary = {
  /** Rejects present in the copied run history. */
  count: number;
  /** Gate codes recorded on those rejects. */
  gateCodes: string[];
  /** True when the run recorded no reject at all, so the tape shows none. */
  none: boolean;
  note: string;
};

export function summariseTapeRejects(rows: readonly IntentFillRow[]): TapeRejectSummary {
  const rejects = rows.filter((row) => row.outcome === "REJECT");
  const gateCodes = [
    ...new Set(
      rejects.map((row) => row.gateCode).filter((code) => code !== RISK_REASON_UNAVAILABLE),
    ),
  ];
  return {
    count: rejects.length,
    gateCodes,
    none: rejects.length === 0,
    note:
      rejects.length === 0
        ? "This run recorded no paper_risk reject. The catalog reference below shows how one reads."
        : `${String(rejects.length)} reject row(s) copied from orders.json risk_reasons.`,
  };
}

export function course1SoakClaimPath(runId: string): string {
  return `${COURSE1_RELATIVE_PREFIX.join("/")}/${runId}`;
}

function isFlatPosition(quantity: string): boolean {
  const value = Number(quantity);
  return !Number.isFinite(value) || value === 0;
}

function paperSide(quantity: string): string {
  const value = Number(quantity);
  if (!Number.isFinite(value) || value === 0) {
    return "FLAT";
  }
  return value > 0 ? "LONG" : "SHORT";
}

function copiedEntryPrice(size: string, fills: readonly PaperFillRow[]): string {
  if (isFlatPosition(size)) {
    return PAPER_BOT_UNAVAILABLE;
  }
  const match = [...fills].reverse().find((fill) => fill.position_after === size);
  return match?.price ?? PAPER_BOT_UNAVAILABLE;
}

function unavailableWhat(): PaperBotWhat {
  return {
    kind: COURSE1_SOAK_KIND,
    mode: "PAPER",
    runId: PAPER_BOT_UNAVAILABLE,
    claimPath: PAPER_BOT_UNAVAILABLE,
    pathContract: COURSE1_PATH_CONTRACT_ID,
    instrument: PAPER_BOT_UNAVAILABLE,
    strategyClass: PAPER_BOT_UNAVAILABLE,
    venueOrdersSubmitted: PAPER_BOT_UNAVAILABLE,
  };
}

function unavailableWhy(error: string): PaperBotWhy {
  return {
    outcome: "UNAVAILABLE",
    gateName: PAPER_BOT_UNAVAILABLE,
    reason: error,
    intentId: PAPER_BOT_UNAVAILABLE,
    source: LAST_DECISION_MISSING,
  };
}

function unavailableResults(): PaperBotResults {
  return {
    side: PAPER_BOT_UNAVAILABLE,
    size: PAPER_BOT_UNAVAILABLE,
    entry: PAPER_BOT_UNAVAILABLE,
    mark: PAPER_BOT_UNAVAILABLE,
    assumedPnl: PAPER_BOT_UNAVAILABLE,
    assumedPnlExact: PAPER_BOT_UNAVAILABLE,
    assumedPnlLabel: ASSUMED_PNL_LABEL,
    assumedNetPnl: PAPER_BOT_UNAVAILABLE,
    endingEquity: PAPER_BOT_UNAVAILABLE,
    d22b: D22B_BLOCKED,
    venueReconciled: NOT_VENUE_RECONCILED,
    venuePnl: "no",
  };
}

function unavailablePreflight(): PreflightCapsView {
  return {
    available: false,
    equity: PAPER_BOT_UNAVAILABLE,
    riskPerTrade: DOCUMENTED_RISK_PER_TRADE,
    maxLeverage: DOCUMENTED_MAX_LEVERAGE,
    maxGross: DOCUMENTED_MAX_GROSS,
    maxNet: DOCUMENTED_MAX_NET,
    maxPositions: DOCUMENTED_MAX_POSITIONS,
    strategyClass: PAPER_BOT_UNAVAILABLE,
    orderQuantityBtc: PAPER_BOT_UNAVAILABLE,
    maxEntryNotionalUsdc: PAPER_BOT_UNAVAILABLE,
    maxAssumedLossUsdc: PAPER_BOT_UNAVAILABLE,
    sameD01SmokeRisk: PAPER_BOT_UNAVAILABLE,
    documentedSource: DOCUMENTED_CAPS_SOURCE,
    source: MISSING_PREFLIGHT,
  };
}

export function lastTapeRows(
  rows: readonly IntentFillRow[],
  limit: number = TAPE_LAST_N,
): IntentFillRow[] {
  if (rows.length <= limit) {
    return [...rows];
  }
  return rows.slice(rows.length - limit);
}

export function buildLastDecision(tape: readonly IntentFillRow[]): PaperBotWhy {
  const last = tape[tape.length - 1];
  if (last === undefined) {
    return unavailableWhy(LAST_DECISION_MISSING);
  }
  if (last.outcome === "REJECT") {
    const gate = resolvePaperRiskGate(last.gateCode) ?? resolvePaperRiskGate(last.riskReasons);
    return {
      outcome: "REJECT",
      gateName: gate?.id ?? last.gateCode,
      reason: gate?.reason ?? last.riskReasons,
      intentId: last.clientOrderId,
      source: last.riskReasonSource,
    };
  }
  if (last.outcome === "ACCEPT") {
    return {
      outcome: "ACCEPT",
      gateName: PAPER_BOT_UNAVAILABLE,
      reason: last.intentReason,
      intentId: last.clientOrderId,
      source: "orders.json + fills.json · last PAPER intent filled",
    };
  }
  return {
    outcome: "UNAVAILABLE",
    gateName: PAPER_BOT_UNAVAILABLE,
    reason: LAST_DECISION_MISSING,
    intentId: last.clientOrderId,
    source: last.riskReasonSource,
  };
}

export function buildPreflightCaps(snapshot: PaperRunSnapshot | undefined): PreflightCapsView {
  const documented = {
    riskPerTrade: DOCUMENTED_RISK_PER_TRADE,
    maxLeverage: DOCUMENTED_MAX_LEVERAGE,
    maxGross: DOCUMENTED_MAX_GROSS,
    maxNet: DOCUMENTED_MAX_NET,
    maxPositions: DOCUMENTED_MAX_POSITIONS,
    documentedSource: DOCUMENTED_CAPS_SOURCE,
  };
  if (snapshot === undefined) {
    return unavailablePreflight();
  }
  const preflight = snapshot.claim.preflight;
  if (preflight === undefined) {
    return {
      ...unavailablePreflight(),
      equity: `${snapshot.pnl.starting_cash_usdc_assumed} USDC assumed`,
      ...documented,
    };
  }
  return {
    available: true,
    equity: `${snapshot.pnl.starting_cash_usdc_assumed} USDC assumed`,
    ...documented,
    strategyClass: preflight.strategy_class,
    orderQuantityBtc: `${preflight.order_quantity_btc} BTC`,
    maxEntryNotionalUsdc: `${preflight.max_entry_notional_usdc} USDC`,
    maxAssumedLossUsdc: `${preflight.max_assumed_loss_usdc} USDC`,
    sameD01SmokeRisk: preflight.same_d01_smoke_risk ? "yes" : "no",
    source: "run-claim.json preflight · fail-closed soak caps",
  };
}

export function buildPaperBotView(
  snapshot: PaperRunSnapshot | undefined,
  snapshotError: string | undefined,
): PaperBotView {
  if (snapshot === undefined) {
    const error = snapshotError ?? "PAPER run data is unavailable.";
    return {
      available: false,
      error,
      what: unavailableWhat(),
      why: unavailableWhy(error),
      results: unavailableResults(),
      preflight: unavailablePreflight(),
      tape: [],
      tapeLimit: TAPE_LAST_N,
      tapeRejects: summariseTapeRejects([]),
      gateExamples: paperRiskGateExamples(),
      riskRejections: paperRiskRejectionView(undefined),
    };
  }
  if (snapshot.pnl.venue_pnl || snapshot.claim.d22b_venue_authoritative_reconciliation === true) {
    const error = "DESK COURSE-1 card refuses venue-reconciled / D22-B PnL.";
    return {
      available: false,
      error,
      what: unavailableWhat(),
      why: unavailableWhy(error),
      results: unavailableResults(),
      preflight: unavailablePreflight(),
      tape: [],
      tapeLimit: TAPE_LAST_N,
      tapeRejects: summariseTapeRejects([]),
      gateExamples: paperRiskGateExamples(),
      riskRejections: paperRiskRejectionView(snapshot.health.risk_rejections),
    };
  }
  const soakTape = joinIntentsToFills(snapshot.orders.intents, snapshot.fills.fills);
  const tape = lastTapeRows(soakTape);
  const preflight = buildPreflightCaps(snapshot);
  const size = `${snapshot.position.final_position_btc} BTC`;
  return {
    available: true,
    error: undefined,
    what: {
      kind: COURSE1_SOAK_KIND,
      mode: "PAPER",
      runId: snapshot.runId,
      claimPath: course1SoakClaimPath(snapshot.runId),
      pathContract: snapshot.position.path_contract,
      instrument: snapshot.position.instrument_id,
      strategyClass: preflight.strategyClass,
      venueOrdersSubmitted: snapshot.orders.venue_orders_submitted ? "yes" : "no",
    },
    why: buildLastDecision(lastTapeRows(soakTape)),
    results: {
      side: paperSide(snapshot.position.final_position_btc),
      size,
      entry: copiedEntryPrice(snapshot.position.final_position_btc, snapshot.fills.fills),
      mark: snapshot.pnl.mark_price,
      assumedPnl: `${formatAssumedUsdcDisplay(snapshot.pnl.net_pnl_usdc_assumed)} USDC`,
      assumedPnlExact: `${snapshot.pnl.net_pnl_usdc_assumed} USDC`,
      assumedPnlLabel: ASSUMED_PNL_LABEL,
      assumedNetPnl: `${snapshot.pnl.net_pnl_usdc_assumed} USDC`,
      endingEquity: `${snapshot.pnl.ending_equity_usdc_assumed} USDC`,
      d22b: D22B_BLOCKED,
      venueReconciled: NOT_VENUE_RECONCILED,
      venuePnl: "no",
    },
    preflight,
    tape,
    tapeLimit: TAPE_LAST_N,
    tapeRejects: summariseTapeRejects(tape),
    gateExamples: paperRiskGateExamples(),
    riskRejections: paperRiskRejectionView(snapshot.health.risk_rejections),
  };
}
