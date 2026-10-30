import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { loadNativePaperProjection } from "../../../lib/native-paper";
import { requirePaperTradingMode } from "../../../lib/paths";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

/** Only the server environment binds a directory; requests cannot select paths. */
export async function GET(): Promise<NextResponse> {
  noStore();
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
  } catch {
    return NextResponse.json(
      { ok: false, error: "Native PAPER reads require PAPER mode." },
      { status: 403, headers: HEADERS },
    );
  }
  try {
    return NextResponse.json(loadNativePaperProjection(), { headers: HEADERS });
  } catch {
    return NextResponse.json(
      { ok: false, error: "Native PAPER snapshot could not be read safely." },
      { status: 500, headers: HEADERS },
    );
  }
}
