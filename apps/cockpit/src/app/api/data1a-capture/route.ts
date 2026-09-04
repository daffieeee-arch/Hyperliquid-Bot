import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { loadData1ACaptureSnapshot } from "../../../lib/data1a-capture";
import { findRepoRoot, firstQueryValue, requirePaperTradingMode } from "../../../lib/paths";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

export async function GET(request: Request): Promise<NextResponse> {
  noStore();
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const url = new URL(request.url);
    const snapshot = loadData1ACaptureSnapshot(process.env, findRepoRoot(), {
      data1a_run_id: firstQueryValue(url.searchParams.get("data1a_run_id") ?? undefined),
    });
    return NextResponse.json({ ok: true, snapshot }, { headers: NO_STORE_HEADERS });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "DATA-1A capture health is unavailable.";
    const status = message.includes("PAPER-only") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
