import type { Data1ACaptureResponse } from "./types";

/** Client poll interval for `/api/data1a-capture`. Keep in sync with cockpit README/runbook. */
export const DATA1A_CAPTURE_POLL_MS = 5_000;

const NO_STORE: RequestInit = {
  cache: "no-store",
  headers: { "cache-control": "no-store" },
};

export function data1aCaptureRequestUrl(queryRunId?: string): string {
  if (queryRunId === undefined || queryRunId === "") {
    return "/api/data1a-capture";
  }
  const params = new URLSearchParams();
  params.set("data1a_run_id", queryRunId);
  return `/api/data1a-capture?${params.toString()}`;
}

export function parseData1ACaptureResponse(payload: unknown): Data1ACaptureResponse {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("ok" in payload) ||
    typeof payload.ok !== "boolean"
  ) {
    throw new Error("DATA-1A capture response was not a fail-closed object.");
  }
  return payload as Data1ACaptureResponse;
}

export async function fetchData1ACapture(queryRunId?: string): Promise<Data1ACaptureResponse> {
  const response = await fetch(data1aCaptureRequestUrl(queryRunId), NO_STORE);
  const payload: unknown = await response.json();
  return parseData1ACaptureResponse(payload);
}

export function data1aCapturePollError(error: unknown): Data1ACaptureResponse {
  return {
    ok: false,
    error: error instanceof Error ? error.message : "DATA-1A capture health is unavailable.",
  };
}
