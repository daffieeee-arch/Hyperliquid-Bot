import type { VenueCaptureQuery } from "./paths";
import { VENUE_CAPTURE_QUERY_KEYS } from "./venue-capture-poll";

/** Serialise the run selection so it survives navigation between workspaces. */
export function searchFromQuery(query: VenueCaptureQuery): string {
  const params = new URLSearchParams();
  for (const key of VENUE_CAPTURE_QUERY_KEYS) {
    const value = query[key]?.trim();
    if (value) {
      params.set(key, value);
    }
  }
  const encoded = params.toString();
  return encoded === "" ? "" : `?${encoded}`;
}
