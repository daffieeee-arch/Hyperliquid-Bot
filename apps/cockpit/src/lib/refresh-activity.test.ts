import { describe, expect, it } from "vitest";

import {
  contentChanged,
  contentFingerprint,
  describeRefreshFeedback,
  initialRefreshActivity,
  refreshBegan,
  refreshSettled,
} from "./refresh-activity";

const T0 = "2026-09-06T12:00:00Z";
const T1 = "2026-09-06T12:00:01Z";
const T2 = "2026-09-06T12:00:02Z";

describe("refresh activity registry", () => {
  it("counts in-flight requests and settles a token only when all its fetches are done", () => {
    let state = initialRefreshActivity();
    state = refreshBegan(state, 1);
    state = refreshBegan(state, 1);
    state = refreshBegan(state, 1);
    expect(state.inFlight).toBe(3);
    expect(describeRefreshFeedback(state, T0)).toMatchObject({ busy: true, label: "Refreshing…" });

    state = refreshSettled(state, 1, { changed: false }, T0);
    state = refreshSettled(state, 1, { changed: true }, T1);
    expect(state.inFlight).toBe(1);
    expect(state.lastOutcome).toBeNull();

    state = refreshSettled(state, 1, { changed: false }, T2);
    expect(state.inFlight).toBe(0);
    expect(state.pending).toEqual({});
    expect(state.lastOutcome).toEqual({
      token: 1,
      settledAt: T2,
      fetches: 3,
      changed: true,
      errors: [],
    });
    expect(state.lastChangedAt).toBe(T2);
    expect(describeRefreshFeedback(state, T2)).toMatchObject({
      tone: "ok",
      busy: false,
      label: "Updated 14:00:02 CEST",
    });
  });

  it("reports 'already current' with the age since data last changed", () => {
    let state = initialRefreshActivity();
    state = refreshBegan(state, 1);
    state = refreshSettled(state, 1, { changed: true }, T0);
    state = refreshBegan(state, 2);
    state = refreshSettled(state, 2, { changed: false }, T1);
    expect(state.lastChangedAt).toBe(T0);
    expect(describeRefreshFeedback(state, "2026-09-06T12:00:15Z")).toMatchObject({
      tone: "muted",
      label: "Already current · age 15s",
    });
    // Before the client clock mounted the label falls back to the settle time.
    expect(describeRefreshFeedback(state, undefined).label).toBe("Already current · 14:00:01 CEST");
  });

  it("re-enables the control and shows a short reason when any fetch failed", () => {
    let state = initialRefreshActivity();
    state = refreshBegan(state, 3);
    state = refreshBegan(state, 3);
    state = refreshSettled(state, 3, { changed: true }, T0);
    state = refreshSettled(
      state,
      3,
      { changed: false, error: "Capture strip request failed (503)" },
      T1,
    );
    const feedback = describeRefreshFeedback(state, T2);
    expect(feedback.busy).toBe(false);
    expect(feedback.tone).toBe("down");
    expect(feedback.label).toBe("Refresh failed · Capture strip request failed (503)");
    expect(feedback.detail).toMatch(/1 of 2 request\(s\) failed at 12:00:01Z/);
    // A failed tick never advances "data last changed".
    expect(state.lastChangedAt).toBeNull();
  });

  it("truncates long reasons and counts additional failures", () => {
    let state = initialRefreshActivity();
    state = refreshBegan(state, 4);
    state = refreshBegan(state, 4);
    state = refreshSettled(state, 4, { changed: false, error: "x".repeat(100) }, T0);
    state = refreshSettled(state, 4, { changed: false, error: "second" }, T1);
    const { label } = describeRefreshFeedback(state, T2);
    expect(label.startsWith("Refresh failed · " + "x".repeat(59) + "…")).toBe(true);
    expect(label.endsWith("(+1 more)")).toBe(true);
  });

  it("keeps overlapping tokens apart and ignores an end without a begin", () => {
    let state = initialRefreshActivity();
    state = refreshBegan(state, 1);
    state = refreshBegan(state, 2);
    expect(state.inFlight).toBe(2);
    state = refreshSettled(state, 2, { changed: true }, T1);
    expect(state.lastOutcome?.token).toBe(2);
    expect(state.inFlight).toBe(1);
    expect(describeRefreshFeedback(state, T1).busy).toBe(true);
    const unchanged = refreshSettled(state, 9, { changed: true }, T2);
    expect(unchanged).toBe(state);
    expect(describeRefreshFeedback(initialRefreshActivity(), T0)).toMatchObject({
      tone: "muted",
      label: "No refresh yet",
    });
  });

  it("ignores backend read stamps when deciding whether data changed", () => {
    const a = { ok: true, tape: { observed_at: T0, venues: [{ lastEventAgeS: 3, price: "1" }] } };
    const b = { ok: true, tape: { observed_at: T1, venues: [{ lastEventAgeS: 4, price: "1" }] } };
    const c = { ok: true, tape: { observed_at: T1, venues: [{ lastEventAgeS: 4, price: "2" }] } };
    expect(contentChanged(a, b)).toBe(false);
    expect(contentChanged(a, c)).toBe(true);
    expect(contentFingerprint({ fetched_at: T0, mid: "1" })).toBe('{"mid":"1"}');
    expect(contentFingerprint({ n: 10n })).toBe('{"n":"10"}');
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(contentFingerprint(circular)).toBeUndefined();
    expect(contentChanged(circular, circular)).toBe(true);
  });
});
