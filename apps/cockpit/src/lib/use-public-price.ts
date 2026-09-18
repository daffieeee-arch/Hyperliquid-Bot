"use client";

import { useMemo } from "react";

import { fetchJsonOrThrow } from "./fetch-json";
import type { PublicMidState } from "./markets";
import type { PollState } from "./poll-state";
import { parsePublicBtcPerpPayload, type PublicBtcPerpPrice } from "./public-price";
import { usePoll } from "./use-poll";

export const PUBLIC_BTC_PERP_REQUEST_URL = "/api/public-btc-perp";

export async function fetchPublicBtcPerp(): Promise<PublicBtcPerpPrice> {
  const payload = await fetchJsonOrThrow(
    PUBLIC_BTC_PERP_REQUEST_URL,
    "Public price request failed",
  );
  return parsePublicBtcPerpPayload(payload);
}

/**
 * Collapse the poll record into the three-way state the Markets tiles render.
 *
 * A failed read is shown as an error rather than as the previous mid: a
 * public quote that could not be re-read is not a quote the operator should
 * keep staring at.
 */
export function publicMidStateFromPoll(
  poll: PollState<PublicBtcPerpPrice | undefined>,
): PublicMidState {
  if (poll.error !== undefined) {
    return { status: "error", message: poll.error };
  }
  if (poll.data === undefined) {
    return { status: "loading" };
  }
  return { status: "ready", price: poll.data };
}

export function usePublicBtcPerp(refreshToken = 0): PublicMidState {
  const poll = usePoll<PublicBtcPerpPrice | undefined>(
    PUBLIC_BTC_PERP_REQUEST_URL,
    fetchPublicBtcPerp,
    undefined,
    refreshToken,
  );
  return useMemo(() => publicMidStateFromPoll(poll), [poll]);
}
