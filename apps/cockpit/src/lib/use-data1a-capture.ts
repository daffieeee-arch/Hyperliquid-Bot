"use client";

import { useEffect, useState } from "react";

import { data1aCapturePollError, fetchData1ACapture } from "./data1a-capture-poll";
import type { Data1ACaptureResponse } from "./types";

export function useData1ACapturePoll(
  queryRunId: string | undefined,
  initial: Data1ACaptureResponse,
  refreshToken = 0,
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
    return () => {
      cancelled = true;
    };
  }, [queryRunId, refreshToken]);

  return result;
}
