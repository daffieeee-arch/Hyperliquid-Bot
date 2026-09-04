import { describe, expect, it } from "vitest";

import {
  formatGroupedNumber,
  healthTone,
  presentCopiedNumber,
  presentCopiedText,
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
});
