import { NextResponse } from "next/server";

import { loadData1ACaptureSnapshot } from "../../../lib/data1a-capture";
import { findRepoRoot, firstQueryValue, requirePaperTradingMode } from "../../../lib/paths";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse> {
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const url = new URL(request.url);
    const snapshot = loadData1ACaptureSnapshot(process.env, findRepoRoot(), {
      data1a_run_id: firstQueryValue(url.searchParams.get("data1a_run_id") ?? undefined),
    });
    return NextResponse.json({ ok: true, snapshot }, { headers: { "cache-control": "no-store" } });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "DATA-1A capture health is unavailable.";
    const status = message.includes("PAPER-only") ? 403 : 200;
    return NextResponse.json(
      { ok: false, error: message },
      { status, headers: { "cache-control": "no-store" } },
    );
  }
}
