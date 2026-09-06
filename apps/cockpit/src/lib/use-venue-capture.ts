"use client";

import { useEffect, useState } from "react";

import type { VenueCaptureQuery } from "./paths";
import type { VenueCaptureStripResponse } from "./types";
import { fetchVenueCaptureStrip, venueCapturePollError } from "./venue-capture-poll";

/**
 * Capture strip bound to the shared cockpit clock.
 *
 * The hook no longer owns an interval: `refreshToken` comes from
 * `CockpitRefreshProvider` so the strip, research and market panels all move
 * on the same tick instead of drifting apart.
 */
export function useVenueCapturePoll(
  query: VenueCaptureQuery,
  initial: VenueCaptureStripResponse,
  refreshToken = 0,
): VenueCaptureStripResponse {
  const [result, setResult] = useState<VenueCaptureStripResponse>(initial);
  const data1a = query.data1a_run_id ?? "";
  const data1b = query.data1b_run_id ?? "";
  const data1e = query.data1e_run_id ?? "";
  const data1f = query.data1f_run_id ?? "";

  useEffect(() => {
    let cancelled = false;
    const nextQuery: VenueCaptureQuery = {
      data1a_run_id: data1a === "" ? undefined : data1a,
      data1b_run_id: data1b === "" ? undefined : data1b,
      data1e_run_id: data1e === "" ? undefined : data1e,
      data1f_run_id: data1f === "" ? undefined : data1f,
    };

    async function refresh(): Promise<void> {
      try {
        const payload = await fetchVenueCaptureStrip(nextQuery);
        if (!cancelled) {
          setResult(payload);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setResult(venueCapturePollError(error));
        }
      }
    }

    void refresh();
    return () => {
      cancelled = true;
    };
  }, [data1a, data1b, data1e, data1f, refreshToken]);

  return result;
}
