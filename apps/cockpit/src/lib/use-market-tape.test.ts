import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "../app/api/market-tape/route";
import { resetMarketTapeCache } from "./market-tape";
import { fetchMarketTape, marketTapeRequestUrl, parseMarketTapeResponse } from "./use-market-tape";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixtureRoot = resolve(repoRoot, "tests", "fixtures", "market_tape", "artifact-root");

describe("market tape poll helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    resetMarketTapeCache();
  });

  it("encodes only present venue run ids", () => {
    expect(marketTapeRequestUrl()).toBe("/api/market-tape");
    expect(marketTapeRequestUrl({ data1a_run_id: " " })).toBe("/api/market-tape");
    expect(marketTapeRequestUrl({ data1a_run_id: "hl", data1f_run_id: "bn" })).toBe(
      "/api/market-tape?data1a_run_id=hl&data1f_run_id=bn",
    );
  });

  it("fails closed on malformed payloads and surfaces backend errors as read failures", () => {
    expect(() => parseMarketTapeResponse(null)).toThrow(/fail-closed/);
    expect(() => parseMarketTapeResponse({ ok: true })).toThrow(/missing the tape/);
    expect(() => parseMarketTapeResponse({ ok: false, error: "PAPER-only" })).toThrow("PAPER-only");
    expect(() => parseMarketTapeResponse({ ok: false })).toThrow(/unavailable/);
    const payload = { ok: true, tape: { observed_at: "x", venues: [], cache: {} } };
    expect(parseMarketTapeResponse(payload)).toEqual(payload);
  });

  it("fetches with cache no-store", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ ok: true, tape: { observed_at: "x", venues: [], cache: {} } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await fetchMarketTape({ data1f_run_id: "bn" });
    expect(fetchMock).toHaveBeenCalledWith("/api/market-tape?data1f_run_id=bn", {
      cache: "no-store",
    });
  });
});

describe("GET /api/market-tape", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    resetMarketTapeCache();
  });

  it("refuses non-PAPER modes with 403 and never returns a tape", async () => {
    vi.stubEnv("TRADING_MODE", "LIVE");
    const response = await GET(new Request("http://cockpit.local/api/market-tape"));
    expect(response.status).toBe(403);
    expect(response.headers.get("cache-control")).toContain("no-store");
    const body: unknown = await response.json();
    expect(body).toMatchObject({ ok: false });
    expect(() => parseMarketTapeResponse(body)).toThrow(/PAPER/);
  });

  it("serves stored data per venue from the fixture root and reports a missing run per venue", async () => {
    vi.stubEnv("TRADING_MODE", "PAPER");
    vi.stubEnv("ARTIFACT_ROOT", fixtureRoot);
    vi.stubEnv("COCKPIT_DATA1A_RUN_ID", "20260905t180000z-live-retained");
    vi.stubEnv("COCKPIT_DATA1E_RUN_ID", "20260905t180200z-live-retained");
    vi.stubEnv("COCKPIT_DATA1B_RUN_ID", "20260905t180300z-live-retained");
    // Binance restarted: the query names a run that does not exist on disk.
    const response = await GET(
      new Request("http://cockpit.local/api/market-tape?data1f_run_id=20260906t101559z-live-retained"),
    );
    expect(response.status).toBe(200);
    const parsed = parseMarketTapeResponse(await response.json());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const byId = new Map(parsed.tape.venues.map((venue) => [venue.id, venue]));
    expect(byId.get("hl")?.status).toBe("ok");
    expect(byId.get("hl")?.instruments[0]?.lastTrade?.price).toBe("109510.0");
    expect(byId.get("binance")).toMatchObject({
      status: "missing",
      runId: "20260906t101559z-live-retained",
      bindingSource: "query",
      instruments: [],
    });
    expect(byId.get("bitvavo")?.instruments[0]?.quote).toBe("EUR");
    expect(byId.get("kraken")?.instruments[0]?.quote).toBe("USD");

    // Second call: nothing new was published, so nothing is parsed again.
    const again = parseMarketTapeResponse(
      await (
        await GET(
          new Request(
            "http://cockpit.local/api/market-tape?data1f_run_id=20260906t101559z-live-retained",
          ),
        )
      ).json(),
    );
    expect(again.ok && again.tape.cache.partsParsedThisCall).toBe(0);
  });
});
