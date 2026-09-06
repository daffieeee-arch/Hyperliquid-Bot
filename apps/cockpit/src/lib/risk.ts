import { deskCaptureGlance, type DeskCaptureGlance } from "./desk";
import { signedTone, yesNo, type SignedTone, type StatusTone } from "./display";
import type { PaperRunSnapshot, VenueCaptureStripResponse } from "./types";

export const RISK_UNAVAILABLE = "UNAVAILABLE";
export const RISK_UNAVAILABLE_REASON = "not in reconstructable PAPER artifacts";
export const RISK_ASSUMED_PNL_SOURCE = "paper-pnl.json · assumed overlay, not venue PnL";
export const RISK_POSITION_SOURCE = "paper-position.json · sandbox PAPER, not account truth";
export const RISK_PREFLIGHT_SOURCE = "run-claim.json preflight · fail-closed bound";
export const RISK_PREFLIGHT_MISSING_SOURCE = "run-claim.json preflight omitted";
export const RISK_HEALTH_SOURCE = "COURSE-1 capture-health.json · bounded soak";
export const RISK_CAPTURE_SOURCE = "venue strip · COCKPIT_CAPTURE_FRESH_MAX_S";
export const RISK_FAIL_CLOSED_SOURCE = "fail-closed cockpit bound";

export type RiskFieldKind = "copied" | "assumed" | "unavailable";

export type RiskFieldTone = SignedTone | StatusTone | "paper";

export type RiskField = {
  id: string;
  label: string;
  value: string;
  kind: RiskFieldKind;
  source: string;
  tone: RiskFieldTone;
};

export type RiskView = {
  available: boolean;
  error: string | undefined;
  mode: "PAPER";
  instrument: string;
  positionBtc: string;
  assumedNetPnl: string;
  assumedPnlTone: SignedTone;
  maxAssumedLoss: string;
  captureLine: string;
  copied: RiskField[];
  unavailable: RiskField[];
};

export const RISK_UNAVAILABLE_FIELDS = [
  { id: "leverage", label: "Leverage" },
  { id: "margin", label: "Margin" },
  { id: "liquidation", label: "Liquidation" },
  { id: "var", label: "VaR" },
  { id: "expected-shortfall", label: "Expected Shortfall" },
  { id: "venue-risk", label: "Venue risk" },
  { id: "gross-exposure", label: "Gross exposure" },
  { id: "net-exposure", label: "Net exposure" },
  { id: "drawdown", label: "Drawdown" },
  { id: "correlation", label: "Correlation" },
  { id: "volatility", label: "Volatility" },
] as const;

function unavailableField(
  id: string,
  label: string,
  source: string = RISK_UNAVAILABLE_REASON,
): RiskField {
  return {
    id,
    label,
    value: RISK_UNAVAILABLE,
    kind: "unavailable",
    source,
    tone: "warn",
  };
}

function copiedField(
  id: string,
  label: string,
  value: string,
  source: string,
  kind: Exclude<RiskFieldKind, "unavailable"> = "copied",
  tone: RiskFieldTone = "neutral",
): RiskField {
  return { id, label, value, kind, source, tone };
}

function boundOrUnavailable(
  id: string,
  label: string,
  value: string | undefined,
  suffix: string,
): RiskField {
  if (value === undefined || value === "") {
    return unavailableField(id, label, RISK_PREFLIGHT_MISSING_SOURCE);
  }
  return copiedField(id, label, `${value} ${suffix}`, RISK_PREFLIGHT_SOURCE);
}

export function unavailableRiskFields(): RiskField[] {
  return RISK_UNAVAILABLE_FIELDS.map((field) => unavailableField(field.id, field.label));
}

export function riskCaptureLine(glance: DeskCaptureGlance): string {
  if (!glance.ok) {
    return RISK_UNAVAILABLE;
  }
  return glance.glanceLine;
}

function captureField(glance: DeskCaptureGlance): RiskField {
  if (!glance.ok) {
    return unavailableField("capture", "Capture freshness", glance.error);
  }
  return copiedField("capture", "Capture freshness", glance.glanceLine, RISK_CAPTURE_SOURCE);
}

export function buildRiskView(
  snapshot: PaperRunSnapshot | undefined,
  snapshotError: string | undefined,
  strip: VenueCaptureStripResponse,
): RiskView {
  const glance = deskCaptureGlance(strip);
  const captureLine = riskCaptureLine(glance);
  const unavailable = unavailableRiskFields();
  const jsonError = snapshotError ?? "PAPER run data is unavailable.";

  if (snapshot === undefined) {
    return {
      available: false,
      error: jsonError,
      mode: "PAPER",
      instrument: RISK_UNAVAILABLE,
      positionBtc: RISK_UNAVAILABLE,
      assumedNetPnl: RISK_UNAVAILABLE,
      assumedPnlTone: "unknown",
      maxAssumedLoss: RISK_UNAVAILABLE,
      captureLine,
      copied: [
        copiedField("mode", "Trading mode", "PAPER", RISK_FAIL_CLOSED_SOURCE, "copied", "paper"),
        unavailableField("position", "Paper position", jsonError),
        unavailableField("assumed-pnl", "Assumed overlay PnL", jsonError),
        unavailableField("max-assumed-loss", "Max assumed loss", jsonError),
        unavailableField("max-entry-notional", "Max entry notional", jsonError),
        captureField(glance),
      ],
      unavailable,
    };
  }

  const preflight = snapshot.claim.preflight;
  const pnlTone = signedTone(snapshot.pnl.net_pnl_usdc_assumed);
  const maxAssumedLossField = boundOrUnavailable(
    "max-assumed-loss",
    "Max assumed loss",
    preflight?.max_assumed_loss_usdc,
    "USDC",
  );

  return {
    available: true,
    error: undefined,
    mode: "PAPER",
    instrument: snapshot.position.instrument_id,
    positionBtc: `${snapshot.position.final_position_btc} BTC`,
    assumedNetPnl: `${snapshot.pnl.net_pnl_usdc_assumed} USDC`,
    assumedPnlTone: pnlTone,
    maxAssumedLoss: maxAssumedLossField.value,
    captureLine,
    copied: [
      copiedField("mode", "Trading mode", "PAPER", RISK_FAIL_CLOSED_SOURCE, "copied", "paper"),
      copiedField(
        "instrument",
        "Instrument",
        snapshot.position.instrument_id,
        RISK_POSITION_SOURCE,
      ),
      copiedField(
        "position",
        "Paper position",
        `${snapshot.position.final_position_btc} BTC`,
        RISK_POSITION_SOURCE,
      ),
      copiedField(
        "business-qty",
        "Business qty",
        snapshot.position.business_final_position_quantity,
        RISK_POSITION_SOURCE,
      ),
      copiedField(
        "venue-authoritative",
        "Venue authoritative",
        yesNo(snapshot.position.venue_authoritative),
        RISK_POSITION_SOURCE,
      ),
      copiedField(
        "assumed-pnl",
        "Assumed overlay PnL",
        `${snapshot.pnl.net_pnl_usdc_assumed} USDC`,
        RISK_ASSUMED_PNL_SOURCE,
        "assumed",
        pnlTone === "unknown" ? "neutral" : pnlTone,
      ),
      copiedField(
        "assumed-flag",
        "Assumed / venue PnL",
        `${yesNo(snapshot.pnl.assumed)} / ${yesNo(snapshot.pnl.venue_pnl)}`,
        RISK_ASSUMED_PNL_SOURCE,
        "assumed",
      ),
      copiedField(
        "starting-cash",
        "Starting cash (assumed)",
        `${snapshot.pnl.starting_cash_usdc_assumed} USDC`,
        RISK_ASSUMED_PNL_SOURCE,
        "assumed",
      ),
      copiedField(
        "ending-equity",
        "Ending equity (assumed)",
        `${snapshot.pnl.ending_equity_usdc_assumed} USDC`,
        RISK_ASSUMED_PNL_SOURCE,
        "assumed",
      ),
      copiedField(
        "funding",
        "Funding (assumed)",
        `${snapshot.pnl.funding_payment_usdc} USDC`,
        RISK_ASSUMED_PNL_SOURCE,
        "assumed",
      ),
      boundOrUnavailable(
        "max-entry-notional",
        "Max entry notional",
        preflight?.max_entry_notional_usdc,
        "USDC",
      ),
      maxAssumedLossField,
      boundOrUnavailable("order-qty", "Order qty", preflight?.order_quantity_btc, "BTC"),
      preflight === undefined
        ? unavailableField(
            "same-d01-smoke-risk",
            "Same D01 smoke risk",
            RISK_PREFLIGHT_MISSING_SOURCE,
          )
        : copiedField(
            "same-d01-smoke-risk",
            "Same D01 smoke risk",
            yesNo(preflight.same_d01_smoke_risk),
            RISK_PREFLIGHT_SOURCE,
          ),
      copiedField(
        "risk-rejections",
        "Risk rejections",
        String(snapshot.health.risk_rejections),
        RISK_HEALTH_SOURCE,
      ),
      copiedField(
        "twenty-four-seven",
        "24/7 claim",
        yesNo(snapshot.health.twenty_four_seven),
        RISK_HEALTH_SOURCE,
      ),
      copiedField("signing", "Signing", "off", RISK_FAIL_CLOSED_SOURCE),
      copiedField("live-mid-for-pnl", "Live mid used for PnL", "no", RISK_FAIL_CLOSED_SOURCE),
      captureField(glance),
    ],
    unavailable,
  };
}
