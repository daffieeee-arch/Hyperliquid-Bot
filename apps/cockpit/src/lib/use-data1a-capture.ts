"use client";

import { data1aCaptureRequestUrl, fetchData1ACapture } from "./data1a-capture-poll";
import type { PollState } from "./poll-state";
import type { Data1ACaptureResponse } from "./types";
import { usePoll } from "./use-poll";

export function useData1ACapturePoll(
  queryRunId: string | undefined,
  initial: Data1ACaptureResponse,
  refreshToken = 0,
): PollState<Data1ACaptureResponse> {
  return usePoll(
    data1aCaptureRequestUrl(queryRunId),
    async () => {
      const payload = await fetchData1ACapture(queryRunId);
      if (!payload.ok) {
        throw new Error(payload.error);
      }
      return payload;
    },
    initial,
    refreshToken,
  );
}
