import { describe, expect, it } from "vitest";

import {
  DATA1A_RUNNING_PENDING_HEALTH,
  data1aCaptureHealthPresentation,
  elapsedSecondsSinceRunId,
  formatGroupedNumber,
  healthTone,
  presentCopiedNumber,
  presentCopiedText,
  presentData1ADuration,
  signedTone,
  uniqueStrings,
  yesNo,
} from "./display";

describe("cockpit display helpers", () => {
  it("groups integer digits without changing the copied decimal string", () => {
    expect(formatGroupedNumber("81156.0")).toBe("81,156.0");
    expect(formatGroupedNumber("100000")).toBe("100,000");
    expect(formatGroupedNumber("-0.0126583080")).toBe("-0.0126583080");
    expect(formatGroupedNumber("0.00000")).toBe("0.00000");
    expect(formatGroupedNumber("not-a-mid")).toBe("not-a-mid");
  });

  it("colors assumed PnL from the copied string and does not invent a number", () => {
    expect(signedTone("-0.0126583080")).toBe("down");
    expect(signedTone("0.00000")).toBe("flat");
    expect(signedTone("1.25")).toBe("up");
    expect(signedTone("")).toBe("unknown");
    expect(signedTone("assumed")).toBe("unknown");
  });

  it("maps COURSE-1 soak and DATA-1A capture statuses without treating them as 24/7 heartbeats", () => {
    expect(healthTone("COMPLETED_FLAT")).toBe("ok");
    expect(healthTone("COMPLETED")).toBe("ok");
    expect(healthTone("BOUNDED_TIMEOUT")).toBe("warn");
    expect(healthTone("OPERATOR_STOP")).toBe("warn");
    expect(healthTone("RISK_REJECTED")).toBe("down");
    expect(healthTone("FAILED")).toBe("down");
    expect(healthTone("UNKNOWN")).toBe("neutral");
  });

  it("keeps missing copied numbers as n/a instead of inventing zero", () => {
    expect(presentCopiedNumber(undefined)).toBe("n/a");
    expect(presentCopiedNumber(0)).toBe("0");
    expect(presentCopiedText(undefined)).toBe("n/a");
    expect(presentCopiedText("STARTED_FAIL_CLOSED")).toBe("STARTED_FAIL_CLOSED");
  });

  it("keeps boolean flags as yes/no labels", () => {
    expect(yesNo(true)).toBe("yes");
    expect(yesNo(false)).toBe("no");
    expect(uniqueStrings(["a", "a", "b"])).toEqual(["a", "b"]);
  });

  it("labels a live-looking DATA-1A run as RUNNING when claim parts exist and health is pending", () => {
    const presentation = data1aCaptureHealthPresentation({
      health: undefined,
      health_missing: true,
      health_error: "capture-health.json is not written yet",
      parts: {
        raw_dir_present: true,
        count: 2,
        last_part_mtime_utc: "2026-09-04T13:49:00.000Z",
      },
    });
    expect(presentation.statusLabel).toBe(DATA1A_RUNNING_PENDING_HEALTH);
    expect(presentation.tileLabel).toBe("RUNNING");
    expect(presentation.tone).toBe("ok");
    expect(presentation.live).toBe(true);
    expect(presentation.note).toMatch(/written at stop/);
  });

  it("does not invent RUNNING when health is missing and no parquet parts exist", () => {
    const presentation = data1aCaptureHealthPresentation({
      health: undefined,
      health_missing: true,
      parts: { raw_dir_present: false, count: undefined, last_part_mtime_utc: undefined },
    });
    expect(presentation.statusLabel).toBe("NOT WRITTEN");
    expect(presentation.tileLabel).toBe("NOT WRITTEN");
    expect(presentation.live).toBe(false);
    expect(presentation.tone).toBe("warn");
  });

  it("derives elapsed capture duration from a timestamp run_id and observed_at without inventing PnL", () => {
    expect(
      elapsedSecondsSinceRunId("20260904t134940z-live-retained", "2026-09-04T13:49:45.000Z"),
    ).toBe(5);
    expect(elapsedSecondsSinceRunId("sample-run", "2026-09-04T13:49:45.000Z")).toBeUndefined();
    expect(
      elapsedSecondsSinceRunId("20260904t134940z-live-retained", "2026-09-04T13:49:00.000Z"),
    ).toBeUndefined();
    expect(
      presentData1ADuration({
        runId: "20260904t134940z-live-retained",
        observed_at: "2026-09-04T13:49:45.000Z",
        claim: { duration_seconds: 259200 },
      }),
    ).toBe("5s elapsed / 259200s claimed");
    expect(
      presentData1ADuration({
        runId: "sample-run",
        observed_at: "2026-09-04T13:49:45.000Z",
        claim: { duration_seconds: 86400 },
      }),
    ).toBe("86400s claimed");
  });

  it("copies finished DATA-1A health status instead of the pending-until-stop label", () => {
    const presentation = data1aCaptureHealthPresentation({
      health: { status: "OPERATOR_STOP" },
      health_missing: false,
      parts: {
        raw_dir_present: true,
        count: 41,
        last_part_mtime_utc: "2026-09-04T01:17:00.000Z",
      },
    });
    expect(presentation.statusLabel).toBe("OPERATOR_STOP");
    expect(presentation.tileLabel).toBe("OPERATOR_STOP");
    expect(presentation.tone).toBe("warn");
    expect(presentation.live).toBe(false);
  });
});
