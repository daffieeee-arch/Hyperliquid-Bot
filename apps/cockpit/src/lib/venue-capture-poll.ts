import type { VenueCaptureQuery } from "./paths";
import type { VenueCaptureStripResponse } from "./types";

const NO_STORE: RequestInit = {
  cache: "no-store",
  headers: { "cache-control": "no-store" },
};

export const VENUE_CAPTURE_QUERY_KEYS: readonly (keyof VenueCaptureQuery)[] = [
  "data1a_run_id",
  "data1b_run_id",
  "data1e_run_id",
  "data1f_run_id",
];

export function venueCaptureRequestUrl(query: VenueCaptureQuery = {}): string {
  const params = new URLSearchParams();
  for (const key of VENUE_CAPTURE_QUERY_KEYS) {
    const value = query[key]?.trim();
    if (value) {
      params.set(key, value);
    }
  }
  const encoded = params.toString();
  return encoded === "" ? "/api/venue-capture-health" : `/api/venue-capture-health?${encoded}`;
}

export function parseVenueCaptureStripResponse(payload: unknown): VenueCaptureStripResponse {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("ok" in payload) ||
    typeof payload.ok !== "boolean"
  ) {
    throw new Error("Venue capture-health response was not a fail-closed object.");
  }
  return payload as VenueCaptureStripResponse;
}

export async function fetchVenueCaptureStrip(
  query: VenueCaptureQuery = {},
): Promise<VenueCaptureStripResponse> {
  const response = await fetch(venueCaptureRequestUrl(query), NO_STORE);
  const payload: unknown = await response.json();
  return parseVenueCaptureStripResponse(payload);
}

export function venueCapturePickerHref(
  pathname: string,
  query: VenueCaptureQuery,
  key: keyof VenueCaptureQuery,
  value: string,
): string {
  const next: VenueCaptureQuery = {
    ...query,
    [key]: value.trim() === "" ? undefined : value.trim(),
  };
  const params = new URLSearchParams();
  for (const queryKey of VENUE_CAPTURE_QUERY_KEYS) {
    const item = next[queryKey]?.trim();
    if (item) {
      params.set(queryKey, item);
    }
  }
  const encoded = params.toString();
  return encoded === "" ? pathname : `${pathname}?${encoded}`;
}

export function venueCapturePollError(error: unknown): VenueCaptureStripResponse {
  return {
    ok: false,
    error: error instanceof Error ? error.message : "Multi-venue capture health is unavailable.",
  };
}
