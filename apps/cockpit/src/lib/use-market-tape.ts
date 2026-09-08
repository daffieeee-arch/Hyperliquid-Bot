"use client";

import type { MarketTapeResponse } from "./market-tape-types";
import type { VenueCaptureQuery } from "./paths";
import type { PollState } from "./poll-state";
import { usePoll } from "./use-poll";
import { VENUE_CAPTURE_QUERY_KEYS } from "./venue-capture-poll";

export function marketTapeRequestUrl(query: VenueCaptureQuery = {}): string {
  const params = new URLSearchParams();
  for (const key of VENUE_CAPTURE_QUERY_KEYS) {
    const value = query[key]?.trim();
    if (value) {
      params.set(key, value);
    }
  }
  const encoded = params.toString();
  return encoded === "" ? "/api/market-tape" : `/api/market-tape?${encoded}`;
}

export function parseMarketTapeResponse(payload: unknown): MarketTapeResponse {
  if (typeof payload !== "object" || payload === null || !("ok" in payload)) {
    throw new Error("Market tape response was not a fail-closed object.");
  }
  if (payload.ok === true) {
    if (!("tape" in payload) || typeof payload.tape !== "object" || payload.tape === null) {
      throw new Error("Market tape response is missing the tape object.");
    }
    return payload as MarketTapeResponse;
  }
  const message =
    "error" in payload && typeof payload.error === "string"
      ? payload.error
      : "Market tape is unavailable.";
  throw new Error(message);
}

export async function fetchMarketTape(query: VenueCaptureQuery): Promise<MarketTapeResponse> {
  const response = await fetch(marketTapeRequestUrl(query), { cache: "no-store" });
  const payload: unknown = await response.json();
  return parseMarketTapeResponse(payload);
}

/**
 * Stored market data (last trade, BBO, spread, recent trades) read from the
 * published Parquet parts of the bound runs, on the shared cockpit clock.
 *
 * The backend caches parsed parts per run, so each tick only costs the parts
 * published since the previous tick. A failed read keeps the previous tape
 * and flags it degraded.
 */
export function useMarketTape(
  query: VenueCaptureQuery,
  initial: MarketTapeResponse,
  refreshToken = 0,
): PollState<MarketTapeResponse> {
  return usePoll(marketTapeRequestUrl(query), () => fetchMarketTape(query), initial, refreshToken);
}
