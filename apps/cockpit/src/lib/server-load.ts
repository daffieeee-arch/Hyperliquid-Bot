import { loadData1ACaptureSnapshot } from "./data1a-capture";
import { buildSeparateIdentityCards, type SeparateIdentityCards } from "./identity-cards";
import { loadMarketTapeStrip } from "./market-tape";
import type { MarketTapeResponse } from "./market-tape-types";
import { buildPaperBotView, type PaperBotView } from "./paper-bot";
import { loadPaperRunSnapshot } from "./paper-run";
import { findRepoRoot, firstQueryValue, type VenueCaptureQuery } from "./paths";
import { buildResearchP0View } from "./research-p0";
import type { ResearchP0View } from "./research-p0-view";
import { buildSecondRowView, type SecondRowView } from "./second-row";
import type { Data1ACaptureResponse, PaperRunSnapshot, VenueCaptureStripResponse } from "./types";
import { loadVenueCaptureStrip } from "./venue-capture";

export type CockpitSearchParams = Record<string, string | string[] | undefined>;

export type CockpitPageData = {
  query: VenueCaptureQuery;
  strip: VenueCaptureStripResponse;
  data1a: Data1ACaptureResponse;
  snapshot: PaperRunSnapshot | undefined;
  snapshotError: string | undefined;
  paper: PaperBotView;
  research: ResearchP0View;
  identities: SeparateIdentityCards;
  secondRow: SecondRowView;
};

export function cockpitQuery(params: CockpitSearchParams): VenueCaptureQuery {
  return {
    data1a_run_id: firstQueryValue(params.data1a_run_id),
    data1b_run_id: firstQueryValue(params.data1b_run_id),
    data1d_run_id: firstQueryValue(params.data1d_run_id),
    data1e_run_id: firstQueryValue(params.data1e_run_id),
    data1f_run_id: firstQueryValue(params.data1f_run_id),
  };
}

function loadStrip(query: VenueCaptureQuery): VenueCaptureStripResponse {
  try {
    return { ok: true, strip: loadVenueCaptureStrip(process.env, findRepoRoot(), query) };
  } catch (error: unknown) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Multi-venue capture health is unavailable.",
    };
  }
}

function loadData1A(runId: string | undefined): Data1ACaptureResponse {
  try {
    return {
      ok: true,
      snapshot: loadData1ACaptureSnapshot(process.env, findRepoRoot(), { data1a_run_id: runId }),
    };
  } catch (error: unknown) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "DATA-1A capture health is unavailable.",
    };
  }
}

/**
 * First-paint stored market data for routes that show it.
 *
 * Kept out of `loadCockpitPageData` because it parses Parquet: only Markets,
 * Overview and System pay for it, and only for parts not already cached.
 */
export async function loadInitialMarketTape(query: VenueCaptureQuery): Promise<MarketTapeResponse> {
  try {
    return { ok: true, tape: await loadMarketTapeStrip(process.env, findRepoRoot(), query) };
  } catch (error: unknown) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Stored market data is unavailable.",
    };
  }
}

/**
 * Server-side snapshot shared by every cockpit route.
 *
 * Each route renders from this so the first paint is already populated; the
 * client panels then keep it current on the shared refresh clock.
 */
export function loadCockpitPageData(params: CockpitSearchParams): CockpitPageData {
  const query = cockpitQuery(params);
  const strip = loadStrip(query);
  const data1a = loadData1A(query.data1a_run_id);

  let snapshot: PaperRunSnapshot | undefined;
  let snapshotError: string | undefined;
  try {
    snapshot = loadPaperRunSnapshot();
  } catch (error: unknown) {
    snapshotError = error instanceof Error ? error.message : "PAPER run data is unavailable.";
  }

  return {
    query,
    strip,
    data1a,
    snapshot,
    snapshotError,
    paper: buildPaperBotView(snapshot, snapshotError),
    research: buildResearchP0View(strip, process.env, findRepoRoot(), query),
    identities: buildSeparateIdentityCards(snapshot, snapshotError, strip),
    secondRow: buildSecondRowView(snapshot, strip),
  };
}
