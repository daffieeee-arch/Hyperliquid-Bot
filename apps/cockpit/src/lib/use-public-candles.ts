"use client";

import { useMemo } from "react";

import { fetchJsonOrThrow } from "./fetch-json";
import type { PollState } from "./poll-state";
import {
  PUBLIC_CANDLE_INTERVAL,
  parsePublicCandleSnapshot,
  type PublicBtcCandleSnapshot,
  type PublicCandleInterval,
} from "./public-candles";
import { usePoll } from "./use-poll";

export type PublicCandleState =
  | { status: "loading" }
  | { status: "ready"; snapshot: PublicBtcCandleSnapshot }
  | { status: "error"; message: string };

export function publicCandlesRequestUrl(interval: PublicCandleInterval): string {
  return `/api/public-btc-perp-candles?interval=${encodeURIComponent(interval)}`;
}

export async function fetchPublicBtcPerpCandles(
  interval: PublicCandleInterval,
): Promise<PublicBtcCandleSnapshot> {
  const payload = await fetchJsonOrThrow(
    publicCandlesRequestUrl(interval),
    "Public candle request failed",
  );
  return parsePublicCandleSnapshot(payload);
}

export function publicCandleStateFromPoll(
  poll: PollState<PublicBtcCandleSnapshot | undefined>,
): PublicCandleState {
  if (poll.error !== undefined) {
    return { status: "error", message: poll.error };
  }
  if (poll.data === undefined) {
    return { status: "loading" };
  }
  return { status: "ready", snapshot: poll.data };
}

export function usePublicBtcPerpCandles(
  interval: PublicCandleInterval = PUBLIC_CANDLE_INTERVAL,
  refreshToken = 0,
): PublicCandleState {
  const poll = usePoll<PublicBtcCandleSnapshot | undefined>(
    publicCandlesRequestUrl(interval),
    () => fetchPublicBtcPerpCandles(interval),
    undefined,
    refreshToken,
  );
  // Stable between renders so chart effects only run when the poll record changes.
  return useMemo(() => publicCandleStateFromPoll(poll), [poll]);
}
