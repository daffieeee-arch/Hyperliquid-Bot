import { describe, expect, it } from "vitest";

import {
  clockLabel,
  describePollRead,
  formatAgeSeconds,
  initialPollState,
  pollFailed,
  pollSucceeded,
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
    expect(described.label).toBe("read failed 12:00:15Z");
    expect(described.detail).toMatch(/values from the server render/);

    const ok = pollSucceeded(initial, "fresh", "2026-09-06T12:00:30Z");
    expect(describePollRead(ok)).toMatchObject({ tone: "ok", label: "read 12:00:30Z" });

    const failedAfterSuccess = pollFailed(ok, new Error("timeout"), "2026-09-06T12:00:45Z");
    expect(describePollRead(failedAfterSuccess).detail).toMatch(/values from 12:00:30Z/);
  });

  it("formats clock labels and ages without inventing values", () => {
    expect(clockLabel(undefined)).toBe("—");
    expect(clockLabel("not-a-date")).toBe("—");
    expect(clockLabel("2026-09-06T12:34:56.789Z")).toBe("12:34:56Z");
    expect(secondsBetween("2026-09-06T12:00:00Z", "2026-09-06T12:01:30Z")).toBe(90);
    expect(secondsBetween(undefined, "2026-09-06T12:01:30Z")).toBeUndefined();
    expect(formatAgeSeconds(undefined)).toBe("n/a");
    expect(formatAgeSeconds(12)).toBe("12s");
    expect(formatAgeSeconds(185)).toBe("3m 05s");
    expect(formatAgeSeconds(7_800)).toBe("2h 10m");
    expect(formatAgeSeconds(100_000)).toBe("1d 3h");
  });
});
