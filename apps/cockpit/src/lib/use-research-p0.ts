"use client";

import { useEffect, useState } from "react";

import type { VenueCaptureQuery } from "./paths";
import type { ResearchP0View } from "./research-p0-view";
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

/**
 * Research P0 view bound to the shared cockpit clock.
 *
 * Research used to be rendered once on the server and then never refreshed,
 * so it could show a run registry that no longer matched the rest of the page.
 * It now re-reads on the same token as every other panel.
 */
export function useResearchP0(
  query: VenueCaptureQuery,
  initial: ResearchP0View,
  refreshToken = 0,
): { view: ResearchP0View; error: string | undefined } {
  const [view, setView] = useState<ResearchP0View>(initial);
  const [error, setError] = useState<string | undefined>(initial.error);
  const data1a = query.data1a_run_id ?? "";
  const data1b = query.data1b_run_id ?? "";
  const data1e = query.data1e_run_id ?? "";
  const data1f = query.data1f_run_id ?? "";

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const response = await fetch(
          researchP0RequestUrl({
            data1a_run_id: data1a === "" ? undefined : data1a,
            data1b_run_id: data1b === "" ? undefined : data1b,
            data1e_run_id: data1e === "" ? undefined : data1e,
            data1f_run_id: data1f === "" ? undefined : data1f,
          }),
          { cache: "no-store" },
        );
        const payload: unknown = await response.json();
        const next = parseResearchP0Response(payload);
        if (!cancelled) {
          setView(next);
          setError(next.error);
        }
      } catch (caught: unknown) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Research P0 view is unavailable.");
        }
      }
    }

    void refresh();
    return () => {
      cancelled = true;
    };
  }, [data1a, data1b, data1e, data1f, refreshToken]);

  return { view, error };
}
