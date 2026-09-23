import { describe, expect, it } from "vitest";

import {
  describePollRead,
  formatAgeSeconds,
  initialPollState,
  pollFailed,
  pollSucceeded,
  readAgeLabel,
  secondsBetween,
} from "./poll-state";

describe("poll state", () => {
  it("keeps the last good data and records the error when a refresh fails", () => {
    const first = pollSucceeded(
      initialPollState({ value: 1 }),
      { value: 2 },
      "2026-09-06T12:00:00Z",
    );
    expect(first.data).toEqual({ value: 2 });
    expect(first.lastSuccessAt).toBe("2026-09-06T12:00:00Z");
    expect(first.error).toBeUndefined();
    expect(first.degraded).toBe(false);

    const failed = pollFailed(first, new Error("ECONNREFUSED"), "2026-09-06T12:00:15Z");
    expect(failed.data).toEqual({ value: 2 });
    expect(failed.lastSuccessAt).toBe("2026-09-06T12:00:00Z");
    expect(failed.lastAttemptAt).toBe("2026-09-06T12:00:15Z");
    expect(failed.error).toBe("ECONNREFUSED");
    expect(failed.failures).toBe(1);
    expect(failed.degraded).toBe(true);

    const failedAgain = pollFailed(failed, "boom", "2026-09-06T12:00:30Z");
    expect(failedAgain.failures).toBe(2);
    expect(failedAgain.lastSuccessAt).toBe("2026-09-06T12:00:00Z");

    const recovered = pollSucceeded(failedAgain, { value: 3 }, "2026-09-06T12:00:45Z");
    expect(recovered.failures).toBe(0);
    expect(recovered.degraded).toBe(false);
    expect(recovered.error).toBeUndefined();
  });

  it("distinguishes a ticking clock from a successful read", () => {
    const initial = initialPollState("ssr");
    expect(describePollRead(initial)).toMatchObject({ tone: "muted", label: "server render" });

    const failedBeforeAnySuccess = pollFailed(initial, new Error("500"), "2026-09-06T12:00:15Z");
    const described = describePollRead(failedBeforeAnySuccess);
    expect(described.tone).toBe("down");
    // Labels and details are Europe/Amsterdam wall clock (CEST in September).
    expect(described.label).toBe("read failed 14:00:15 CEST");
    expect(described.detail).toMatch(/values from the server render/);
    expect(described.detail).toMatch(/Attempt at 14:00:15 CEST/);
    expect(described.detail).not.toMatch(/Z/);

    const ok = pollSucceeded(initial, "fresh", "2026-09-06T12:00:30Z");
    expect(describePollRead(ok)).toMatchObject({ tone: "ok", label: "read 14:00:30 CEST" });
    expect(describePollRead(ok).detail).toMatch(/at 14:00:30 CEST/);
    expect(describePollRead(ok).detail).not.toMatch(/Z/);

    const failedAfterSuccess = pollFailed(ok, new Error("timeout"), "2026-09-06T12:00:45Z");
    expect(describePollRead(failedAfterSuccess).detail).toMatch(/values from 14:00:30 CEST/);
    expect(describePollRead(failedAfterSuccess).detail).not.toMatch(/Z/);

    const winter = pollSucceeded(initial, "fresh", "2026-01-06T12:00:30Z");
    expect(describePollRead(winter).label).toBe("read 13:00:30 CET");
  });

  it("ticks the read age from the last successful read only", () => {
    const initial = initialPollState("ssr");
    expect(readAgeLabel(initial, "2026-09-06T12:00:00Z")).toBeUndefined();

    const ok = pollSucceeded(initial, "fresh", "2026-09-06T12:00:30Z");
    expect(readAgeLabel(ok, undefined)).toBeUndefined();
    expect(readAgeLabel(ok, "2026-09-06T12:00:33Z")).toBe("updated 3s ago");
    expect(readAgeLabel(ok, "2026-09-06T12:03:35Z")).toBe("updated 3m 05s ago");

    // A failed attempt does not reset the age: the clock keeps counting from the last good read.
    const failed = pollFailed(ok, new Error("timeout"), "2026-09-06T12:00:45Z");
    expect(readAgeLabel(failed, "2026-09-06T12:00:50Z")).toBe("last good read 20s ago");
  });

  it("formats ages without inventing values", () => {
    expect(secondsBetween("2026-09-06T12:00:00Z", "2026-09-06T12:01:30Z")).toBe(90);
    expect(secondsBetween(undefined, "2026-09-06T12:01:30Z")).toBeUndefined();
    expect(formatAgeSeconds(undefined)).toBe("n/a");
    expect(formatAgeSeconds(12)).toBe("12s");
    expect(formatAgeSeconds(185)).toBe("3m 05s");
    expect(formatAgeSeconds(7_800)).toBe("2h 10m");
    expect(formatAgeSeconds(100_000)).toBe("1d 3h");
  });
});
