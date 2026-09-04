import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DATA1A_CAPTURE_POLL_MS,
  data1aCapturePollError,
  data1aCaptureRequestUrl,
  fetchData1ACapture,
  parseData1ACaptureResponse,
} from "./data1a-capture-poll";

describe("DATA-1A capture poll helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("documents a 5s filesystem poll and does not invent a path", () => {
    expect(DATA1A_CAPTURE_POLL_MS).toBe(5_000);
    expect(data1aCaptureRequestUrl()).toBe("/api/data1a-capture");
    expect(data1aCaptureRequestUrl("")).toBe("/api/data1a-capture");
    expect(data1aCaptureRequestUrl("20260904t134940z-live-retained")).toBe(
      "/api/data1a-capture?data1a_run_id=20260904t134940z-live-retained",
    );
  });

  it("fails closed when the payload is not a capture response object", () => {
    expect(() => parseData1ACaptureResponse(null)).toThrow(/fail-closed/);
    expect(() => parseData1ACaptureResponse({ snapshot: {} })).toThrow(/fail-closed/);
    expect(parseData1ACaptureResponse({ ok: false, error: "missing run" })).toEqual({
      ok: false,
      error: "missing run",
    });
  });

  it("fetches with cache no-store and copies a fail-closed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ ok: false, error: "DATA-1A run directory is missing: /missing." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchData1ACapture("20260904t134940z-live-retained")).resolves.toEqual({
      ok: false,
      error: "DATA-1A run directory is missing: /missing.",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/data1a-capture?data1a_run_id=20260904t134940z-live-retained",
      {
        cache: "no-store",
        headers: { "cache-control": "no-store" },
      },
    );
  });

  it("does not invent a PnL string when the poll throws", () => {
    expect(data1aCapturePollError(new Error("network down"))).toEqual({
      ok: false,
      error: "network down",
    });
    expect(data1aCapturePollError("nope").error).toBe("DATA-1A capture health is unavailable.");
  });
});
