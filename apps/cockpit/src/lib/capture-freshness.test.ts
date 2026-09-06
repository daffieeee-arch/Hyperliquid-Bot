import { describe, expect, it } from "vitest";

import {
  DEFAULT_CAPTURE_FRESH_MAX_S,
  isLastPartFresh,
  lastPartAgeSeconds,
  resolveCaptureFreshMaxSeconds,
} from "./capture-freshness";

const observedAt = "2026-09-04T13:49:40.000Z";

describe("capture freshness", () => {
  it("defaults COCKPIT_CAPTURE_FRESH_MAX_S to 180 seconds", () => {
    expect(DEFAULT_CAPTURE_FRESH_MAX_S).toBe(180);
    expect(resolveCaptureFreshMaxSeconds({})).toBe(180);
    expect(resolveCaptureFreshMaxSeconds({ COCKPIT_CAPTURE_FRESH_MAX_S: "" })).toBe(180);
    expect(resolveCaptureFreshMaxSeconds({ COCKPIT_CAPTURE_FRESH_MAX_S: "60" })).toBe(60);
  });

  it("refuses a non-positive or non-integer freshness window", () => {
    expect(() => resolveCaptureFreshMaxSeconds({ COCKPIT_CAPTURE_FRESH_MAX_S: "0" })).toThrow(
      /positive integer/,
    );
    expect(() => resolveCaptureFreshMaxSeconds({ COCKPIT_CAPTURE_FRESH_MAX_S: "-1" })).toThrow(
      /positive integer/,
    );
    expect(() => resolveCaptureFreshMaxSeconds({ COCKPIT_CAPTURE_FRESH_MAX_S: "abc" })).toThrow(
      /positive integer/,
    );
  });

  it("treats last part mtime within FRESH_MAX as fresh and older mtime as stale", () => {
    expect(lastPartAgeSeconds("2026-09-04T13:49:00.000Z", observedAt)).toBe(40);
    expect(isLastPartFresh("2026-09-04T13:49:00.000Z", observedAt, 180)).toBe(true);
    expect(isLastPartFresh("2026-09-04T13:46:40.000Z", observedAt, 180)).toBe(true);
    expect(isLastPartFresh("2026-09-04T13:46:39.000Z", observedAt, 180)).toBe(false);
    expect(isLastPartFresh("2026-09-04T13:40:00.000Z", observedAt, 180)).toBe(false);
  });

  it("fails closed when mtime or observed_at cannot be compared", () => {
    expect(isLastPartFresh(undefined, observedAt, 180)).toBe(false);
    expect(isLastPartFresh("2026-09-04T13:49:00.000Z", undefined, 180)).toBe(false);
    expect(isLastPartFresh("not-a-date", observedAt, 180)).toBe(false);
  });

  it("treats a last_part_mtime slightly ahead of the cockpit clock as still fresh", () => {
    expect(lastPartAgeSeconds("2026-09-04T13:49:50.000Z", observedAt)).toBe(-10);
    expect(isLastPartFresh("2026-09-04T13:49:50.000Z", observedAt, 180)).toBe(true);
  });
});
