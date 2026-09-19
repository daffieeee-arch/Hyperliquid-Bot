import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { findRepoRoot, firstQueryValue, requirePaperTradingMode } from "../../../lib/paths";
import { buildResearchP0View } from "../../../lib/research-p0";
import { loadVenueCaptureStrip } from "../../../lib/venue-capture";
import type { VenueCaptureStripResponse } from "../../../lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

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
    const repoRoot = findRepoRoot();
    let strip: VenueCaptureStripResponse;
    try {
      strip = { ok: true, strip: loadVenueCaptureStrip(process.env, repoRoot, query) };
    } catch (error: unknown) {
      strip = {
        ok: false,
        error:
          error instanceof Error ? error.message : "Multi-venue capture health is unavailable.",
      };
    }
    const view = buildResearchP0View(strip, process.env, repoRoot, query);
    return NextResponse.json({ ok: true, view }, { headers: NO_STORE_HEADERS });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Research P0 view is unavailable.";
    const status = message.includes("PAPER-only") || message.includes("fails closed") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
