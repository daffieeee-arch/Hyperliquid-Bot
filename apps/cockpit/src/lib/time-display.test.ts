import { describe, expect, it } from "vitest";

import {
  CLOCK_UNKNOWN,
  COCKPIT_TIME_ZONE,
  dualClockLabel,
  localClockLabel,
  localDateTimeLabel,
} from "./time-display";

describe("time display", () => {
  it("renders Europe/Amsterdam summer time as CEST (UTC+2)", () => {
    expect(COCKPIT_TIME_ZONE).toBe("Europe/Amsterdam");
    expect(localClockLabel("2026-09-06T12:34:56.789Z", COCKPIT_TIME_ZONE)).toBe("14:34:56 CEST");
    // A late-evening UTC event rolls over to the next local day; only the clock is shown.
    expect(localClockLabel("2026-09-06T23:30:00Z", COCKPIT_TIME_ZONE)).toBe("01:30:00 CEST");
  });

  it("renders Europe/Amsterdam winter time as CET (UTC+1)", () => {
    expect(localClockLabel("2026-01-06T12:34:56Z", COCKPIT_TIME_ZONE)).toBe("13:34:56 CET");
    expect(localClockLabel("2026-12-31T23:30:00Z", COCKPIT_TIME_ZONE)).toBe("00:30:00 CET");
  });

  it("renders operator clocks in Europe/Amsterdam without a trailing Z", () => {
    expect(localDateTimeLabel("2026-09-06T12:34:56.789Z", COCKPIT_TIME_ZONE)).toBe(
      "2026-09-06 14:34:56 CEST",
    );
    expect(localDateTimeLabel("2026-01-06T12:34:56Z", COCKPIT_TIME_ZONE)).toBe(
      "2026-01-06 13:34:56 CET",
    );
    expect(dualClockLabel("2026-09-06T12:34:56.789Z")).toBe("14:34:56 CEST");
    expect(dualClockLabel("2026-09-06T12:34:56.789Z")).not.toMatch(/Z/);
  });

  it("never invents a time for missing or malformed input", () => {
    for (const bad of [undefined, null, "", "not-a-date", "2026-13-45T99:00:00Z"]) {
      expect(localClockLabel(bad, COCKPIT_TIME_ZONE)).toBe(CLOCK_UNKNOWN);
      expect(localDateTimeLabel(bad, COCKPIT_TIME_ZONE)).toBe(CLOCK_UNKNOWN);
      expect(dualClockLabel(bad)).toBe(CLOCK_UNKNOWN);
    }
  });

  it("respects an explicit zone so tests do not depend on the machine clock", () => {
    expect(localClockLabel("2026-09-06T12:34:56Z", "UTC")).toBe("12:34:56 UTC");
    expect(localClockLabel("2026-09-06T12:34:56Z", "Asia/Tokyo")).toMatch(/^21:34:56 /);
  });
});
