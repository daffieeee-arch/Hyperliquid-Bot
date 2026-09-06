import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { firstQueryValue, requirePaperTradingMode } from "../../../lib/paths";
import { listResearchSummaries, readResearchSummary } from "../../../lib/research-summaries";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

export async function GET(request: Request): Promise<NextResponse> {
  noStore();
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const url = new URL(request.url);
    const requested = firstQueryValue(url.searchParams.get("path") ?? undefined);
    if (requested !== undefined) {
      const summary = readResearchSummary(process.env, requested);
      return NextResponse.json(
        { ok: true, path: requested, summary },
        { headers: NO_STORE_HEADERS },
      );
    }
    const list = listResearchSummaries(process.env);
    return NextResponse.json({ ok: true, ...list }, { headers: NO_STORE_HEADERS });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "research-out panel summaries are unavailable.";
    const status = message.includes("PAPER-only") || message.includes("fails closed") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
