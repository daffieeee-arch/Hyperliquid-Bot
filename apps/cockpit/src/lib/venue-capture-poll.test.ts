import { afterEach, describe, expect, it, vi } from "vitest";

import { DATA1A_CAPTURE_POLL_MS } from "./data1a-capture-poll";
import {
  fetchVenueCaptureStrip,
  parseVenueCaptureStripResponse,
  venueCapturePickerHref,
  venueCapturePollError,
  venueCaptureRequestUrl,
} from "./venue-capture-poll";

describe("multi-venue capture poll helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("builds picker hrefs without inventing missing venue query params", () => {
    expect(venueCapturePickerHref("/", {}, "data1f_run_id", "20260905t235830z-live-retained")).toBe(
      "/?data1f_run_id=20260905t235830z-live-retained",
    );
    expect(
      venueCapturePickerHref(
        "/",
        { data1a_run_id: "hl-run", data1f_run_id: "bn-run" },
        "data1f_run_id",
        "",
      ),
    ).toBe("/?data1a_run_id=hl-run");
    expect(venueCapturePickerHref("/", { data1a_run_id: "hl-run" }, "data1a_run_id", "")).toBe("/");
  });

  it("reuses the DATA-1A 5s poll and encodes only present run ids", () => {
    expect(DATA1A_CAPTURE_POLL_MS).toBe(5_000);
    expect(venueCaptureRequestUrl()).toBe("/api/venue-capture-health");
    expect(venueCaptureRequestUrl({ data1a_run_id: "" })).toBe("/api/venue-capture-health");
    expect(
      venueCaptureRequestUrl({
        data1a_run_id: "hl-run",
        data1f_run_id: "bn-run",
      }),
    ).toBe("/api/venue-capture-health?data1a_run_id=hl-run&data1f_run_id=bn-run");
  });

  it("fails closed when the payload is not a strip response object", () => {
    expect(() => parseVenueCaptureStripResponse(null)).toThrow(/fail-closed/);
    expect(() => parseVenueCaptureStripResponse({ strip: {} })).toThrow(/fail-closed/);
    expect(parseVenueCaptureStripResponse({ ok: false, error: "PAPER-only" })).toEqual({
      ok: false,
      error: "PAPER-only",
    });
  });

  it("fetches with cache no-store and copies a fail-closed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        ok: false,
        error: "Cockpit first screen is PAPER-only and fails closed.",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchVenueCaptureStrip({ data1b_run_id: "kr-run" })).resolves.toEqual({
      ok: false,
      error: "Cockpit first screen is PAPER-only and fails closed.",
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/venue-capture-health?data1b_run_id=kr-run", {
      cache: "no-store",
      headers: { "cache-control": "no-store" },
    });
  });

  it("does not invent a PnL string when the poll throws", () => {
    expect(venueCapturePollError(new Error("network down"))).toEqual({
      ok: false,
      error: "network down",
    });
    expect(venueCapturePollError("nope")).toEqual({
      ok: false,
      error: "Multi-venue capture health is unavailable.",
    });
  });
});
