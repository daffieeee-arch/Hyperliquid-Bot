"use client";

import {
  parseHypothesisResultListPayload,
  type HypothesisResultList,
} from "./hypothesis-result-model";
import type { PollState } from "./poll-state";
import { usePoll } from "./use-poll";

export async function fetchHypothesisResults(): Promise<HypothesisResultList> {
  const response = await fetch("/api/hypothesis-results", { cache: "no-store" });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error("Hypothesis results response was not JSON.");
  }
  return parseHypothesisResultListPayload(payload);
}

export function useHypothesisResults(
  initial: HypothesisResultList,
  refreshToken = 0,
): PollState<HypothesisResultList> {
  return usePoll("hypothesis-results", fetchHypothesisResults, initial, refreshToken);
}
