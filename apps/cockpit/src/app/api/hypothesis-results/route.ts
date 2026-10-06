import { unstable_noStore as noStore } from "next/cache";
import { NextResponse } from "next/server";

import { listHypothesisResults } from "../../../lib/hypothesis-results";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

const NO_STORE_HEADERS = { "cache-control": "no-store, no-cache, must-revalidate" };

export async function GET(): Promise<NextResponse> {
  noStore();
  try {
    const list = listHypothesisResults(process.env);
    return NextResponse.json(list, { headers: NO_STORE_HEADERS });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Hypothesis results are unavailable.";
    const status = message.includes("PAPER-only") || message.includes("fails closed") ? 403 : 200;
    return NextResponse.json({ ok: false, error: message }, { status, headers: NO_STORE_HEADERS });
  }
}
