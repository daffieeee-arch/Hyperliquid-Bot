import { joinIntentsToFills, type IntentFillRow } from "./intent-fill";
import { paperRiskRejectionView, type PaperRiskRejectionView } from "./paper-risk-gates";
import type { PaperRunSnapshot } from "./types";

export const PAPER_BOT_UNAVAILABLE = "UNAVAILABLE";
export const NOT_VENUE_RECONCILED = "not venue-reconciled";
export const ASSUMED_OVERLAY_NOT_VENUE_PNL =
  "assumed overlay · not venue-reconciled · not venue PnL";

export type PaperBotWhat = {
  strategyClass: string;
  instrument: string;
  positionBtc: string;
  positionLabel: string;
  intentCount: string;
  fillCount: string;
  venueOrdersSubmitted: string;
};

export type PaperBotWhy = {
  intentReasons: string[];
  preflightStrategy: string;
  sameD01SmokeRisk: string;
  limitations: string[];
};

export type PaperBotResults = {
  assumedNetPnl: string;
  assumedPnlLabel: string;
  endingEquity: string;
  soakMark: string;
  venuePnl: string;
};

export type PreflightCapsView = {
  available: boolean;
  strategyClass: string;
  orderQuantityBtc: string;
  maxEntryNotionalUsdc: string;
  maxAssumedLossUsdc: string;
  sameD01SmokeRisk: string;
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
  riskRejections: PaperRiskRejectionView;
};

const MISSING_PREFLIGHT = "run-claim.json preflight omitted";

function unavailableWhat(): PaperBotWhat {
  return {
    strategyClass: PAPER_BOT_UNAVAILABLE,
    instrument: PAPER_BOT_UNAVAILABLE,
    positionBtc: PAPER_BOT_UNAVAILABLE,
    positionLabel: NOT_VENUE_RECONCILED,
    intentCount: PAPER_BOT_UNAVAILABLE,
    fillCount: PAPER_BOT_UNAVAILABLE,
    venueOrdersSubmitted: PAPER_BOT_UNAVAILABLE,
  };
}

function unavailableWhy(error: string): PaperBotWhy {
  return {
    intentReasons: [],
    preflightStrategy: PAPER_BOT_UNAVAILABLE,
    sameD01SmokeRisk: PAPER_BOT_UNAVAILABLE,
    limitations: [error],
  };
}

function unavailableResults(): PaperBotResults {
  return {
    assumedNetPnl: PAPER_BOT_UNAVAILABLE,
    assumedPnlLabel: ASSUMED_OVERLAY_NOT_VENUE_PNL,
    endingEquity: PAPER_BOT_UNAVAILABLE,
    soakMark: PAPER_BOT_UNAVAILABLE,
    venuePnl: "no",
  };
}

function unavailablePreflight(): PreflightCapsView {
  return {
    available: false,
    strategyClass: PAPER_BOT_UNAVAILABLE,
    orderQuantityBtc: PAPER_BOT_UNAVAILABLE,
    maxEntryNotionalUsdc: PAPER_BOT_UNAVAILABLE,
    maxAssumedLossUsdc: PAPER_BOT_UNAVAILABLE,
    sameD01SmokeRisk: PAPER_BOT_UNAVAILABLE,
    source: MISSING_PREFLIGHT,
  };
}

export function buildPreflightCaps(snapshot: PaperRunSnapshot | undefined): PreflightCapsView {
  const preflight = snapshot?.claim.preflight;
  if (preflight === undefined) {
    return unavailablePreflight();
  }
  return {
    available: true,
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
      riskRejections: paperRiskRejectionView(undefined),
    };
  }
  const preflight = buildPreflightCaps(snapshot);
  return {
    available: true,
    error: undefined,
    what: {
      strategyClass: preflight.strategyClass,
      instrument: snapshot.position.instrument_id,
      positionBtc: `${snapshot.position.final_position_btc} BTC`,
      positionLabel: NOT_VENUE_RECONCILED,
      intentCount: String(snapshot.orders.order_count),
      fillCount: String(snapshot.fills.fill_count),
      venueOrdersSubmitted: snapshot.orders.venue_orders_submitted ? "yes" : "no",
    },
    why: {
      intentReasons: snapshot.orders.intents.map((intent) => intent.reason),
      preflightStrategy: preflight.strategyClass,
      sameD01SmokeRisk: preflight.sameD01SmokeRisk,
      limitations: [
        ...snapshot.position.limitations,
        ...snapshot.orders.limitations,
        ...snapshot.fills.limitations,
      ],
    },
    results: {
      assumedNetPnl: `${snapshot.pnl.net_pnl_usdc_assumed} USDC`,
      assumedPnlLabel: ASSUMED_OVERLAY_NOT_VENUE_PNL,
      endingEquity: `${snapshot.pnl.ending_equity_usdc_assumed} USDC`,
      soakMark: snapshot.pnl.mark_price,
      venuePnl: snapshot.pnl.venue_pnl ? "yes" : "no",
    },
    preflight,
    tape: joinIntentsToFills(snapshot.orders.intents, snapshot.fills.fills),
    riskRejections: paperRiskRejectionView(snapshot.health.risk_rejections),
  };
}
