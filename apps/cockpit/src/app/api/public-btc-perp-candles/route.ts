import { NextResponse } from "next/server";

import { requirePaperTradingMode } from "../../../lib/paths";
import { fetchPublicBtcPerpCandles, parsePublicCandleInterval } from "../../../lib/public-candles";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse> {
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const url = new URL(request.url);
    const interval = parsePublicCandleInterval(url.searchParams.get("interval") ?? undefined);
    const snapshot = await fetchPublicBtcPerpCandles(interval);
    return NextResponse.json(snapshot, { headers: { "cache-control": "no-store" } });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Public BTC-PERP candles are unavailable.";
    const status = message.includes("PAPER-only") ? 403 : 502;
    return NextResponse.json({ error: message }, { status });
  }
}
