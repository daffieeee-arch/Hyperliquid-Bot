"use client";

import type { VenueCaptureQuery } from "./paths";
import type { PollState } from "./poll-state";
import type { VenueCaptureStripResponse } from "./types";
import { usePoll } from "./use-poll";
import { fetchVenueCaptureStrip, venueCaptureRequestUrl } from "./venue-capture-poll";

/**
 * Capture strip bound to the shared cockpit clock.
 *
 * A fail-closed `{ ok: false }` answer counts as a failed read: the previous
 * strip stays on screen, flagged degraded, instead of being replaced by an
 * error card that hides the last known run binding.
 */
export function useVenueCapturePoll(
  query: VenueCaptureQuery,
  initial: VenueCaptureStripResponse,
  refreshToken = 0,
): PollState<VenueCaptureStripResponse> {
  const url = venueCaptureRequestUrl(query);
  return usePoll(
    url,
    async () => {
      const payload = await fetchVenueCaptureStrip(query);
      if (!payload.ok) {
        throw new Error(payload.error);
      }
      return payload;
    },
    initial,
    refreshToken,
  );
}
