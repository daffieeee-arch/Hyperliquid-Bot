import { describe, expect, it } from "vitest";

import { extractBtcMid, fetchPublicBtcPerpMid, HYPERLIQUID_PUBLIC_INFO_URL } from "./public-price";

describe("public BTC-PERP mid", () => {
  it("reads the BTC string from allMids and does not invent a number", () => {
    expect(extractBtcMid({ BTC: "81156.0", ETH: "1" })).toBe("81156.0");
    expect(() => extractBtcMid({ ETH: "1" })).toThrow(/BTC mid/);
    expect(() => extractBtcMid({ BTC: 81156 })).toThrow(/BTC mid/);
  });

  it("labels the public info route as credentialless and unsigned", async () => {
    const fetchImpl: typeof fetch = async (input) => {
      expect(String(input)).toBe(HYPERLIQUID_PUBLIC_INFO_URL);
      return new Response(JSON.stringify({ BTC: "81254.5" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    const price = await fetchPublicBtcPerpMid(fetchImpl, () => "2026-09-04T00:00:00.000Z");
    expect(price.mid).toBe("81254.5");
    expect(price.signing).toBe(false);
    expect(price.credentialless).toBe(true);
    expect(price.source).toBe("hyperliquid-public-info-allMids");
  });
});
