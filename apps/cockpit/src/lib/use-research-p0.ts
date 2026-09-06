"use client";

import type { VenueCaptureQuery } from "./paths";
import type { PollState } from "./poll-state";
import type { ResearchP0View } from "./research-p0-view";
import { usePoll } from "./use-poll";
import { VENUE_CAPTURE_QUERY_KEYS } from "./venue-capture-poll";

export function researchP0RequestUrl(query: VenueCaptureQuery = {}): string {
  const params = new URLSearchParams();
  for (const key of VENUE_CAPTURE_QUERY_KEYS) {
    const value = query[key]?.trim();
    if (value) {
      params.set(key, value);
    }
  }
  const encoded = params.toString();
  return encoded === "" ? "/api/research-p0" : `/api/research-p0?${encoded}`;
}

export function parseResearchP0Response(payload: unknown): ResearchP0View {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("ok" in payload) ||
    payload.ok !== true ||
    !("view" in payload) ||
    typeof payload.view !== "object" ||
    payload.view === null
  ) {
    const message =
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload &&
      typeof payload.error === "string"
        ? payload.error
        : "Research P0 response was not a fail-closed object.";
    throw new Error(message);
  }
  return payload.view as ResearchP0View;
}

export async function fetchResearchP0(query: VenueCaptureQuery): Promise<ResearchP0View> {
  const response = await fetch(researchP0RequestUrl(query), { cache: "no-store" });
  const payload: unknown = await response.json();
  return parseResearchP0Response(payload);
}

/**
 * Research P0 view bound to the shared cockpit clock.
 *
 * Research used to be rendered once on the server and then never refreshed,
 * so it could show a run registry that no longer matched the rest of the page.
 * It now re-reads on the same token as every other panel, and a failed read
 * keeps the previous view flagged as degraded rather than silently freezing.
 */
export function useResearchP0(
  query: VenueCaptureQuery,
  initial: ResearchP0View,
  refreshToken = 0,
): PollState<ResearchP0View> {
  return usePoll(researchP0RequestUrl(query), () => fetchResearchP0(query), initial, refreshToken);
}
