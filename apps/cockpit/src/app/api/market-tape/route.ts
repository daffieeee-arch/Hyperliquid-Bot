import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { loadMarketTapeStrip } from "../../../lib/market-tape";
import { findRepoRoot, firstQueryValue, requirePaperTradingMode } from "../../../lib/paths";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

/**
 * Stored market data from the bound capture runs.
 *
 * Reads only newly published Parquet parts since the previous call (process
 * cache), never the whole retain. Credentials are never involved: this is a
 * read of files the collectors already wrote.
 */
export async function GET(request: Request): Promise<NextResponse> {
  noStore();
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const url = new URL(request.url);
    const query = {
      data1a_run_id: firstQueryValue(url.searchParams.get("data1a_run_id") ?? undefined),
      data1b_run_id: firstQueryValue(url.searchParams.get("data1b_run_id") ?? undefined),
      data1d_run_id: firstQueryValue(url.searchParams.get("data1d_run_id") ?? undefined),
      data1e_run_id: firstQueryValue(url.searchParams.get("data1e_run_id") ?? undefined),
      data1f_run_id: firstQueryValue(url.searchParams.get("data1f_run_id") ?? undefined),
    };
    const tape = await loadMarketTapeStrip(process.env, findRepoRoot(), query);
    return NextResponse.json({ ok: true, tape }, { headers: NO_STORE_HEADERS });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Market tape is unavailable.";
    const status = message.includes("PAPER-only") || message.includes("fails closed") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
