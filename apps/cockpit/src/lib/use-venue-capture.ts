"use client";

import { useEffect, useState } from "react";

import { DATA1A_CAPTURE_POLL_MS } from "./data1a-capture-poll";
import type { VenueCaptureQuery } from "./paths";
import type { VenueCaptureStripResponse } from "./types";
import { fetchVenueCaptureStrip, venueCapturePollError } from "./venue-capture-poll";

export function useVenueCapturePoll(
  query: VenueCaptureQuery,
  initial: VenueCaptureStripResponse,
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
    const timer = window.setInterval(() => {
      void refresh();
    }, DATA1A_CAPTURE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [data1a, data1b, data1e, data1f]);

  return result;
}
