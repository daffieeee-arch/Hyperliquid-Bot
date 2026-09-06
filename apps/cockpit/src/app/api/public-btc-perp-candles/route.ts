import { NextResponse } from "next/server";

import { requirePaperTradingMode } from "../../../lib/paths";
import { fetchPublicBtcPerpCandles } from "../../../lib/public-candles";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const snapshot = await fetchPublicBtcPerpCandles();
    return NextResponse.json(snapshot, { headers: { "cache-control": "no-store" } });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Public BTC-PERP candles are unavailable.";
    const status = message.includes("PAPER-only") ? 403 : 502;
    return NextResponse.json({ error: message }, { status });
  }
}
