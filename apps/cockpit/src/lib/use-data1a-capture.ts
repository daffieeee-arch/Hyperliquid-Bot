"use client";

import { useEffect, useState } from "react";

import {
  DATA1A_CAPTURE_POLL_MS,
  data1aCapturePollError,
  fetchData1ACapture,
} from "./data1a-capture-poll";
import type { Data1ACaptureResponse } from "./types";

export function useData1ACapturePoll(
  queryRunId: string | undefined,
  initial: Data1ACaptureResponse,
): Data1ACaptureResponse {
  const [result, setResult] = useState<Data1ACaptureResponse>(initial);

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const payload = await fetchData1ACapture(queryRunId);
        if (!cancelled) {
          setResult(payload);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setResult(data1aCapturePollError(error));
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
  }, [queryRunId]);

  return result;
}
