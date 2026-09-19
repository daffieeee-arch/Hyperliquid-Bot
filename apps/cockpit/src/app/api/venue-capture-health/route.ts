import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { findRepoRoot, firstQueryValue, requirePaperTradingMode } from "../../../lib/paths";
import { loadVenueCaptureStrip } from "../../../lib/venue-capture";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

export async function GET(request: Request): Promise<NextResponse> {
  noStore();
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const url = new URL(request.url);
    const strip = loadVenueCaptureStrip(process.env, findRepoRoot(), {
      data1a_run_id: firstQueryValue(url.searchParams.get("data1a_run_id") ?? undefined),
      data1b_run_id: firstQueryValue(url.searchParams.get("data1b_run_id") ?? undefined),
      data1d_run_id: firstQueryValue(url.searchParams.get("data1d_run_id") ?? undefined),
      data1e_run_id: firstQueryValue(url.searchParams.get("data1e_run_id") ?? undefined),
      data1f_run_id: firstQueryValue(url.searchParams.get("data1f_run_id") ?? undefined),
    });
    return NextResponse.json({ ok: true, strip }, { headers: NO_STORE_HEADERS });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Multi-venue capture health is unavailable.";
    const status = message.includes("PAPER-only") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
