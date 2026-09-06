import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { buildPaperBotView } from "../../../lib/paper-bot";
import { loadPaperRunSnapshot } from "../../../lib/paper-run";
import { requirePaperTradingMode } from "../../../lib/paths";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

/**
 * PAPER desk view on the shared refresh clock.
 *
 * The COURSE-1 artifacts are re-read from disk on every call so the PAPER
 * workspace moves with the rest of the cockpit. A finished run simply keeps
 * returning the same historical snapshot, labelled as such by `lifecycle`.
 */
export async function GET(): Promise<NextResponse> {
  noStore();
  try {
    requirePaperTradingMode(process.env.TRADING_MODE);
    let view;
    try {
      view = buildPaperBotView(loadPaperRunSnapshot(), undefined);
    } catch (error: unknown) {
      view = buildPaperBotView(
        undefined,
        error instanceof Error ? error.message : "PAPER run data is unavailable.",
      );
    }
    return NextResponse.json(
      { ok: true, view, observed_at: new Date().toISOString() },
      { headers: NO_STORE_HEADERS },
    );
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "PAPER run view is unavailable.";
    const status = message.includes("PAPER-only") || message.includes("fails closed") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
