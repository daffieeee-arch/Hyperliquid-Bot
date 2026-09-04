import { NextResponse } from "next/server";

import { requirePaperTradingMode } from "../../../lib/paths";
import { fetchPublicBtcPerpMid } from "../../../lib/public-price";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    const price = await fetchPublicBtcPerpMid();
    return NextResponse.json(price, { headers: { "cache-control": "no-store" } });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Public BTC-PERP price is unavailable.";
    const status = message.includes("PAPER-only") ? 403 : 502;
    return NextResponse.json({ error: message }, { status });
  }
}
