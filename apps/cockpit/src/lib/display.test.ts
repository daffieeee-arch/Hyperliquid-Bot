import { describe, expect, it } from "vitest";

import { STALE_MTIME_REASON } from "./capture-freshness";
import {
  DATA1A_RUNNING_PENDING_HEALTH,
  DATA1A_STALE_MTIME_LABEL,
  data1aCaptureHealthPresentation,
  captureBindingSourceLabel,
  captureRunOptionLabel,
  paperRunSourceLabel,
  elapsedSecondsSinceRunId,
  formatAssumedUsdcDisplay,
  formatGroupedNumber,
  healthTone,
  parseRunIdStartedAt,
  presentCopiedNumber,
  presentCopiedText,
  presentData1ADuration,
  presentGapReconnect,
  presentGapReconnectClusters,
  presentLastPartAge,
  presentLastPartMtime,
  signedTone,
  uniqueStrings,
  venueCaptureChipStatus,
  yesNo,
} from "./display";
import {
  TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
  TERRAPC_SHARED_RETAIN_RUN_ID,
} from "./terrapc-defaults";

describe("cockpit display helpers", () => {
  it("groups integer digits without changing the copied decimal string", () => {
    expect(formatGroupedNumber("81156.0")).toBe("81,156.0");
    expect(formatGroupedNumber("100000")).toBe("100,000");
    expect(formatGroupedNumber("-0.0126583080")).toBe("-0.0126583080");
    expect(formatGroupedNumber("0.00000")).toBe("0.00000");
    expect(formatGroupedNumber("not-a-mid")).toBe("not-a-mid");
  });

  it("scans assumed USDC at 2–4 decimals and keeps unparseable copied text", () => {
    expect(formatAssumedUsdcDisplay("-0.0126583080")).toBe("-0.0127");
    expect(formatAssumedUsdcDisplay("12.34567")).toBe("12.35");
    expect(formatAssumedUsdcDisplay("UNAVAILABLE")).toBe("UNAVAILABLE");
    expect(formatAssumedUsdcDisplay("not-a-pnl")).toBe("not-a-pnl");
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

  it("reconstructs UTC start from a YYYYMMDDtHHMMSSz run_id and fails closed otherwise", () => {
    expect(parseRunIdStartedAt(TERRAPC_SHARED_RETAIN_RUN_ID)).toBe("2026-09-05T23:26:35Z");
    expect(parseRunIdStartedAt(TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID)).toBe("2026-09-05T23:58:30Z");
    expect(parseRunIdStartedAt(TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID)).toBe("2026-09-06T10:15:59Z");
    expect(parseRunIdStartedAt("sample-run")).toBeUndefined();
    expect(captureBindingSourceLabel("auto-detect")).toBe("auto-detect");
    expect(captureBindingSourceLabel("query")).toBe("query");
    expect(captureBindingSourceLabel("unbound")).toBe("unbound");
    expect(paperRunSourceLabel("default-fixture")).toBe("default-fixture");
    expect(paperRunSourceLabel("path-contract")).toBe("path-contract");
    expect(paperRunSourceLabel("paper-run-dir")).toBe("paper-run-dir");
  });

  it("keeps boolean flags as yes/no labels", () => {
    expect(yesNo(true)).toBe("yes");
    expect(yesNo(false)).toBe("no");
    expect(uniqueStrings(["a", "a", "b"])).toEqual(["a", "b"]);
  });

  it("labels a live-looking DATA-1A run as RUNNING when claim parts are fresh and health is pending", () => {
    const presentation = data1aCaptureHealthPresentation({
      health: undefined,
      health_missing: true,
      health_error: "capture-health.json is not written yet",
      observed_at: "2026-09-04T13:49:40.000Z",
      fresh_max_s: 180,
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
    expect(presentation.reason).toBeUndefined();
    expect(presentation.note).toMatch(/written at stop/);
  });

  it("labels a claim with stale last_part_mtime as STALE (stale_mtime), not RUNNING", () => {
    const presentation = data1aCaptureHealthPresentation({
      health: undefined,
      health_missing: true,
      health_error: "capture-health.json is not written yet",
      observed_at: "2026-09-04T13:49:40.000Z",
      fresh_max_s: 180,
      parts: {
        raw_dir_present: true,
        count: 2,
        last_part_mtime_utc: "2026-09-04T13:40:00.000Z",
      },
    });
    expect(presentation.statusLabel).toBe(DATA1A_STALE_MTIME_LABEL);
    expect(presentation.tileLabel).toBe("STALE");
    expect(presentation.tone).toBe("warn");
    expect(presentation.live).toBe(false);
    expect(presentation.reason).toBe(STALE_MTIME_REASON);
    expect(presentation.note).toMatch(/COCKPIT_CAPTURE_FRESH_MAX_S=180/);
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
    ).toBe("5s elapsed / 72h claimed");
    expect(
      presentData1ADuration({
        runId: "sample-run",
        observed_at: "2026-09-04T13:49:45.000Z",
        claim: { duration_seconds: 86400 },
      }),
    ).toBe("24h claimed");
  });

  it("copies gaps/reconnects only when health JSON supplied them", () => {
    expect(presentGapReconnect(undefined, undefined)).toBe("n/a");
    expect(presentGapReconnect(0, 0)).toBe("0/0");
    expect(presentGapReconnect(2, undefined)).toBe("2/n/a");
    expect(presentGapReconnectClusters(undefined, undefined, undefined)).toBe("n/a");
    expect(presentGapReconnectClusters(1, 6, 3)).toBe("1 gaps · 6 raw / 3 clusters");
    expect(presentGapReconnectClusters(0, 2, undefined)).toBe("0 gaps · 2 raw / n/a clusters");
    expect(presentLastPartMtime(undefined)).toBe("n/a");
    expect(presentLastPartMtime("2026-09-06T10:15:59.000Z")).toBe("2026-09-06 12:15:59 CEST");
    expect(presentLastPartMtime("not-a-time")).toBe("n/a");
    expect(
      captureRunOptionLabel("binance", {
        run_id: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
        has_claim: true,
        has_health: false,
        live: true,
      }),
    ).toBe(`${TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID} · live · TerraPC`);
    expect(
      captureRunOptionLabel("binance", {
        run_id: TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID,
        has_claim: true,
        has_health: true,
        live: false,
      }),
    ).toBe(`${TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID} · stopped`);
  });

  it("formats last part age from mtime without inventing a zero age", () => {
    expect(presentLastPartAge(undefined, "2026-09-04T13:49:45.000Z")).toBe("n/a");
    expect(presentLastPartAge("2026-09-04T13:49:33.000Z", "2026-09-04T13:49:45.000Z")).toBe("12s");
    expect(presentLastPartAge("2026-09-04T13:19:45.000Z", "2026-09-04T13:49:45.000Z")).toBe("30m");
    expect(presentLastPartAge("2026-09-04T11:49:45.000Z", "2026-09-04T13:49:45.000Z")).toBe("2h");
    expect(presentLastPartAge("2026-09-01T13:49:45.000Z", "2026-09-04T13:49:45.000Z")).toBe("3d");
    expect(presentLastPartAge("2026-09-04T13:50:00.000Z", "2026-09-04T13:49:45.000Z")).toBe("n/a");
  });

  it("maps DATA-1A health presentation onto strip chips without inventing RUNNING", () => {
    expect(
      venueCaptureChipStatus(
        data1aCaptureHealthPresentation({
          health: undefined,
          health_missing: true,
          observed_at: "2026-09-04T13:49:40.000Z",
          fresh_max_s: 180,
          parts: {
            raw_dir_present: true,
            count: 2,
            last_part_mtime_utc: "2026-09-04T13:49:00.000Z",
          },
        }),
      ),
    ).toBe("RUNNING");
    expect(
      venueCaptureChipStatus(
        data1aCaptureHealthPresentation({
          health: undefined,
          health_missing: true,
          observed_at: "2026-09-04T13:49:40.000Z",
          fresh_max_s: 180,
          parts: {
            raw_dir_present: true,
            count: 2,
            last_part_mtime_utc: "2026-09-04T13:40:00.000Z",
          },
        }),
      ),
    ).toBe("STALE");
    expect(
      venueCaptureChipStatus(
        data1aCaptureHealthPresentation({
          health: { status: "COMPLETED" },
          health_missing: false,
          parts: {
            raw_dir_present: true,
            count: 3,
            last_part_mtime_utc: "2026-09-04T13:49:00.000Z",
          },
        }),
      ),
    ).toBe("STOPPED");
    expect(
      venueCaptureChipStatus(
        data1aCaptureHealthPresentation({
          health: { status: "OPERATOR_STOP" },
          health_missing: false,
          parts: {
            raw_dir_present: true,
            count: 41,
            last_part_mtime_utc: "2026-09-04T01:17:00.000Z",
          },
        }),
      ),
    ).toBe("STOPPED");
    expect(
      venueCaptureChipStatus(
        data1aCaptureHealthPresentation({
          health: { status: "FAILED" },
          health_missing: false,
          parts: {
            raw_dir_present: true,
            count: 1,
            last_part_mtime_utc: "2026-09-04T13:49:00.000Z",
          },
        }),
      ),
    ).toBe("DEGRADED");
    expect(
      venueCaptureChipStatus(
        data1aCaptureHealthPresentation({
          health: undefined,
          health_missing: true,
          parts: { raw_dir_present: false, count: undefined, last_part_mtime_utc: undefined },
        }),
      ),
    ).toBe("DEGRADED");
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
