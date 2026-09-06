import { deskCaptureGlance } from "./desk";
import { captureBindingSourceLabel, presentCopiedText, presentGapReconnect } from "./display";
import type { PaperRunSnapshot, VenueCaptureChip, VenueCaptureStripResponse } from "./types";

export const SECOND_ROW_UNAVAILABLE = "UNAVAILABLE";

export const BINANCE_USDM_PUBLIC_PROFILE = "usdm_public";
export const BINANCE_USDM_PUBLIC_CHANNEL = "btcusdt@bookTicker";
export const BINANCE_USDM_PUBLIC_NOTE =
  "Independent DATA-1F transport. Not mixed with COURSE-1 soak PnL. Read-only; start/stop vetoed.";

export type D01BindHealthCard = {
  bound: string;
  strategyClass: string;
  sameD01SmokeRisk: string;
  paperRiskCatalog: "documented";
  source: string;
  note: string;
};

export type ReadonlyCaptureCard = {
  glanceLine: string;
  freshMaxS: string;
  startStop: "vetoed";
  source: string;
  note: string;
};

export type BinanceUsdmCallout = {
  profile: typeof BINANCE_USDM_PUBLIC_PROFILE;
  channel: typeof BINANCE_USDM_PUBLIC_CHANNEL;
  runId: string;
  status: string;
  lastPartAge: string;
  gapsReconnects: string;
  binding: string;
  note: typeof BINANCE_USDM_PUBLIC_NOTE;
};

export type SecondRowView = {
  bind: D01BindHealthCard;
  capture: ReadonlyCaptureCard;
  binance: BinanceUsdmCallout;
};

function binanceChip(strip: VenueCaptureStripResponse): VenueCaptureChip | undefined {
  if (!strip.ok) {
    return undefined;
  }
  return strip.strip.venues.find((venue) => venue.id === "binance");
}

export function buildSecondRowView(
  snapshot: PaperRunSnapshot | undefined,
  strip: VenueCaptureStripResponse,
): SecondRowView {
  const preflight = snapshot?.claim.preflight;
  const glance = deskCaptureGlance(strip);
  const binance = binanceChip(strip);
  return {
    bind: {
      bound: preflight === undefined ? SECOND_ROW_UNAVAILABLE : "COURSE-1 preflight + paper_risk",
      strategyClass: preflight?.strategy_class ?? SECOND_ROW_UNAVAILABLE,
      sameD01SmokeRisk:
        preflight === undefined
          ? SECOND_ROW_UNAVAILABLE
          : preflight.same_d01_smoke_risk
            ? "yes"
            : "no",
      paperRiskCatalog: "documented",
      source:
        preflight === undefined
          ? "run-claim.json preflight omitted"
          : "run-claim.json preflight.same_d01_smoke_risk",
      note: "COURSE-1 soak bind only · not a live risk-engine heartbeat",
    },
    capture: {
      glanceLine: glance.ok ? glance.glanceLine : SECOND_ROW_UNAVAILABLE,
      freshMaxS: glance.ok ? `${String(glance.freshMaxS)}s` : SECOND_ROW_UNAVAILABLE,
      startStop: "vetoed",
      source: "venue strip · COCKPIT_CAPTURE_FRESH_MAX_S · read-only",
      note: "Collectors are never started or stopped from this cockpit",
    },
    binance: {
      profile: BINANCE_USDM_PUBLIC_PROFILE,
      channel: BINANCE_USDM_PUBLIC_CHANNEL,
      runId: presentCopiedText(binance?.run_id),
      status: binance?.status ?? SECOND_ROW_UNAVAILABLE,
      lastPartAge: binance?.last_part_age ?? "n/a",
      gapsReconnects: presentGapReconnect(binance?.gaps, binance?.reconnects),
      binding:
        binance === undefined ? "unbound" : captureBindingSourceLabel(binance.binding_source),
      note: BINANCE_USDM_PUBLIC_NOTE,
    },
  };
}
