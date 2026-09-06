export const RISK_UNAVAILABLE = "UNAVAILABLE";

export const PAPER_RISK_SOURCE = "hyperliquid_bot.paper_risk · documented PAPER gates (#65)";

export const HALT_STATE_UNAVAILABLE_REASON =
  "COURSE-1 JSON has no daily/weekly/drawdown snapshot; halt state is not invented.";

export const REDUCE_ONLY_AFTER_HALT_RULE =
  "Halt gates block new entries; reduce-only flatten is still allowed.";

export type PaperRiskGateKind = "entry" | "halt";

export type PaperRiskGate = {
  id: string;
  label: string;
  defaultCap: string;
  onBreach: string;
  kind: PaperRiskGateKind;
  reason: string;
};

export const PAPER_HARD_LIMIT_GATES: readonly PaperRiskGate[] = [
  {
    id: "risk_based_size",
    label: "Risk-based size",
    defaultCap: "equity × 0.25% / stop",
    onBreach: "reject entry",
    kind: "entry",
    reason: "entry notional exceeds risk-based size (risk budget / stop distance)",
  },
  {
    id: "max_paper_leverage",
    label: "Max PAPER leverage",
    defaultCap: "1.0× equity",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected gross leverage exceeds the paper maximum",
  },
  {
    id: "max_gross_exposure",
    label: "Max gross exposure",
    defaultCap: "1.0× equity",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected gross exposure exceeds the portfolio notional cap",
  },
  {
    id: "max_net_exposure",
    label: "Max net exposure",
    defaultCap: "1.0× equity",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected net exposure exceeds the portfolio net cap",
  },
  {
    id: "max_simultaneous_positions",
    label: "Max simultaneous positions",
    defaultCap: "3",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected open position count exceeds the simultaneous-position cap",
  },
  {
    id: "max_asset_concentration",
    label: "Max asset concentration",
    defaultCap: "50% equity",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected asset concentration exceeds the portfolio cap",
  },
  {
    id: "max_strategy_concentration",
    label: "Max strategy concentration",
    defaultCap: "50% equity",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected strategy concentration exceeds the portfolio cap",
  },
  {
    id: "max_venue_concentration",
    label: "Max venue concentration",
    defaultCap: "100% equity",
    onBreach: "reject entry",
    kind: "entry",
    reason: "projected venue concentration exceeds the portfolio cap",
  },
  {
    id: "daily_loss_guard",
    label: "Daily loss guard",
    defaultCap: "1% already lost",
    onBreach: "reject entry · reduce-only still allowed",
    kind: "halt",
    reason: "daily loss guard is already breached; new entries are blocked",
  },
  {
    id: "weekly_loss_guard",
    label: "Weekly loss guard",
    defaultCap: "3% already lost",
    onBreach: "reject entry · reduce-only still allowed",
    kind: "halt",
    reason: "weekly loss guard is already breached; new entries are blocked",
  },
  {
    id: "drawdown_kill",
    label: "Drawdown kill",
    defaultCap: "7% from peak",
    onBreach: "reject entry · reduce-only still allowed",
    kind: "halt",
    reason: "portfolio drawdown kill threshold is already breached; new entries are blocked",
  },
  {
    id: "no_averaging_down",
    label: "No averaging down",
    defaultCap: "no add to an open name",
    onBreach: "reject entry",
    kind: "entry",
    reason: "averaging down is not permitted outside a predeclared reduce-only exit",
  },
];

export type PaperRiskRejectionView = {
  catalog: readonly PaperRiskGate[];
  copiedRejectionCount: string;
  copiedRejectionSource: string;
  haltState: string;
  haltStateSource: string;
  reduceOnlyAfterHalt: string;
  source: string;
};

export function paperRiskRejectionView(
  riskRejectionCount: number | undefined,
): PaperRiskRejectionView {
  return {
    catalog: PAPER_HARD_LIMIT_GATES,
    copiedRejectionCount:
      riskRejectionCount === undefined ? RISK_UNAVAILABLE : String(riskRejectionCount),
    copiedRejectionSource:
      riskRejectionCount === undefined
        ? "capture-health.json risk_rejections omitted"
        : "COURSE-1 capture-health.json · risk_rejections count only",
    haltState: RISK_UNAVAILABLE,
    haltStateSource: HALT_STATE_UNAVAILABLE_REASON,
    reduceOnlyAfterHalt: REDUCE_ONLY_AFTER_HALT_RULE,
    source: PAPER_RISK_SOURCE,
  };
}

export function paperRiskGateIds(): readonly string[] {
  return PAPER_HARD_LIMIT_GATES.map((gate) => gate.id);
}
