import { describe, expect, it } from "vitest";

import {
  extractBtcMid,
  fetchPublicBtcPerpMid,
  HYPERLIQUID_PUBLIC_INFO_URL,
  parsePublicBtcPerpPayload,
} from "./public-price";

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

  it("fails closed unless the cockpit API payload is a credentialless unsigned mid", () => {
    const price = parsePublicBtcPerpPayload({
      coin: "BTC",
      instrument: "BTC-PERP",
      mid: "81156.0",
      source: "hyperliquid-public-info-allMids",
      endpoint: HYPERLIQUID_PUBLIC_INFO_URL,
      signing: false,
      credentialless: true,
      fetched_at: "2026-09-06T10:16:00.000Z",
    });
    expect(price.mid).toBe("81156.0");
    expect(() => parsePublicBtcPerpPayload({ mid: "81156.0" })).toThrow(/credentialless/);
    expect(() => parsePublicBtcPerpPayload({ error: "no" })).toThrow(/BTC mid/);
  });
});
